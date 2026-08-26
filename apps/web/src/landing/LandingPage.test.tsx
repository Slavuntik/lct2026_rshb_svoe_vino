import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { storage } from "../lib/storage";
import { renderApp } from "../test/renderApp";
import { LandingPage } from "./LandingPage";

function renderLanding() {
  return renderApp(
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/app" element={<div>APP_PROBE</div>} />
    </Routes>,
    "/",
  );
}

describe("LandingPage — юридические ссылки", () => {
  it("на странице есть ссылка на политику обработки данных", () => {
    renderLanding();
    const links = screen.getAllByRole("link", { name: /политика обработки данных/i });
    expect(links.length).toBeGreaterThan(0);
    links.forEach((link) => expect(link).toHaveAttribute("href", "/legal/privacy.html"));
  });

  it("на странице есть ссылки на тексты согласий и условия использования", () => {
    renderLanding();
    expect(screen.getAllByRole("link", { name: /тексты согласий/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /условия использования/i })).toHaveAttribute(
      "href",
      "/legal/terms.html",
    );
  });
});

describe("LandingPage — тизер «Винные дороги»", () => {
  it("показан как информационный блок про будущую функцию, без CTA", () => {
    renderLanding();
    expect(screen.getByRole("heading", { name: /винные дороги/i })).toBeInTheDocument();
    // Тизер не должен превращаться в отдельный вход в приложение — это не кнопка.
    expect(screen.queryByRole("button", { name: /винные дороги/i })).not.toBeInTheDocument();
  });
});

describe("LandingPage — 18+ интерстициал перед гостевым входом", () => {
  it("по умолчанию не показан — только после клика по CTA", () => {
    renderLanding();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("при отказе честно блокирует и не запоминает согласие", () => {
    renderLanding();
    fireEvent.click(screen.getByRole("button", { name: /попробовать в браузере/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Нет" }));

    expect(screen.getByText(/доступ только для совершеннолетних/i)).toBeInTheDocument();
    expect(screen.queryByText("APP_PROBE")).not.toBeInTheDocument();
    expect(storage.hasSeenLandingAgeGate()).toBe(false);
  });

  it("при подтверждении пускает в приложение гостем и запоминает выбор", async () => {
    renderLanding();
    fireEvent.click(screen.getByRole("button", { name: /попробовать в браузере/i }));
    fireEvent.click(screen.getByRole("button", { name: /да, мне есть 18/i }));

    await waitFor(() => expect(screen.getByText("APP_PROBE")).toBeInTheDocument());
    expect(storage.hasSeenLandingAgeGate()).toBe(true);
    expect(storage.getAccountKind()).toBe("guest");
  });

  it("повторно не показывается — второй заход сразу ведёт в приложение", async () => {
    storage.setSeenLandingAgeGate();
    renderLanding();

    fireEvent.click(screen.getByRole("button", { name: /попробовать в браузере/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("APP_PROBE")).toBeInTheDocument());
  });
});

describe("LandingPage — SEO-минимум", () => {
  it("задаёт landing-специфичные title и og-теги, не трогая их безвозвратно", () => {
    const previousTitle = document.title;
    const { unmount } = renderLanding();

    expect(document.title).toBe("Свой Сомелье — честный разбор вина по этикетке и вкусу");
    expect(document.head.querySelector('meta[property="og:title"]')).toHaveAttribute(
      "content",
      "Свой Сомелье — честный разбор вина по этикетке и вкусу",
    );
    expect(document.head.querySelector('meta[property="og:description"]')?.getAttribute("content")).toBeTruthy();

    unmount();
    expect(document.title).toBe(previousTitle);
    expect(document.head.querySelector('meta[property="og:title"]')).not.toBeInTheDocument();
  });
});
