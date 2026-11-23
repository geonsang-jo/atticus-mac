"""Spoken session and test requests backed by an owner-bound observer process."""

from __future__ import annotations

from contextlib import contextmanager, suppress
import ctypes
import fcntl
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata

from .sessions import SessionStream


_MAX_WATCHES = 16
_MAX_NOTIFICATIONS = 64
_MAX_NOTIFICATION_ID = 200
_SELECTION_SECONDS = 120
_PAGE_SIZE = 8
_STREAM_SECONDS = 50
_DAEMON_START_SECONDS = 2.5
_LEASE_FD = "ATTICUS_ASSISTANT_LEASE_FD"
_OWNER_BIRTH = "ATTICUS_ASSISTANT_OWNER_BIRTH"
_STATES = {
    "working": "작업 중", "blocked": "입력 필요", "done": "작업 완료",
    "error": "오류 발생", "idle": "대기 중", "unknown": "상태 확인 불가",
}


def _state_path():
    return Path(os.environ.get("ATTICUS_DIR") or Path.home() / ".config/atticus") / "assistant"


def _private_directory():
    directory = _state_path()
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError("Invalid assistant state directory")
    if stat.S_IMODE(info.st_mode) != 0o700:
        directory.chmod(0o700)
    return directory


def _lock_fd(name):
    path = _private_directory() / name
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise OSError("Invalid assistant lock file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
        return fd
    except OSError:
        os.close(fd)
        raise


def _read_state(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    with os.fdopen(fd, encoding="utf-8") as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise OSError("Invalid assistant state file")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Invalid assistant state")
    return value


@contextmanager
def _state(write=False):
    fd = _lock_fd("state.lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        path = _state_path() / "state.json"
        state = _read_state(path)
        yield state
        if write:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".state-", delete=False) as stream:
                temp = stream.name
                try:
                    json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.replace(temp, path)
                finally:
                    with suppress(FileNotFoundError):
                        os.unlink(temp)
    finally:
        os.close(fd)


def _receipt(state, identifier, reply, kind):
    queue = state.setdefault("notifications", [])
    if all(item["id"] != identifier for item in queue):
        queue.append({"id": identifier, "text": reply, "kind": kind})
        del queue[:-_MAX_NOTIFICATIONS]


def peek_notifications() -> list[dict]:
    """Read pending notifications without changing their delivery state."""
    try:
        # os.replace makes the queue visible as a complete snapshot to readers.
        return _read_state(_state_path() / "state.json").get("notifications", [])
    except (OSError, ValueError):
        return []


def ack_notifications(ids: list[str]) -> int:
    """Acknowledge exactly the delivered notification IDs in one transaction."""
    if not isinstance(ids, list) or len(ids) > _MAX_NOTIFICATIONS or any(
        not isinstance(identifier, str) or not 1 <= len(identifier) <= _MAX_NOTIFICATION_ID
        or not identifier.isprintable() or identifier.strip() != identifier
        for identifier in ids
    ):
        raise ValueError("Invalid notification IDs")
    delivered = set(ids)
    if not delivered:
        return 0
    queue = _read_state(_state_path() / "state.json").get("notifications", [])
    if not any(item["id"] in delivered for item in queue):
        return 0
    with _state(write=True) as state:
        queue = state.get("notifications", [])
        pending = [item for item in queue if item["id"] not in delivered]
        state["notifications"] = pending
        return len(queue) - len(pending)


def pop_notifications() -> list[dict]:
    """Consume the pending notifications in a private atomic transaction."""
    try:
        if not _state_path().exists():
            return []
        with _state() as state:
            if not state.get("notifications"):
                return []
        with _state(write=True) as state:
            return state.pop("notifications", [])
    except (OSError, ValueError):
        return []


def _publish(reducer, completions, daemon=None, owner=None):
    sessions = reducer.sessions if reducer else []
    for item in sessions:
        item["watch_watermark"] = reducer.watch_watermark(item)
    with _state(write=True) as state:
        state["herdr"] = {
            "connected": bool(reducer and reducer.connected), "sessions": sessions,
            "published": time.time(), "daemon": daemon, "owner": owner,
        }
        watches = state.get("watches", {})
        for item in completions:
            watch = watches.get(item["id"])
            if watch is None or item["turn"] <= watch["last_notified_turn"]:
                continue
            watch["last_notified_turn"] = item["turn"]
            _receipt(state, f'session:{item["id"]}:{item["turn"]}',
                     f'{_session_name(item)} 세션의 작업이 끝났어요. 결과 요약은 확인할 수 없어요.', "session")


class _DarwinBSDInfo(ctypes.Structure):
    # proc_bsdinfo in the macOS SDK's sys/proc_info.h.
    _fields_ = [
        ("flags", ctypes.c_uint32), ("status", ctypes.c_uint32), ("exit_status", ctypes.c_uint32),
        ("pid", ctypes.c_uint32), ("ppid", ctypes.c_uint32), ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32), ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32), ("svgid", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
        ("command", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
        ("files", ctypes.c_uint32), ("pgid", ctypes.c_uint32), ("jobc", ctypes.c_uint32),
        ("tty", ctypes.c_uint32), ("tty_pgid", ctypes.c_uint32), ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64),
    ]


@lru_cache(maxsize=1)
def _darwin_proc_info():
    query = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidinfo
    query.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
    query.restype = ctypes.c_int
    return query


@lru_cache(maxsize=1)
def _linux_boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _process_identity(pid, *, include_zombie=False):
    if type(pid) is not int or not 0 < pid < 2**31:
        return None
    try:
        if sys.platform == "darwin":
            details = _DarwinBSDInfo()
            size = ctypes.sizeof(details)
            if _darwin_proc_info()(pid, 3, 0, ctypes.byref(details), size) != size:
                return None
            if details.pid != pid or details.uid != os.geteuid() or (details.status == 5 and not include_zombie):
                return None
            birth = f"{details.start_sec}.{details.start_usec:06d}"
        elif sys.platform.startswith("linux"):
            path = Path("/proc") / str(pid)
            if path.stat().st_uid != os.geteuid():
                return None
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] in {"Z", "X"} and not include_zombie:
                return None
            birth = f"{_linux_boot_id()}:{fields[19]}"
        else:
            return None
    except (OSError, IndexError):
        return None
    return {"pid": pid, "started": birth}


