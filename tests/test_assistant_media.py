"""Exercise media commands with local fixtures and an injected Aside transport."""
import importlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "assistant/media_script.js"


def media_module(test):
    try:
        return importlib.import_module("assistant.media")
    except ModuleNotFoundError as error:
        if error.name not in {"assistant", "assistant.media"}:
            raise
        test.fail("The Korean media adapter has not been implemented")


class MediaParserTests(unittest.TestCase):
    def assert_command(self, text, action, value=None):
        command = media_module(self).parse_command(text)
        self.assertIsNotNone(command, text)
        self.assertEqual((command.action.value, command.value), (action, value), text)

    def test_open_and_search_only(self):
        self.assert_command("유튜브 열어 주세요.", "open")
        self.assert_command("유튜브에서 재즈 검색해.", "search", "재즈")
        self.assert_command("유튜브에서 재즈 검색만 해줘.", "search", "재즈")
        self.assert_command("YouTube에서 Studio Ghibli를 검색해줘", "search", "Studio Ghibli")
        self.assert_command("Search YouTube for keyboard music", "search", "keyboard music")

    def test_search_preserves_a_noun_with_a_particle_like_ending(self):
        self.assert_command("유튜브에서 노을 검색해", "search", "노을")
        self.assert_command("유튜브에서 서울 검색해", "search", "서울")

    def test_explicit_playback_and_status(self):
        for text, action in [
            ("영상 멈춰", "pause"),
            ("유튜브 일시 정지해 줘.", "pause"),
            ("계속 재생해.", "play"),
            ("유튜브 영상 다시 틀어줘", "play"),
            ("소리 꺼", "mute"),
            ("유튜브 소리 다시 켜 줘", "unmute"),
            ("지금 뭐 틀어져 있어?", "status"),
            ("유튜브 영상 제목 알려줘", "status"),
            ("pause YouTube", "pause"),
        ]:
            with self.subTest(text=text):
                self.assert_command(text, action)

    def test_conversational_leading_context_preserves_explicit_control(self):
        for text, action in (
            ("지금 듣고 있는 YouTube 다시 틀어 줘.", "play"),
            ("지금 보고 있는 유튜브를 다시 틀어 줘.", "play"),
            ("현재 열려 있는 유튜브에서 영상 멈춰 줘.", "pause"),
            ("보고 있던 영상을 다시 틀어줘", "play"),
        ):
            with self.subTest(text=text):
                self.assert_command(text, action)

    def test_playback_state_transition_phrasing(self):
        for text, action in (
            ("재생으로 바꿔 줘", "play"),
            ("유튜브를 재생으로 바꿔 줘", "play"),
            ("유튜브 재생으로 바꿔줘", "play"),
            ("지금 듣고 있는 YouTube를 재생 상태로 전환해 주세요", "play"),
            ("영상을 재생 모드로 바꿔 줘요", "play"),
            ("일시정지로 바꿔 줘", "pause"),
            ("유튜브 영상 일시 정지 상태로 전환해 주세요", "pause"),
            ("보고 있던 영상을 정지 모드로 바꿔주세요", "pause"),
        ):
            with self.subTest(text=text):
                self.assert_command(text, action)

    def test_numeric_bounds_signed_direction_and_spoken_numbers(self):
        self.assert_command("볼륨 ３０퍼센트로 해.", "volume", 30)
        self.assert_command("볼륨을 삼십 퍼센트로 설정해줘", "volume", 30)
        self.assert_command("유튜브 볼륨 백 프로로 해줘", "volume", 100)
        self.assert_command("볼륨 0%로 해", "volume", 0)
        self.assert_command("10초 앞으로", "seek", 10)
        self.assert_command("십 초 뒤로 가 줘", "seek", -10)
        self.assert_command("300초 뒤로", "seek", -300)
        for text in ("볼륨 101퍼센트로 해", "볼륨 -1퍼센트", "볼륨 3.5퍼센트",
                     "301초 앞으로", "0초 뒤로", "-10초 앞으로"):
            with self.subTest(text=text):
                command = media_module(self).parse_command(text)
                self.assertEqual(command.action.value, "invalid")
                self.assertTrue(command.value)

    def test_negative_and_multi_intent_commands_cannot_execute(self):
        for text in ("유튜브 열지 마", "영상 멈추지 마", "유튜브 재생하지 말아 줘",
                     "볼륨 30퍼센트로 하지 마", "소리 안 꺼도 돼", "유튜브 검색 안 해도 돼",
                     "유튜브에서 재즈 검색해 그리고 영상 멈춰", "don't pause YouTube"):
            with self.subTest(text=text):
                self.assert_command(text, "reject")

    def test_unrelated_and_underspecified_requests(self):
        parse = media_module(self).parse_command
        for text in ("테스트 실행해", "반드시 영상 멈춰야 하는 정책", "개발 세션들 지금 뭐 해?",
                     "play some jazz", "2번", "전체 화면으로 해 줘",
                     "search about NotYouTube", "다음 영상 재생하는 법 알려줘"):
            with self.subTest(text=text):
                self.assertIsNone(parse(text))


FIRST = {
    "targetId": "tab-1",
    "url": "https://www.youtube.com/watch?v=abcdefghijk",
    "videoId": "abcdefghijk",
    "title": "피아노 연주",
    "paused": True,
    "muted": False,
    "volume": 0.5,
    "currentTime": 20,
    "duration": 200,
}
SECOND = {
    **FIRST,
    "targetId": "tab-2",
    "url": "https://youtube.com/shorts/lmnopqrstuv",
    "videoId": "lmnopqrstuv",
    "title": "오케스트라 합주",
}


