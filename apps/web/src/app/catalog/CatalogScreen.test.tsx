import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CatalogScreen } from "./CatalogScreen";
import { apiClient } from "../../lib/apiClient";
import { renderApp } from "../../test/renderApp";

function WineProbe() {
  const { wineId } = useParams<{ wineId: string }>();
  return <div>WINE_CARD_PROBE:{wineId}</div>;
}

function renderCatalog() {
  return renderApp(
    <Routes>
      <Route path="/app/catalog" element={<CatalogScreen />} />
      <Route path="/app/wine/:wineId" element={<WineProbe />} />
    </Routes>,
    "/app/catalog",
  );
}

/**
 * Мок каталога (mocks/fixtures/catalog.ts): 7 реальных вин фикстур + 50 синтетических
 * («Тестовое вино 1..50»), лимит по умолчанию 24 — 57 позиций хватает проверить постраничность
 * (24+24+9), поиск, фильтры и заглушку без превью (каждая 5-я синтетическая позиция).
 */
describe("CatalogScreen (GET /v1/catalog, contracts/openapi.yaml v0.3.7, задача тимлида 27.09)", () => {
  it("грузит первую страницу (не всё сразу, лимит по умолчанию 24), показывает найденное количество", async () => {
    renderCatalog();
    expect(screen.getByText(/загружаем каталог/i)).toBeInTheDocument();

    const grid = await screen.findByTestId("catalog-grid");
    // Порядок — по имени (casefold): не полагаемся на то, КАКИЕ конкретно 24 из 57 попали
    // на первую страницу, только на то, что их ровно 24, а не все 57 разом.
    expect(within(grid).getAllByRole("button")).toHaveLength(24);
    expect(await screen.findByText("Найдено вин: 57")).toBeInTheDocument();
  });

  it("клик по плитке ведёт на карточку вина (/app/wine/:id)", async () => {
    renderCatalog();
    await screen.findByTestId("catalog-grid");
    // Ищем конкретное вино явно (не полагаемся на алфавитную позицию в первой странице).
    fireEvent.change(screen.getByRole("searchbox", { name: /поиск по названию или винодельне/i }), {
      target: { value: "Шардоне" },
    });
    const tile = await screen.findByText("Шардоне Резерв");
    fireEvent.click(tile);
    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:tihaya-buhta-chardonnay-reserve-2023")).toBeInTheDocument(),
    );
  });

  it("позиция без превью (image_url=null) — заглушка WineImage, вино не выброшено из выдачи", async () => {
    renderCatalog();
    await screen.findByTestId("catalog-grid");
    // Синтетическая фикстура: индекс, кратный 5, — без превью (mocks/fixtures/catalog.ts);
    // "10" не встречается ни в одном другом индексе как подстрока — однозначный поиск.
    fireEvent.change(screen.getByRole("searchbox", { name: /поиск по названию или винодельне/i }), {
      target: { value: "Тестовое вино 10" },
    });
    const name = await screen.findByText("Тестовое вино 10");
    const tile = name.closest("button") as HTMLElement;
    expect(within(tile).getByTestId("wine-image-placeholder")).toBeInTheDocument();
  });

  /**
   * Дефект hack-v27 (задача тимлида 27.09): превью каталога приходят разных пропорций —
   * без общей рамки картинка растягивала ячейку на свою высоту, соседние плитки в ряду грид-
   * сетки не совпадали по высоте (рваные ряды). Каждая плитка обязана нести один и тот же
   * контейнер-рамку фиксированной пропорции (CSS aspect-ratio 3:4, .catalog-tile__image-frame)
   * независимо от того, пришло реальное фото или заглушка WineImage — сам object-fit:contain
   * визуально не проверить в jsdom (нет layout-движка), поэтому фиксируем разметку, которая
   * его включает, плюс однострочную/двухстрочную обрезку названия и винодельни тем же приёмом.
   */
  it("каждая плитка несёт рамку фиксированной пропорции под фото — не зависит от того, есть превью или заглушка", async () => {
    renderCatalog();
    const grid = await screen.findByTestId("catalog-grid");
    const tiles = within(grid).getAllByRole("button");
    expect(tiles.length).toBeGreaterThan(0);
    for (const tile of tiles) {
      const frame = tile.querySelector(".catalog-tile__image-frame");
      expect(frame, "у каждой плитки должна быть общая рамка изображения").toBeInTheDocument();
      // Реальное фото и заглушка WineImage лежат В рамке, не рядом с ней — рамка задаёт высоту.
      expect(frame?.querySelector("img.catalog-tile__image, svg.catalog-tile__image")).toBeTruthy();
    }
  });

  it("длинное название и винодельня обрезаются (line-clamp), не растягивают ячейку", async () => {
    vi.spyOn(apiClient, "getCatalog").mockResolvedValueOnce({
      wines: [
        {
          wine_id: "long-name-wine",
          name: "Очень-очень-очень длинное название вина, которое в один ряд точно не поместится",
          winery: "Не менее длинное название винодельни с большим количеством слов подряд",
          color: "белое",
          sugar: "сухое",
          image_url: null,
        },
      ],
      total: 1,
      limit: 24,
      offset: 0,
    });
    renderCatalog();

    const name = await screen.findByText(/очень-очень-очень длинное название/i);
    expect(name).toHaveClass("catalog-tile__name");
    const winery = screen.getByText(/не менее длинное название винодельни/i);
    expect(winery).toHaveClass("catalog-tile__winery");
    // Классы несут -webkit-line-clamp (см. styles/global.css) — сам визуальный обрез не
    // проверить в jsdom, но правильный класс на правильном узле — то, от чего он зависит.
  });

  it("поиск по названию/винодельне сужает список (дебаунс, не запрос на каждую клавишу)", async () => {
    renderCatalog();
    await screen.findByTestId("catalog-grid");

    fireEvent.change(screen.getByRole("searchbox", { name: /поиск по названию или винодельне/i }), {
      target: { value: "Шардоне" },
    });

    await waitFor(() => expect(screen.getByText("Найдено вин: 1")).toBeInTheDocument());
    const grid = screen.getByTestId("catalog-grid");
    expect(within(grid).getByText("Шардоне Резерв")).toBeInTheDocument();
    expect(within(grid).queryByText(/Тестовое вино/)).not.toBeInTheDocument();
  });

  it("фильтр по цвету сужает список (точное совпадение, регистр не важен)", async () => {
    renderCatalog();
    // "Красностоп Крепкий" — красное вино фикстуры (mocks/fixtures/wines.ts), алфавитно 2-я
    // позиция из 57 — точно на первой странице. Его исчезновение после фильтра "белое" —
    // надёжный сигнал, что фильтр применился, без хрупкого расчёта точного числа совпадений.
    await screen.findByText("Красностоп Крепкий");

    fireEvent.click(screen.getByRole("button", { name: "Фильтры" }));
    fireEvent.change(screen.getByLabelText("Цвет"), { target: { value: "БЕЛОЕ" } });

    await waitFor(() => expect(screen.queryByText("Красностоп Крепкий")).not.toBeInTheDocument());
    const grid = screen.getByTestId("catalog-grid");
    expect(within(grid).getAllByRole("button").length).toBeGreaterThan(0);
  });

  it("пустой результат поиска — честный текст, не пустой экран", async () => {
    renderCatalog();
    await screen.findByTestId("catalog-grid");

    fireEvent.change(screen.getByRole("searchbox", { name: /поиск по названию или винодельне/i }), {
      target: { value: "зюмбревокс-которого-не-существует" },
    });

    expect(await screen.findByText(/ничего не найдено/i)).toBeInTheDocument();
    expect(screen.queryByTestId("catalog-grid")).not.toBeInTheDocument();
  });

  it("«Показать ещё» подгружает следующую страницу и исчезает, когда показано всё", async () => {
    renderCatalog();
    await screen.findByTestId("catalog-grid");

    fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
    await waitFor(() => expect(within(screen.getByTestId("catalog-grid")).getAllByRole("button")).toHaveLength(48));

    fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
    await waitFor(() => expect(within(screen.getByTestId("catalog-grid")).getAllByRole("button")).toHaveLength(57));

    expect(screen.queryByRole("button", { name: "Показать ещё" })).not.toBeInTheDocument();
  });

  it("ошибка первой загрузки — явный текст и рабочий повтор", async () => {
    const spy = vi.spyOn(apiClient, "getCatalog").mockRejectedValueOnce(new Error("network down"));
    renderCatalog();

    expect(await screen.findByText(/не удалось загрузить каталог/i)).toBeInTheDocument();
    spy.mockRestore();

    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByTestId("catalog-grid")).toBeInTheDocument();
  });

  it("ошибка «Показать ещё» — список из первой страницы остаётся на месте, видна ошибка", async () => {
    renderCatalog();
    await screen.findByTestId("catalog-grid");

    vi.spyOn(apiClient, "getCatalog").mockRejectedValueOnce(new Error("network down"));
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));

    expect(await screen.findByText(/не удалось загрузить ещё/i)).toBeInTheDocument();
    expect(within(screen.getByTestId("catalog-grid")).getAllByRole("button")).toHaveLength(24);
  });
});
