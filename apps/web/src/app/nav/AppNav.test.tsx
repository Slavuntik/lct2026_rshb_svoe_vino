import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../../test/renderApp";
import AppShell from "../AppShell";
import { storage } from "../../lib/storage";
import { resetShelfAvailabilityForTests } from "../../lib/shelfAvailability";
import { AppNav } from "./AppNav";

// Лёгкий стенд без реальных экранов — фокус на самой навигации (шапка/меню), а не на том,
// что происходит на экранах-целях. Интеграционный сценарий с настоящим ScanScreen — ниже,
// отдельным describe (проверяет именно то, что должен делать поиск из шапки: фокус в
// текстовое поле скана, contracts/image-scan.md тут ни при чём — чисто клиентский переход).
function Harness({ shelfAvailable = false }: { shelfAvailable?: boolean }) {
  return (
    <>
      <AppNav shelfAvailable={shelfAvailable} />
      <Routes>
        <Route path="/app/scan" element={<p>экран скана</p>} />
        <Route path="/app/shelf" element={<p>экран витрины</p>} />
        <Route path="/app/chat" element={<p>экран сомелье</p>} />
        <Route path="/app/taste" element={<p>экран вкуса</p>} />
        <Route path="/app/profile" element={<p>экран профиля</p>} />
      </Routes>
    </>
  );
}

function openMenu() {
  return userEvent.click(screen.getByRole("button", { name: "Открыть меню разделов" }));
}

describe("AppNav — шапка и сэндвич-меню", () => {
  it("рендерит марку, поиск и закрытую по умолчанию кнопку меню", () => {
    renderApp(<Harness />, "/app/profile");
    expect(screen.getByRole("link", { name: "На экран скана" })).toHaveAttribute("href", "/app/scan");
    expect(screen.getByRole("img", { name: "Своё Вино" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Найти вино по названию" })).toBeInTheDocument();
    const menuButton = screen.getByRole("button", { name: "Открыть меню разделов" });
    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("открывается по клику на сэндвич-кнопку и переносит фокус в панель", async () => {
    renderApp(<Harness />, "/app/profile");
    await openMenu();
    const dialog = screen.getByRole("dialog", { name: "Разделы" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свернуть меню разделов" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Закрыть меню" })).toHaveFocus();
  });

  it("сохраняет все разделы, включая «Витрина», во время недоступности", async () => {
    renderApp(<Harness shelfAvailable={false} />, "/app/profile");
    await openMenu();
    expect(screen.getByRole("link", { name: "Скан" })).toHaveAttribute("href", "/app/scan");
    // Задача тимлида 27.09: последний пункт «есть в Figma, нет у нас» — «Каталог вин» отдельным
    // разделом меню (сканер остаётся главным действием, домашний экран не меняем).
    expect(screen.getByRole("link", { name: "Каталог вин" })).toHaveAttribute("href", "/app/catalog");
    expect(screen.getByRole("link", { name: "Сомелье" })).toHaveAttribute("href", "/app/chat");
    expect(screen.getByRole("link", { name: "Вкус" })).toHaveAttribute("href", "/app/taste");
    expect(screen.getByRole("link", { name: "Профиль" })).toHaveAttribute("href", "/app/profile");
    expect(screen.getByRole("link", { name: "Витрина" })).toHaveAttribute("href", "/app/shelf");
  });

  it("показывает «Витрина», когда живой health-чек её разрешает", async () => {
    renderApp(<Harness shelfAvailable={true} />, "/app/profile");
    await openMenu();
    expect(screen.getByRole("link", { name: "Витрина" })).toHaveAttribute("href", "/app/shelf");
  });

  it("содержит ссылки на юридические страницы внизу списка", async () => {
    renderApp(<Harness />, "/app/profile");
    await openMenu();
    expect(screen.getByRole("link", { name: "Политика обработки данных" })).toHaveAttribute(
      "href",
      "/legal/privacy.html",
    );
    expect(screen.getByRole("link", { name: "Тексты согласий" })).toHaveAttribute("href", "/legal/consent.html");
    expect(screen.getByRole("link", { name: "Условия использования" })).toHaveAttribute(
      "href",
      "/legal/terms.html",
    );
  });

  it("закрывается по Escape и возвращает фокус на кнопку-триггер", async () => {
    renderApp(<Harness />, "/app/profile");
    await openMenu();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открыть меню разделов" })).toHaveFocus();
  });

  it("закрывается по клику вне панели (по подложке)", async () => {
    renderApp(<Harness />, "/app/profile");
    await openMenu();
    await userEvent.click(screen.getByTestId("nav-menu-backdrop"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("клик внутри самой панели меню не закрывает её", async () => {
    renderApp(<Harness />, "/app/profile");
    await openMenu();
    await userEvent.click(screen.getByRole("heading", { name: "Разделы" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("закрывается при переходе по пункту меню", async () => {
    renderApp(<Harness />, "/app/profile");
    await openMenu();
    await userEvent.click(screen.getByRole("link", { name: "Вкус" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("экран вкуса")).toBeInTheDocument();
  });

  it("держит фокус внутри панели: Tab с последнего элемента уходит на кнопку закрытия", async () => {
    renderApp(<Harness shelfAvailable={false} />, "/app/profile");
    await openMenu();
    const closeButton = screen.getByRole("button", { name: "Закрыть меню" });
    const legalLinks = screen.getAllByRole("link", { name: /Политика обработки данных|Тексты согласий|Условия использования/ });
    const lastLink = legalLinks[legalLinks.length - 1];

    lastLink.focus();
    expect(lastLink).toHaveFocus();
    await userEvent.tab();
    expect(closeButton).toHaveFocus();
  });

  it("держит фокус внутри панели: Shift+Tab с кнопки закрытия уходит на последнюю ссылку", async () => {
    renderApp(<Harness shelfAvailable={false} />, "/app/profile");
    await openMenu();
    const closeButton = screen.getByRole("button", { name: "Закрыть меню" });
    expect(closeButton).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("link", { name: "Условия использования" })).toHaveFocus();
  });

  it("поиск в шапке ведёт на экран скана", async () => {
    renderApp(<Harness />, "/app/profile");
    await userEvent.click(screen.getByRole("button", { name: "Найти вино по названию" }));
    expect(await screen.findByText("экран скана")).toBeInTheDocument();
  });

  it("не показывает анимацию открытия при prefers-reduced-motion: reduce", async () => {
    const matchMediaMock = vi.fn().mockReturnValue({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });
    vi.stubGlobal("matchMedia", matchMediaMock);

    renderApp(<Harness />, "/app/profile");
    await openMenu();
    expect(screen.getByTestId("nav-menu-backdrop")).toHaveStyle({ animation: "none" });
    expect(screen.getByRole("dialog")).toHaveStyle({ animation: "none" });

    vi.unstubAllGlobals();
  });
});

describe("AppNav — поиск фокусирует настоящее текстовое поле скана (интеграция через AppShell)", () => {
  afterEach(() => {
    storage.setOnboardingComplete(false);
    resetShelfAvailabilityForTests();
  });

  it("клик по иконке поиска на любом экране переносит на /app/scan и фокусирует textarea", async () => {
    storage.setOnboardingComplete(true);
    renderApp(<Routes><Route path="/app/*" element={<AppShell />} /></Routes>, "/app/profile");

    await userEvent.click(screen.getByRole("button", { name: "Найти вино по названию" }));

    await waitFor(() => {
      expect(document.getElementById("scan-text-search")).toHaveFocus();
    });
  });
});
