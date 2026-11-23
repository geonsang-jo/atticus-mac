// Offline regression. This source substitutes a file-backed mic for the live
// capture device, then exercises the app's actual WakeSpeech implementation.
import Foundation
import Speech
import AVFoundation

struct AVCaptureDevice {}

final class Mic {
    func start(_ device: AVCaptureDevice, sampleRate: Double,
               _ send: @escaping (AVAudioPCMBuffer) -> Void,
               completion: @escaping (Result<Void, Error>) -> Void) {
        DispatchQueue.global().async {
            do {
                let file = try AVAudioFile(forReading: URL(fileURLWithPath: CommandLine.arguments[1]))
                let format = file.processingFormat, frames = AVAudioFrameCount(format.sampleRate / 25)
                for _ in 0..<100 {
                    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
                    if file.framePosition < file.length { try file.read(into: buffer) }
                    else {
                        buffer.frameLength = frames
                        for channel in 0..<Int(format.channelCount) {
                            buffer.floatChannelData![channel].initialize(repeating: 0, count: Int(frames))
                        }
                    }
                    send(buffer)
                    Thread.sleep(forTimeInterval: 0.04)
                }
            } catch { DispatchQueue.main.async { completion(.failure(error)) } }
        }
        completion(.success(()))
    }
}

/*WAKE_ENGINE*/

let session = WakeSpeech(), target = "컴퓨터야"
Task { @MainActor in
    guard await SpeechTranscriber.installedLocales.contains(where: { $0.identifier(.bcp47) == "ko-KR" }) else { exit(77) }
    var ready = false
    session.start(Mic(), device: AVCaptureDevice(), locale: Locale(identifier: "ko-KR"), phrase: target,
                  ready: { ready = true }, heard: { words in
        if words.contains(target) {
            guard ready else { exit(1) }
            session.stop(); print("matched after first accepted buffer"); exit(0)
        }
    }, failed: { reason in print("failed", reason); exit(1) })
}
RunLoop.current.run(until: Date().addingTimeInterval(20))
exit(1)
