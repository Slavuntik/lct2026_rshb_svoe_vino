import { describe, expect, it } from "vitest";
import config from "../../vite.config";

// Блокер ревью 02, п.3: VITE_API_MODE=real была без транспорта — dev-сервер бил в никуда.
// Тест на форму конфига (не на живой прокси — тут это оверкилл): /v1 обязан идти на
// локальный agents/B. Если кто-то в будущем снесёт proxy при рефакторинге конфига, тест упадёт.
describe("vite.config.ts — proxy для VITE_API_MODE=real", () => {
  it("проксирует /v1 на локальный бэкенд agents/B", () => {
    const proxy = config.server?.proxy;
    expect(proxy).toBeDefined();
    const entry = proxy?.["/v1"];
    expect(entry).toBeDefined();
    if (typeof entry === "object" && entry !== null && "target" in entry) {
      expect(String(entry.target)).toMatch(/^http:\/\/localhost:8000$/);
      expect(entry.changeOrigin).toBe(true);
    } else {
      throw new Error("Ожидалась объектная форма proxy-записи с target/changeOrigin");
    }
  });
});
