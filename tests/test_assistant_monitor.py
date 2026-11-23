import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from assistant import monitor
from assistant.sessions import SessionReducer
from tests.test_assistant_sessions import event, snapshot, terminal


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"ATTICUS_DIR": self.directory}))
        self.reducer = SessionReducer("fixture-core")

    def publish(self, item):
        completed = self.reducer.receive(item)
        monitor._publish(self.reducer, completed)

    def test_query_watch_selection_and_result_preserve_session_identity(self):
        self.publish(snapshot(7, terminal(), terminal("t2", state="blocked", pid=59)))
        self.assertIn("입력 필요", monitor.handle("개발 세션들 지금 뭐 하고 있어?"))
        answer = monitor.handle("herdr 세션 끝나면 알려줘")
        self.assertIn("1번", answer)
        self.assertIn("2번", answer)
        self.assertIn("t2", monitor.handle("두 번째"))
        self.assertIn("결과 요약", monitor.handle("그 세션 결과 확인해줘"))
        self.publish(event(8, "lost", {}, address="t2"))
        self.assertIn("확인할 수 없어요", monitor.handle("그 세션 결과 확인해줘"))
        self.assertIn("중지", monitor.handle("감시 그만해"))
        self.assertIsNone(monitor.handle("유튜브에서 재즈 검색해"))
        self.assertIsNone(monitor.handle("두 번째"))

    def test_reconnect_and_replayed_turn_cannot_duplicate_a_popped_receipt(self):
        self.publish(snapshot(7, terminal(state="working")))
        self.assertIn("알려드릴게요", monitor.handle("herdr 세션 끝나면 알려줘"))
        self.publish(event(8, "status", {"status": {"state": "done"}}))
        self.publish(event(9, "turn", {"turn": 1}))
        first = monitor.pop_notifications()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["kind"], "session")
        self.assertIn("작업이 끝났어요", first[0]["text"])
        self.assertEqual(monitor.pop_notifications(), [])
        self.publish(snapshot(7, terminal(state="working")))
        self.publish(event(8, "status", {"status": {"state": "done"}}))
        self.publish(event(9, "turn", {"turn": 1}))
        self.assertEqual(monitor.pop_notifications(), [])

    def test_disconnection_reports_unknown(self):
        self.publish(snapshot(7, terminal(state="working")))
        self.reducer.disconnect()
        monitor._publish(self.reducer, [])
        self.assertIn("연결 상태를 확인할 수 없어요", monitor.handle("개발 세션들 지금 뭐 해?"))

    def test_private_files_and_dynamic_path(self):
        self.publish(snapshot(7, terminal()))
        private = Path(self.directory) / "assistant"
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
        for path in private.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with tempfile.TemporaryDirectory() as other, patch.dict(os.environ, {"ATTICUS_DIR": other}):
            self.assertIn("연결 상태를 확인할 수 없어요", monitor.handle("개발 세션 상태"))
        self.assertIn("대기 중", monitor.handle("개발 세션 상태"))

    def test_daemon_is_single_instance_and_releases_lease_when_owner_exits(self):
        fixture = Path(self.directory) / "hc"
        marker = Path(self.directory) / "stream-starts"
        fixture.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys, time
if sys.argv[1:] == ["server", "status", "--json"]:
    print(json.dumps({"core": {"id": "fixture-core"}, "pid": 300}))
else:
    assert sys.argv[1:] == ["events", "--json"] and "HC_TERMINAL_ID" not in os.environ
    with open(os.environ["FIXTURE_MARKER"], "a") as f:
        f.write(str(os.getpid()) + "\\n")
    print(json.dumps({"seq": 1, "terminals": []}), flush=True)
    time.sleep(30)
