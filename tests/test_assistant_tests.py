import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from assistant import monitor, test_runs


class TestTrackerTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"ATTICUS_DIR": self.directory}))
        self.enterContext(patch.object(test_runs, "TEST_TIMEOUT", 1))
        self.runner = test_runs.TestRunner()
        self.addCleanup(self.runner.close)

    def state(self):
        return json.loads((Path(self.directory) / "assistant" / "state.json").read_text())

    def run_fixture(self, code):
        test_runs.register_test("fixture", self.directory, [sys.executable, "-c", code])
        self.assertIn("요청했어요", monitor.handle("fixture 테스트 실행해"))
        self.assertIn("대기 중", monitor.handle("테스트 상태"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.runner.tick()
            row = self.state()["runs"][-1]
            if row["ended_at"] is not None:
                return row
            time.sleep(0.02)
        self.fail("Fixture did not finish")

    def test_success_records_verified_exit_and_bounded_output_in_registered_cwd(self):
        row = self.run_fixture("import os; print('x' * 100000); print(os.getcwd())")
        self.assertEqual(row["status"], "passed")
        self.assertEqual(row["cwd"], os.path.realpath(self.directory))
        self.assertEqual(row["exit_code"], 0)
        self.assertIsNone(row["signal"])
        self.assertFalse(row["timeout"])
        self.assertIsInstance(row["owner"]["pid"], int)
        self.assertIsInstance(row["child"]["pid"], int)
        self.assertLessEqual(len(row["output"].encode()), test_runs.MAX_OUTPUT)
        self.assertIn(row["cwd"], row["output"])
        self.assertIn("성공", monitor.handle("테스트 결과 알려줘"))
        self.assertEqual(len(monitor.pop_notifications()), 1)
        self.assertEqual(monitor.pop_notifications(), [])

    def test_nonzero_exit_reports_failure(self):
        row = self.run_fixture("raise SystemExit(7)")
        self.assertEqual((row["status"], row["exit_code"]), ("failed", 7))
        self.assertIn("종료 코드 7", monitor.pop_notifications()[0]["text"])

    def test_signal_is_distinct_from_exit_code(self):
        row = self.run_fixture("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
        self.assertEqual((row["status"], row["exit_code"], row["signal"]), ("signaled", None, signal.SIGTERM))
        self.assertIn("시그널", monitor.pop_notifications()[0]["text"])

    def test_timeout_is_distinct_and_reaps_child(self):
        row = self.run_fixture("import time; time.sleep(30)")
        self.assertEqual((row["status"], row["timeout"]), ("timeout", True))
        self.assertIsNone(monitor._process_identity(row["child"]["pid"]))
        self.assertIn("제한 시간", monitor.pop_notifications()[0]["text"])

    def test_lost_supervisor_is_unknown_even_if_replacement_pid_is_alive(self):
        test_runs.register_test("fixture", self.directory, [sys.executable, "-c", "pass"])
        monitor.handle("테스트 실행해")
        with monitor._state(write=True) as state:
            run = state["runs"][0]
            run.update(status="running", owner={"pid": os.getpid(), "started": "different process"})
        replacement = test_runs.TestRunner()
        self.addCleanup(replacement.close)
        replacement.tick()
        self.assertEqual(self.state()["runs"][0]["status"], "unknown")
        self.assertIn("확인할 수 없어요", monitor.handle("테스트 상태"))
        self.assertEqual(monitor.pop_notifications(), [])

    def test_only_explicit_registrations_can_run(self):
        self.assertIn("등록", monitor.handle("임의 명령 테스트 실행해"))
        self.assertIn("등록해서 실행한", monitor.handle("테스트 결과 알려줘"))
        with self.assertRaises(ValueError):
            test_runs.register_test("fixture", self.directory, "echo wrong")
        with self.assertRaises(ValueError):
            test_runs.register_test("fixture", str(Path(self.directory) / "missing"), [sys.executable])
        self.assertEqual(self.state().get("runs", []), [])

    def test_ambiguous_test_names_support_spoken_selection(self):
        for name in ("첫 프로젝트", "다음 프로젝트"):
            test_runs.register_test(name, self.directory, [sys.executable, "-c", "pass"])
        self.assertIn("2번", monitor.handle("테스트 실행해"))
        self.assertIn("다음 프로젝트", monitor.handle("2번"))
        self.assertEqual(self.state()["runs"][0]["name"], "다음 프로젝트")

    def test_negated_start_queues_no_test(self):
        test_runs.register_test("fixture", self.directory, [sys.executable, "-c", "pass"])
        self.assertTrue(monitor.recognizes("fixture 테스트 실행하지 마"))
        self.assertIsNotNone(monitor.handle("fixture 테스트 실행하지 마"))
        self.assertEqual(self.state().get("runs", []), [])

    def test_explicit_named_start_supersedes_an_older_test_selection(self):
        for name in ("one", "two"):
            test_runs.register_test(name, self.directory, [sys.executable, "-c", "pass"])
        self.assertIn("2번", monitor.handle("테스트 실행해"))
        self.assertIn("요청했어요", monitor.handle("one 테스트 실행해"))
        self.assertIsNone(monitor.handle("2번"))
        self.assertEqual([run["name"] for run in self.state()["runs"]], ["one"])

    def test_observer_shutdown_preserves_exit_code_if_child_already_finished(self):
        test_runs.register_test("fixture", self.directory, [sys.executable, "-c", "pass"])
        monitor.handle("테스트 실행해")
        self.runner.tick()
        self.assertEqual(self.runner.child.wait(timeout=3), 0)
        self.runner.close()
        row = self.state()["runs"][-1]
        self.assertEqual((row["status"], row["exit_code"]), ("passed", 0))
