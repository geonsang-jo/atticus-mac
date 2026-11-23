"""The assistant router never delegates confirmed built-ins to an AI worker."""
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import atticus
from assistant import commands, media


class AssistantRouterTests(unittest.TestCase):
    def adapter(self, matches, reply):
        return SimpleNamespace(recognizes=Mock(return_value=matches), handle=Mock(return_value=reply))

    def test_unrecognized_request_has_no_adapter_side_effect(self):
        media, monitor = self.adapter(False, 'media'), self.adapter(False, 'monitor')
        with patch.object(commands, '_adapters', return_value=(media, monitor)):
            self.assertFalse(commands.recognizes('오늘 날씨'))
            self.assertIsNone(commands.handle('오늘 날씨'))
        media.handle.assert_not_called()
        monitor.handle.assert_not_called()

    def test_ambiguous_contextual_selection_does_not_guess(self):
        media, monitor = self.adapter(True, 'media'), self.adapter(True, 'monitor')
        with patch.object(commands, '_adapters', return_value=(media, monitor)):
            self.assertIn('유튜브', commands.handle('2번'))
        media.handle.assert_not_called()
        monitor.handle.assert_not_called()

    def test_confirmed_builtin_uses_exactly_one_adapter(self):
        media, monitor = self.adapter(True, '영상을 멈췄어요.'), self.adapter(False, 'monitor')
        with patch.object(commands, '_adapters', return_value=(media, monitor)):
            self.assertEqual(commands.handle('유튜브 멈춰'), '영상을 멈췄어요.')
        media.handle.assert_called_once_with('유튜브 멈춰')
        monitor.handle.assert_not_called()

    def test_adapter_error_is_a_truthful_failure_not_ai_fallback(self):
        media, monitor = self.adapter(True, ''), self.adapter(False, '')
        media.handle.side_effect = OSError('transport unavailable')
        with patch.object(commands, '_adapters', return_value=(media, monitor)):
            self.assertIn('못했', commands.handle('유튜브 멈춰'))


class AssistantNotificationTests(unittest.TestCase):
    def test_busy_delivery_is_deferred_and_duplicate_ids_are_consumed_once(self):
        source = (Path(__file__).resolve().parents[1] / 'Atticus.swift').read_text()
        queue = source.split('struct AssistantNoticeQueue {', 1)[1].split('// The observer owns', 1)[0]
        program = 'import Foundation\nstruct AssistantNoticeQueue {' + queue + '''
var queue = AssistantNoticeQueue()
queue.append([["id":"one", "text":"테스트 완료"], ["id":"one", "text":"중복"]])
precondition(queue.take(ready:false) == nil)
precondition(queue.items.count == 1)
precondition(queue.take(ready:true)?.text == "테스트 완료")
queue.append([["id":"one", "text":"중복"], ["id":"", "text":"잘못된 알림"]])
precondition(queue.take(ready:true) == nil)
print("deferred once")
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'notice.swift'
            path.write_text(program)
            result = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'deferred once')

class AssistantBoundaryTests(unittest.TestCase):
    def test_builtin_wait_request_needs_neither_ai_warmup_nor_screen_capture(self):
        with patch('sys.argv', ['atticus.py', '--wait']), \
             patch('sys.stdin', io.StringIO('Finder\t유튜브 멈춰\n')), \
             patch('sys.stdout', new_callable=io.StringIO) as output, \
             patch.object(atticus, 'pref', side_effect=lambda key, default: default), \
             patch.object(atticus.threading, 'Thread'), patch.object(atticus, 'start_ai') as ai, \
             patch.object(atticus, 'window_summary') as screen, \
             patch.object(atticus, 'wait_commit', return_value=True), \
             patch.object(commands, 'recognizes', return_value=True), \
             patch.object(commands, 'handle', return_value='영상을 멈췄어요.'):
            atticus.main()
        self.assertIn('영상을 멈췄어요.', output.getvalue())
        ai.assert_not_called()
        screen.assert_not_called()

    def test_spoken_tab_choice_without_prompt_cannot_use_generic_keyboard_actions(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'ATTICUS_DIR': folder}), \
             patch('sys.argv', ['atticus.py', '--wait']), \
             patch('sys.stdin', io.StringIO('Finder\t첫 번째 탭으로 해 줘.\n')), \
             patch('sys.stdout', new_callable=io.StringIO) as output, \
             patch.object(atticus, 'pref', side_effect=lambda key, default: default), \
             patch.object(atticus.threading, 'Thread'), patch.object(atticus, 'start_ai') as ai, \
             patch.object(atticus, 'window_summary') as screen, patch.object(atticus, 'fast_hands') as hands, \
             patch.object(atticus, 'point_context', side_effect=AssertionError('generic AI path was entered')), \
             patch.object(atticus, 'wait_commit', return_value=True), \
             patch.object(media.AsideMedia, '_request') as browser:
            atticus.main()
        self.assertIn('유튜브 탭 목록', output.getvalue())
        ai.assert_not_called()
        screen.assert_not_called()
        hands.assert_not_called()
        browser.assert_not_called()

    def test_live_partial_cannot_execute_or_fall_through_to_english_actions(self):
        with patch.dict(os.environ, {'ATTICUS_LIVE': '1'}), \
             patch.object(commands, 'recognizes', return_value=True), \
             patch.object(commands, 'handle') as execute, patch.object(atticus, 'fast_hands') as hands:
            self.assertIsNone(atticus.fast('pause youtube'))
        execute.assert_not_called()
        hands.assert_not_called()

    def test_cancelled_transcript_does_not_execute(self):
        with patch.dict(os.environ, {'ATTICUS_LIVE': '0'}), \
             patch.object(commands, 'recognizes', return_value=True), \
             patch.object(commands, 'handle') as execute, patch.object(atticus, 'wait_commit', return_value=False):
            self.assertIsNone(atticus.fast('유튜브 멈춰'))
        execute.assert_not_called()

    def test_confirmed_builtin_skips_other_fast_actions(self):
        with patch.dict(os.environ, {'ATTICUS_LIVE': '0'}), \
             patch.object(commands, 'recognizes', return_value=True), \
             patch.object(commands, 'handle', return_value='영상을 멈췄어요.') as execute, \
             patch.object(atticus, 'wait_commit', return_value=True), patch.object(atticus, 'fast_hands') as hands:
            self.assertEqual(atticus.fast('유튜브 멈춰'), '영상을 멈췄어요.')
        execute.assert_called_once_with('유튜브 멈춰')
        hands.assert_not_called()

    def test_pop_notifications_cli_does_not_start_ai(self):
        with patch('sys.argv', ['atticus.py', '--assistant-pop']), \
             patch('assistant.monitor.pop_notifications', return_value=[{'id': 'one', 'text': '완료'}]), \
             patch.object(atticus, 'start_ai') as ai, patch('sys.stdout', new_callable=io.StringIO) as output:
            atticus.main()
        self.assertIn('one', output.getvalue())
        ai.assert_not_called()

    def test_acknowledgement_cli_does_not_start_ai(self):
        with patch('sys.argv', ['atticus.py', '--assistant-ack', '["test:one"]']), \
             patch('assistant.monitor.ack_notifications', return_value=1) as acknowledge, \
             patch.object(atticus, 'start_ai') as ai, patch('sys.stdout', new_callable=io.StringIO) as output:
            atticus.main()
        acknowledge.assert_called_once_with(['test:one'])
        self.assertIn('"acknowledged": 1', output.getvalue())
        ai.assert_not_called()
