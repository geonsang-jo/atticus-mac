"""Run explicitly registered tests and retain only the supervisor's observed result."""

from __future__ import annotations

from contextlib import suppress
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from uuid import uuid4

from . import monitor


TEST_TIMEOUT = 30 * 60
MAX_OUTPUT = 16 * 1024
_MAX_REGISTRATIONS = 16
_MAX_RUNS = 24
_MAX_QUEUE = 8


def register_test(name, cwd, argv):
    """Register a spoken name and exact argv from the local configuration CLI."""
    if (not isinstance(name, str) or not name.strip() or len(name) > 80
            or any(not char.isprintable() for char in name)):
        raise ValueError("Test name must have 1–80 printable characters")
    if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 64
            or not all(isinstance(arg, str) and len(arg) <= 4096 and "\0" not in arg for arg in argv)
            or not argv[0]):
        raise ValueError("Test command must be a nonempty argv array")
    try:
        directory = Path(cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        raise ValueError("Test working directory does not exist") from None
    if not directory.is_dir():
        raise ValueError("Test working directory must be a directory")
    if "/" in argv[0]:
        executable = shutil.which(str(directory / argv[0]))
    else:
        executable = shutil.which(argv[0])
    if executable is None:
        raise ValueError("Test executable is unavailable")
    name = " ".join(name.split())
    key = monitor._normalize(name)
    if not key:
        raise ValueError("Test name is empty after normalization")
    with monitor._state(write=True) as state:
        tests = state.setdefault("tests", {})
        if key not in tests and len(tests) >= _MAX_REGISTRATIONS:
            raise ValueError("Too many registered tests")
        tests[key] = {"id": uuid4().hex, "name": name, "cwd": str(directory),
                      "argv": [os.path.abspath(executable), *argv[1:]]}
    return {"registered": name}


def _summary(run):
    status = run["status"]
    name = run["name"]
    if status == "running" and not monitor._identity_is_live(run["owner"]):
        status = "unknown"
    if status == "passed":
        return f"{name} 테스트가 성공했어요. 종료 코드는 0이에요."
    if status == "failed":
        return f'{name} 테스트가 종료 코드 {run["exit_code"]}로 실패했어요.'
    if status == "signaled":
        return f'{name} 테스트가 시그널 {run["signal"]}로 종료됐어요.'
    phrases = {
        "queued": "실행 대기 중이에요.", "running": "실행 중이에요.",
        "timeout": "제한 시간을 넘어 종료됐어요.", "unknown": "종료 결과를 확인할 수 없어요.",
        "start_error": "시작할 수 없었어요.",
    }
    return f"{name} 테스트는 {phrases[status]}"


def test_reply(state, action, target, key=None):
    runs = state.get("runs", [])
    if action != "start":
        row = next((row for row in reversed(runs) if not target or monitor._normalize(row["name"]) == target), None)
        return _summary(row) if row else "등록해서 실행한 테스트의 상태만 확인할 수 있어요. 해당 실행 기록이 없어요."
    tests = state.get("tests", {})
    if key is not None:
        candidates = [test for test in tests.values() if test["id"] == key]
    elif target:
        candidates = [tests[target]] if target in tests else []
    else:
        candidates = list(tests.values())
    if not candidates:
        return "해당 이름으로 등록된 테스트가 없어요. 등록한 테스트 이름으로 요청해 주세요."
    if len(candidates) > 1:
        return monitor._offer(state, "test", action, [(test["id"], test["name"]) for test in candidates])
    test = candidates[0]
    active = [row for row in runs if row["status"] in ("queued", "running")]
    if existing := next((row for row in active if row["name"] == test["name"]), None):
        return _summary(existing)
    if len(active) >= _MAX_QUEUE:
        return "테스트 실행 대기 목록이 가득 찼어요. 잠시 뒤 다시 요청해 주세요."
    discarded = max(0, len(runs) - _MAX_RUNS + 1)
    expired = [row["id"] for row in runs if row["status"] not in ("queued", "running")][:discarded]
    state["runs"] = [row for row in runs if row["id"] not in expired]
    state["runs"].append({
        "id": uuid4().hex, "name": test["name"], "cwd": test["cwd"], "argv": test["argv"],
        "status": "queued", "requested_at": time.time(), "started_at": None, "ended_at": None,
        "owner": None, "child": None, "exit_code": None, "signal": None, "timeout": False, "output": "",
    })
    return f'{test["name"]} 테스트 실행을 요청했어요.'


class TestRunner:
    """Supervise one process at a time; each tick is nonblocking while it runs."""

    def __init__(self):
        self.owner = monitor._process_identity(os.getpid())
        self.child = None
        self._run_id = None
        self._deadline = 0.0
        self._next_claim = 0.0
        self._timed_out = False
        self._output = bytearray()

    def _claim(self):
        with monitor._state() as state:
            if not any(row["status"] in ("queued", "running") for row in state.get("runs", [])):
                return None
        with monitor._state(write=True) as state:
            runs = state.get("runs", [])
            for row in runs:
                if row["status"] == "running" and not monitor._identity_is_live(row["owner"]):
                    row.update(status="unknown", ended_at=time.time())
            if any(row["status"] == "running" for row in runs):
                return None
            row = next((row for row in runs if row["status"] == "queued"), None)
            if row is not None:
                row.update(status="running", started_at=time.time(), owner=self.owner)
                return row.copy()
        return None

    def _update(self, *, notify=False, **changes):
        with monitor._state(write=True) as state:
            for row in state.get("runs", []):
                if row["id"] == self._run_id and row["owner"] == self.owner and row["status"] == "running":
                    row.update(changes)
                    if notify:
                        monitor._receipt(state, f'test:{row["id"]}', _summary(row), "test")
                    return

    def _start(self, row):
        self._run_id = row["id"]
        self._output.clear()
        self._deadline = time.monotonic() + TEST_TIMEOUT
        self._timed_out = False
        env = os.environ.copy()
        env.pop("HC_TERMINAL_ID", None)
        try:
            self.child = subprocess.Popen(row["argv"], cwd=row["cwd"], env=env, start_new_session=True,
                                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT)
        except (OSError, ValueError):
            self._update(notify=True, status="start_error", ended_at=time.time())
            self._run_id = None
            return
        child = monitor._process_identity(self.child.pid, include_zombie=True)
        self._update(child=child or {"pid": self.child.pid, "started": None})
        os.set_blocking(self.child.stdout.fileno(), False)

    def _drain(self):
        for _ in range(4):
            try:
                data = os.read(self.child.stdout.fileno(), 65536)
            except BlockingIOError:
                return
            if not data:
                return
            self._output.extend(data)
            del self._output[:-MAX_OUTPUT]

    def _kill(self):
        # The unreaped group leader is our own child, so the PID/group cannot
        # have been recycled into an unrelated process.
        with suppress(ProcessLookupError):
            os.killpg(self.child.pid, signal.SIGKILL)

    def tick(self):
        if self.child is None:
            now = time.monotonic()
            if now >= self._next_claim:
                self._next_claim = now + 0.5
                if row := self._claim():
                    self._start(row)
            return
        self._drain()
        code = self.child.poll()
        if code is None:
            if not self._timed_out and time.monotonic() >= self._deadline:
                self._timed_out = True
                self._kill()
            return
        self._finish(code)

    def _finish(self, code):
        self._drain()
        self.child.stdout.close()
        self.child = None
        normalized = self._output.decode("utf-8", errors="replace").encode("utf-8")
        output = normalized[-MAX_OUTPUT:].decode("utf-8", errors="ignore")
        if code is None:
            self._update(status="unknown", ended_at=time.time(), output=output)
        else:
            status = "timeout" if self._timed_out else "signaled" if code < 0 else "passed" if code == 0 else "failed"
            self._update(notify=True, status=status, ended_at=time.time(), timeout=self._timed_out,
                         exit_code=code if code >= 0 else None, signal=-code if code < 0 else None, output=output)
        self._run_id = None

    def close(self):
        if self.child is None:
            return
        code = self.child.poll()
        if code is None:
            self._kill()
            with suppress(subprocess.TimeoutExpired):
                self.child.wait(timeout=1)
        self._finish(code)
