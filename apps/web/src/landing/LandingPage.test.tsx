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
      <Route path="/app/shelf" element={<div>SHELF_PROBE</div>} />
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

  /**
   * Задача тимлида 23.09 (qa-manual-hack-v16.md §1): на живом стенде подтверждение возраста
   * показалось отсутствующим — одного клика по CTA хватало, чтобы localStorage уже нёс
   * landing_age_ack=1. Живая проверка на ЭТОЙ сессии (чистый localStorage, apps/web dev-сборка)
   * не воспроизвела дефект: интерстициал открывается корректно, ack остаётся не выставлен до
   * явного ответа. Разбор среды qa-manual: "браузер этой сессии за день мог использовать другой
   * агент/роль... 100% чистый профиль не проверял" — согласуется с тем, что найдено здесь.
   * Тест ниже фиксирует ИМЕННО тот сценарий регрессии: один клик по CTA САМ ПО СЕБЕ не должен
   * выставлять ack, пока пользователь не ответил на вопрос модалки.
   */
  it("один клик по CTA не подтверждает возраст сам по себе — ack ставится только явным ответом (qa-manual §1, регресс)", () => {
    renderLanding();
    fireEvent.click(screen.getByRole("button", { name: /попробовать в браузере/i }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(storage.hasSeenLandingAgeGate()).toBe(false);
    expect(screen.queryByText("APP_PROBE")).not.toBeInTheDocument();
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


it('opens shelf from the landing page after age confirmation',async()=>{
  renderLanding();
  fireEvent.click(screen.getByRole('button',{name:'Вино на полке'}));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(screen.queryByText('SHELF_PROBE')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/да, мне есть 18/i}));
  await waitFor(()=>expect(screen.getByText('SHELF_PROBE')).toBeInTheDocument());
});