class AsideTransport:
    def __init__(self, *receipts):
        self.receipts = iter(receipts)
        self.requests = []
        self.commands = []

    def __call__(self, args, **kwargs):
        self.commands.append((args, kwargs))
        script = args[-1]
        bootstrap, static_script = script.split("\n", 1)
        prefix = "const request = JSON.parse("
        if not bootstrap.startswith(prefix) or not bootstrap.endswith(");"):
            raise AssertionError("Expected a fixed JSON bootstrap")
        self.requests.append(json.loads(json.loads(bootstrap[len(prefix):-2])))
        if static_script != SCRIPT.read_text():
            raise AssertionError("Action payload changed the static script")
        receipt = next(self.receipts)
        if isinstance(receipt, Exception):
            raise receipt
        stdout = receipt if isinstance(receipt, str) else json.dumps({"atticusMedia": receipt})
        return subprocess.CompletedProcess(args, 0, stdout, "")


def listed(*videos):
    return {"op": "list", "ok": True, "videos": list(videos)}


def changed(op, before, **after):
    return {"op": op, "ok": True, "before": before, "after": {**before, **after}}


class MediaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.media = media_module(self)
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.state = Path(state.name) / "media.json"

    def adapter(self, *receipts):
        transport = AsideTransport(*receipts)
        return self.media.AsideMedia(transport=transport, state_path=self.state), transport

    def test_explicit_youtube_domain_replies_locally_without_browser_actions(self):
        adapter, transport = self.adapter()
        for text in (
            "유튜브에서 재즈 재생해", "유튜브 검색 결과 첫 번째 틀어 줘",
            "유튜브에서 전체 화면으로 전환해 줘", "유튜브 다음 추천 영상 선택해",
            "유튜브에서 다음 영상으로 넘어가 줘", "유튜브에 댓글 달아줘",
            "유튜브 정책 알려줘", "유튜브에서 다음 영상 재생하는 법 알려줘",
            "Play jazz on YouTube", "skip the ad on YouTube",
            "How can I upload a video to You Tube?",
        ):
            with self.subTest(text=text):
                self.assertTrue(adapter.recognizes(text))
                reply = adapter.handle(text)
                self.assertIn("동작을 실행하지 않았어요", reply)
                self.assertIn("유튜브 재생해 줘", reply)
        self.assertEqual(transport.requests, [])

    def test_conversational_context_never_acts_on_rejects_or_unsupported_requests(self):
        adapter, transport = self.adapter()
        self.assertIn("실행하지", adapter.handle("지금 듣고 있는 YouTube 다시 틀지 마."))
        self.assertIn("실행하지", adapter.handle("지금 듣고 있는 YouTube 다음 영상으로 넘어가 줘."))
        self.assertEqual(transport.requests, [])

    def test_playback_state_transition_resumes_a_verified_target(self):
        adapter, transport = self.adapter(listed(FIRST), changed("play", FIRST, paused=False))
        self.assertEqual(adapter.handle("유튜브를 재생으로 바꿔 줘"), "영상을 재생했어요.")
        self.assertEqual([request["op"] for request in transport.requests], ["list", "play"])

    def test_playback_state_transition_refusals_never_call_aside(self):
        adapter, transport = self.adapter()
        for text in (
            "유튜브를 재생으로 바꾸지 마",
            "지금 듣고 있는 YouTube 재생 모드로 전환하지 마",
            "정지 상태로 바꾸지 마",
            "재생으로 안 바꿔도 돼",
        ):
            with self.subTest(text=text):
                reply = adapter.handle(text)
                self.assertIsNotNone(reply, text)
                self.assertIn("실행하지", reply)
        self.assertIn("실행하지", adapter.handle("유튜브를 전체 화면 모드로 전환해 줘"))
        self.assertEqual(transport.requests, [])

    def test_negative_command_never_calls_aside(self):
        adapter, transport = self.adapter()
        self.assertIn("실행하지", adapter.handle("영상 멈추지 마"))
        self.assertEqual(transport.requests, [])

    def test_replacement_ends_a_pending_selection_before_dispatch(self):
        for text, receipts in (
            ("멈추지 마", ()),
            ("볼륨 101퍼센트", ()),
            ("유튜브 열어", ({"op": "open", "ok": True, "url": "https://www.youtube.com/"},)),
            ("유튜브에서 재즈 검색해", ({"op": "search", "ok": False, "error": "browser"},)),
            ("소리 꺼", ("<unavailable>",)),
        ):
            with self.subTest(text=text):
                adapter, _ = self.adapter(listed(FIRST, SECOND))
                self.assertIn("2번", adapter.handle("영상 멈춰"))
                adapter, transport = self.adapter(*receipts)
                self.assertIsNotNone(adapter.handle(text))
                self.assertFalse(adapter.recognizes("2번"))
                self.assertIsNone(adapter.handle("2번"))
                self.assertNotIn("pause", [request["op"] for request in transport.requests])

    def test_list_release_failure_stops_without_player_action(self):
        adapter, transport = self.adapter({"op": "list", "ok": False, "error": "cleanup"})
        self.assertIn("탭 연결을 해제하지 못해", adapter.handle("영상 멈춰"))
        self.assertEqual(transport.requests, [{"op": "list"}])

    def test_action_release_failure_is_reported_alongside_observed_state(self):
        receipt = {**changed("pause", FIRST, paused=True), "cleanup": False}
        adapter, _ = self.adapter(listed(FIRST), receipt)
        reply = adapter.handle("영상 멈춰")
        self.assertIn("일시 정지했어요", reply)
        self.assertIn("탭 연결을 해제하지 못했어요", reply)

    def test_selects_unique_playing_video_for_verified_pause(self):
        playing = {**SECOND, "paused": False}
        adapter, transport = self.adapter(listed(FIRST, playing), changed("pause", playing, paused=True))
        self.assertEqual(adapter.handle("영상 멈춰"), "영상을 일시 정지했어요.")
        self.assertEqual([x["op"] for x in transport.requests], ["list", "pause"])
        self.assertEqual(transport.requests[1]["target"], {
            key: SECOND[key] for key in ("targetId", "url", "videoId")
        })

    def test_pause_acknowledgment_requires_observed_pause(self):
        playing = {**FIRST, "paused": False}
        adapter, _ = self.adapter(listed(playing), changed("pause", playing))
        self.assertIn("확인할 수", adapter.handle("영상 멈춰"))

    def test_ambiguous_prompt_survives_worker_restart_and_accepts_number(self):
        adapter, transport = self.adapter(listed(FIRST, SECOND))
        prompt = adapter.handle("계속 재생해")
        self.assertIn("1번, 피아노 연주", prompt)
        self.assertIn("2번, 오케스트라 합주", prompt)
        self.assertEqual([x["op"] for x in transport.requests], ["list"])
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        adapter, transport = self.adapter(listed(SECOND, FIRST), changed("play", SECOND, paused=False))
        self.assertTrue(adapter.recognizes("두 번째"))
        self.assertTrue(adapter.recognizes("유튜브 2번"))
        self.assertEqual(adapter.handle("유튜브 2번"), "영상을 재생했어요.")
        self.assertEqual(transport.requests[-1]["target"]["targetId"], SECOND["targetId"])
        self.assertFalse(adapter.recognizes("2번"))
        adapter, transport = self.adapter(
            listed({**FIRST, "paused": False}, SECOND), changed("status", SECOND)
        )
        self.assertIn("오케스트라 합주", adapter.handle("지금 뭐 틀어져 있어?"))

    def test_follow_up_accepts_repeated_ordinal_tab_and_playback_command(self):
        adapter, _ = self.adapter(listed(FIRST, SECOND))
        self.assertIn("1번", adapter.handle("계속 재생해"))
        adapter, transport = self.adapter(listed(SECOND, FIRST), changed("play", FIRST, paused=False))
        text = "첫 번 첫 번째 탭 유튜브를 재생해줘."
        self.assertTrue(adapter.recognizes(text))
        self.assertEqual(adapter.handle(text), "영상을 재생했어요.")
        self.assertEqual([request["op"] for request in transport.requests], ["list", "play"])
        self.assertEqual(transport.requests[-1]["target"]["targetId"], FIRST["targetId"])

    def test_locative_tab_action_uses_the_offered_number_and_new_action(self):
        for text, action in (
            ("첫번째탭에 있는 유튜브 재생해줘", "play"),
            ("첫 번째 탭에 있는 YouTube 재생해 줘.", "play"),
            ("첫 번 첫 번째 탭에 있는 유튜브를 멈춰 줘.", "pause"),
        ):
            with self.subTest(text=text):
                self.state.unlink(missing_ok=True)
                adapter, _ = self.adapter(listed(FIRST, SECOND))
                self.assertIn("1번", adapter.handle("계속 재생해"))
                expected = "영상을 재생했어요." if action == "play" else "영상을 일시 정지했어요."
                target = {**FIRST, "paused": action == "play"}
                adapter, transport = self.adapter(
                    listed(SECOND, target), changed(action, target, paused=action == "pause")
                )
                self.assertEqual(adapter.handle(text), expected)
                self.assertEqual([request["op"] for request in transport.requests], ["list", action])
                self.assertEqual(transport.requests[-1]["target"]["targetId"], FIRST["targetId"])

    def test_locative_tab_keeps_the_offered_video_identity(self):
        adapter, _ = self.adapter(listed(FIRST, SECOND))
        adapter.handle("계속 재생해")
        replacement = {**FIRST, "videoId": "AAAAAAAAAAA",
                       "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"}
        adapter, transport = self.adapter(listed(replacement, SECOND))
        self.assertIn("영상이 바뀌었어요", adapter.handle("첫번째탭에 있는 유튜브 재생해줘"))
        self.assertEqual([request["op"] for request in transport.requests], ["list"])

    def test_locative_first_tab_with_no_or_expired_prompt_uses_verified_unique_video(self):
        text = "첫번째탭에 있는 유튜브 재생해줘"
        for expired in (False, True):
            with self.subTest(expired=expired):
                self.state.unlink(missing_ok=True)
                if expired:
                    adapter, _ = self.adapter(listed(FIRST, SECOND))
                    adapter.handle("영상 멈춰")
                    state = json.loads(self.state.read_text())
                    state["pending"]["expires"] = 0
                    self.state.write_text(json.dumps(state))
                adapter, transport = self.adapter(listed(SECOND), changed("play", SECOND, paused=False))
                self.assertTrue(adapter.recognizes(text))
                self.assertEqual(adapter.handle(text), "영상을 재생했어요.")
                self.assertEqual([request["op"] for request in transport.requests], ["list", "play"])
                self.assertEqual(transport.requests[-1]["target"]["targetId"], SECOND["targetId"])

    def test_unprompted_locative_tab_offers_multiple_videos_before_mutating(self):
        playing = {**FIRST, "paused": False}
        adapter, transport = self.adapter(listed(playing, SECOND))
        reply = adapter.handle("첫 번째 탭에 있는 유튜브 재생해줘")
        self.assertIn("1번, 피아노 연주", reply)
        self.assertIn("2번, 오케스트라 합주", reply)
        self.assertEqual(transport.requests, [{"op": "list"}])

        adapter, transport = self.adapter(listed(SECOND, playing), changed("play", SECOND, paused=False))
        self.assertEqual(adapter.handle("유튜브2번"), "영상을 재생했어요.")
        self.assertEqual(transport.requests[-1]["target"]["targetId"], SECOND["targetId"])

    def test_unprompted_locative_tab_needs_matching_ordinal_and_observed_player(self):
        for text, videos in (
            ("2번 탭에 있는 유튜브 재생해줘", (FIRST,)),
            ("3번 탭에 있는 유튜브 재생해줘", (FIRST, SECOND)),
            ("1번 탭에 있는 유튜브 재생해줘", ({**FIRST, "paused": None},)),
        ):
            with self.subTest(text=text):
                adapter, transport = self.adapter(listed(*videos))
                reply = adapter.handle(text)
                self.assertIn("확인할 수 없어요", reply)
                self.assertEqual(transport.requests, [{"op": "list"}])
                self.assertFalse(adapter.recognizes("1번"))

    def test_locative_tab_with_missing_or_conflicting_action_never_replays_pending(self):
        for text in (
            "첫번째탭에 있는",
            "첫번째탭에 있는 유튜브 재생해저ㅜ",
            "첫 번째 탭에 있는 유튜브 재생하지 마",
            "첫 번 두 번째 탭에 있는 유튜브 재생해줘",
            "첫 번째 탭에 있는 유튜브 재생해줘 그리고 멈춰",
            "99번 탭에 있는 유튜브 재생해줘",
        ):
            with self.subTest(text=text):
                adapter, _ = self.adapter(listed(FIRST, SECOND))
                self.assertIn("1번", adapter.handle("계속 재생해"))
                adapter, transport = self.adapter()
                self.assertIsNotNone(adapter.handle(text))
                self.assertFalse(adapter.recognizes("1번"))
                self.assertEqual(transport.requests, [])

    def test_follow_up_tab_command_uses_its_explicit_operation(self):
        for text in ("유튜브 첫 번째 탭 멈춰 줘", "첫 번째 탭 일시 정지해줘", "유튜브1번 탭 멈춰"):
            with self.subTest(text=text):
                self.state.unlink(missing_ok=True)
                adapter, _ = self.adapter(listed(FIRST, SECOND))
                self.assertIn("1번", adapter.handle("계속 재생해"))
                playing = {**FIRST, "paused": False}
                adapter, transport = self.adapter(listed(SECOND, playing), changed("pause", playing, paused=True))
                self.assertTrue(adapter.recognizes(text))
                self.assertEqual(adapter.handle(text), "영상을 일시 정지했어요.")
                self.assertEqual([request["op"] for request in transport.requests], ["list", "pause"])
                self.assertEqual(transport.requests[-1]["target"]["targetId"], FIRST["targetId"])

    def test_spoken_ordinal_tab_choice_stays_local_without_a_current_prompt(self):
        adapter, transport = self.adapter()
        text = "첫 번째 탭으로 해 줘."
        self.assertTrue(adapter.recognizes(text))
        self.assertIn("유튜브 탭 목록", adapter.handle(text))
        self.assertFalse(adapter.recognizes("1번"))
        self.assertEqual(transport.requests, [])

        adapter, _ = self.adapter(listed(FIRST, SECOND))
        adapter.handle("계속 재생해")
        expired = json.loads(self.state.read_text())
        expired["pending"]["expires"] = 0
        self.state.write_text(json.dumps(expired))
        adapter, transport = self.adapter()
        self.assertIn("유튜브 탭 목록", adapter.handle(text))
        self.assertEqual(transport.requests, [])

    def test_spoken_ordinal_tab_choice_uses_the_active_prompt_target(self):
        adapter, _ = self.adapter(listed(FIRST, SECOND))
        self.assertIn("1번", adapter.handle("계속 재생해"))
        adapter, transport = self.adapter(listed(SECOND, FIRST), changed("play", FIRST, paused=False))
        self.assertEqual(adapter.handle("첫 번째 탭으로 해 줘."), "영상을 재생했어요.")
        self.assertEqual(transport.requests[-1]["target"]["targetId"], FIRST["targetId"])

    def test_follow_up_selects_a_title_beginning_with_an_ordinal(self):
        titled = {**SECOND, "title": "1번 영상 편집"}
        for text, expected in (
            ("1번 영상 편집", titled),
            ("1번 영상 편집 선택해줘", titled),
            ("1번 선택해 줘", FIRST),
        ):
            with self.subTest(text=text):
                self.state.unlink(missing_ok=True)
                adapter, _ = self.adapter(listed(FIRST, titled))
                self.assertIn("2번", adapter.handle("계속 재생해"))
                adapter, transport = self.adapter(
                    listed(titled, FIRST), changed("play", expected, paused=False)
                )
                self.assertEqual(adapter.handle(text), "영상을 재생했어요.")
                self.assertEqual(transport.requests[-1]["target"]["targetId"], expected["targetId"])

    def test_follow_up_requires_disambiguation_between_title_and_tab_action(self):
        titled = {**SECOND, "title": "1번 재생"}
        adapter, _ = self.adapter(listed(FIRST, titled))
        self.assertIn("2번", adapter.handle("영상 멈춰"))
        # Provide receipts so a regression attempting the wrong action fails an assertion.
        adapter, transport = self.adapter(listed(FIRST, titled), changed("play", FIRST, paused=False))
        reply = adapter.handle("1번 재생")
        self.assertIn("탭 번호와 영상 제목", reply)
        self.assertEqual(transport.requests, [])
        self.assertFalse(adapter.recognizes("1번"))

        adapter, _ = self.adapter(listed(FIRST, titled))
        self.assertIn("2번", adapter.handle("영상 멈춰"))
        adapter, transport = self.adapter(listed(FIRST, titled), changed("pause", titled))
        self.assertEqual(adapter.handle("1번 재생 선택해 줘"), "영상을 일시 정지했어요.")
        self.assertEqual(transport.requests[-1]["target"]["targetId"], titled["targetId"])

    def test_follow_up_unsafe_or_invalid_tab_command_never_reuses_pending_action(self):
        for text in (
            "첫 번 두 번째 탭 유튜브를 재생해줘",
            "첫 번째 탭 멈춰 그리고 재생해",
            "첫 번째 탭 유튜브를 재생하지 마",
            "첫 번째 탭 유튜브에서 재즈 검색해",
            "9번 탭 멈춰",
        ):
            with self.subTest(text=text):
                adapter, _ = self.adapter(listed(FIRST, SECOND))
                self.assertIn("1번", adapter.handle("계속 재생해"))
                adapter, transport = self.adapter()
                self.assertTrue(adapter.recognizes(text))
                self.assertIsNotNone(adapter.handle(text))
                self.assertFalse(adapter.recognizes("1번"))
                self.assertEqual(transport.requests, [])

    def test_follow_up_failed_browser_query_clears_superseded_action(self):
        adapter, _ = self.adapter(listed(FIRST, SECOND))
        adapter.handle("계속 재생해")
        adapter, transport = self.adapter("<unavailable>")
        self.assertIn("확인할 수", adapter.handle("첫 번째 탭 멈춰"))
        self.assertFalse(adapter.recognizes("1번"))
        self.assertEqual([request["op"] for request in transport.requests], ["list"])

    def test_paginated_spoken_selection_keeps_original_order_across_workers(self):
        videos = [{
            **FIRST, "targetId": f"tab-{index}", "videoId": f"{index:011}",
            "url": f"https://www.youtube.com/watch?v={index:011}",
            "title": f"테스트 연주 {index}",
        } for index in range(1, 41)]
        adapter, _ = self.adapter(listed(*videos))
        first = adapter.handle("소리 꺼")
        self.assertIn("8번, 테스트 연주 8", first)
        self.assertIn("다음 목록", first)
        self.assertNotIn("9번, 테스트 연주 9", first)

        adapter, transport = self.adapter()
        self.assertTrue(adapter.recognizes("유튜브 다음 목록"))
        second = adapter.handle("유튜브 다음 목록")
        self.assertIn("9번, 테스트 연주 9", second)
        self.assertIn("16번, 테스트 연주 16", second)
        self.assertNotIn("17번, 테스트 연주 17", second)
        self.assertEqual(transport.requests, [])

        adapter, _ = self.adapter()
        self.assertIn("17번, 테스트 연주 17", adapter.handle("다음 목록"))
        adapter, _ = self.adapter()
        self.assertIn("9번, 테스트 연주 9", adapter.handle("이전 목록"))

        adapter, transport = self.adapter(
            listed(*reversed(videos)), changed("mute", videos[8], muted=True)
        )
        self.assertEqual(adapter.handle("9번"), "영상의 소리를 껐어요.")
        self.assertEqual(transport.requests[-1]["target"]["targetId"], "tab-9")

        self.state.unlink()
        adapter, _ = self.adapter(listed(*videos))
        adapter.handle("소리 꺼")
        adapter, transport = self.adapter(listed(*videos), changed("mute", videos[-1], muted=True))
        self.assertEqual(adapter.handle("테스트 연주 40"), "영상의 소리를 껐어요.")
        self.assertEqual(transport.requests[-1]["target"]["targetId"], "tab-40")

    def test_contextual_selection_accepts_unique_title(self):
        adapter, _ = self.adapter(listed(FIRST, SECOND))
        adapter.handle("소리 꺼")
        adapter, transport = self.adapter(listed(FIRST, SECOND), changed("mute", FIRST, muted=True))
        self.assertTrue(adapter.recognizes("피아노 연주"))
        self.assertEqual(adapter.handle("유튜브 피아노 연주 선택해 줘"), "영상의 소리를 껐어요.")
        self.assertEqual(transport.requests[-1]["target"]["targetId"], FIRST["targetId"])

    def test_selection_title_with_missing_unicode_is_safe_to_store(self):
        adapter, _ = self.adapter(listed({**FIRST, "title": "피아노\ud800 연주"}, SECOND))
        self.assertIn("피아노 연주", adapter.handle("소리 꺼"))

    def test_selected_identity_must_still_exist(self):
        adapter, _ = self.adapter(listed(FIRST, SECOND))
        adapter.handle("영상 멈춰")
        adapter, transport = self.adapter(listed(FIRST))
        self.assertIn("선택한 탭", adapter.handle("2번"))
        self.assertEqual([x["op"] for x in transport.requests], ["list"])

    def test_changed_video_and_closed_tab_cannot_be_reported_as_success(self):
        other_video = {
            **FIRST, "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA", "videoId": "AAAAAAAAAAA"
        }
        for receipt in (
            {"op": "pause", "ok": True, "before": FIRST, "after": other_video},
            {"op": "pause", "ok": True, "before": other_video, "after": FIRST},
            {"op": "pause", "ok": False, "error": "changed"},
            {"op": "pause", "ok": False, "error": "missing"},
        ):
            with self.subTest(receipt=receipt):
                adapter, _ = self.adapter(listed(FIRST), receipt)
                self.assertIn("확인할 수", adapter.handle("영상 멈춰"))

    def test_ignores_unrelated_and_spoofed_video_tabs(self):
        adapter, transport = self.adapter(listed(
            {**FIRST, "url": "https://youtube.com.attacker.test/watch?v=abcdefghijk"},
            {**FIRST, "url": "https://example.test/?url=https://www.youtube.com/watch?v=abcdefghijk"},
            {**FIRST, "url": "https://www.youtube.com/results?search_query=piano"},
            {**FIRST, "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"},
            {**FIRST, "url": "https://www.youtube.com:444/watch?v=abcdefghijk"},
        ))
        self.assertIn("영상 탭이", adapter.handle("영상 멈춰"))
        self.assertEqual(len(transport.requests), 1)

    def test_unloaded_video_keeps_truthful_reply(self):
        unloaded = {**FIRST, "paused": None, "muted": None, "volume": None,
                    "currentTime": None, "duration": None}
        adapter, _ = self.adapter(listed(unloaded), {"op": "play", "ok": False, "error": "unloaded"})
        self.assertIn("플레이어", adapter.handle("계속 재생해"))

    def test_volume_uses_observed_state(self):
        for actual, expected in [(0.3, "영상 볼륨을 30퍼센트로 맞췄어요."), (0.5, "확인할 수")]:
            with self.subTest(actual=actual):
                adapter, _ = self.adapter(listed(FIRST), changed("volume", FIRST, volume=actual))
                self.assertIn(expected, adapter.handle("볼륨 30퍼센트로 해"))

    def test_seek_requires_actual_position_and_clamps_at_start(self):
        at_start = {**FIRST, "currentTime": 3}
        adapter, _ = self.adapter(listed(at_start), changed("seek", at_start, currentTime=0))
        self.assertIn("처음", adapter.handle("10초 뒤로"))
        adapter, _ = self.adapter(listed(FIRST), changed("seek", FIRST))
        self.assertIn("확인할 수", adapter.handle("10초 앞으로"))

    def test_search_only_passes_encoded_query_and_checks_destination(self):
        query = '재즈 & 단어 "]); malicious();"'
        search_url = (
            "https://www.youtube.com/results?search_query="
            "%EC%9E%AC%EC%A6%88+%26+%EB%8B%A8%EC%96%B4+%22%5D%29%3B+malicious%28%29%3B%22"
        )
        adapter, transport = self.adapter({"op": "search", "ok": True, "url": search_url})
        self.assertEqual(adapter.handle(f"유튜브에서 {query} 검색해"), "유튜브에 검색 결과를 열었어요.")
        self.assertEqual(transport.requests, [{"op": "search", "url": search_url}])
        args, kwargs = transport.commands[0]
        self.assertEqual(args[:3], ["aside", "repl", "--json"])
        self.assertFalse(kwargs.get("shell"))
        self.assertGreater(kwargs["timeout"], 0)
        adapter, _ = self.adapter({"op": "search", "ok": True, "url": FIRST["url"]})
        self.assertIn("확인할 수", adapter.handle(f"유튜브에서 {query} 검색해"))

    def test_cli_json_receipt_can_be_inside_console_output(self):
        receipt = {"atticusMedia": {"op": "open", "ok": True, "url": "https://www.youtube.com/"}}
        output = json.dumps({"run": {"content": [{"type": "text", "text": json.dumps(receipt)}],
                                     "error": None, "elapsedMs": 7}})
        adapter, _ = self.adapter(output)
        self.assertEqual(adapter.handle("유튜브 열어"), "유튜브를 열었어요.")

    def test_timeout_missing_binary_and_corrupt_receipts_are_failures(self):
        for output in (
            subprocess.TimeoutExpired("aside", 1), FileNotFoundError("aside"),
            "<not JSON>", '{"atticusMedia": {"op": "list", "ok": true, "videos": false}}',
        ):
            with self.subTest(output=output):
                adapter, _ = self.adapter(output)
                self.assertIn("확인할 수", adapter.handle("영상 멈춰"))


