# Korean Hands-free Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement and review each task.

**Goal:** Call Atticus by Korean or English wake phrase without touching the keyboard, control YouTube in Aside, and observe herdr sessions and registered tests with spoken results.

**Architecture:** Retain the native Swift app and existing Python worker. Add deterministic, independently tested Python adapters for media and observations. Native wake and notification delivery stay independent of background monitoring.

**Tech Stack:** Swift/AppKit/Speech, Python standard library, Aside CLI, herdr hc JSON event stream.

## Global Constraints

- The approved spec is docs/superpowers/specs/2026-11-23-korean-assistant-design.md; the user has approved implementation and geonsang-jo fork.
- Highest priority: keyboard-free wake invocation whenever awake and unlocked, including with overlay visible and background watches/tests running. English invocation is acceptable as a fallback but Korean is the intended default.
- Default phrase: 헤이 아티. Configurable wakeLanguage and speechLanguage independent of interface language; English supported.
- Every new command requires a wake phrase. Do not auto-capture YouTube audio after a reply. Own TTS suppresses wake briefly then restores it. Two-stage phrase, chime, command is the v1 contract.
- Python parsers and adapters execute only on final confirmed transcription, never speculative/live partial output.
- Actual browser interaction/QA must use mcp__aside__repl. Runtime Aside adapter uses a fixed script via argv, not shell or generated model code.
- Preserve the existing herdr repositories and credentials. Do not read unrelated private transcripts.
- Use per-command DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; do not change system-wide Xcode selection.
- Do not commit personal paths, project registrations, recordings, credentials or runtime logs. Scratch files belong in ignored work/.
- No claim of actual microphone correctness until real speech has exercised the installed app.

## Task 1 — Native wake and Korean recognition

**Owner:** wake implementer. Files: Atticus.swift, ui/settings.html, ui/onboarding.html only where voice controls need changes; new tests/test_korean_wake.py. Do not modify atticus.py or build.sh.

**Interfaces:** Existing App.wakeTick/stopWake/enableWakeWord and speechModule; new App.pollAssistantNotifications() is owned by root and can be called from root's timer. Root may integrate a small notification method in Atticus.swift after ownership handoff.

- [ ] Add failing behavior tests for Unicode normalization/matching, language selection, wake eligibility while panel visible/background work active, callback generation and no automatic follow-up capture.
- [ ] Run focused tests and save RED evidence in work/reports/wake.md.
- [ ] Add explicit Korean/English wake and speech languages. Default new Korean fork to wake enabled after permissions/setup, with clear readiness state. Preserve safe permission and capture teardown.
- [ ] Permit wake independent of panel visibility and worker busy state; new request may cancel/replace foreground AI only after actual wake detection. Background watchers/tests must remain alive.
- [ ] Disable implicit quiet-followup microphone. Restore wake after answer/cancel/error. Preserve sleep/lock/device error recovery and bounded retry.
- [ ] Add Korean setting copy and practical onboarding wake trial. No fake passing microphone indicator.
- [ ] Run focused tests and Swift typecheck with full Xcode; write GREEN results and self-review. Notify root when Atticus.swift ownership is released.

## Task 2 — Deterministic Korean YouTube control

**Owner:** media implementer. Files: assistant/media.py, assistant/media_script.js, tests/test_assistant_media.py. Root owns assistant/__init__.py, atticus.py, build.sh.

**Interface:** assistant.media.handle(text: str) -> str | None; return None only when text is not a supported media request. A recognized request with ambiguity/failure returns truthful Korean text. Also expose parse_command(text) and a testable adapter accepting injected subprocess transport. No imports with side effects.

- [ ] Write parser tests using Korean/English request examples, negation, numeric bounds, explicit play/pause, search-only, and unrecognized text; verify RED.
- [ ] Implement fixed action enums. Validate numeric args. Handle media with static Aside REPL script, stdout JSON receipts and timeout. No shell=True or LLM.
- [ ] Resolve actual YouTube video tabs by exact trusted hostname and target identity. Use unique playing tab or remembered explicit selection. When ambiguous, speak titles and accept a later contextual selection; never guess the first tab.
- [ ] Verify video identity and actual paused/muted/volume/currentTime state before reporting success. Handle closed tabs, changed videos, unloaded players and browser errors.
- [ ] Add deterministic adapter/fixture tests for the above, run GREEN, document tests/self-review in work/reports/media.md.

