"""Opt-in, local trace of the last confirmed assistant request."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat


_FLAG = "debug-enabled"
_TRACE = "last-command.json"
_MAX_TEXT = 4000
_MAX_STAGE = 128
_MAX_DETAILS = 16_000


def _private_file(directory, name, *, allow_missing=False):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return allow_missing
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600
    )


def _bounded_details(details):
    try:
        encoded = json.dumps(details, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= _MAX_DETAILS:
            return details
        return {"truncated": True}
    except (TypeError, ValueError, RecursionError):
        return {"unserializable": True}


def _write(directory, data):
    name = f".last-command-{secrets.token_hex(8)}.tmp"
    fd = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(data)
        if _private_file(directory, _FLAG) and _private_file(directory, _TRACE, allow_missing=True):
            os.replace(name, _TRACE, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def record(text: str, stage: str, details: dict | None = None) -> None:
    """Replace the private trace when the caller has explicitly enabled it."""
    try:
        root = Path(os.environ.get("ATTICUS_DIR") or Path.home() / ".config" / "atticus")
        directory = os.open(root / "assistant", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                return
            if not _private_file(directory, _FLAG) or not _private_file(directory, _TRACE, allow_missing=True):
                return
            trace = {"text": text[:_MAX_TEXT], "stage": stage[:_MAX_STAGE], "details": _bounded_details(details)}
            payload = json.dumps(trace, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            _write(directory, payload.encode("utf-8", errors="replace"))
        finally:
            os.close(directory)
    except (OSError, RuntimeError, TypeError, ValueError):
        # This diagnostic must never affect the assistant request.
        pass
