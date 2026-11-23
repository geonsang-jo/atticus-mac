# Atticus — 한국어 Mac 비서

[SaarthurR/atticus-mac](https://github.com/SaarthurR/atticus-mac)의 개인용 포크입니다. 키보드를 다른 컴퓨터에서 사용하는 동안에도 호출어로 YouTube를 조작하고 herdr 개발 세션과 등록한 테스트 상태를 확인합니다.

**기본 사용:** “컴퓨터야” → 마이크 준비 안내음 → 한국어 요청 → 답변 후 호출어 대기. 매 요청마다 호출어가 필요합니다. 한 호흡으로 호출어와 요청을 붙여 말하기와 낭독 중 끼어들기는 첫 버전 범위에 포함하지 않습니다.

- 기본 한국어 호출어와 요청 인식은 macOS 26의 온디바이스 SpeechAnalyzer를 사용합니다. Siri와 시스템 받아쓰기를 켜지 않아도 동작합니다. 설정에서 호출어와 요청 언어를 각각 선택할 수 있으며 영어 `Atticus` 호출도 설정할 수 있습니다(해당 언어 모델 준비 필요).
- 최초 설정에서 마이크와 음성 인식 권한을 허용합니다. “호출어 대기 중”은 실제 마이크가 열린 뒤에만 표시됩니다. 패널 표시나 작업 감시는 호출어 대기를 막지 않습니다.
- 현재 YouTube 제어 대상은 **Aside** 브라우저입니다. YouTube가 명시된 미지원 요청과 일반 질문도 로컬에서 지원 범위를 안내합니다. Aside CLI와 연결된 프로필이 필요합니다. 여러 영상 탭은 음성으로 선택합니다.
- herdr의 `hc events --json`으로 상태를 관찰합니다. 작업 완료와 테스트 성공을 구분하며, 현재 버전은 세션 상태·완료 알림을 제공하며, 세션의 최종 답변 요약은 제공하지 않습니다. 세션의 대화 내용을 임의로 스캔하지 않습니다.
- 이 포크의 자동 업데이트는 기본으로 꺼져 있습니다. 내장 assistant 모드는 upstream 사용량 telemetry를 보내지 않습니다.

| 요청 예 | 동작 |
|---|---|
| 유튜브 멈춰 / 유튜브 계속 재생해 | 주 영상 정지 / 재생 |
| 유튜브 소리 꺼 / 소리 켜 | 음소거 / 해제 |
| 유튜브 볼륨 30퍼센트로 해 / 10초 앞으로 | 음량 / 재생 위치 변경 |
| 유튜브에서 재즈 검색해 / 지금 뭐 틀어져 있어 | 검색 / 영상 제목·상태 |
| 개발 세션들 지금 뭐 하고 있어 | 세션 상태 확인 |
| 프로젝트이름 세션 끝나면 알려줘 / 감시 그만해 | 완료 알림 등록 / 해제 |
| 세션 2번 / 다음 세션 목록 | 모호한 대상을 음성으로 선택 |
| 테스트 돌려줘 / 테스트 상태 알려줘 | 등록된 테스트 실행 / 상태 확인 |

테스트는 최초 설정에서 이름·작업 폴더·실행 인자를 명시적으로 등록합니다. 예:

```sh
python3 atticus.py --assistant-register-test 예제 "$PWD" '["python3", "-m", "unittest", "tests.test_assistant_sessions"]'
```

음성으로 임의 shell 명령을 생성하지 않습니다. 직접 추적한 테스트만 종료 코드·시그널·시간 초과를 확정할 수 있습니다. 기존 에이전트 안에서 실행한 임의 테스트 결과와 혼동하지 않습니다.

로컬 검증 빌드(인증서 생성·로그인 항목 변경 없음):

```sh
ATTICUS_LOCAL_BUILD=1 DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer bash build.sh
```

결과 앱은 `work/Atticus.app`입니다. 빠른 로컬 개발 빌드에는 `ATTICUS_DEBUG_BUILD=1`을 추가할 수 있습니다. 일반 `./build.sh`는 아래 upstream 방식으로 설치하고 로그인 시 실행을 등록합니다. 새 Python 모듈은 `assistant/`에 있으며, 로컬 설정·실행 기록과 검증 산출물은 커밋하지 않습니다.

현재 검증 결과와 실제 마이크 확인 여부는 실행한 리비전의 검증 기록을 확인하세요. API 지원 조회나 합성 입력 테스트만으로 실제 호출 성공을 주장하지 않습니다.

---

아래는 upstream의 기능 및 설치 설명입니다. 이 포크의 첫 지원 범위는 위 내용을 따릅니다.

<p align="center"><img src="docs/banner.png" alt="Atticus. Your whole Mac, one sentence away." width="100%"></p>

<p align="center">
  Hold <kbd>⌃ control</kbd> <kbd>⌥ option</kbd> <kbd>space</kbd>, say what you need, let go.<br>
  Atticus moves your windows, finds your files, reads your screen and gets it done.
</p>

<p align="center"><a href="https://ko-fi.com/saarthr"><b>☕ Leave a tip</b></a> · Atticus is free; tips keep it going.</p>

<p align="center">
  <b>🧠 Learns as you use it</b> &nbsp;·&nbsp; <b>⚡ Fast</b> &nbsp;·&nbsp; <b>🔑 No API key</b> &nbsp;·&nbsp; <b>Free and open source</b>
</p>

https://github.com/user-attachments/assets/26822ad2-6281-477d-b676-2f490a4a207b

<br>

## Circle it. Ask about it.

<img src="docs/circle.gif" width="100%" alt="Circling part of a photo and asking what it is">

*"What is this?"* Hold the shortcut, circle anything on your screen and ask. Atticus looks at what you circled and answers.
Works on photos, pages, code, anything you can see.

<br>

## And it does the work

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/arrange.gif" width="100%" alt="Spotify moves left and Chrome right">
      <h3>Arrange windows</h3>
      <i>"Spotify on the left, Chrome on the right."</i><br>
      Atticus moves and sizes your windows in one go.
    </td>
    <td width="50%" valign="top">
      <img src="docs/pdf.gif" width="100%" alt="Three photos become one PDF">
      <h3>Hand off a job</h3>
      <i>"Turn these photos into one PDF."</i><br>
      An agent does the whole job in the background and hands you the file.
    </td>
  </tr>
</table>

## Install

```
curl -fsSL https://raw.githubusercontent.com/SaarthurR/atticus-mac/main/install.sh | bash
```

Needs **macOS 26** and an AI client: Claude Code on a Claude plan, Devin, Pi or a local model. Nix: `nix run github:SaarthurR/atticus-mac` (Nix installations show this update path in Settings). More in the [install guide](docs/GUIDE.md#install).

## Just ask

| You say | Atticus |
|---|---|
| *"Code on my big screen, Chrome and Terminal on the laptop."* | Arranges three windows across two displays. |
| *"Open Spotify."* · *"Open Gmail."* · *"Terminal on the left."* | Runs instantly, with no AI at all. |
| *"Play Blinding Lights by The Weeknd."* | Starts it in Spotify in about a second. |
| *"Find my notes about the Tokyo trip."* | Searches your files by what's inside them. |
| *"What's wrong with this code?"* | Looks at your screen and answers in a line. |
| *"Move my dentist appointment to 4."* | Shows the change on a card. Adding is immediate; moving or deleting asks first. |
| *"Find me a desk lamp under $30 on Amazon."* | Works in Chrome behind your apps. |
| *"Where do I turn on Night Shift?"* | Points at the exact spot on your screen. |
| *"When I say focus mode, close Slack and open my to-do list."* | Remembers it. Next time, two words do it all. |

## What it does

- **Learns.** Ask once and it works out the steps. Ask again and it runs in a blink, with no AI.
- **Sees your screen.** Ask about anything on it. Point or circle with your mouse while you hold the shortcut, or use **Point at your screen** to mark a spot first.
- **Teaches and draws.** "Teach me…" points at each spot, one step at a time. "Explain this and draw it" builds a labeled diagram over your screen.
- **Picks the right model.** Easy asks go to a quick model; harder ones go to a stronger one. Change it in Settings → Intelligence.
- **Dictate anywhere.** Hold fn, talk, let go. Your words land in any text field, cleaned up.
- **Hands-free.** Turn on the wake word and just say "Atticus".
- **Background agents.** Big jobs run behind your apps while you keep working.
- **Meeting Copilot.** Answers and notes during calls, saved on your Mac.
- **Watch and routines.** "Tell me when Codex is done." "Every weekday at 8, summarize my calendar."
- **Your plan, no API key.** Runs on Claude, Devin, Pi or a [local model](docs/GUIDE.md#use-a-local-model).
- **Private and safe.** Your voice stays on your Mac. Deletes, sends and purchases ask first.

Every feature in detail: **[the guide](docs/GUIDE.md)**.

> **On Windows?** [@FindlayHi](https://github.com/FindlayHi) built **[Atticus for Windows](https://github.com/FindlayHi/atticus-windows)**.

## Privacy

Speech is turned into text on your Mac, and audio never leaves it. Anonymous usage counts send a random ID and feature names, never your words, files or screen. Turn them off in Settings → Account & Access. [Full details](docs/GUIDE.md#anonymous-usage).

## Contributing

Clone the repo and run `./build.sh` after each change. Tests: `python3 atticus.py --selftest` and `python3 -m unittest discover -s tests -v`, with a scratch `ATTICUS_DIR`. Keep personal settings, credentials and runtime state out of commits.

- `Atticus.swift`: the app (hotkey, speech, overlay)
- `atticus.py`: the brain (fast commands, system prompt, helpers)
- `ui/`: the overlay, setup and Settings pages

## License

MIT. See `LICENSE`. `ui/orbs.js` is from [thinking-orbs](https://github.com/Jakubantalik/thinking-orbs) (MIT, Jakub Antalik). Hands ideas adapted from [Plip](https://github.com/hussainn7/plip-oss) (MIT) and [Cua Driver](https://github.com/trycua/cua) (MIT).