''')
        fixture.chmod(0o700)
        context = multiprocessing.get_context("spawn")
        owner = context.Process(target=time.sleep, args=(30,))
        with patch.dict(os.environ, {"PATH": str(fixture.parent), "FIXTURE_MARKER": str(marker), "HC_TERMINAL_ID": "t99"}):
            owner.start()
            children = [context.Process(target=monitor.run_daemon, args=(owner.pid,)) for _ in range(2)]
            try:
                for item in children:
                    item.start()
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                starts = marker.read_text().splitlines()
                self.assertEqual(len(starts), 1)
                owner.terminate()
                owner.join(3)
                for item in children:
                    item.join(3)
                    self.assertEqual(item.exitcode, 0)
                self.assertIsNone(monitor._process_identity(int(starts[0])))
                self.assertIn("연결 상태를 확인할 수 없어요", monitor.handle("개발 세션 상태"))
            finally:
                for item in [*children, owner]:
                    if item.is_alive():
                        item.terminate()
                        item.join(3)

    def test_process_identity_detects_dead_owner(self):
        self.assertEqual(monitor._process_identity(os.getpid())["pid"], os.getpid())
        self.assertIsNone(monitor._process_identity(2**30))

    def test_recognition_is_read_only_and_bare_selection_needs_context(self):
        self.assertTrue(monitor.recognizes("herdr 세션 끝나면 알려줘"))
        self.assertTrue(monitor.recognizes("세션 2번"))
        self.assertFalse(monitor.recognizes("2번"))
        self.assertFalse((Path(self.directory) / "assistant").exists())
        self.publish(snapshot(7, terminal(), terminal("t2", pid=59)))
        monitor.handle("herdr 세션 끝나면 알려줘")
        self.assertTrue(monitor.recognizes("2번"))
        self.assertIn("t2", monitor.handle("세션 2번"))
        self.assertFalse(monitor.recognizes("2번"))

    def test_watch_started_between_turn_and_status_still_notifies(self):
        self.publish(snapshot(7, terminal(state="working", turn=3)))
        self.publish(event(8, "turn", {"turn": 4}))
        self.assertIn("알려드릴게요", monitor.handle("herdr 세션 끝나면 알려줘"))
        self.publish(event(9, "status", {"status": {"state": "done"}}))
        self.assertEqual(len(monitor.pop_notifications()), 1)

    def test_held_daemon_lease_requires_the_same_live_owner(self):
        context = multiprocessing.get_context("spawn")
        owner = context.Process(target=time.sleep, args=(30,))
        owner.start()
        try:
            lease = monitor._acquire_lease()
            try:
                with monitor._state(write=True) as state:
                    state["herdr"] = {"owner": monitor._process_identity(owner.pid),
                                      "daemon": monitor._process_identity(os.getpid())}
                with patch.object(monitor, "_DAEMON_START_SECONDS", 0.2, create=True):
                    self.assertTrue(monitor.ensure_daemon(owner.pid))
                    self.assertFalse(monitor.ensure_daemon(os.getpid()))
            finally:
                os.close(lease)
        finally:
            owner.terminate()
            owner.join(3)

    def test_page_keeps_stable_ordinals_for_all_sessions(self):
        records = [terminal(f"t{index}", pid=50 + index) for index in range(1, 12)]
        self.publish(snapshot(7, *records))
        initial = monitor.handle("herdr 세션 끝나면 알려줘")
        self.assertIn("1번", initial)
        self.assertIn("다음 세션 목록", initial)
        self.assertNotIn("9번", initial)
        self.publish(snapshot(8, *reversed(records)))
        self.assertTrue(monitor.recognizes("다음 세션 목록"))
        self.assertIn("9번 fixture t9", monitor.handle("다음 세션 목록"))
        self.assertIn("1번 fixture t1", monitor.handle("이전 세션 목록"))
        self.assertIn("t9", monitor.handle("세션 9번"))

    def test_notification_peek_is_repeatable_and_survives_process_restart(self):
        self.assertEqual(monitor.peek_notifications(), [])
        self.assertFalse((Path(self.directory) / "assistant").exists())
        with monitor._state(write=True) as state:
            monitor._receipt(state, "test:fixture", "fixture finished", "test")
            monitor._receipt(state, "test:fixture-more", "other fixture finished", "test")
        notices = monitor.peek_notifications()
        self.assertEqual(monitor.peek_notifications(), notices)
        self.assertEqual(len(notices), 2)
        restarted = subprocess.check_output([sys.executable, "-c",
            "import json; from assistant.monitor import peek_notifications; print(json.dumps(peek_notifications()))"])
        self.assertEqual(json.loads(restarted), notices)
        self.assertEqual(monitor.ack_notifications(["test:fixture", "test:unknown"]), 1)
        self.assertEqual(monitor.peek_notifications(), notices[1:])
        self.assertEqual(monitor.ack_notifications(["test:fixture", "test:fixture"]), 0)
        self.assertEqual(monitor.pop_notifications(), notices[1:])
        self.assertEqual(monitor.peek_notifications(), [])

    def test_ack_validates_entire_batch_before_removing_any_receipt(self):
        with monitor._state(write=True) as state:
            monitor._receipt(state, "test:fixture", "fixture finished", "test")
        for invalid in ("test:fixture", ["test:fixture", ""], ["test:fixture", 1],
                        ["test:fixture", "x" * 201], ["test:fixture", "has\nnewline"], ["test:fixture"] * 65):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                monitor.ack_notifications(invalid)
        self.assertEqual(monitor.ack_notifications([]), 0)
        self.assertEqual([notice["id"] for notice in monitor.peek_notifications()], ["test:fixture"])

    def test_daemon_launch_disables_bytecode_in_the_worker_bundle(self):
        with patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": ""}), \
                patch.object(monitor.subprocess, "Popen") as spawn:
            monitor.ensure_daemon(os.getpid())
            self.assertEqual(os.environ["PYTHONDONTWRITEBYTECODE"], "")
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
