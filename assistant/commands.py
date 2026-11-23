"""Route supported requests without generating code or falling back on errors."""
import subprocess

from .diagnostics import record


def _adapters():
    from . import media, monitor
    return media, monitor


def recognizes(text):
    return any(adapter.recognizes(text) for adapter in _adapters())


def handle(text):
    matches = [adapter for adapter in _adapters() if adapter.recognizes(text)]
    details = {
        'adapters': [getattr(adapter, '__name__', 'unknown') for adapter in matches],
        'parsed': [repr(adapter.parse_command(text)) for adapter in matches if hasattr(adapter, 'parse_command')],
        'router': __file__,
    }
    record(text, 'dispatch', details)
    if not matches:
        return None
    if len(matches) > 1:
        reply = '대상을 구분해 주세요. “유튜브 2번” 또는 “세션 2번”처럼 말해 주세요.'
        record(text, 'ambiguous', dict(details, reply=reply))
        return reply
    try:
        reply = matches[0].handle(text)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        record(text, 'failed', dict(details, error=type(error).__name__))
        return '요청을 완료하지 못했어요. 연결 상태를 확인한 뒤 다시 불러 주세요.'
    record(text, 'completed', dict(details, reply=reply))
    return reply if isinstance(reply, str) and reply else '요청 결과를 확인하지 못했어요. 다시 불러 주세요.'
