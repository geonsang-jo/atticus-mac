import unittest

from assistant.sessions import SessionReducer


def terminal(address="t1", state="idle", turn=0, pid=51, **fields):
    return {"address": address, "state": "live", "created_ms": 100, "name": "fixture",
            "status": {"state": state}, "turn": turn,
            "foreground": {"pid": pid, "argv": ["/bin/codex"]}, **fields}


def event(seq, kind, data=None, address="t1"):
    return {"method": "event", "params": {"seq": seq, "type": kind, "terminal": address, "data": data}}


def snapshot(seq, *rows):
    return {"seq": seq, "terminals": list(rows)}


class SessionReducerTests(unittest.TestCase):
    def setUp(self):
        self.reducer = SessionReducer("fixture-core")

    def test_idle_snapshot_ignores_ui_badge_and_done_without_a_working_turn(self):
        self.assertEqual(self.reducer.receive(snapshot(7, terminal(labels={"herdr.seen": "20"}))), [])
        self.assertEqual(self.reducer.sessions[0]["state"], "idle")
        self.assertEqual(self.reducer.receive(event(8, "status", {"status": {"state": "done"}})), [])
        self.assertEqual(self.reducer.receive(event(9, "turn", {"turn": 1})), [])

    def test_working_to_done_requires_status_and_turn_and_only_fires_once(self):
        self.reducer.receive(snapshot(7, terminal()))
        self.reducer.receive(event(8, "status", {"status": {"state": "working"}}))
        self.assertEqual(self.reducer.receive(event(9, "status", {"status": {"state": "done"}})), [])
        self.assertEqual(self.reducer.receive(event(10, "turn", {"turn": 1})), self.reducer.sessions)
        self.assertEqual(self.reducer.receive(event(10, "turn", {"turn": 1})), [])
        self.assertEqual(self.reducer.receive(event(9, "status", {"status": {"state": "done"}})), [])

    def test_turn_event_may_arrive_before_status(self):
        self.reducer.receive(snapshot(7, terminal(state="working", turn=3)))
        self.assertEqual(self.reducer.receive(event(8, "turn", {"turn": 4})), [])
        self.assertEqual(self.reducer.receive(event(9, "status", {"status": {"state": "done"}})),
                         self.reducer.sessions)

    def test_blocked_and_error_do_not_count_as_done(self):
        for state in ("blocked", "error"):
            with self.subTest(state=state):
                self.reducer.receive(snapshot(7, terminal(state="working", turn=3)))
                self.assertEqual(self.reducer.receive(event(8, "status", {"status": {"state": state}})), [])
                self.assertEqual(self.reducer.receive(event(9, "turn", {"turn": 4})), [])
                self.assertEqual(self.reducer.sessions[0]["state"], state)
                self.assertEqual(self.reducer.receive(event(10, "status", {"status": {"state": "done"}})), [])

    def test_missing_and_gapped_sequence_require_a_fresh_snapshot(self):
        for seq in (None, 10):
            with self.subTest(seq=seq):
                self.reducer.receive(snapshot(7, terminal(state="working")))
                self.assertEqual(self.reducer.receive(event(seq, "status", {"status": {"state": "done"}})), [])
                self.assertFalse(self.reducer.connected)
                self.assertEqual(self.reducer.sessions[0]["state"], "unknown")
                self.assertEqual(self.reducer.receive(event(8, "turn", {"turn": 1})), [])
                self.assertFalse(self.reducer.connected)
                self.assertEqual(self.reducer.receive(snapshot(1, terminal(state="done", turn=1))), [])
                self.assertTrue(self.reducer.connected)

    def test_missed_snapshot_does_not_report_completion(self):
        self.reducer.receive(snapshot(7, terminal(state="working")))
        self.assertEqual(self.reducer.receive({"method": "missed", "params": snapshot(19, terminal(state="done", turn=3))}), [])
        self.assertEqual(self.reducer.sessions[0]["state"], "done")

    def test_identity_replacement_and_lost_terminal_do_not_report_completion(self):
        self.reducer.receive(snapshot(7, terminal(state="working")))
        original = self.reducer.sessions[0]["id"]
        self.assertEqual(self.reducer.receive(event(8, "foreground", {"pid": 62, "argv": ["/bin/codex"]})), [])
        self.assertNotEqual(self.reducer.sessions[0]["id"], original)
        self.assertEqual(self.reducer.receive(event(9, "turn", {"turn": 1})), [])
        self.assertEqual(self.reducer.receive(event(10, "status", {"status": {"state": "done"}})), [])
        self.assertEqual(self.reducer.receive(event(11, "lost", {})), [])
        self.assertEqual(self.reducer.sessions, [])

    def test_core_and_executable_are_part_of_identity(self):
        self.reducer.receive(snapshot(7, terminal()))
        second = SessionReducer("another-core")
        second.receive(snapshot(7, terminal()))
        self.assertNotEqual(self.reducer.sessions[0]["id"], second.sessions[0]["id"])
        second.receive(snapshot(8, terminal(foreground={"pid": 51, "argv": ["/opt/codex"]})))
        self.assertNotEqual(self.reducer.sessions[0]["id"], second.sessions[0]["id"])

    def test_nonterminal_event_consumes_sequence_number(self):
        self.reducer.receive(snapshot(7, terminal(state="working")))
        self.reducer.receive(event(8, "doc", {}, address=None))
        self.reducer.receive(event(9, "status", {"status": {"state": "done"}, "turn": 1}))
        self.assertTrue(self.reducer.connected)
        self.assertEqual(self.reducer.sessions[0]["state"], "done")

    def test_explicit_idle_ends_an_observed_working_turn(self):
        self.reducer.receive(snapshot(7, terminal(state="working", turn=3)))
        self.assertEqual(self.reducer.receive(event(8, "status", {"status": {"state": "idle"}})), [])
        self.assertEqual(self.reducer.receive(event(9, "turn", {"turn": 4})), self.reducer.sessions)

    def test_new_agent_in_existing_shell_becomes_visible(self):
        shell = terminal()
        shell["foreground"] = {"pid": 45, "argv": ["/bin/zsh"]}
        self.reducer.receive(snapshot(7, shell))
        self.assertEqual(self.reducer.sessions, [])
        self.reducer.receive(event(8, "foreground", {"pid": 46, "argv": ["/bin/claude"]}))
        self.assertEqual(self.reducer.sessions[0]["agent"], "claude")

    def test_full_labels_record_cannot_backfill_a_missing_status_transition(self):
        self.reducer.receive(snapshot(7, terminal(state="working")))
        self.reducer.receive(event(8, "labels", {"record": terminal(state="done", turn=1)}))
        self.assertEqual(self.reducer.receive(event(9, "turn", {"turn": 1})), [])