@unittest.skipUnless(shutil.which("node"), "Node is needed for static JS fixtures")
class MediaScriptTests(unittest.TestCase):
    """Run the static script against in-memory tab and video objects."""
    HARNESS = """
        const fs = require("node:fs");
        const request = JSON.parse(process.argv[1]);
        const fixture = process.argv[2];
        const source = JSON.parse(process.argv[4]);
        const original = source.url;
        const replacement = "https://www.youtube.com/watch?v=AAAAAAAAAAA";
        const location = globalThis.location = {href: original};
        let calls = 0;
        let currentTime = 20;
        const attached = new Set();
        const closed = [];
        const opened = [];
        const shortsPage = new URL(original).pathname.startsWith("/shorts/");
        const unmarkedShort = fixture.startsWith("unmarkedShort");
        const player = {getVideoData: () => {
            if (["missingID", "domID"].includes(fixture) || (fixture === "losesID" && calls)) return {};
            if (fixture === "unmarkedShortWrongId") return {video_id: "AAAAAAAAAAA"};
            const url = new URL(location.href);
            return {video_id: url.searchParams.get("v") ?? url.pathname.split("/").at(-1)};
        }};
        const video = {
            paused: unmarkedShort, muted: false, volume: 0.5, duration: 200,
            readyState: unmarkedShort ? 4 : 2, seeking: false,
            get currentTime() { return currentTime; },
            set currentTime(value) {
                this.seeking = true;
                this.seekTarget = value;
            },
            addEventListener(event, callback) {
                if (event === "seeked") setImmediate(() => {
                    currentTime = this.seekTarget;
                    this.seeking = false;
                    callback();
                });
            },
            removeEventListener() {},
            getBoundingClientRect: () => unmarkedShort ? {width: 399, height: 709} : {width: 1132, height: 637},
            closest: (selector) => {
                if (selector === ".html5-video-player") return player;
                if (selector === "#movie_player" && !shortsPage) return player;
                if (selector === "#shorts-player" && shortsPage) return player;
                if (selector === "ytd-watch-flexy, ytm-watch" && !shortsPage) return {};
                if (fixture === "domID" && selector.includes("[video-id]"))
                    return {getAttribute: () => source.videoId};
                if (shortsPage && (selector === "ytd-reel-video-renderer" ||
                    (!unmarkedShort && selector === "ytd-reel-video-renderer[is-active]")))
                    return {getAttribute: () => null};
                return null;
            },
            pause: () => {
                calls++;
                if (["changesAfter", "cleanupFailsAfterChange"].includes(fixture))
                    location.href = replacement;
                video.paused = true;
            },
            play: async () => {
                calls++;
                video.paused = false;
            },
        };
        function extraVideo(id, videoId, width, inReel = false) {
            const player = {getVideoData: () => ({video_id: videoId})};
            return {
                ...video,
                getBoundingClientRect: () => ({width, height: width}),
                closest: (selector) => {
                    if (selector === ".html5-video-player" || selector === "#" + id) return player;
                    if (selector === "ytd-watch-flexy, ytm-watch" && id === "movie_player") return {};
                    if (inReel && selector.startsWith("ytd-reel-video-renderer"))
                        return {hasAttribute: () => true};
                    return null;
                },
                pause: () => { throw new Error("unexpected mutation on a non-target video"); },
            };
        }
        const videos = fixture === "noPlayer" ? [] : [video];
        if (fixture === "watchPreviews") {
            videos.push(extraVideo("shorts-player", "AAAAAAAAAAA", 0));
            videos.push(extraVideo("inline-preview-player", source.videoId, 0));
        }
        if (fixture === "twoMainPlayers") videos.push(extraVideo("movie_player", source.videoId, 400));
        if (fixture === "hiddenShort") videos.push(extraVideo("shorts-player", "AAAAAAAAAAA", 0, true));
        if (unmarkedShort) {
            videos.push({...extraVideo("movie_player", "AAAAAAAAAAA", 0), readyState: 0});
            videos.push(extraVideo("inline-preview-player", source.videoId, 0));
        }
        if (fixture === "unmarkedShortTwoVisible")
            videos.push(extraVideo("shorts-player", source.videoId, 399, true));
        globalThis.document = {
            title: "Fixture - YouTube",
            querySelector: () => null,
            querySelectorAll: () => videos,
        };
        const listBrowserTabs = async () => {
            if (fixture === "tabGoneAfter" && calls) return [];
            const tabs = [{targetId: source.targetId, url: location.href, title: document.title}];
            if (fixture === "cleanupFailsWithMoreTabs") {
                tabs.push({targetId: "tab-next", url: replacement, title: "Next"});
            }
            return tabs;
        };
        const makePage = () => ({
            evaluate: async (fn, arg) => {
                if (fixture === "changesBefore") location.href = replacement;
                if (fixture === "evaluateThrows") throw new Error("player unavailable");
                return fn(arg);
            },
        });
        const preexisting = fixture === "preexisting" ? makePage() : null;
        const getTabByTargetId = targetId => {
            if (fixture === "lookupThrows") throw new Error("lookup unavailable");
            return targetId === source.targetId ? preexisting : null;
        };
        const attachBrowserTab = async () => {
            const page = makePage();
            attached.add(page);
            return page;
        };
        const closeTab = async page => {
            closed.push(attached.has(page) ? "borrowed" : "unowned");
            if (fixture.startsWith("cleanupFails")) throw new Error("cleanup unavailable");
        };
        const openTab = async url => {
            opened.push(url);
            return {url: () => url};
        };
        const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
        const script = fs.readFileSync(process.argv[3], "utf8");
        const run = new AsyncFunction(
            "request", "listBrowserTabs", "getTabByTargetId", "attachBrowserTab", "closeTab", "openTab", script
        );
        run(request, listBrowserTabs, getTabByTargetId, attachBrowserTab, closeTab, openTab)
            .then(() => console.log(JSON.stringify({calls, closed, opened, attached: attached.size})));
    """

    def run_script(self, fixture, op="pause", source=FIRST, **params):
        self.assertTrue(SCRIPT.is_file(), "The Aside action script is missing")
        target = {key: source[key] for key in ("targetId", "url", "videoId")}
        request = {"op": op, **params}
        if op not in {"list", "open", "search"}:
            request["target"] = target
        process = subprocess.run(
            ["node", "-e", self.HARNESS, json.dumps(request), fixture, str(SCRIPT), json.dumps(target)],
            capture_output=True, check=True, text=True, timeout=3,
        )
        receipt, details = [json.loads(line) for line in process.stdout.splitlines()]
        return receipt["atticusMedia"], details

    def test_static_script_releases_borrowed_player_after_listing_or_action(self):
        for op, fixture, error in (
            ("list", "normal", None),
            ("list", "evaluateThrows", None),
            ("pause", "normal", None),
            ("pause", "evaluateThrows", "browser"),
            ("pause", "changesBefore", "changed"),
            ("pause", "changesAfter", "changed"),
            ("pause", "tabGoneAfter", "missing"),
        ):
            with self.subTest(op=op, fixture=fixture):
                receipt, details = self.run_script(fixture, op)
                self.assertEqual(receipt.get("error"), error)
                self.assertEqual(details["attached"], 1)
                self.assertEqual(details["closed"], ["borrowed"])

    def test_static_script_stops_listing_after_failed_release(self):
        receipt, details = self.run_script("cleanupFailsWithMoreTabs", "list")
        self.assertEqual(receipt, {"op": "list", "ok": False, "error": "cleanup"})
        self.assertEqual(details["attached"], 1)
        self.assertEqual(details["closed"], ["borrowed"])

    def test_static_script_does_not_claim_ownership_after_a_failed_lookup(self):
        for op in ("list", "pause"):
            with self.subTest(op=op):
                receipt, details = self.run_script("lookupThrows", op)
                self.assertEqual(details["attached"], 0)
                self.assertEqual(details["closed"], [])
                if op == "pause":
                    self.assertEqual(receipt.get("error"), "browser")

    def test_static_script_reuses_a_preexisting_session_page_without_detaching(self):
        for op in ("list", "pause"):
            with self.subTest(op=op):
                receipt, details = self.run_script("preexisting", op)
                self.assertTrue(receipt["ok"])
                self.assertEqual(details["attached"], 0)
                self.assertEqual(details["closed"], [])

    def test_static_script_cleanup_failure_does_not_mask_receipt(self):
        succeeded, details = self.run_script("cleanupFails")
        self.assertTrue(succeeded["ok"])
        self.assertTrue(succeeded["after"]["paused"])
        self.assertIs(succeeded.get("cleanup"), False)
        self.assertEqual(details["closed"], ["borrowed"])

        failed, details = self.run_script("cleanupFailsAfterChange")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"], "changed")
        self.assertIs(failed.get("cleanup"), False)
        self.assertEqual(details["closed"], ["borrowed"])

    def test_static_script_leaves_new_navigation_tabs_open(self):
        for op, url in (
            ("open", "https://www.youtube.com/"),
            ("search", "https://www.youtube.com/results?search_query=jazz"),
        ):
            with self.subTest(op=op):
                receipt, details = self.run_script("normal", op, url=url)
                self.assertEqual(receipt, {"op": op, "ok": True, "url": url})
                self.assertEqual(details["opened"], [url])
                self.assertEqual(details["attached"], 0)
                self.assertEqual(details["closed"], [])

    def test_static_script_reads_actual_paused_player(self):
        receipt, details = self.run_script("normal")
        self.assertTrue(receipt["ok"])
        self.assertIs(receipt["before"]["paused"], False)
        self.assertIs(receipt["after"]["paused"], True)
        self.assertEqual(details["calls"], 1)

    def test_static_script_chooses_watch_player_over_hidden_shorts_and_preview(self):
        receipt, details = self.run_script("watchPreviews")
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["after"]["videoId"], FIRST["videoId"])
        self.assertTrue(receipt["after"]["paused"])
        self.assertEqual(details["calls"], 1)

    def test_static_script_refuses_two_visible_main_players(self):
        receipt, details = self.run_script("twoMainPlayers")
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["error"], "unloaded")
        self.assertEqual(details["calls"], 0)

    def test_static_script_chooses_only_visible_active_short(self):
        receipt, details = self.run_script("hiddenShort", source=SECOND)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["after"]["videoId"], SECOND["videoId"])
        self.assertEqual(details["calls"], 1)

    def test_static_script_reads_and_plays_visible_short_with_no_reel_markers(self):
        status, details = self.run_script("unmarkedShort", "list", source=SECOND)
        self.assertTrue(status["videos"][0].get("paused"))
        self.assertEqual(details["calls"], 0)

        receipt, details = self.run_script("unmarkedShort", "play", source=SECOND)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["before"]["videoId"], SECOND["videoId"])
        self.assertTrue(receipt["before"]["paused"])
        self.assertFalse(receipt["after"]["paused"])
        self.assertEqual(details["calls"], 1)

    def test_static_script_rejects_two_visible_shorts_or_mismatched_player_id(self):
        for fixture, error in (("unmarkedShortTwoVisible", "unloaded"), ("unmarkedShortWrongId", "changed")):
            with self.subTest(fixture=fixture):
                receipt, details = self.run_script(fixture, "play", source=SECOND)
                self.assertFalse(receipt["ok"])
                self.assertEqual(receipt["error"], error)
                self.assertEqual(details["calls"], 0)

    def test_static_script_does_not_touch_wrong_video_or_unloaded_player(self):
        for fixture, error in [("changesBefore", "changed"), ("noPlayer", "unloaded")]:
            with self.subTest(fixture=fixture):
                receipt, details = self.run_script(fixture)
                self.assertFalse(receipt["ok"])
                self.assertEqual(receipt["error"], error)
                self.assertEqual(details["calls"], 0)

    def test_static_script_requires_observed_player_id(self):
        receipt, details = self.run_script("missingID")
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["error"], "unloaded")
        self.assertEqual(details["calls"], 0)

    def test_static_script_requires_player_id_during_readback(self):
        receipt, details = self.run_script("losesID")
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["error"], "unloaded")
        self.assertEqual(details["calls"], 1)

    def test_static_script_accepts_observed_watch_ancestor_video_id(self):
        receipt, details = self.run_script("domID")
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["before"]["videoId"], FIRST["videoId"])
        self.assertEqual(details["calls"], 1)

    def test_static_script_waits_for_completed_seek_before_reading_position(self):
        receipt, _ = self.run_script("normal", "seek", value=10)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["before"]["currentTime"], 20)
        self.assertEqual(receipt["after"]["currentTime"], 30)

    def test_static_script_detects_replacement_after_action(self):
        receipt, details = self.run_script("changesAfter")
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["error"], "changed")
        self.assertEqual(details["calls"], 1)


if __name__ == "__main__":
    unittest.main()
