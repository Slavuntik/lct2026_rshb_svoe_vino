import { Capacitor } from "@capacitor/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isNativeOcrAvailable, recognizeLabelText } from "./nativeOcr";

// Ocr — Capacitor Proxy(registerPlugin), у него нет "своих" свойств для vi.spyOn.
// Подменяем модуль целиком, это стандартный способ тестировать код вокруг нативных плагинов.
const recognizeTextMock = vi.fn();
vi.mock("svoy-somelye-ocr-plugin", () => ({
  Ocr: { recognizeText: (...args: unknown[]) => recognizeTextMock(...args) },
}));

describe("lib/nativeOcr — ветвление native/web (блокер ревью 02, п.2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    recognizeTextMock.mockReset();
  });

  it("isNativeOcrAvailable() — true только на iOS, false на web и на android", () => {
    vi.spyOn(Capacitor, "getPlatform").mockReturnValue("ios");
    expect(isNativeOcrAvailable()).toBe(true);

    vi.spyOn(Capacitor, "getPlatform").mockReturnValue("android");
    expect(isNativeOcrAvailable()).toBe(false);

    vi.spyOn(Capacitor, "getPlatform").mockReturnValue("web");
    expect(isNativeOcrAvailable()).toBe(false);
  });

  it("recognizeLabelText кодирует File в base64 без data:-префикса и возвращает text плагина", async () => {
    recognizeTextMock.mockResolvedValue({ text: "Шардоне Резерв\nТихая бухта", confidence: 0.91 });
    const file = new File(["не важно, любые байты"], "label.jpg", { type: "image/jpeg" });

    const text = await recognizeLabelText(file);

    expect(text).toBe("Шардоне Резерв\nТихая бухта");
    expect(recognizeTextMock).toHaveBeenCalledTimes(1);
    const call = recognizeTextMock.mock.calls[0] as [{ base64Image: string }];
    expect(call[0].base64Image).not.toMatch(/^data:/);
    expect(call[0].base64Image.length).toBeGreaterThan(0);
  });

  it("ошибку самого плагина не глотает — прокидывает наверх для честной деградации в ScanScreen", async () => {
    recognizeTextMock.mockRejectedValue(new Error("vision_error"));
    const file = new File(["x"], "label.jpg", { type: "image/jpeg" });

    await expect(recognizeLabelText(file)).rejects.toThrow("vision_error");
  });
});
