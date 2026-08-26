export interface OcrRecognizeOptions {
  /** Фото этикетки: JPEG/PNG в base64, без префикса data:. */
  base64Image: string;
}

export interface OcrRecognizeResult {
  /** Сырой распознанный текст (строки через \n) — тот же вход, что принимает
   * POST /scan/resolve (contracts/openapi.yaml). Разбор и сопоставление с каталогом —
   * задача клиента/бэкенда, не этого плагина. */
  text: string;
  /** Средняя уверенность Vision по всем найденным текстовым областям, 0..1. */
  confidence: number;
}

export interface OcrAvailability {
  /** false на вебе и на устройствах без нативной реализации — вызывающая сторона обязана
   * тогда предложить равноправный путь: ручной ввод текста. */
  available: boolean;
}

export interface OcrPlugin {
  recognizeText(options: OcrRecognizeOptions): Promise<OcrRecognizeResult>;
  isAvailable(): Promise<OcrAvailability>;
}
