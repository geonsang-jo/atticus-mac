"""Exercise the app's wake loop without opening an audio device or asking for TCC."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / "Atticus.swift").read_text()


def swift_output(case, source):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "wake.swift"
        path.write_text(source)
        proc = subprocess.run(["swift", str(path), "-onboarded", "YES"], text=True,
                              capture_output=True, timeout=60)
    case.assertEqual(proc.returncode, 0, proc.stderr)
    return proc.stdout.splitlines()


class KoreanWake(unittest.TestCase):
    def test_phrase_accepts_korean_spacing_normalization_and_word_boundaries(self):
        matcher = SWIFT[SWIFT.index("    static func hasWakeWord("):SWIFT.index("    func stopWake(")]
        lines = swift_output(self, "import Foundation\nstruct Matcher {\n" + matcher + """}
let phrase = "헤이 아티"
for words in ["헤이 아티!", "헤이아티", "헤이   아티", "헤이 아티".decomposedStringWithCanonicalMapping,
              "그럼, 헤이 아티?", "헤이 아티스트", "오헤이아티", "헤이 아트", "헤이 정말 아티", ""] {
    print(Matcher.hasWakeWord(words, word: phrase))
}
print(Matcher.hasWakeWord("Atticus!", word: "Atticus"))
print(Matcher.hasWakeWord("Atticuses", word: "Atticus"))
print(Matcher.hasWakeWord("무엇이든", word: ""))
print(Matcher.hasWakeWord("헤이, 아티", word: phrase))
print(Matcher.hasWakeWord("헤이 아티", word: "헤이아티"))
""")
        self.assertEqual(lines, ["true"] * 5 + ["false"] * 5 + ["true", "false", "false", "true", "true"])

    def test_fresh_fork_has_independent_speech_and_wake_languages(self):
        defaults = SWIFT.split("static let defaults: [String: Any] =", 1)[1].split("static let retiredKeys", 1)[0]
        for key, value in [("wakeWord", "true"), ("wakeWordName", '"헤이 아티"'),
                           ("wakeLanguage", '"ko-KR"'), ("speechLanguage", '"ko-KR"')]:
            self.assertIn(f'"{key}": {value}', defaults)
        ui = (ROOT / "ui/settings.html").read_text()
        for key in ["wakeLanguage", "speechLanguage"]:
            self.assertIn(f'data-k="{key}"', ui)
        self.assertIn('value="en-US"', ui)

    def test_onboarding_voice_permissions_suffice_without_ai_or_screen_access(self):
        html = (ROOT / "ui/onboarding.html").read_text()
        ready = next(line for line in html.splitlines() if line.strip().startswith("const ready ="))
        fixture = """let builtin=true, planOK=false, last={mic:true,speech:true,screen:false};
const P=[['mic'],['speech'],['screen']];
""" + ready + """
console.log(ready('perms')); last.speech=false; console.log(ready('perms'));
"""
        result = subprocess.run(["node", "-e", fixture], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ["true", "false"])

    def test_begin_uses_native_mode_before_any_permission_or_provider_callback(self):
        html = (ROOT / "ui/onboarding.html").read_text()
        initial = next(line for line in html.splitlines() if "let practice = null" in line)
        click = next(line for line in html.splitlines() if "$('start').onclick" in line)
        script = """
function firstClick(mode) {
  const window = {atticusBuiltin:mode}, button = {}, $ = () => button, go = id => id;
""" + initial + click + """
  return button.onclick();
}
console.log(firstClick(true), firstClick(false));"""
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), "perms ai")
        setup = SWIFT.split('final class Onboarding:', 1)[1].split('    func push()', 1)[0]
        self.assertRegex(setup, r'window\.atticusBuiltin.*?injectionTime: \.atDocumentStart')

    def test_engine_start_and_result_failures_end_current_capture_and_rearm(self):
        methods = SWIFT[SWIFT.index("    func beginSpeechSegment("):SWIFT.index("    func speechPiece(")]
        harness = """import Foundation