## Task 3 — Session observer and tracked tests

**Owner:** monitor implementer. Files: assistant/monitor.py, assistant/sessions.py, assistant/test_runs.py, tests/test_assistant_monitor.py, tests/test_assistant_sessions.py, tests/test_assistant_tests.py. Root owns central package/CLI/native wiring.

**Interfaces:** assistant.monitor.handle(text: str) -> str | None; assistant.monitor.ensure_daemon(owner_pid: int) -> bool; assistant.monitor.run_daemon(owner_pid: int) -> None; assistant.monitor.pop_notifications() -> list[dict] where dict has id/text and optional kind. Root wires flags --assistant-start OWNER_PID, --assistant-daemon OWNER_PID, --assistant-pop and --assistant-register-test NAME CWD ARGV_JSON. Provide register_test(name, cwd, argv) entry point in assistant.test_runs. Paths derive from ATTICUS_DIR on each call. No side effects at import.

- [ ] Start with event reducer fixtures covering initial snapshot/idle, working→done, blocked/error turn, missing/duplicate/out-of-order sequence, identity replacement, reconnect. Run RED.
- [ ] Read hc events --json using argv. Strip inherited HC_TERMINAL_ID. Do not autostart unrelated app windows. On disconnect show unknown and resubscribe with bounded retry.
- [ ] Match sessions by stable identity, not UI badge. Register watch targets in bounded local state; duplicate events and reconnect must not retrigger completed notifications. Query and target-selection commands return Korean messages.
- [ ] Implement owner-bound background daemon with one-instance lease and private files. Observe without blocking foreground requests. Store notification receipts in a private atomic spool; pop only once.
- [ ] Track explicitly registered test argv in their cwd. Record run ID, owner/child identity, start/end time, exit code, signal, timeout, bounded output. State unknown when evidence insufficient. Do not equate agent completion with test success.
- [ ] Tests execute harmless fixture commands only. Cover zero/nonzero/signal/timeout, stale process, duplicate notifications, daemon lock and shutdown. Run GREEN and document in work/reports/monitor.md.

## Task 4 — Root integration, build and startup

**Owner:** root. Files: assistant/__init__.py, assistant/commands.py if necessary, atticus.py, build.sh, new developer build script if needed, README.md, tests/test_assistant_integration.py. Modify native notification hook only after Task 1 releases Atticus.swift.

- [ ] Add integration RED tests that Korean commands use adapters only after confirmation; ATTICUS_LIVE=1 is side-effect free for assistant intents.
- [ ] Wire deterministic adapter responses into fast() before English gist parsing. Wire assistant CLI flags before ordinary AI startup. No assistant activity during upstream selftests.
- [ ] Add asynchronous native notification polling and short Korean TTS delivery. Serialize the poll, bound delivery, defer until current utterance/voice completes, return to wake. Pass native owner PID to daemon.
- [ ] Bundle assistant package and JS asset. Build into a local app without changing global Xcode or silently adding signing certificates. Install the verified app for live wake testing; preserve previous app if present.
- [ ] Document setup, mandatory microphone/wake checks, Aside connection, test registration and known v1 limits. Commit approved spec/plan and implementation changes only after relevant checks pass.

## Task 5 — Review and measured verification

- [ ] Review each task diff for spec coverage, concurrency/resource lifecycle and meaningful tests; fix material findings.
- [ ] Run focused new tests, full host-safe ./check --all and native --selftest on the integrated revision.
- [ ] Confirm fork origin/upstream and push verified branch to geonsang-jo.
- [ ] With Aside MCP, inspect real YouTube, exercise reversible media actions, read back state, restore original playback/volume if borrowed.
- [ ] Exercise live hc observer without reading unrelated transcripts. Run controlled fixture tests through tracker.
- [ ] Install/open app, obtain macOS permissions as needed, and perform a real voice wake trial. Ask user to speak only once the app and diagnostic view are ready. Keep working after user replies until verified.
- [ ] Report source/build/test evidence and distinguish actual voice verification from API support and synthetic checks.
