import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChatMessageText } from "./ChatMessageText";

/**
 * Регресс qa-manual-final.md п.6: ответ сомелье иногда приходит с markdown-разметкой
 * (`**жирный**`, нумерованные списки) — до этого компонента звёздочки были видны буквально
 * ("1. **Рубин Голодриги** — сухое красное…"), выглядело недоделанным. ChatMessageText
 * рендерит `**жирный**` как <strong> и построчные "1. "/"- " списки как <ol>/<ul> —
 * остальной текст проходит как раньше.
 */
describe("ChatMessageText", () => {
  it("обычный текст без разметки рендерится как есть, одним узлом — без побочных эффектов на существующие тесты ChatScreen", () => {
    const text = "К стейку хорошо подойдёт «Красностоп Крепкий» [1] — плотные танины.";
    const { container } = render(<ChatMessageText text={text} />);
    expect(container.textContent).toBe(text);
    expect(container.querySelector("strong")).toBeNull();
    expect(container.querySelector("ol")).toBeNull();
    expect(container.querySelector("ul")).toBeNull();
  });

  it("**жирный** внутри строки рендерится как <strong>, звёздочки не видны в тексте", () => {
    const { container } = render(<ChatMessageText text="1. **Рубин Голодриги** — сухое красное." />);
    expect(container.textContent).not.toMatch(/\*/);
    const strong = container.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong?.textContent).toBe("Рубин Голодриги");
  });

  it("одиночная непарная звёздочка остаётся обычным текстом (не ломается)", () => {
    const { container } = render(<ChatMessageText text="3*4=12, не формула Марковица" />);
    expect(container.textContent).toBe("3*4=12, не формула Марковица");
    expect(container.querySelector("strong")).toBeNull();
  });

  it("нумерованный список строк рендерится как <ol> с отдельными <li>", () => {
    const text = [
      "1. Бельбек Саперави Резерв — насыщенный вкус [1].",
      "2. Adagum Valley Saperavi — интенсивный аромат [2].",
      "3. Ведерниковъ Цимлянский Чёрный — полнотелое вино [5].",
    ].join("\n");
    const { container } = render(<ChatMessageText text={text} />);
    const items = container.querySelectorAll("ol > li");
    expect(items).toHaveLength(3);
    expect(items[0].textContent).toBe("Бельбек Саперави Резерв — насыщенный вкус [1].");
    expect(items[2].textContent).toBe("Ведерниковъ Цимлянский Чёрный — полнотелое вино [5].");
    // Порядковые "1. "/"2. "/"3. " не дублируются текстом — их даёт сам <ol> визуально.
    expect(container.textContent).not.toMatch(/^\d\./m);
  });

  it("маркированный список («- ») рендерится как <ul>", () => {
    const text = "- Закуски\n- Мясное ассорти\n- Сыры";
    const { container } = render(<ChatMessageText text={text} />);
    const items = container.querySelectorAll("ul > li");
    expect(items).toHaveLength(3);
    expect(Array.from(items).map((li) => li.textContent)).toEqual(["Закуски", "Мясное ассорти", "Сыры"]);
  });

  it("список окружён обычными абзацами — три отдельных блока, ничего не потеряно", () => {
    const text = "Вот варианты:\n1. Первый\n2. Второй\nЭто всё, что нашли.";
    const { container } = render(<ChatMessageText text={text} />);
    expect(container.querySelectorAll("p")).toHaveLength(2);
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(container.textContent).toContain("Вот варианты:");
    expect(container.textContent).toContain("Это всё, что нашли.");
  });
});