def _identity_is_live(identity):
    return bool(identity and identity == _process_identity(identity["pid"]))


def _acquire_lease():
    fd = _lock_fd("daemon.lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError:
        os.close(fd)
        raise


def ensure_daemon(owner_pid: int) -> bool:
    """Wait briefly for a live observer to claim this exact native app owner."""
    identity = _process_identity(owner_pid)
    if identity is None:
        return False
    deadline = time.monotonic() + _DAEMON_START_SECONDS
    child = None
    try:
        while _identity_is_live(identity):
            fd = _acquire_lease()
            if fd is not None:
                try:
                    if child is not None or time.monotonic() >= deadline:
                        return False
                    worker = Path(__file__).resolve().parent.parent / "atticus.py"
                    # The direct Python launch bypasses atticus.py's shell
                    # wrapper, which normally caches outside the signed app.
                    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                               **{_LEASE_FD: str(fd), _OWNER_BIRTH: identity["started"]})
                    child = subprocess.Popen([sys.executable, str(worker), "--assistant-daemon", str(owner_pid)],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, env=env, pass_fds=(fd,), start_new_session=True)
                finally:
                    os.close(fd)
            else:
                lease = _read_state(_state_path() / "state.json").get("herdr", {})
                if lease.get("owner") == identity and _identity_is_live(lease.get("daemon")):
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (child is not None and child.poll() is not None):
                return False
            time.sleep(min(remaining, 0.05))
    except (OSError, ValueError):
        pass
    return False


