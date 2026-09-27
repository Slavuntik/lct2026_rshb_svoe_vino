import { describe, expect, it } from "vitest";
import { truncateAtWordBoundary } from "./text";

/**
 * Регресс qa-manual-final.md п.5: цитаты в чате резались `quote.slice(0, 40)` — фиксированная
 * длина без учёта границ слов. На живых данных это давало то обрубок слова («· Каберне
 * Совинь», «· Кры»), то висячий разделитель без ничего после («Adagum Valley Saperavi ·
 * Olymp Winery ·»). Кейсы ниже — ровно те строки со скриншота qa-manual-final-shots/
 * 12-chat-answer-citations.png (см. отчёт), плюс граничные случаи.
 */
describe("truncateAtWordBoundary", () => {
  it("короткую строку (<= maxLength) не трогает — без многоточия", () => {
    expect(truncateAtWordBoundary("Короткая строка", 40)).toBe("Короткая строка");
  });

  it("строку ровно в maxLength символов не трогает", () => {
    const exact = "a".repeat(40);
    expect(truncateAtWordBoundary(exact, 40)).toBe(exact);
  });

  it("не обрубает слово посреди — откатывается к последней границе пробела", () => {
    // Жёсткий срез на 40 символов даёт "...Чёрный · Ведерник" (без "овъ") — регресс из
    // отчёта. Ожидаем полный откат ДО "·", целиком отбрасывая недописанное слово.
    const quote = "Ведерниковъ Цимлянский Чёрный · Ведерниковъ";
    const result = truncateAtWordBoundary(quote, 40);
    expect(result).toBe("Ведерниковъ Цимлянский Чёрный…");
    // Ни одно слово результата не должно быть обрубком: каждое слово (кроме многоточия)
    // целиком встречается в исходной строке как отдельное слово.
    const originalWords = new Set(quote.split(/\s+/));
    for (const word of result.replace("…", "").trim().split(/\s+/)) {
      expect(originalWords.has(word)).toBe(true);
    }
  });

  it("убирает висячий разделитель на конце (после отката к границе слова)", () => {
    // Жёсткий срез на 40 символов заканчивается ровно на "Winery · " — висячее "·" без
    // ничего после (регресс из отчёта). Ожидаем, что "·" тоже уйдёт вместе с пробелом.
    const quote = "Adagum Valley Saperavi · Olymp Winery · Кубань";
    const result = truncateAtWordBoundary(quote, 40);
    expect(result).toBe("Adagum Valley Saperavi · Olymp Winery…");
    expect(result).not.toMatch(/[·•\-–—,;:|]\s*…$/);
  });

  it("однословный текст длиннее maxLength (нет пробела) — жёсткий срез с многоточием", () => {
    const longWord = "a".repeat(50);
    const result = truncateAtWordBoundary(longWord, 40);
    expect(result).toBe(`${"a".repeat(40)}…`);
  });

  it("добавляет многоточие только когда текст реально обрезан", () => {
    expect(truncateAtWordBoundary("Точно 5", 40).endsWith("…")).toBe(false);
    expect(truncateAtWordBoundary("а".repeat(41), 40).endsWith("…")).toBe(true);
  });
});
