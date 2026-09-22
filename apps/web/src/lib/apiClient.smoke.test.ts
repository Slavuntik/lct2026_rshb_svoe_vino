import { describe, expect, it } from "vitest";
import { apiClient, ApiRequestError } from "./apiClient";
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

  // pairingDishPhoto (multipart) намеренно не проверяется здесь тем же приёмом, что scanPhoto —
  // см. apiClient.scanPhoto.node.test.ts (jsdom FormData/File теряет имя файла при реальной
  // fetch-сериализации; тот файл проверяет транспорт нативными классами Node).
  it("pairingDish (JSON, задача тимлида 22.09) отвечает по схеме — вина по категории без фото", async () => {
    const result = await apiClient.pairingDish({ category: "BBQ" });
    expect(result.status).toBe("food");
    expect(result.wines.length).toBeGreaterThan(0);
    expect(result.dish.category).toBe("BBQ");
    expect(result.dish.source).toBe("user");
  });

  // contracts/post-scan.md v1.1 §4.2: category вне 9 тегов -> 400 validation_error (не
  // 200 status=unsure — в отличие от фото, здесь пользователь выбирает строго из наших чипов).
  it("pairingDish — незнакомая категория честно отдаёт 400 validation_error, не падает молча", async () => {
    await expect(apiClient.pairingDish({ category: "Совсем не блюдо" })).rejects.toMatchObject({
      status: 400,
      code: "validation_error",
    } satisfies Partial<ApiRequestError>);
  });
});