def _daemon_lease(inherited):
    if inherited is None:
        return _acquire_lease()
    fd = int(inherited)
    if fd < 3:
        raise ValueError("Invalid daemon lease descriptor")
    info = os.fstat(fd)
    path = _private_directory() / "daemon.lock"
    if not os.path.samestat(info, path.lstat()) or not stat.S_ISREG(info.st_mode):
        raise ValueError("Invalid daemon lease identity")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(fd, False)
    return fd


def run_daemon(owner_pid: int) -> None:
    from .test_runs import TestRunner

    inherited = os.environ.pop(_LEASE_FD, None)
    expected_birth = os.environ.pop(_OWNER_BIRTH, None)
    identity = _process_identity(owner_pid)
    if identity is None or (expected_birth and identity["started"] != expected_birth):
        return
    try:
        fd = _daemon_lease(inherited)
    except (OSError, ValueError):
        return
    if fd is None:
        return
    stop = threading.Event()
    handlers = {}
    stream = SessionStream()
    runner = TestRunner()
    daemon = _process_identity(os.getpid())
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            handlers[number] = signal.signal(number, lambda *_: stop.set())
        _publish(None, [], daemon, identity)
        while not stop.is_set() and _identity_is_live(identity):
            runner.tick()
            update = stream.tick()
            if update is not None:
                _publish(*update, daemon, identity)
            stop.wait(0.12)
    finally:
        try:
            try:
                stream.close()
            finally:
                runner.close()
                _publish(stream.reducer, [])
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
            os.close(fd)


def _normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).strip(" .?!。？！")


_ORDINALS = {"첫": 1, "첫째": 1, "한": 1, "두": 2, "둘째": 2, "세": 3, "셋째": 3,
             "네": 4, "넷째": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
             "열": 10, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7,
             "팔": 8, "구": 9, "십": 10, "one": 1, "two": 2, "three": 3, "four": 4,
             "first": 1, "second": 2, "third": 3, "fourth": 4}
_PICK = re.compile(r"(?:(세션|session|테스트|test)(?:은|는)?\s+)?(?:number\s+)?"
                   r"([0-9]{1,3}|" + "|".join(_ORDINALS) + r")\s*(?:번\s*째|번째|번|st|nd|rd|th)?"
                   r"(?:\s*(?:골라줘|선택해\s*줘|선택|으로|로))?\Z")
_SESSION = r"(?:(?P<prefix>.+?)\s+)?(?:세션(?:들|을|이|의)?|sessions?)\s*"
_TEST = r"(?:(?P<prefix>.+?)\s+)?(?:테스트(?:를|의)?|tests?)\s*"
_TARGET = r"(?:(?P<suffix>.+?)\s+)?"
_SAY = r"(?:\s*(?:알려\s*(?:줘|주세요)|말해\s*줘|확인해\s*(?:줘|주세요)|보여\s*줘))?"
_PAGE = re.compile(r"(다음|이전)\s+(세션|테스트)\s*목록\Z")
_PATTERNS = tuple((kind, action, re.compile(pattern + r"\Z")) for kind, action, pattern in (
    ("session", "unwatch", r"(?:(?:한|모든|세션|herdr|개발)\s+)*감시(?:를)?\s*(?:그만해|중지해|해제해|멈춰)"),
    ("session", "unwatch", r"stop (?:watching|monitoring)(?: (?:the )?(?:herdr )?sessions?)?"),
    ("session", "decline", _SESSION + _TARGET + r"감시(?:하지\s*마(?:요|세요)?|\s*말아\s*줘)"),
    ("test", "decline", _TEST + _TARGET + r"(?:실행|시작)하지\s*마(?:요|세요)?"),
    ("test", "decline", r"(?:don't|do not) run\s+(?:the\s+)?" + _TEST + r"(?P<suffix>[^?]*)"),
    ("session", "watch", _SESSION + _TARGET + r"(?:끝나면|완료(?:되|하)면)\s*(?:알려\s*(?:줘|주세요)|말해\s*줘)"),
    ("session", "watch", _SESSION + _TARGET + r"감시(?:해\s*(?:줘|주세요)|해)"),
    ("session", "watch", r"watch\s+(?:the\s+)?" + _SESSION + r"(?P<suffix>[^?]*)"),
    ("session", "watch", r"(?:tell me|let me know) when\s+(?:the\s+)?" + _SESSION + r"(?:ends|finishes)"),
    ("session", "result", _SESSION + _TARGET + r"(?:작업\s*)?결과(?:를)?" + _SAY),
    ("session", "query", _SESSION + _TARGET + r"(?:상태|뭐\s*(?:하고\s*있어|해|하는\s*중이야)|status)(?:야)?" + _SAY),
    ("session", "query", r"what are (?:my |the )?" + _SESSION + r"doing"),
    ("test", "start", _TEST + _TARGET + r"(?:실행(?:해\s*(?:줘|주세요)|해)?|돌려\s*(?:줘|주세요)?|시작(?:해\s*(?:줘|주세요)|해)?)"),
    ("test", "start", r"run\s+(?:the\s+)?" + _TEST + r"(?P<suffix>[^?]*)"),
    ("test", "result", _TEST + _TARGET + r"(?:최근\s*)?(?:결과(?:를)?|result)" + _SAY),
    ("test", "query", _TEST + _TARGET + r"(?:최근\s*)?(?:상태|status)(?:야)?" + _SAY),
))


