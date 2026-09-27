import { describe, expect, it } from "vitest";
import { SensoryVectorView } from "./SensoryVectorView";
import type { SensoryVector } from "../lib/apiTypes";
import { renderApp } from "../test/renderApp";
// Обязательно явный импорт (как в themes/portal.test.tsx) — jsdom резолвит правила из
// реальных .css только для файлов, которые тест-модуль импортировал сам; setup.ts
// глобального импорта global.css не делает.
import "../styles/global.css";

/**
 * Регресс qa-manual-final.md п.1 (КРИТИЧНО, видно на каждой карточке): `.taste-axis__fill`
 * не задавал display, поэтому браузерный дефолт для <span> (inline) побеждал и инлайновый
 * style="width:NN%" молча игнорировался (width не действует на non-replaced inline-элементы)
 * — все 7 шкал выглядели одинаково пустыми независимо от чисел рядом. Чинится
 * `display: block` в global.css (.taste-axis__fill). Этот тест ловит именно регресс display,
 * а не просто наличие style="width" (оно было верным и ДО фикса — баг был чисто в CSS).
 */
describe("SensoryVectorView — регресс «шкалы вкуса визуально пустые» (qa-manual-final.md п.1)", () => {
  const vector: SensoryVector = {
    sweetness: 0.05,
    acidity: 0.6,
    tannin: 0.7,
    body: 0.75,
    oak: 0.1,
    aromatic_intensity: 0,
    bubbles: 0,
  };

  it("каждый .taste-axis__fill вычисляется как block (не inline) — иначе width молча не применяется", () => {
    const { container } = renderApp(<SensoryVectorView vector={vector} />);
    const fills = container.querySelectorAll<HTMLElement>(".taste-axis__fill");

    expect(fills).toHaveLength(7);
    fills.forEach((fill) => {
      expect(getComputedStyle(fill).display).toBe("block");
    });
  });

  it("ширины полос соответствуют числам рядом и НЕ все одинаковые (разные оси — разная ширина)", () => {
    const { container } = renderApp(<SensoryVectorView vector={vector} />);
    const fills = Array.from(container.querySelectorAll<HTMLElement>(".taste-axis__fill"));

    // Порядок осей — как в SensoryVectorView.AXES: sweetness, acidity, tannin, body, oak,
    // aromatic_intensity, bubbles. Значение% = Math.round(value*100), как в самом компоненте.
    const expectedWidths = ["5%", "60%", "70%", "75%", "10%", "0%", "0%"];
    expect(fills.map((fill) => fill.style.width)).toEqual(expectedWidths);

    // Санити-чек «не все одинаковые»: до фикса display это бы не поймал (style.width и так был
    // верным в разметке), но вместе с проверкой display выше это ровно тот регресс, который
    // видело жюри — визуально все семь шкал выглядели одной и той же пустой серой полосой.
    const distinctWidths = new Set(expectedWidths);
    expect(distinctWidths.size).toBeGreaterThan(1);
  });
});
