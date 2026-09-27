import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { setAnalyticsSink, type AnalyticsEvent } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import { findWineBySlug } from "../../mocks/fixtures/wines";
import { renderApp } from "../../test/renderApp";
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
      // Полная подпись винодельни+региона карточки — не путать с "Дом Тихая Бухта" в списке
      // «Похожие вина» ниже (тот же слаг owns два вина фикстуры, отдельный тест на этот блок).
      expect(screen.getByText("Дом Тихая Бухта · Тихая бухта")).toBeInTheDocument();

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
      // poluslad-2023; клик по тексту всплывает на кнопку-обёртку (тот же приём, что
      // ScanScreen.test.tsx для WineResultChip — обходит дублирование alt/текста в имени роли).
      fireEvent.click(screen.getByText("Рислинг Полусладкий · Усадьба Северный Склон"));

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

  it("«Похожие вина» (similar_wines) показывают название/винодельню/фото, не сырой слаг (qa-manual-hack-v16.md §5.1, регресс)", async () => {
    renderCard("tihaya-buhta-chardonnay-reserve-2023");
    await screen.findByText("Шардоне Резерв");

    // Фикстура несёт similar: ["severny-sklon-riesling-poluslad-2023", "dom-tihaya-buhta-brut-2022"]
    // (mocks/fixtures/wines.ts), мок обогащает их similar_wines тем же приёмом, что боевой
    // build_wine_card() (openapi 0.3.6) — ни один слаг не должен всплыть видимым текстом/
    // атрибутом: часть QA-тулинга вычисляет accessible name из title раньше текста узла.
    expect(screen.getByRole("heading", { name: "Похожие вина" })).toBeInTheDocument();
    expect(screen.queryByText("severny-sklon-riesling-poluslad-2023")).not.toBeInTheDocument();
    expect(screen.queryByText("dom-tihaya-buhta-brut-2022")).not.toBeInTheDocument();
    expect(screen.queryByText(/Похожее вино/)).not.toBeInTheDocument();

    const first = screen.getByText("Рислинг Полусладкий · Усадьба Северный Склон");
    const second = screen.getByText("Брют Резерв · Дом Тихая Бухта");
    expect(first.closest("button")).not.toHaveAttribute("title");
    expect(second.closest("button")).not.toHaveAttribute("title");
    // Картинка — WineImage с alt по имени вина (contracts/openapi.yaml similar_wines.image_url).
    expect(screen.getByAltText("Рислинг Полусладкий")).toBeInTheDocument();
    expect(screen.getByAltText("Брют Резерв")).toBeInTheDocument();
  });

  it("«Похожие вина» — запасной путь: similar_wines отсутствует, similar непуст — честная подпись, не сырой слаг (регресс)", async () => {
    const baseWine = findWineBySlug("tihaya-buhta-chardonnay-reserve-2023");
    if (!baseWine) throw new Error("fixture-вино не найдено");
    const { searchTerms: _searchTerms, ...card } = baseWine;
    // similar_wines НЕ передан вовсе (контракт nullable/опционален) — тот случай, когда старый
    // backend/фолбэк каталога кейса ещё не обогащает similar (задача тимлида 23.09).
    const getWineSpy = vi.spyOn(apiClient, "getWine").mockResolvedValue({ ...card, similar_wines: undefined });
    try {
      renderCard("tihaya-buhta-chardonnay-reserve-2023");
      await screen.findByText("Шардоне Резерв");

      expect(screen.getByRole("button", { name: "Похожее вино 1" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Похожее вино 2" })).toBeInTheDocument();
      expect(screen.queryByText("severny-sklon-riesling-poluslad-2023")).not.toBeInTheDocument();
      expect(screen.queryByText("dom-tihaya-buhta-brut-2022")).not.toBeInTheDocument();
    } finally {
      getWineSpy.mockRestore();
    }
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

  it("«Спросить сомелье об этом вине» — задача тимлида 27.09 (макет Figma): фокусирует встроенный виджет вместо перехода на /app/chat, первый вопрос уносит wine_id открытой карточки", async () => {
    const chatSpy = vi.spyOn(apiClient, "chat").mockResolvedValue(undefined);
    renderCard("tihaya-buhta-chardonnay-reserve-2023");
    await screen.findByText("Шардоне Резерв");

    // Виджет уже встроен в карточку (WineCardContent → SomelierCardWidget) — никакого
    // /app/chat в этом тесте нет вовсе, переход не нужен и не происходит.
    const questionInput = screen.getByPlaceholderText(/например: с чем подать это вино/i);
    expect(questionInput).not.toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: /спросить сомелье об этом вине/i }));
    expect(questionInput).toHaveFocus();

    fireEvent.change(questionInput, { target: { value: "С чем подать это вино?" } });
    fireEvent.click(screen.getByRole("button", { name: /^спросить$/i }));

    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy.mock.calls[0][0].wine_id).toBe("tihaya-buhta-chardonnay-reserve-2023");
  });
});