def _parse(text):
    text = _normalize(text)
    if match := _PAGE.fullmatch(text):
        direction, label = match.groups()
        return "session" if label == "세션" else "test", "page", 1 if direction == "다음" else -1
    if match := _PICK.fullmatch(text):
        marker, ordinal = match.groups()
        kind = {"세션": "session", "session": "session", "테스트": "test", "test": "test"}.get(marker)
        return kind, "pick", int(ordinal) if ordinal.isascii() and ordinal.isdigit() else _ORDINALS[ordinal]
    for kind, action, pattern in _PATTERNS:
        if match := pattern.fullmatch(text):
            groups = match.groupdict()
            target = _normalize(groups.get("suffix") or groups.get("prefix") or "")
            if target in {"herdr", "개발", "코딩", "coding", "development", "지금", "현재", "최근"}:
                target = ""
            return kind, action, target
    return None


def _pending(state):
    context = state.get("selection")
    if context and 0 <= time.time() - context["created"] <= _SELECTION_SECONDS:
        return context
    return None


def recognizes(text: str) -> bool:
    """Recognize a command without creating files or changing observer state."""
    parsed = _parse(text)
    if not parsed:
        return False
    kind, action, _ = parsed
    if action != "pick" or kind is not None:
        return True
    try:
        return _pending(_read_state(_state_path() / "state.json")) is not None
    except (OSError, ValueError):
        return False


def _choice_prompt(context):
    labels = context.get("labels")
    if labels is None:
        return "목록을 다시 요청해 주세요."
    size = len(labels)
    start = context["page"] * _PAGE_SIZE
    end = min(start + _PAGE_SIZE, size)
    kind = "세션" if context["kind"] == "session" else "테스트"
    spoken = ", ".join(f"{index + 1}번 {labels[index]}" for index in range(start, end))
    pages = ([f"이전 {kind} 목록"] if start else []) + ([f"다음 {kind} 목록"] if end < size else [])
    suffix = f" {'이나 '.join(pages)}도 요청할 수 있어요." if pages else ""
    return f"어느 {kind}인가요? {spoken}. 번호를 말해 주세요.{suffix}"


def _offer(state, kind, action, choices):
    context = {"kind": kind, "action": action, "ids": [key for key, _ in choices],
               "labels": [label for _, label in choices], "page": 0, "created": time.time()}
    state["selection"] = context
    return _choice_prompt(context)


