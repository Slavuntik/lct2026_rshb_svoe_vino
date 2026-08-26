import { describe, expect, it } from "vitest";
import { apiClient } from "./apiClient";
import type { ChatStreamEvent } from "./apiTypes";

// Проверка сквозной цепочки fetch (jsdom) -> MSW (node) -> обработчики -> apiClient,
// прежде чем строить на ней экраны. Если эта цепочка сломана, дальше нет смысла писать UI.
describe("apiClient (smoke, MSW)", () => {
  it("healthz отвечает ok", async () => {
    const result = await apiClient.healthz();
    expect(result.status).toBe("ok");
  });

  it("scanResolve находит вино по тексту этикетки", async () => {
    const result = await apiClient.scanResolve({ text: "Шардоне Резерв Тихая Бухта" });
    expect(result.matches.length).toBeGreaterThan(0);
    expect(result.matches[0].wine_id).toBe("tihaya-buhta-chardonnay-reserve-2023");
  });

  it("chat стримит token/citation/done с сопоставимыми n", async () => {
    const events: ChatStreamEvent[] = [];
    await apiClient.chat({ message: "что взять к стейку" }, (event) => events.push(event));
    expect(events.some((e) => e.type === "citation")).toBe(true);
    expect(events.at(-1)?.type).toBe("done");
  });
});
