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
 * совпасть 1-в-1. Заодно проверяем, что тема реально переопределяет :root (а не no-op из-за
 * опечатки в селекторе/подключении файла) — jsdom резолвит custom properties из
 * :root[data-theme=...] при vitest.config `test.css: true`.
 *
 * 27.09 (frontend-figma-restyle): значения тем НАМЕРЕННО сведены к одному источнику —
 * макету Figma (см. portal.css) — а не различаются, как раньше (дефолт был приблизительной
 * догадкой по strategy.html, portal — реконструкцией по SSR-снэпшокам, отсюда разные хексы
 * #7D2A3C/#8F3D42). Сейчас обе темы должны отдавать ОДИН и тот же перенесённый акцент;
 * если когда-то снова разойдутся — тест на no-op (сравнение с дефолтом) должен об этом
 * сказать явно, поэтому оставлен, просто с новым ожидаемым значением.
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

    // Обе темы сведены к одному значению из макета Figma (см. комментарий portal.css) —
    // проверяем именно это (а не "отличаются"), плюс что селектор :root[data-theme="portal"]
    // реально резолвится в jsdom (иначе portalAccent пришёл бы пустой строкой, а не хексом).
    expect(defaultAccent).toBe("#AB494F");
    expect(portalAccent).toBe("#AB494F");
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
