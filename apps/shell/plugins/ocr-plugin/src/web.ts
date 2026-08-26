import { WebPlugin } from "@capacitor/core";
import type { OcrAvailability, OcrPlugin, OcrRecognizeOptions, OcrRecognizeResult } from "./definitions";

/**
 * Веб-реализация намеренно недоступна: OCR на этом плагине — только нативный Vision (iOS).
 * И на вебе, и на iOS до сборки плагина на publish-Mac равноправный путь — ручной ввод текста
 * (apps/web/src/app/scan/ScanScreen.tsx), поэтому честная ошибка лучше поддельной реализации.
 */
export class OcrWeb extends WebPlugin implements OcrPlugin {
  async recognizeText(_options: OcrRecognizeOptions): Promise<OcrRecognizeResult> {
    throw this.unavailable("OCR недоступен в браузере — используйте ручной ввод текста этикетки.");
  }

  async isAvailable(): Promise<OcrAvailability> {
    return { available: false };
  }
}