struct AnalyzerInput {}
class AnalysisContext {
    enum Hint { case general }
    var contextualStrings: [Hint:[String]] = [:]
}
struct Piece { var text = AttributedString(""); var isFinal = false }
protocol SpeechModule {}
var failStart = false, failResults = false
class SpeechTranscriber: SpeechModule {
    var results: AsyncThrowingStream<Piece, Error> {
        AsyncThrowingStream { continuation in
            if failResults { continuation.finish(throwing: NSError(domain:"fixture-results",code:2)) }
            else { continuation.finish() }
        }
    }
}
class DictationTranscriber: SpeechTranscriber {}
class SpeechAnalyzer {
    init(modules: [any SpeechModule]) {}
    func setContext(_ c: AnalysisContext) async throws {}
    func start(inputSequence: AsyncStream<AnalyzerInput>) async throws {
        if failStart { throw NSError(domain:"fixture-start", code:1) }
    }
}
class SpeechInputs { func add(_ feed: AsyncStream<AnalyzerInput>.Continuation) -> UUID { UUID() } }
class Engine { var isRunning = true }
class Trace { func mark(_ s:String) {} }
class App {
    var engine = Engine(), speechInputs: SpeechInputs? = SpeechInputs(), speechTrace: Trace?
    var listenID = 1, speechSegment = 0, activeSpeechSegment = 0, nextSpeechSegment = 0
    var speechText: [Int: (base: String, done: String, live: String)] = [:]
    var hints: [String] = [], dictating = false, speechFailed = false, handsFree = true
    var offerText: String?, releasePending = false, busy = false, proc: Process?
    var speechFormat: Int? = 1, messages: [String] = [], rearmed = 0
    func speechModule(dictate: Bool) -> any SpeechModule { SpeechTranscriber() }
    func speechPiece(_ s:String, final:Bool, segment:Int, listeningID:Int) {}
    func hush() { engine.isRunning = false; listenID += 1 }
    func stopOfferInput() { hush() }
    func js(_ name: String, _ message: String) { messages.append(message) }
    func hide(after: Double) {}
    func wakeTick() { if !engine.isRunning { rearmed += 1 } }
    func prepareSpeech() {}
""" + methods + """
}
func roundTrip(start: Bool, result: Bool, stale: Bool) {
    failStart = start; failResults = result
    let app = App()
    let segment = app.beginSpeechSegment("", listeningID: app.listenID)
    app.activeSpeechSegment = segment.segment
    if stale { app.listenID += 1 }
    RunLoop.current.run(until: Date().addingTimeInterval(0.12))
    print(app.engine.isRunning, app.messages.count, app.rearmed,
          app.messages.contains { $0.contains("fixture-") })
}
roundTrip(start:true,result:false,stale:false)
roundTrip(start:false,result:true,stale:false)
roundTrip(start:true,result:true,stale:false)
roundTrip(start:true,result:true,stale:true)
"""
        self.assertEqual(swift_output(self, harness), ["false 1 1 true"] * 3 + ["true 0 0 false"])

    def test_partials_wait_for_confirmation_in_assistant_mode(self):
        # Use the native scheduling and finalization bodies, with receipts at the
        # two worker boundaries. No process, audio input or browser is opened.
        heard = SWIFT.split('trace.mark("first_frame") }\n            }\n        }', 1)[1].split('    // Whisper', 1)[0]
        ahead = SWIFT[SWIFT.index('    func words()'):SWIFT.index('    // Runs `atticus.py --fast`')]
        fast = SWIFT.split('    func tryFast()', 1)[1].split('        let p = py(["--fast"', 1)[0]
        final = SWIFT.split('    func typed(', 1)[1].split('    func finish() {', 1)[1].split('    // atticus.py --wait', 1)[0]
        native = """
