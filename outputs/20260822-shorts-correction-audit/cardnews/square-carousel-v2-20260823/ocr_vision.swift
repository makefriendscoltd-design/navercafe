import AppKit
import Foundation
import Vision

func recognize(_ path: String) -> [String: Any] {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        return ["path": path, "error": "image_load_failed", "observations": []]
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["ko-KR", "en-US"]
    do {
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
        let rows: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            return ["text": candidate.string, "confidence": Double(candidate.confidence)]
        }
        return ["path": path, "observations": rows]
    } catch {
        return ["path": path, "error": String(describing: error), "observations": []]
    }
}

let rows = CommandLine.arguments.dropFirst().map { recognize($0) }
let data = try JSONSerialization.data(withJSONObject: rows, options: [])
FileHandle.standardOutput.write(data)
