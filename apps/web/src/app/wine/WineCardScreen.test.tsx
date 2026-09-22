import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { setAnalyticsSink, type AnalyticsEvent } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import { renderApp } from "../../test/renderApp";
import { ChatScreen } from "../chat/ChatScreen";
import { WineCardScreen } from "./WineCardScreen";

function renderCard(wineId: string, state?: { from: string }) {
  return renderApp(
    <Routes>
      <Route path="/app/wine/:wineId" element={<WineCardScreen />} />
    </Routes>,
    state ? { pathname: `/app/wine/${wineId}`, state } : `/app/wine/${wineId}`,
  );
}

describe("WineCardScreen", () => {
  it("грузит карточку по GET /wines/{id} и шлёт wine_card_viewed", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderCard("tihaya-buhta-chardonnay-reserve-2023", { from: "scan" });

      await screen.findByText("Шардоне Резерв");
      expect(screen.getByText(/Дом Тихая Бухта/)).toBeInTheDocument();

      const viewed = events.find((e) => e.name === "wine_card_viewed");
      expect(viewed?.props).toMatchObject({ wine_id: "tihaya-buhta-chardonnay-reserve-2023", from: "scan" });
    } finally {
      restore();
    }
  });

  it("клик по первоисточнику шлёт source_link_clicked", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderCard("tihaya-buhta-chardonnay-reserve-2023");
      await screen.findByText("Шардоне Резерв");

      fireEvent.click(screen.getByRole("link", { name: /первоисточник/i }));
      expect(events.some((e) => e.name === "source_link_clicked")).toBe(true);
    } finally {
      restore();
    }
  });

  it("похожие вина ведут на другую карточку (from=similar)", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderCard("tihaya-buhta-chardonnay-reserve-2023");
      await screen.findByText("Шардоне Резерв");

      // Первая позиция в фикстуре similar (mocks/fixtures/wines.ts) — severny-sklon-riesling-
      // poluslad-2023; кнопка подписана честной порядковой подписью, не слагом (см. тест ниже).
      fireEvent.click(screen.getByRole("button", { name: "Похожее вино 1" }));

      await waitFor(() => expect(screen.getByText("Рислинг Полусладкий")).toBeInTheDocument());
      const viewed = events.filter((e) => e.name === "wine_card_viewed");
      expect(viewed.at(-1)?.props).toMatchObject({
        wine_id: "severny-sklon-riesling-poluslad-2023",
        from: "similar",
      });
    } finally {
      restore();
    }
  });

  it("«Похожие вина» не показывают сырой слаг вместо названия (qa-manual-hack-v16.md §5.1, регресс)", async () => {
    renderCard("tihaya-buhta-chardonnay-reserve-2023");
    await screen.findByText("Шардоне Резерв");

    // Фикстура несёт similar: ["severny-sklon-riesling-poluslad-2023", "dom-tihaya-buhta-brut-2022"]
    // (mocks/fixtures/wines.ts) — ни один из этих слагов не должен всплыть видимым текстом,
    // и ни в одном атрибуте (title/aria-*): часть QA-тулинга вычисляет accessible name из
    // title раньше текста узла — слаг в title тихо вернул бы регресс в том же инструменте.
    expect(screen.getByRole("heading", { name: "Похожие вина" })).toBeInTheDocument();
    expect(screen.queryByText("severny-sklon-riesling-poluslad-2023")).not.toBeInTheDocument();
    expect(screen.queryByText("dom-tihaya-buhta-brut-2022")).not.toBeInTheDocument();

    const first = screen.getByRole("button", { name: "Похожее вино 1" });
    const second = screen.getByRole("button", { name: "Похожее вино 2" });
    expect(first).toBeInTheDocument();
    expect(second).toBeInTheDocument();
    expect(first).not.toHaveAttribute("title");
    expect(second).not.toHaveAttribute("title");
  });

  it("несуществующее вино — честное «не найдено», без падения экрана", async () => {
    renderCard("net-takogo-vina");
    expect(await screen.findByText(/не найдена/i)).toBeInTheDocument();
  });

  it("фолбэк-карточка каталога кейса (v0.4.11, slug вне нашего RAG) — брендированная ссылка «Своё Вино»", async () => {
    renderCard("case-shato-yuzhny-sklon-saperavi-2019");
    await screen.findByText("Саперави Резерв");

    const link = screen.getByRole("link", { name: /открыть на «своё вино»/i });
    expect(link).toHaveAttribute("href", "https://vino-svoe.ru/wines/case-shato-yuzhny-sklon-saperavi-2019");
  });

  it("«Спросить сомелье об этом вине» — первый запрос /v1/chat уносит wine_id открытой карточки (задача тимлида 22.09)", async () => {
    const chatSpy = vi.spyOn(apiClient, "chat").mockResolvedValue(undefined);
    renderApp(
      <Routes>
        <Route path="/app/wine/:wineId" element={<WineCardScreen />} />
        <Route path="/app/chat" element={<ChatScreen />} />
      </Routes>,
      "/app/wine/tihaya-buhta-chardonnay-reserve-2023",
    );
    await screen.findByText("Шардоне Резерв");

    fireEvent.click(screen.getByRole("button", { name: /спросить сомелье об этом вине/i }));

    // Приземлились на реальном ChatScreen с префиллом — отправляем ровно его.
    await screen.findByDisplayValue(/расскажи про шардоне резерв/i);
    fireEvent.click(screen.getByRole("button", { name: /^спросить$/i }));

    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy.mock.calls[0][0].wine_id).toBe("tihaya-buhta-chardonnay-reserve-2023");
  });
});
