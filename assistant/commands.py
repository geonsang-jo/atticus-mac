"""Route supported requests without generating code or falling back on errors."""
import subprocess


def _adapters():
    from . import media, monitor
    return media, monitor


def recognizes(text):
    return any(adapter.recognizes(text) for adapter in _adapters())


def handle(text):
    matches = [adapter for adapter in _adapters() if adapter.recognizes(text)]
    if not matches:
        return None
    if len(matches) > 1:
        return '대상을 구분해 주세요. “유튜브 2번” 또는 “세션 2번”처럼 말해 주세요.'
    try:
        reply = matches[0].handle(text)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return '요청을 완료하지 못했어요. 연결 상태를 확인한 뒤 다시 불러 주세요.'
    return reply if isinstance(reply, str) and reply else '요청 결과를 확인하지 못했어요. 다시 불러 주세요.'