def _session_name(item):
    return item["address"] if item["name"] == item["address"] else f'{item["name"]} {item["address"]}'


def _observed(state):
    stream = state.get("herdr", {})
    if (stream.get("connected") and 0 <= time.time() - stream.get("published", 0) <= _STREAM_SECONDS
            and (not stream.get("daemon") or _identity_is_live(stream["daemon"]))):
        return stream.get("sessions", [])
    return None


def _session_reply(state, action, target, key=None):
    if action == "unwatch":
        old = state.pop("watches", {})
        return "세션 감시를 중지했어요." if old else "감시 중인 세션이 없어요."
    rows = _observed(state)
    if rows is None:
        return "herdr 연결 상태를 확인할 수 없어요."
    if target == "그":
        key = state.get("selected_session", "")
    if key is not None:
        rows = [row for row in rows if row["id"] == key]
    elif target:
        rows = [row for row in rows if target in {row["address"], _normalize(row["name"]), _normalize(_session_name(row)), row["agent"]}]
    if not rows:
        return "해당 세션의 상태를 확인할 수 없어요." if target or key is not None else "활성 개발 세션이 없어요."
    if action == "query":
        description = ", ".join(f'{_session_name(row)} {_STATES[row["state"]]}' for row in rows[:8])
        suffix = f" 외 {len(rows) - 8}개" if len(rows) > 8 else ""
        return f"{description}{suffix}."
    if len(rows) > 1:
        return _offer(state, "session", action, [(row["id"], _session_name(row)) for row in rows])
    row = rows[0]
    state["selected_session"] = row["id"]
    if action == "result":
        return f'{_session_name(row)} 세션 상태는 {_STATES[row["state"]]}입니다. 결과 요약은 확인할 수 없어요.'
    watches = state.setdefault("watches", {})
    if row["id"] not in watches and len(watches) >= _MAX_WATCHES:
        return "세션 감시 목록이 가득 찼어요. 기존 감시를 중지한 뒤 다시 요청해 주세요."
    watermark = row.get("watch_watermark", row["turn"])
    watches.setdefault(row["id"], {"last_notified_turn": watermark if watermark is not None else -1})
    return f'{_session_name(row)} 세션의 다음 작업이 끝나면 알려드릴게요.' if row["state"] == "done" else (
        f'{_session_name(row)} 세션의 작업이 끝나면 알려드릴게요.')


def handle(text: str) -> str | None:
    """Handle a confirmed spoken request using cached data and queued tests."""
    parsed = _parse(text)
    if not parsed or (parsed[:2] == (None, "pick") and not recognizes(text)):
        return None
    from .test_runs import test_reply

    kind, action, target = parsed
    try:
        with _state(write=action != "query") as state:
            key = None
            if action == "page":
                context = _pending(state)
                if not context or context["kind"] != kind:
                    return "선택 목록이 없어요. 먼저 감시나 테스트 실행을 요청해 주세요."
                last_page = (len(context["ids"]) - 1) // _PAGE_SIZE
                context["page"] = max(0, min(last_page, context.get("page", 0) + target))
                context["created"] = time.time()
                return _choice_prompt(context)
            if action == "pick":
                context = _pending(state)
                if not context or (kind and context["kind"] != kind):
                    return "선택을 기다리는 요청이 없어요. 먼저 감시나 테스트 실행을 요청해 주세요."
                if not 1 <= target <= len(context["ids"]):
                    return f'1번부터 {len(context["ids"])}번 중에서 골라 주세요.'
                key = context["ids"][target - 1]
                kind, action, target = context["kind"], context["action"], ""
                state.pop("selection", None)
            elif action != "query" and state.get("selection", {}).get("kind") == kind:
                state.pop("selection", None)
            if action == "decline":
                return "알겠어요."
            reply = _session_reply if kind == "session" else test_reply
            return reply(state, action, target, key)
    except (OSError, ValueError):
        return "비서의 상태를 확인할 수 없어요. 잠시 뒤 다시 요청해 주세요."
