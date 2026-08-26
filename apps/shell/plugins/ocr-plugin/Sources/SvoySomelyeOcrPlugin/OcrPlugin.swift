import Capacitor
import Foundation
import UIKit
import Vision

/// OCR-плагин «Свой Сомелье»: распознавание текста с этикетки на устройстве через Vision
/// (VNRecognizeTextRequest), без отправки фото на сервер. Это быстрее и юридически чище, чем
/// серверный веб-фолбэк POST /scan/ocr (contracts/openapi.yaml) — фото вообще не покидает
/// устройство. TS-сторона: ../../src/definitions.ts, ../../src/index.ts.
///
/// Статус: ПОЛНАЯ реализация (не заглушка) — решение оркестратора после ревью 01, блокер 7
/// («сцена 1 демо — скан → карточка — не может остаться без владельца»). Собирать и гонять на
/// реальном устройстве может только publish-Mac (здесь нет Xcode, см. reports/c-report.md).
/// Текстовый ввод в ScanScreen остаётся равноправным путём независимо от этого плагина.
@objc(OcrPlugin)
public class OcrPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "OcrPlugin"
    public let jsName = "Ocr"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "recognizeText", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "isAvailable", returnType: CAPPluginReturnPromise),
    ]

    /// Языки этикеток в демо-наборе: русские названия + латиница (бренд, объём, %).
    /// TODO(publish-Mac): сверить с VNRecognizeTextRequest.supportedRecognitionLanguages(for:
    /// revision:) на целевой версии iOS — набор поддерживаемых языков и качество кириллицы
    /// зависят от версии Vision framework на устройстве.
    private static let recognitionLanguages = ["ru-RU", "en-US"]

    @objc func isAvailable(_ call: CAPPluginCall) {
        call.resolve(["available": true])
    }

    @objc func recognizeText(_ call: CAPPluginCall) {
        guard let base64 = call.getString("base64Image"), !base64.isEmpty else {
            call.reject("Нужен base64Image — этикетка не передана", "invalid_image")
            return
        }
        // Клиент может передать data:-префикс по невнимательности — срезаем его на всякий случай.
        let commaIndex = base64.firstIndex(of: ",")
        let rawBase64 = commaIndex.map { String(base64[base64.index(after: $0)...]) } ?? base64

        guard let imageData = Data(base64Encoded: rawBase64) else {
            call.reject("Не удалось декодировать base64Image", "invalid_image")
            return
        }
        guard let image = UIImage(data: imageData), let cgImage = image.cgImage else {
            call.reject("Не удалось прочитать изображение из base64Image", "invalid_image")
            return
        }

        let request = VNRecognizeTextRequest { visionRequest, error in
            if let error = error {
                call.reject("Vision не смог распознать текст: \(error.localizedDescription)", "vision_error")
                return
            }
            let observations = (visionRequest.results as? [VNRecognizedTextObservation]) ?? []
            let candidates = observations.compactMap { $0.topCandidates(1).first }
            let text = candidates.map(\.string).joined(separator: "\n")
            let confidence: Double =
                candidates.isEmpty
                ? 0
                : candidates.reduce(0.0) { $0 + Double($1.confidence) } / Double(candidates.count)

            call.resolve([
                "text": text,
                "confidence": confidence,
            ])
        }

        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = OcrPlugin.recognitionLanguages

        let handler = VNImageRequestHandler(
            cgImage: cgImage,
            orientation: OcrPlugin.cgOrientation(for: image.imageOrientation),
            options: [:]
        )

        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try handler.perform([request])
            } catch {
                call.reject("Не удалось запустить распознавание: \(error.localizedDescription)", "vision_error")
            }
        }
    }

    /// UIImage.Orientation -> CGImagePropertyOrientation. Без этого текст на фото, снятом не
    /// в базовой портретной ориентации (обычный случай для камеры телефона), распознаётся
    /// с систематическими ошибками — Vision ожидает EXIF-подобную ориентацию, а не пиксели как есть.
    private static func cgOrientation(for orientation: UIImage.Orientation) -> CGImagePropertyOrientation {
        switch orientation {
        case .up: return .up
        case .down: return .down
        case .left: return .left
        case .right: return .right
        case .upMirrored: return .upMirrored
        case .downMirrored: return .downMirrored
        case .leftMirrored: return .leftMirrored
        case .rightMirrored: return .rightMirrored
        @unknown default: return .up
        }
    }
}
