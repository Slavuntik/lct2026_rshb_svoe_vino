import { Capacitor } from "@capacitor/core";
import { Ocr } from "svoy-somelye-ocr-plugin";

/**
 * Нативный OCR (apps/shell/plugins/ocr-plugin) реализован ТОЛЬКО на iOS (Vision). На Android
 * плагин не имеет нативной имплементации — вызов метода на несуществующей платформе упал бы
 * ошибкой моста, а не мягким фолбэком, поэтому проверяем платформу явно, а не общий
 * Capacitor.isNativePlatform() (он true и для Android).
 */
export function isNativeOcrAvailable(): boolean {
  return Capacitor.getPlatform() === "ios";
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("fileToBase64: FileReader error"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("fileToBase64: expected a data URL string from FileReader"));
        return;
      }
      // reader.readAsDataURL даёт "data:image/jpeg;base64,<...>" — плагину нужен только хвост.
      const commaIndex = result.indexOf(",");
      resolve(commaIndex === -1 ? result : result.slice(commaIndex + 1));
    };
    reader.readAsDataURL(file);
  });
}

/** Скан → сырой текст этикетки через Vision, без обращения к серверу вообще (см. /scan/ocr = 501). */
export async function recognizeLabelText(file: File): Promise<string> {
  const base64Image = await fileToBase64(file);
  const { text } = await Ocr.recognizeText({ base64Image });
  return text;
}
