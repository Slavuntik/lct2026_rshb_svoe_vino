import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../../test/renderApp";
import { ScanShortcut } from "./ScanShortcut";

/**
 * Задача тимлида 27.09 (второй заход, придирчивая сверка): ярлык «Скан» наезжал на фото
 * виноградника карточки вина (position:fixed поверх ЛЮБОГО контента в своём углу — не только
 * во время прокрутки, но и на самом первом экране без единого скролла, см. reports/frontend-
 * layout-audit.md). Проверяем оба решения: скрытие на /app/wine/* (там уже есть свой явный
 * «К сканеру») и скрытие при прокрутке вниз на остальных экранах.
 */
function scrollTo(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, writable: true, configurable: true });
  act(() => {
    window.dispatchEvent(new Event("scroll"));
  });
}

describe("ScanShortcut", () => {
  it("не рендерится на /app/scan", () => {
    renderApp(<ScanShortcut />, "/app/scan");
    expect(screen.queryByRole("link", { name: "Быстрый переход к скану" })).not.toBeInTheDocument();
  });

  it("не рендерится на карточке вина (/app/wine/:id) — там уже есть свой «К сканеру»", () => {
    renderApp(<ScanShortcut />, "/app/wine/tihaya-buhta-chardonnay-reserve-2023");
    expect(screen.queryByRole("link", { name: "Быстрый переход к скану" })).not.toBeInTheDocument();
  });

  it("виден по умолчанию на остальных экранах (например, каталог)", () => {
    renderApp(<ScanShortcut />, "/app/catalog");
    const link = screen.getByRole("link", { name: "Быстрый переход к скану" });
    expect(link).not.toHaveClass("scan-shortcut--hidden");
    expect(link).not.toHaveAttribute("aria-hidden", "true");
  });

  it("прячется при прокрутке вниз и возвращается при прокрутке вверх", () => {
    renderApp(<ScanShortcut />, "/app/catalog");
    const link = screen.getByRole("link", { name: "Быстрый переход к скану" });

    scrollTo(400);
    expect(link).toHaveClass("scan-shortcut--hidden");
    expect(link).toHaveAttribute("aria-hidden", "true");
    expect(link).toHaveAttribute("tabIndex", "-1");

    scrollTo(340);
    expect(link).not.toHaveClass("scan-shortcut--hidden");
    expect(link).not.toHaveAttribute("aria-hidden", "true");
  });

  it("у самого верха страницы всегда виден, даже сразу после прокрутки вниз", () => {
    renderApp(<ScanShortcut />, "/app/catalog");
    const link = screen.getByRole("link", { name: "Быстрый переход к скану" });

    scrollTo(500);
    expect(link).toHaveClass("scan-shortcut--hidden");

    scrollTo(10);
    expect(link).not.toHaveClass("scan-shortcut--hidden");
  });

  it("новый экран сбрасывает спрятанность — иначе короткая страница унаследует «спрятан» с прошлой длинной", async () => {
    function Harness() {
      return (
        <>
          <ScanShortcut />
          <nav>
            <Link to="/app/profile">К профилю</Link>
          </nav>
        </>
      );
    }
    renderApp(<Harness />, "/app/catalog");
    // Ссылку берём ДО прокрутки: после неё элемент aria-hidden, и getByRole("link", ...) без
    // {hidden:true} его больше не находит (так и задумано — это и есть проверяемое поведение).
    const link = screen.getByRole("link", { name: "Быстрый переход к скану" });
    scrollTo(500);
    expect(link).toHaveClass("scan-shortcut--hidden");

    await userEvent.click(screen.getByRole("link", { name: "К профилю" }));
    expect(link).not.toHaveClass("scan-shortcut--hidden");
  });

  it("не анимирует скрытие при prefers-reduced-motion: reduce", () => {
    const matchMediaMock = vi.fn().mockReturnValue({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });
    vi.stubGlobal("matchMedia", matchMediaMock);

    renderApp(<ScanShortcut />, "/app/catalog");
    expect(screen.getByRole("link", { name: "Быстрый переход к скану" })).toHaveStyle({ transition: "none" });

    vi.unstubAllGlobals();
  });
});
