"""Read-only observation of local herdr sessions from the hc JSON stream."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import time


_AGENT_NAMES = {"claude", "codex"}
_STATES = {"idle", "working", "blocked", "done", "error"}
_ADDRESS = re.compile(r"t[0-9a-z]{1,30}\Z")
_MAX_LINE = 4 * 1024 * 1024
_MAX_SESSIONS = 256
_HEARTBEAT_TIMEOUT = 45.0


def _count(value):
    return type(value) is int and value >= 0


def _label(value):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable()).strip()[:80]


def _session(core, record):
    address = record.get("address")
    foreground = record.get("foreground")
    if (record.get("state") != "live" or not isinstance(address, str)
            or not _ADDRESS.fullmatch(address) or not isinstance(foreground, dict)):
        return None
    argv = foreground.get("argv")
    executable = argv[0] if isinstance(argv, list) and argv and isinstance(argv[0], str) else ""
    agent = Path(executable).name
    pid, created = foreground.get("pid"), record.get("created_ms")
    if agent not in _AGENT_NAMES or not _count(pid) or pid == 0 or not _count(created):
        return None
    identity = json.dumps([core, address, created, pid, executable]).encode()
    status = record.get("status")
    status = status.get("state") if isinstance(status, dict) else None
    turn = record.get("turn")
    cwd = foreground.get("cwd") or record.get("cwd")
    project = _label(Path(cwd).name) if isinstance(cwd, str) else ""
    return {
        "id": hashlib.sha256(identity).hexdigest(), "address": address, "agent": agent,
        "name": _label(record.get("name")) or project or _label(record.get("title")) or address,
        "state": status if isinstance(status, str) and status in _STATES else "unknown",
        "turn": turn if _count(turn) else None,
    }


class SessionReducer:
    """Correlate an observed working turn with its successful end on the same session."""

    def __init__(self, core):
        self.core = core
        self._records = {}
        self._sessions = {}
        self._working_since = {}
        self._seq = None

    @property
    def connected(self):
        return self._seq is not None

    @property
    def sessions(self):
        return [dict(item) if self.connected else {**item, "state": "unknown", "turn": None}
                for item in self._sessions.values()]

    def disconnect(self):
        self._seq = None
        self._working_since.clear()

    def _snapshot(self, seq, records):
        self._records.clear()
        self._sessions.clear()
        self._working_since.clear()
        self._seq = seq
        for record in records:
            if not isinstance(record, dict) or record.get("state") != "live":
                continue
            address = record.get("address")
            if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
                continue
            self._records[address] = record.copy()
            if item := _session(self.core, record):
                self._sessions[address] = item
                if item["state"] == "working" and item["turn"] is not None:
                    self._working_since[address] = item["turn"]
            if len(self._records) == _MAX_SESSIONS:
                break

    def receive(self, message):
        if not isinstance(message, dict):
            self.disconnect()
            return []
        data = message.get("params", message.get("result", message))
        if not isinstance(data, dict):
            self.disconnect()
            return []
        method = message.get("method")
        seq = data.get("seq")
        if "terminals" in data:
            if _count(seq) and isinstance(data["terminals"], list):
                self._snapshot(seq, data["terminals"])
            else:
                self.disconnect()
            return []
        if self._seq is None or method not in (None, "event"):
            return []
        if not _count(seq) or seq > self._seq + 1:
            self.disconnect()
            return []
        if seq <= self._seq:
            return []
        self._seq = seq
        kind, address, detail = (data.get(key) for key in ("type", "terminal", "data"))
        if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
            return []
        return self._apply(address, kind, detail if isinstance(detail, dict) else {})

    def _apply(self, address, kind, detail):
        if kind in ("lost", "removed", "finished"):
            self._records.pop(address, None)
            self._sessions.pop(address, None)
            self._working_since.pop(address, None)
            return []
        before = self._sessions.get(address)
        full = kind in ("created", "restarted") or (kind == "labels" and isinstance(detail.get("record"), dict))
        if full:
            if address not in self._records and len(self._records) == _MAX_SESSIONS:
                return []
            self._records[address] = {**(detail["record"] if kind == "labels" else detail), "address": address}
        elif address in self._records:
            record = self._records[address]
            if kind == "foreground":
                record["foreground"] = detail
            elif kind in ("status", "turn", "title"):
                record[kind] = detail.get(kind)
                if kind == "status" and "turn" in detail:
                    record["turn"] = detail["turn"]
            else:
                return []
        else:
            return []
        item = _session(self.core, self._records[address])
        if item is None:
            self._sessions.pop(address, None)
            self._working_since.pop(address, None)
            return []
        self._sessions[address] = item
        if before is None or before["id"] != item["id"] or kind in ("created", "restarted"):
            self._working_since.pop(address, None)
            if item["state"] == "working" and item["turn"] is not None and full:
                self._working_since[address] = item["turn"]
            return []
        status, turn = item["state"], item["turn"]
        if full and (status != before["state"] or turn != before["turn"]):
            self._working_since.pop(address, None)
            if status == "working" and turn is not None:
                self._working_since[address] = turn
            return []
        if (kind == "status" and status == "working" and turn is not None
                and (before["state"] != "working" or address not in self._working_since)):
            self._working_since[address] = turn
        since = self._working_since.get(address)
        if (since is not None and status in ("done", "idle") and turn == since + 1
                and kind in ("status", "turn")):
            self._working_since.pop(address, None)
            return [dict(item)]
        if (status not in ("working", "done", "idle") or turn is None
                or (since is not None and turn not in (since, since + 1))):
            self._working_since.pop(address, None)
        return []


def _hc_path():
    return shutil.which("hc") or next((str(candidate) for candidate in (
        Path.home() / ".local/bin/hc", Path("/opt/homebrew/bin/hc"), Path("/usr/local/bin/hc")
    ) if os.access(candidate, os.X_OK)), None)


class SessionStream:
    """Poll one nonblocking subscription without exposing raw terminal records to callers."""

    def __init__(self):
        self.child = None
        self.reducer = None
        self._buffer = bytearray()
        self._retry = 1.0
        self._next_attempt = 0.0
        self._last_message = 0.0

    def _open(self):
        path = _hc_path()
        if not path:
            return
        env = os.environ.copy()
        env.pop("HC_TERMINAL_ID", None)
        # `server status` fails when the core is unavailable. Confirm a running
        # core before subscribing instead of allowing normal startup on demand.
        status = subprocess.run([path, "server", "status", "--json"], capture_output=True,
                                env=env, timeout=1, check=True)
        core = json.loads(status.stdout)["core"]["id"]
        if not isinstance(core, str) or not core:
            return
        self.child = subprocess.Popen([path, "events", "--json"], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        self.reducer = SessionReducer(core)
        self._last_message = time.monotonic()
        self._buffer.clear()

    def tick(self):
        """Return current reducer and verified completions when a publish is needed."""
        now = time.monotonic()
        if self.child is None:
            if now < self._next_attempt:
                return None
            try:
                self._open()
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                self.close()
            if self.child is None:
                self._backoff()
                return None
        completed = []
        try:
            if select.select([self.child.stdout], [], [], 0)[0]:
                chunk = os.read(self.child.stdout.fileno(), 256 * 1024)
                if not chunk:
                    raise EOFError
                self._buffer.extend(chunk)
            if b"\n" not in self._buffer:
                if (len(self._buffer) > _MAX_LINE
                        or now - self._last_message > _HEARTBEAT_TIMEOUT):
                    raise EOFError
                return None
            while (end := self._buffer.find(b"\n")) >= 0:
                if end > _MAX_LINE:
                    raise EOFError
                message = json.loads(self._buffer[:end])
                del self._buffer[:end + 1]
                was_connected = self.reducer.connected
                completed.extend(self.reducer.receive(message))
                if was_connected and not self.reducer.connected:
                    raise EOFError
                if self.reducer.connected:
                    self._retry = 1.0
                    self._last_message = now
            if now - self._last_message > _HEARTBEAT_TIMEOUT:
                raise EOFError
            return self.reducer, completed
        except (EOFError, OSError, ValueError):
            self.close()
            self._backoff()
            return self.reducer, completed

    def _backoff(self):
        self._next_attempt = time.monotonic() + self._retry
        self._retry = min(30.0, self._retry * 2)

    def close(self):
        if self.reducer is not None:
            self.reducer.disconnect()
        if self.child is not None:
            if self.child.poll() is None:
                try:
                    self.child.terminate()
                    self.child.wait(timeout=0.3)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    self.child.kill()
            self.child.wait()
            self.child.stdout.close()
            self.child = None
