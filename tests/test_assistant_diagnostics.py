"""Local opt-in assistant diagnostics use an isolated filesystem."""
import importlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


class AssistantDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.diagnostics = importlib.import_module("assistant.diagnostics")
        except ModuleNotFoundError as error:
            if error.name != "assistant.diagnostics":
                raise
            self.fail("The opt-in request trace has not been implemented")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "atticus"
        self.directory = self.root / "assistant"
        self.flag = self.directory / "debug-enabled"
        self.trace = self.directory / "last-command.json"
        env = patch.dict(os.environ, {"ATTICUS_DIR": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def enable(self):
        self.directory.mkdir(parents=True, mode=0o700)
        self.flag.touch(mode=0o600)

    def read_trace(self):
        return json.loads(self.trace.read_text(encoding="utf-8"))

    def test_default_creates_neither_directory_nor_file(self):
        self.diagnostics.record("지금 듣고 있는 YouTube", "route", {"matched": ["media"]})
        self.assertFalse(self.root.exists())
        self.directory.mkdir(parents=True, mode=0o700)
        self.diagnostics.record("유튜브 열어", "route")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_opt_in_records_exact_text_utf8_and_replaces_the_private_trace(self):
        self.enable()
        self.diagnostics.record("유튜브를 재생으로 바꿔 줘.", "route", {"matched": ["media"]})
        self.assertIn("유튜브".encode("utf-8"), self.trace.read_bytes())
        self.assertEqual(self.read_trace(), {
            "text": "유튜브를 재생으로 바꿔 줘.",
            "stage": "route",
            "details": {"matched": ["media"]},
        })
        self.assertEqual(stat.S_IMODE(self.trace.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(self.trace.stat().st_uid, os.getuid())
        self.diagnostics.record("새 요청", "parse", {"action": "play"})
        self.assertEqual(self.read_trace()["text"], "새 요청")
        self.assertEqual({path.name for path in self.directory.iterdir()}, {"last-command.json", "debug-enabled"})

    def test_trace_payload_is_bounded(self):
        self.enable()
        self.diagnostics.record("한" * 4100, "a" * 200, {"analysis": "한" * 20_000})
        trace = self.read_trace()
        self.assertEqual(trace["text"], "한" * 4000)
        self.assertLessEqual(len(trace["stage"]), 128)
        self.assertLessEqual(len(json.dumps(trace["details"], ensure_ascii=False).encode("utf-8")), 16_000)
        self.assertNotIn("한" * 4100, self.trace.read_text(encoding="utf-8"))

    def test_flag_must_be_private_regular_and_owned_directory_must_be_private(self):
        self.enable()
        self.flag.chmod(0o644)
        self.diagnostics.record("요청", "route")
        self.assertFalse(self.trace.exists())
        self.flag.unlink()
        self.flag.mkdir(mode=0o600)
        self.diagnostics.record("요청", "route")
        self.assertFalse(self.trace.exists())
        self.flag.rmdir()
        self.flag.touch(mode=0o600)
        self.directory.chmod(0o755)
        self.diagnostics.record("요청", "route")
        self.assertFalse(self.trace.exists())

    def test_symlinks_never_enable_or_replace_a_queue(self):
        self.enable()
        queue = self.directory / "notifications.json"
        queue.touch(mode=0o600)
        queue.write_text('["existing notification"]', encoding="utf-8")
        self.flag.unlink()
        self.flag.symlink_to(queue)
        self.diagnostics.record("요청", "route")
        self.assertFalse(self.trace.exists())
        self.flag.unlink()
        self.flag.touch(mode=0o600)
        self.trace.symlink_to(queue)
        self.diagnostics.record("요청", "route")
        self.assertTrue(self.trace.is_symlink())
        self.assertEqual(queue.read_text(encoding="utf-8"), '["existing notification"]')

    def test_symlinked_assistant_directory_does_not_record(self):
        self.enable()
        original = self.directory.rename(self.root / "external")
        self.directory.symlink_to(original, target_is_directory=True)
        self.diagnostics.record("요청", "route")
        self.assertFalse((original / "last-command.json").exists())

    def test_disabling_preserves_existing_trace_without_writing(self):
        self.enable()
        self.diagnostics.record("첫 요청", "route")
        before = self.trace.read_bytes()
        self.flag.unlink()
        self.diagnostics.record("두 번째 요청", "route")
        self.assertEqual(self.trace.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
