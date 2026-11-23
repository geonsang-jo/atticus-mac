"""Deterministic YouTube commands and verified receipts from Aside."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit


_SCRIPT = Path(__file__).with_name("media_script.js")
_HOME_URL = "https://www.youtube.com/"
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_TARGET_FIELDS = ("targetId", "url", "videoId")
_MAX_SEEK = 300
_MAX_QUERY = 240
_SELECTION_TTL = 120
_PAGE_SIZE = 8
_MAX_OPTIONS = 40
_MAX_STATE_SIZE = 262_144
_PAGE_MOVES = {"다음 목록": 1, "이전 목록": -1}
_UNVERIFIED = "Aside에서 유튜브 동작을 확인할 수 없어요."
_PLAYER_UNAVAILABLE = "유튜브 플레이어 상태를 확인할 수 없어요."
_CAPABILITIES = "유튜브 열기와 검색, 열린 영상의 재생·정지, 소리와 시간 조절, 제목 확인을 지원해요."


class Action(str, Enum):
    OPEN = "open"
    SEARCH = "search"
    PAUSE = "pause"
    PLAY = "play"
    MUTE = "mute"
    UNMUTE = "unmute"
    VOLUME = "volume"
    SEEK = "seek"
    STATUS = "status"
    INVALID = "invalid"
    REJECT = "reject"


@dataclass(frozen=True)
class Command:
    action: Action
    value: str | int | None = None


_PLAYER_ACTIONS = {
    Action.PAUSE, Action.PLAY, Action.MUTE, Action.UNMUTE, Action.VOLUME, Action.SEEK, Action.STATUS,
}

_YOUTUBE = r"(?:유튜브|(?<![A-Za-z0-9_])you ?tube(?![A-Za-z0-9_]))"
_YOUTUBE_MENTION = re.compile(_YOUTUBE, re.IGNORECASE)
_UNSUPPORTED_YOUTUBE = Command(Action.INVALID, _CAPABILITIES)
_PREFIX = rf"(?:{_YOUTUBE}(?:에서|의)?\s*)?"
_PLEASE = r"(?:\s*(?:줘|주세요|주십시오))?"
_DO = rf"(?:\s*(?:해|하세요|해요){_PLEASE})?"
_OPEN = rf"(?:열어{_PLEASE}|열기|켜{_PLEASE})"
_SEARCH = rf"검색(?:\s*만)?(?:을)?{_DO}"
_TEXT_ACTIONS = (
    (Action.OPEN, rf"{_YOUTUBE}(?:를)?\s*{_OPEN}|open (?:youtube|you tube)"),
    (Action.PAUSE, rf"{_PREFIX}(?:영상(?:을)?\s*)?(?:멈춰{_PLEASE}|일시\s*정지{_DO})"
                   r"|pause (?:youtube|the video)"),
    (Action.PLAY, rf"{_PREFIX}(?:영상(?:을)?\s*)?(?:계속\s*|다시\s*)?"
                  rf"(?:재생{_DO}|틀어{_PLEASE}|재개{_DO})"
                  r"|(?:play|resume) (?:youtube|the video)"),
    (Action.MUTE, rf"{_PREFIX}(?:영상(?:의)?\s*)?(?:소리(?:를)?\s*꺼{_PLEASE}|음소거{_DO})"
                  r"|mute (?:youtube|the video)"),
    (Action.UNMUTE, rf"{_PREFIX}(?:영상(?:의)?\s*)?(?:소리(?:를)?\s*(?:다시\s*)?켜{_PLEASE}"
                    rf"|음소거\s*해제{_DO})|unmute (?:youtube|the video)"),
    (Action.STATUS, rf"{_PREFIX}(?:지금\s*)?(?:뭐|무슨\s*영상(?:이)?)\s*(?:틀어져|재생되고|나오고)"
                    r"\s*있(?:어|어요|니)"
                    rf"|{_PREFIX}(?:현재\s*|지금\s*)?(?:영상(?:의)?\s*)?제목(?:을)?\s*알려{_PLEASE}"
                    r"|what(?:'s| is) playing on youtube"),
)
_MEDIA_CONTEXT = re.compile(
    rf"{_YOUTUBE}|영상|재생|일시\s*정지|멈추|볼륨|음량|소리|음소거|"
    r"\d+\s*초|\b(?:pause|resume|mute|volume|seek)\b", re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"(?:하지|열지|켜지|끄지|틀지|멈추지|가지|올리지|내리지)\s*말?"
    r"(?:마|말|아|아요|아줘)|\b(?:안|못)\s*(?:해|열어|틀어|멈춰|꺼|켜)"
    r"|\b(?:do not|don't|never)\b", re.IGNORECASE,
)
_SEARCH_QUERY = re.compile(rf"{_YOUTUBE}(?:에서|에)\s+(.+?)\s*{_SEARCH}", re.IGNORECASE)
_VOLUME = re.compile(
    rf"{_PREFIX}(?:볼륨|음량)(?:을)?\s*([+\-−]?[\d가-힣.]+)\s*(?:%|퍼센트|프로)"
    rf"(?:로)?\s*(?:(?:설정|조절|맞춰){_DO}{_PLEASE}|해{_PLEASE})?", re.IGNORECASE,
)
_SEEK = re.compile(
    rf"{_PREFIX}(?:영상(?:을)?\s*)?([+\-−]?[\d가-힣.]+)\s*초\s*(앞으로|뒤로)"
    rf"(?:\s*(?:가|이동{_DO}|돌려){_PLEASE})?", re.IGNORECASE,
)
_NUMERALS = {
    "영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6,
    "칠": 7, "팔": 8, "구": 9, "하나": 1, "한": 1, "첫": 1, "첫째": 1, "둘": 2,
    "두": 2, "둘째": 2, "셋": 3, "세": 3, "셋째": 3, "넷": 4, "네": 4, "넷째": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "스물": 20,
    "서른": 30, "마흔": 40, "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90,
}
_SINO_DIGITS = "일이삼사오육칠팔구"


def _clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).strip(" .?!,。！？")


def _spoken_number(text: str) -> int | None:
    if re.fullmatch(r"[0-9]{1,4}", text):
        return int(text)
    if text in _NUMERALS:
        return _NUMERALS[text]
    match = re.fullmatch(
        rf"(?:([{_SINO_DIGITS}]?)백)?(?:([{_SINO_DIGITS}]?)십)?([{_SINO_DIGITS}])?", text,
    )
    if not match or not text:
        return None
    result = 0
    for digit, scale in zip(match.groups(), (100, 10, 1)):
        if digit is not None:
            result += (_NUMERALS[digit] if digit else 1) * scale
    return result


def _search_term(text):
    text = text.strip()
    if len(text) > 1 and text[-1] in "을를":
        previous = text[-2]
        if "가" <= previous <= "힣":
            has_final = (ord(previous) - ord("가")) % 28 != 0
            if has_final == (text[-1] == "을"):
                return text[:-1]
        if previous.isascii() and previous.isalnum():
            return text[:-1]
    return text


def parse_command(text: str) -> Command | None:
    """Parse complete, explicit requests without touching browser or state."""
    text = _clean(text)
    if _MEDIA_CONTEXT.search(text) and _NEGATIVE.search(text):
        return Command(Action.REJECT)

    korean_search = _SEARCH_QUERY.fullmatch(text)
    english_search = re.fullmatch(r"search (?:youtube|you tube) for (.+)", text, re.IGNORECASE)
    if korean_search or english_search:
        query = _search_term(korean_search[1]) if korean_search else english_search[1]
        if query and len(query) <= _MAX_QUERY and all(char.isprintable() for char in query):
            return Command(Action.SEARCH, query)
        return Command(Action.INVALID, "유튜브 검색어를 짧게 말해 주세요.")
    if re.search(rf"{_YOUTUBE}.*검색.*(?:그리고|하고).*(?:멈춰|정지|재생|틀어|음소거)", text, re.IGNORECASE):
        return Command(Action.REJECT)

    for action, pattern in _TEXT_ACTIONS:
        if re.fullmatch(pattern, text, re.IGNORECASE):
            return Command(action)
    volume = _VOLUME.fullmatch(text)
    if volume:
        value = _spoken_number(volume[1])
        if value is not None and 0 <= value <= 100:
            return Command(Action.VOLUME, value)
        return Command(Action.INVALID, "영상 볼륨은 0에서 100퍼센트 사이로 말해 주세요.")
    seek = _SEEK.fullmatch(text)
    if seek:
        value = _spoken_number(seek[1])
        if value is not None and 1 <= value <= _MAX_SEEK:
            return Command(Action.SEEK, value if seek[2] == "앞으로" else -value)
        return Command(Action.INVALID, f"이동할 시간을 1초에서 {_MAX_SEEK}초 사이로 말해 주세요.")
    if _YOUTUBE_MENTION.search(text):
        return _UNSUPPORTED_YOUTUBE
    return None


def _youtube_url(url):
    if not isinstance(url, str) or len(url) > 4096 or not url.startswith("https://"):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.netloc in _HOSTS and all(char.isprintable() and not char.isspace() for char in url):
            return parsed
    except ValueError:
        pass
    return None


def _identity(video):
    if not isinstance(video, dict):
        return None
    target_id, url, video_id = (video.get(key) for key in _TARGET_FIELDS)
    if not isinstance(target_id, str) or not target_id or len(target_id) > 128:
        return None
    parsed = _youtube_url(url)
    if not parsed or not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
        return None
    if parsed.path == "/watch":
        if parse_qs(parsed.query).get("v") != [video_id]:
            return None
    elif parsed.path not in {f"/shorts/{video_id}", f"/live/{video_id}", f"/embed/{video_id}"}:
        return None
    return {key: video[key] for key in _TARGET_FIELDS}


def _number(value, lower=0, upper=1_000_000_000):
    return type(value) in (int, float) and lower <= value <= upper


def _player_state(video):
    return (
        isinstance(video, dict)
        and type(video.get("paused")) is bool
        and type(video.get("muted")) is bool
        and _number(video.get("volume"), upper=1)
        and _number(video.get("currentTime"))
        and (video.get("duration") is None or _number(video.get("duration")))
    )


def _title(video):
    title = video.get("title")
    title = " ".join(title.split()) if isinstance(title, str) else ""
    return "".join(char for char in title if char.isprintable())[:180] or "제목 확인 불가"


def _selection_request(text, choices):
    """Resolve pagination or a selection within the current prompt."""
    text = re.sub(rf"^{_YOUTUBE}(?:에서|의)?\s+", "", _clean(text), flags=re.IGNORECASE)
    if text in _PAGE_MOVES:
        return text
    choice = re.fullmatch(
        r"([0-9]{1,4}|[가-힣]{1,4})\s*(?:번(?:째)?|번째)(?:\s*(?:선택|으로|영상))?"
        rf"{_DO}", text,
    )
    if choice:
        number = _spoken_number(choice[1])
        if number is not None:
            return [number - 1] if 1 <= number <= len(choices) else []
    if text in {"첫째", "둘째", "셋째", "넷째"}:
        number = _NUMERALS[text]
        return [number - 1] if number <= len(choices) else []

    explicit = re.fullmatch(rf"(.+?)\s*선택{_DO}", text)
    name = _clean(explicit[1] if explicit else text).casefold()
    keys = [_clean(_title(video)).casefold() for video in choices]
    exact = [index for index, key in enumerate(keys) if name == key]
    if exact:
        return exact
    if explicit and len(name) >= 2:
        partial = [index for index, key in enumerate(keys) if name in key]
        return partial or None
    return None


def _saved_command(value):
    if not isinstance(value, dict):
        return None
    try:
        action = Action(value.get("action"))
    except (TypeError, ValueError):
        return None
    arg = value.get("value")
    if action == Action.VOLUME and type(arg) is int and 0 <= arg <= 100:
        return Command(action, arg)
    if action == Action.SEEK and type(arg) is int and 1 <= abs(arg) <= _MAX_SEEK:
        return Command(action, arg)
    if action in _PLAYER_ACTIONS - {Action.VOLUME, Action.SEEK} and arg is None:
        return Command(action)
    return None


def _pending(state):
    entry = state.get("pending")
    if not isinstance(entry, dict):
        return None
    now = time.time()
    if not _number(entry.get("expires"), now, now + _SELECTION_TTL):
        return None
    command = _saved_command(entry.get("command"))
    choices = entry.get("choices")
    page = entry.get("page", 0)
    if not command or not isinstance(choices, list) or not 1 < len(choices) <= _MAX_OPTIONS:
        return None
    if type(page) is not int or not 0 <= page <= (len(choices) - 1) // _PAGE_SIZE:
        return None
    return (command, choices, page) if all(_identity(video) for video in choices) else None


def _read_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                return {}
            raw = source.read(_MAX_STATE_SIZE + 1)
        data = json.loads(raw) if len(raw) <= _MAX_STATE_SIZE else None
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _write_json(path, state):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".media-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            json.dump(state, destination)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _receipt(stdout):
    result = json.loads(stdout)
    if not isinstance(result, dict):
        return None
    if "run" in result:
        run = result["run"]
        if not isinstance(run, dict) or run.get("error") is not None:
            return None
        content = run.get("content")
        if not isinstance(content, list) or len(content) != 1:
            return None
        line = content[0]
        if not isinstance(line, dict) or line.get("type") != "text":
            return None
        result = json.loads(line.get("text", ""))
    receipt = result.get("atticusMedia") if isinstance(result, dict) else None
    return receipt if isinstance(receipt, dict) else None


class AsideMedia:
    def __init__(self, transport=None, state_path=None):
        root = Path(os.environ.get("ATTICUS_DIR") or Path.home() / ".config" / "atticus")
        self.state_path = Path(state_path) if state_path is not None else root / "assistant" / "media.json"
        self.transport = transport if transport is not None else subprocess.run

    def recognizes(self, text: str) -> bool:
        if parse_command(text) is not None:
            return True
        pending = _pending(_read_json(self.state_path))
        return pending is not None and _selection_request(text, pending[1]) is not None

    def _request(self, request):
        # Aside takes a code argv. The only variable portion is an encoded JSON
        # string consumed by a fixed bootstrap, separate from the static program.
        try:
            payload = json.dumps(json.dumps(request, ensure_ascii=True))
            script = f"const request = JSON.parse({payload});\n" + _SCRIPT.read_text(encoding="utf-8")
            process = self.transport(
                ["aside", "repl", "--json", script], capture_output=True, encoding="utf-8",
                timeout=12, check=False,
            )
            if process.returncode or not isinstance(process.stdout, str) or len(process.stdout) > 262_144:
                return None
            receipt = _receipt(process.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
            return None
        return receipt if receipt and receipt.get("op") == request["op"] else None

    def _navigate(self, command):
        url = _HOME_URL
        if command.action == Action.SEARCH:
            url += "results?" + urlencode({"search_query": command.value})
        receipt = self._request({"op": command.action.value, "url": url})
        parsed = _youtube_url(receipt.get("url")) if receipt and receipt.get("ok") is True else None
        if parsed and command.action == Action.OPEN and parsed.path == "/":
            return "유튜브를 열었어요."
        if parsed and command.action == Action.SEARCH and parsed.path == "/results":
            if parse_qs(parsed.query).get("search_query") == [command.value]:
                return "유튜브에 검색 결과를 열었어요."
        return _UNVERIFIED

    def _videos(self):
        receipt = self._request({"op": "list"})
        if not receipt or receipt.get("ok") is not True or not isinstance(receipt.get("videos"), list):
            return None
        videos = [video for video in receipt["videos"] if _identity(video)]
        if len(videos) > _MAX_OPTIONS or len({video["targetId"] for video in videos}) != len(videos):
            return None
        return videos

    def _apply(self, command, video):
        action = command.action
        target = _identity(video)
        request = {"op": action.value, "target": target}
        if command.value is not None:
            request["value"] = command.value
        receipt = self._request(request)
        if not receipt or receipt.get("ok") is not True:
            return _PLAYER_UNAVAILABLE if receipt and receipt.get("error") == "unloaded" else _UNVERIFIED
        before, after = receipt.get("before"), receipt.get("after")
        if _identity(before) != target or _identity(after) != target:
            return _UNVERIFIED
        if not _player_state(before) or not _player_state(after):
            return _PLAYER_UNAVAILABLE
        return _describe(command, before, after)

    def _follow_up(self, text, state):
        pending = _pending(state)
        if pending is None:
            return None
        command, choices, page = pending
        selection = _selection_request(text, choices)
        if selection is None:
            return None
        if isinstance(selection, str):
            last_page = (len(choices) - 1) // _PAGE_SIZE
            page = max(0, min(last_page, page + _PAGE_MOVES[selection]))
            state["pending"]["page"] = page
            _write_json(self.state_path, state)
            return _prompt(choices, page)
        if len(selection) != 1:
            return _prompt(choices, page)

        videos = self._videos()
        if videos is None:
            return _UNVERIFIED
        target = _identity(choices[selection[0]])
        state.pop("pending")
        video = next((video for video in videos if _identity(video) == target), None)
        if video is None:
            _write_json(self.state_path, state)
            return "선택한 탭이 닫혔거나 영상이 바뀌었어요. 유튜브 요청을 다시 말해 주세요."
        state["selected"] = target
        _write_json(self.state_path, state)
        return self._apply(command, video)

    def _handle_player(self, command, state):
        videos = self._videos()
        if videos is None:
            return _UNVERIFIED
        if not videos:
            return "Aside에 열린 유튜브 영상 탭이 없어요."
        selected = _identity(state.get("selected"))
        video = next((video for video in videos if selected and _identity(video) == selected), None)
        if video or len(videos) == 1:
            return self._apply(command, video or videos[0])
        playing = [video for video in videos if video.get("paused") is False and _player_state(video)]
        if len(playing) == 1 and all(_player_state(video) for video in videos):
            return self._apply(command, playing[0])
        choices = [{**_identity(video), "title": _title(video)} for video in videos]
        state["pending"] = {
            "expires": time.time() + _SELECTION_TTL,
            "command": {"action": command.action.value, "value": command.value},
            "choices": choices,
        }
        _write_json(self.state_path, state)
        return _prompt(choices)

    def _handle_locked(self, text, command, state):
        # A scoped tab choice also mentions YouTube, so check it before the capability reply.
        if command is None or command is _UNSUPPORTED_YOUTUBE:
            reply = self._follow_up(text, state)
            if reply is not None or command is None:
                return reply
        if "pending" in state:
            state.pop("pending")
            _write_json(self.state_path, state)
        if command.action == Action.REJECT:
            return "유튜브 동작을 실행하지 않았어요. 원하는 동작 하나를 말해 주세요."
        if command.action == Action.INVALID:
            return str(command.value)
        if command.action in {Action.OPEN, Action.SEARCH}:
            return self._navigate(command)
        return self._handle_player(command, state)

    def handle(self, text: str) -> str | None:
        command = parse_command(text)
        if command is None and not self.recognizes(text):
            return None
        try:
            self.state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            fd = os.open(
                self.state_path.with_suffix(".lock"),
                os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            with os.fdopen(fd, "w") as lock:
                info = os.fstat(lock.fileno())
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                    return _UNVERIFIED
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                reply = self._handle_locked(text, command, _read_json(self.state_path))
                return reply or "유튜브 탭 선택 시간이 지났어요. 유튜브 요청을 다시 말해 주세요."
        except BlockingIOError:
            return "앞선 유튜브 요청을 처리 중이에요."
        except OSError:
            return _UNVERIFIED


def _prompt(choices, page=0):
    start = page * _PAGE_SIZE
    end = start + _PAGE_SIZE
    listings = ". ".join(
        f"{index}번, {_title(video)[:55]}" for index, video in enumerate(choices[start:end], start + 1)
    )
    reply = f"유튜브 영상 탭이 {len(choices)}개 있어요. {listings}. 다시 불러서 번호나 제목을 말해 주세요."
    pages = []
    if start > 0:
        pages.append("이전 목록")
    if end < len(choices):
        pages.append("다음 목록")
    if pages:
        reply += f" {'이나 '.join(pages)}도 요청할 수 있어요."
    return reply


def _describe(command, before, after):
    action = command.action
    if action == Action.STATUS:
        playback = "일시 정지 상태예요" if after["paused"] else "재생 중이에요"
        return f"{_title(after)}, {playback}."
    replies = {
        Action.PAUSE: ("paused", True, "영상을 일시 정지했어요."),
        Action.PLAY: ("paused", False, "영상을 재생했어요."),
        Action.MUTE: ("muted", True, "영상의 소리를 껐어요."),
        Action.UNMUTE: ("muted", False, "영상의 소리를 켰어요."),
    }
    if action in replies:
        key, expected, message = replies[action]
        return message if after[key] is expected else _UNVERIFIED
    if action == Action.VOLUME:
        actual = round(after["volume"] * 100)
        return f"영상 볼륨을 {actual}퍼센트로 맞췄어요." if actual == command.value else _UNVERIFIED
    if action == Action.SEEK and before["duration"] is not None:
        expected = max(0, min(before["duration"], before["currentTime"] + command.value))
        if abs(after["currentTime"] - expected) > 1:
            return _UNVERIFIED
        if expected == 0:
            return "영상 처음으로 이동했어요."
        if expected == before["duration"]:
            return "영상 끝으로 이동했어요."
        seconds = round(after["currentTime"] - before["currentTime"])
        if abs(seconds) > 0 and (seconds > 0) == (command.value > 0):
            direction = "앞으로" if seconds > 0 else "뒤로"
            return f"{abs(seconds)}초 {direction} 이동했어요."
    return _UNVERIFIED


def recognizes(text: str) -> bool:
    return AsideMedia().recognizes(text)


def handle(text: str) -> str | None:
    return AsideMedia().handle(text)
