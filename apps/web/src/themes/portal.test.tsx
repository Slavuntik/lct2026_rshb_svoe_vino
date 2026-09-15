import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ScanScreen } from "../app/scan/ScanScreen";
import { WineCardContent } from "../components/WineCardContent";
import { findWineBySlug } from "../mocks/fixtures/wines";
import { renderApp } from "../test/renderApp";
import "../styles/global.css";
import "./portal.css";

/**
 * Тема «портал» (VITE_THEME=portal, кейс ЛЦТ — см. portal.css) — ТОЛЬКО переопределение
 * CSS custom properties под :root[data-theme="portal"]. Она обязана не менять ни один
 * className и не менять структуру рендера — иначе «модуль внутри каталога платформы»
 * перестаёт быть тем же самым модулем, что проходил остальные тесты этого файла/экрана.
 *
 * Здесь это зафиксировано как инвариант: рендерим одни и те же компоненты под дефолтной
 * и портальной темой и сверяем полный снапшот "ТЕГ.className" по каждому узлу — должен
 * совпасть 1-в-1. Заодно проверяем, что тема не no-op: реальные значения custom
 * properties действительно другие (jsdom резолвит их из :root[data-theme=...] при
 * vitest.config `test.css: true` — эмпирически проверено перед тем, как полагаться на
 * это в assert, отдельным разовым прогоном).
 *
 * global.css импортирован явно, как в main.tsx (он же @import'ит tokens.css — источник
 * дефолтных значений); без явного импорта здесь jsdom не увидит вообще никаких правил —
 * тесты не проходят через main.tsx, только через сами компоненты.
 */

function nodeSnapshot(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("*")).map((el) => `${el.tagName}.${el.getAttribute("class") ?? ""}`);
}

function accentToken(): string {
  return getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
}

function wineFixture() {
  const wine = findWineBySlug("tihaya-buhta-chardonnay-reserve-2023");
  if (!wine) throw new Error("fixture-вино не найдено в mocks/fixtures/wines");
  const { searchTerms: _searchTerms, ...card } = wine;
  return card;
}

afterEach(() => {
  // dataset на document.documentElement — глобальное состояние, testing-library cleanup()
  // его не трогает (он только размонтирует React-деревья), поэтому чистим руками, иначе
  // "portal" утечёт в соседние тестовые файлы, использующие тот же jsdom-документ.
  delete document.documentElement.dataset.theme;
});

describe("Тема portal не ломает структуру дефолтной темы (снапшот классов)", () => {
  it("WineCardContent — идентичный снапшот тегов/классов, разные CSS-токены", () => {
    const defaultRun = renderApp(<WineCardContent wine={wineFixture()} />);
    const defaultNodes = nodeSnapshot(defaultRun.container);
    const defaultAccent = accentToken();
    cleanup();

    document.documentElement.dataset.theme = "portal";
    const portalRun = renderApp(<WineCardContent wine={wineFixture()} />);
    const portalNodes = nodeSnapshot(portalRun.container);
    const portalAccent = accentToken();
    cleanup();

    expect(portalNodes).toEqual(defaultNodes);
    expect(portalNodes.length).toBeGreaterThan(10);

    // Тема реально активна, а не no-op — токены не совпадают с дефолтом и равны
    // значениям, реально извлечённым из архива портала (см. portal.css).
    expect(defaultAccent).toBe("#7D2A3C");
    expect(portalAccent).toBe("#8F3D42");
  });

  it("ScanScreen (дропзона фото-first) — идентичный снапшот тегов/классов под обеими темами", () => {
    const defaultRun = renderApp(<ScanScreen />);
    const defaultNodes = nodeSnapshot(defaultRun.container);
    cleanup();

    document.documentElement.dataset.theme = "portal";
    const portalRun = renderApp(<ScanScreen />);
    const portalNodes = nodeSnapshot(portalRun.container);
    cleanup();

    expect(portalNodes).toEqual(defaultNodes);
    expect(portalNodes.length).toBeGreaterThan(10);
  });
});
