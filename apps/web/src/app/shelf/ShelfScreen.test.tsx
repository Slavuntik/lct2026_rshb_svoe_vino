import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { resetShelfAvailabilityForTests, SHELF_HEALTH_PATH } from "../../lib/shelfAvailability";
import { storage } from "../../lib/storage";
import { server } from "../../mocks/server";
import { renderApp } from "../../test/renderApp";
import AppShell from "../AppShell";

// Живая проверка (src/lib/shelfAvailability.ts) держит один кешированный промис на модуль —
// без сброса второй it() унаследует результат первого.
beforeEach(() => {
  resetShelfAvailabilityForTests();
});

function mockShelfServiceHealthy() {
  server.use(
    http.get(SHELF_HEALTH_PATH, () =>
      HttpResponse.json({ ready: true, busy: false, state: "ready", catalogSize: 42 }),
    ),
  );
}

// Задача тимлида 27.09 (agents/frontend-nav-transfer.md): «Витрина» переехала из всегда
// видимой нижней панели в сэндвич-меню шапки — пункт появляется только внутри открытой
// панели, поэтому тесты навигации теперь сперва открывают меню кнопкой из TopHeader.
function openNavMenu() {
  return userEvent.click(screen.getByRole("button", { name: "Открыть меню разделов" }));
}

describe("Shelf navigation", () => {
  it("keeps the shelf behind onboarding", async () => {
    storage.setOnboardingComplete(false);
    renderApp(<Routes><Route path="/app/*" element={<AppShell />} /></Routes>, "/app/shelf");
    expect(screen.queryByTitle("Поиск вин на витрине")).not.toBeInTheDocument();
  });

  it("embeds the separate shelf app once the shelf service answers its health check", async () => {
    mockShelfServiceHealthy();
    storage.setOnboardingComplete(true);
    renderApp(<Routes><Route path="/app/*" element={<AppShell />} /></Routes>, "/app/shelf");
    expect(await screen.findByTitle("Поиск вин на витрине")).toHaveAttribute(
      "src",
      "/shelf-ui/?engine=server&embedded=1",
    );
    await openNavMenu();
    expect(await screen.findByRole("link", { name: "Витрина" })).toHaveAttribute("href", "/app/shelf");
    expect(screen.getByRole("link", { name: "Сомелье" })).toHaveAttribute("href", "/app/chat");
    storage.setOnboardingComplete(false);
  });

  it("hides the nav item and shows an honest placeholder when nginx's SPA fallback answers instead of the shelf service", async () => {
    // Прод-ловушка (reports/architect-post-merge-review.md §3): nginx без локейшна для
    // /v1/shelf отдаёт try_files-фолбэк — наш же index.html с кодом 200. Это же поведение
    // и есть дефолтный мок-хендлер в src/mocks/handlers.ts, здесь переопределён явно для
    // ясности теста.
    server.use(
      http.get(SHELF_HEALTH_PATH, () =>
        new HttpResponse("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );
    storage.setOnboardingComplete(true);
    renderApp(<Routes><Route path="/app/*" element={<AppShell />} /></Routes>, "/app/shelf");

    expect(
      await screen.findByText("Раздел витрины доступен, когда запущен сервис распознавания витрин"),
    ).toBeInTheDocument();
    expect(screen.queryByTitle("Поиск вин на витрине")).not.toBeInTheDocument();
    await openNavMenu();
    // Пункт «Витрина» честно скрыт (не задизейблен — его просто нет в DOM), пока живой
    // health-чек не подтвердит сервис: сверяем, что меню открылось (есть другие пункты),
    // а не то, что мы забыли его открыть.
    expect(screen.getByRole("link", { name: "Сомелье" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Витрина" })).not.toBeInTheDocument();
    storage.setOnboardingComplete(false);
  });
});
