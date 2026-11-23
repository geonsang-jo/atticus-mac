"""A misunderstood voice request must not become general keyboard automation."""
import io
import unittest
from unittest.mock import patch

import atticus
from assistant import commands


class AssistantModeBoundaryTests(unittest.TestCase):
    def test_unknown_confirmed_voice_request_stays_local(self):
        with patch('sys.argv', ['atticus.py', '--wait']), \
             patch('sys.stdin', io.StringIO('Aside\t첫 번째 탭으로 해 줘.\n')), \
             patch.object(atticus, 'pref', side_effect=lambda key, default: default), \
             patch.object(atticus.threading, 'Thread'), \
             patch.object(atticus, 'wait_commit', return_value=True), \
             patch.object(commands, 'recognizes', return_value=False), \
             patch.object(atticus, 'say') as say, \
             patch.object(atticus, 'start_ai') as ai, \
             patch.object(atticus, 'window_summary') as screen, \
             patch.object(atticus, 'fast_hands') as hands, \
             patch.object(atticus, 'begin_turn', side_effect=AssertionError('entered general automation')):
            atticus.main()
        say.assert_called_once()
        self.assertIn('실행하지 않았어요', say.call_args.args[1])
        ai.assert_not_called()
        screen.assert_not_called()
        hands.assert_not_called()

    def test_cancelled_unknown_request_does_not_reply_or_start_an_ai(self):
        with patch('sys.argv', ['atticus.py', '--wait']), \
             patch('sys.stdin', io.StringIO('Aside\t그거 해줘\n')), \
             patch.object(atticus, 'pref', side_effect=lambda key, default: default), \
             patch.object(atticus.threading, 'Thread'), \
             patch.object(atticus, 'wait_commit', return_value=False), \
             patch.object(commands, 'recognizes', return_value=False), \
             patch.object(atticus, 'say') as say, \
             patch.object(atticus, 'start_ai') as ai, \
             patch.object(atticus, 'begin_turn', side_effect=AssertionError('entered general automation')):
            atticus.main()
        say.assert_not_called()
        ai.assert_not_called()

    def test_general_mode_retains_the_upstream_request_path(self):
        class GeneralPathReached(Exception):
            pass

        with patch('sys.argv', ['atticus.py', '--wait']), \
             patch('sys.stdin', io.StringIO('Finder\t어떤 파일인지 설명해줘\n')), \
             patch.object(atticus, 'pref', side_effect=lambda key, default: False if key == 'assistantMode' else default), \
             patch.object(atticus.threading, 'Thread'), \
             patch.object(commands, 'recognizes', return_value=False), \
             patch.object(atticus, 'start_ai', return_value=None), \
             patch.object(atticus, 'begin_turn', side_effect=GeneralPathReached):
            with self.assertRaises(GeneralPathReached):
                atticus.main()