import Foundation
enum Prefs {
    static var assistant = true
    static func bool(_ key:String) -> Bool { key == "assistantMode" ? assistant : true }
}
func transcriptKey(_ text:String) -> String { text.lowercased() }
class Engine { var isRunning = true }
class Panel { func makeKey() {}; func makeFirstResponder(_ x:Int) {} }
class Drawing { var hasInput=false; func next() {} }
struct SpeechSentences {}
class Harness {
    let engine=Engine(), panel=Panel(), drawing=Drawing(), web=0
    var text="", consumed=0, consumedText="", liveRevised=false, clickTalk=true, whisperListen=false, toggleTalk=false
    var pause: DispatchWorkItem?, aheadItem: DispatchWorkItem?, quiet: DispatchWorkItem?, ahead="", fastBusy=false
    var selectionPending=false, pointContext:Int?, talkMoved=false, hints:[String]=[], warm:Int?
    var busy=true, offerText:String?, drawingReply=false, stops=0, did:[String]=[], proc:Process?, commit="", pinned=false
    var guardUntil=Date(), answer="", handsFree=false, speechSentences=SpeechSentences(), speechMuted=false, lastDone=Date()
    var fast:[String]=[], speculative:[String]=[], confirmed:[String]=[]
    func stop() {}; func pauseWait() -> Double? { 0.005 }
    func spawn() -> Int? { 1 }; func dropAhead() { ahead = "" }
    func run(_ s:String,speculative:Bool=false,remaining:String?=nil) {
        if speculative { self.speculative.append(s) } else { confirmed.append(s) }
    }
    func unwarm() {}; func hush() {}; func hush(keepReply:Bool,keepWarm:Bool) {}; func typed(_ s:String,heard:Bool) {}
    func js(_ n:String, _ v:String="") {}; func settleTalkPointer() {}; func cancel() {}
    func hide(after:Double) {}; func cue(_ s:String) {}; func deliver(_ s:String) {}; func linger(_ v:Double)->Double {v}
    func received(_ partial:String) { text = partial
""" + heard + ahead + '    func tryFast()' + fast + """
        fast.append(seg)
    }
    func finish() {
""" + final + """
}
func pump() { RunLoop.current.run(until:Date().addingTimeInterval(0.04)) }
let app=Harness()
app.received("search YouTube"); pump()
app.received("유튜브에서 재즈"); pump()
app.tryFast(); app.startAhead()   // previously queued work must observe the current mode, too
print("partial", app.fast.count, app.speculative.count, app.pause == nil, app.aheadItem == nil, app.quiet != nil)
app.received("search YouTube for jazz"); app.engine.isRunning=false; app.finish()
print("final", app.confirmed)
Prefs.assistant=false
let legacy=Harness(); legacy.received("search YouTube"); pump()
legacy.received("유튜브에서 재즈"); pump()
print("legacy", legacy.fast, legacy.speculative)
"""
        self.assertEqual(swift_output(self, native), [
            "partial 0 0 true true true", 'final ["search YouTube for jazz"]',
            'legacy ["search YouTube", "유튜브에서 재즈"] ["유튜브에서 재즈"]'])

    def test_chime_follows_open_command_microphone(self):
        start = SWIFT.split("    func start(dictate: Bool, quiet: Bool = false)", 1)[1].split("    func heard(", 1)[0]
        self.assertLess(start.index("engine.start(device"), start.index('cue("soundStart")'))
        self.assertLess(start.index("if case .failure(let error) = result"), start.index('cue("soundStart")'))

    def test_wake_loop_rearms_after_answer_and_consumes_each_match_once(self):
        wake = SWIFT[SWIFT.index("    let wakeMic = Mic()"):SWIFT.index("    // Whisper: the mic fills")]
        harness = """import Foundation
enum Prefs {
    static var enabled = true
    static func set(_ k: String, _ v: Bool) { enabled = v }
    static func str(_ key: String) -> String { key == "wakeWordName" ? "헤이 아티" : "ko-KR" }
    static func bool(_ key: String) -> Bool { key == "wakeWord" && enabled }
}
enum Access { case authorized, denied }
var granted = true
enum Media { case audio }
final class AVCaptureDevice {
    let uniqueID: String, localizedName = "fixture"
    init?(uniqueID: String) { self.uniqueID = uniqueID }
    static func authorizationStatus(for: Media) -> Access { granted ? .authorized : .denied }
    static func `default`(for: Media) -> AVCaptureDevice? { AVCaptureDevice(uniqueID:"fixture") }
}
var selectedID = "fixture"
struct Device { let uid = selectedID }
func listenMic() -> Device? { Device() }
class Mic {
    var isRunning = false
    func stop(_ done: () -> Void) { isRunning = false; done() }
    func start(_ device: AVCaptureDevice, sampleRate: Double, _ buffer: (Int) -> Void, completion: (Result<Void, Error>) -> Void) { isRunning = true; completion(.success(())) }
}
class SFSpeechAudioBufferRecognitionRequest {
    var requiresOnDeviceRecognition = false, shouldReportPartialResults = false, contextualStrings = [String]()
    func append(_ i: Int) {}; func endAudio() {}
}
class SFSpeechRecognitionTask { func cancel() {} }
struct Transcription { let formattedString: String }
struct Words { let bestTranscription: Transcription; let isFinal = false }
class SFSpeechRecognizer {
    static var callbacks: [(Words?, Error?) -> Void] = []
    static var languages: [String] = []
    let locale: Locale
    init?(locale: Locale) { self.locale = locale; Self.languages.append(locale.identifier) }
    static func authorizationStatus() -> Access { granted ? .authorized : .denied }
    let isAvailable = true, supportsOnDeviceRecognition = true
    func recognitionTask(with: SFSpeechAudioBufferRecognitionRequest, resultHandler: @escaping (Words?, Error?) -> Void) -> SFSpeechRecognitionTask {
        Self.callbacks.append(resultHandler); return SFSpeechRecognitionTask()
    }
}
class Flag { var isRunning = false, isPlaying = false }
struct Meeting { var running = false }
var meeting = Meeting()
struct Onboarding {
    var win: Int?; var wakeTrial = false, setupClosed = false
    func request(_ need:String, settingsOnRefusal:Bool, current:()->Bool, done:(Bool)->Void) { if current() { done(granted) } }
}
var onboarding = Onboarding()
struct Settings { func push() {}; func send(_ s: String, _ v: Any) {} }
enum Atticus { static let settings = Settings() }
struct Drawing { var voice: Flag?, active = true; mutating func clear() { active = false } }
struct Panel { var isVisible = true }
final class App {
    var busy = true, dictating = false, composing = false, shortcutDown = false, dictateShortcutDown = false
    var handsFree = false, wakeSleeping = false, wakeInactive = false, wakeLocked = false
    var releasePending = false, voicePermissionPending = false, retryOffer = false, askFile = ""
    var panel = Panel(), engine = Flag(), voice: Flag?, drawing = Drawing(), cueSound: Flag?, cueEnds = Date.distantPast
    var clickTalk = false, captures = 0, cancelled = 0, pinned = false, voicePermissionID = 0
    var voicePermissionAction: (() -> Void)?
    func js(_ name: String, _ value: String?) {}
    func missingVoicePermission(_ need: String, retry: @escaping () -> Void) { voicePermissionAction = retry; voicePermissionPending = true }
    func start(dictate: Bool, quiet: Bool) { captures += 1; engine.isRunning = true }
    func hush() { busy = false; retryOffer = false; cancelled += 1 }
""" + wake + """
}
let app = App(), matched = Words(bestTranscription: Transcription(formattedString: "헤이아티"))
func cycle() { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
app.wakeTick()
print("armed", SFSpeechRecognizer.callbacks.count, "cancelled", app.cancelled)
if let old = SFSpeechRecognizer.callbacks.first {
    old(Words(bestTranscription: Transcription(formattedString: "유튜브 소리")), nil); cycle()
    print("ambient", app.captures, app.cancelled)
    old(matched, nil); old(matched, nil); cycle()
    print("wake", app.captures, app.cancelled, app.drawing.active)
    app.engine.isRunning = false; app.wakeTick()
    old(matched, nil); cycle()
    print("rearmed", SFSpeechRecognizer.callbacks.count, app.captures)
    app.voice = Flag(); app.voice?.isRunning = true; app.wakeTick()
    SFSpeechRecognizer.callbacks.last?(matched, nil); cycle()
    print("speaking", app.wakeRequest == nil, app.captures)
    app.voice = nil; app.wakeTick()
    app.wakeSleeping = true; app.wakeTick()
    SFSpeechRecognizer.callbacks.last?(matched, nil); cycle()
    print("sleep", app.wakeRequest == nil, app.captures)
    app.wakeSleeping = false; app.wakeTick()
    print("resumed", app.wakeRequest != nil, Set(SFSpeechRecognizer.languages) == ["ko-KR"])
    app.wakeLocked = true
    SFSpeechRecognizer.callbacks.last?(matched, nil); cycle()
    print("screen-locked", app.wakeRequest == nil, app.captures)
    app.wakeLocked = false; app.wakeTick(); selectedID = "usb-mic"
    let previous = SFSpeechRecognizer.callbacks.last
    app.wakeTick(); previous?(matched, nil); cycle()
    print("device", app.wakeDevice, app.captures)
    let failed = SFSpeechRecognizer.callbacks.last
    failed?(nil, NSError(domain:"fixture", code:1)); cycle()
    failed?(matched, nil); cycle()
    print("failed", app.wakeRequest == nil, app.wakeRetry > Date(), app.captures)
    app.wakeRetry = .distantPast; app.wakeTick()
    print("recovered", app.wakeRequest != nil)
    app.stopWake(); granted = false; app.enableWakeWord(); cycle()
    print("denied", Prefs.enabled, app.wakeRequest == nil)
    granted = true; app.wakeTick()
    print("later-grant", app.wakeRequest != nil, app.voicePermissionPending)
}
"""
        self.assertEqual(swift_output(self, harness), ["armed 1 cancelled 0", "ambient 0 0",
                         "wake 1 1 false", "rearmed 2 1", "speaking true 1", "sleep true 1", "resumed true true",
                         "screen-locked true 1", "device usb-mic 1", "failed true true 1", "recovered true",
                         "denied true true", "later-grant true false"])


if __name__ == "__main__":
    unittest.main()
