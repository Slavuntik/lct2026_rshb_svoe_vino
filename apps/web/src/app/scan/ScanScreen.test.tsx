import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "../../lib/apiClient";
import type { ScanPhotoRichResponse } from "../../lib/apiTypes";
import { findWineBySlug } from "../../mocks/fixtures/wines";
import { renderApp } from "../../test/renderApp";
import { ScanScreen } from "./ScanScreen";

// apiClient.scanPhoto — реальный multipart-транспорт (File/FormData) проверен отдельно,
// нативными классами Node, в lib/apiClient.scanPhoto.node.test.ts (см. директиву среды
// в начале того файла): под jsdom File/FormData — собственная реализация jsdom, которая
// при настоящей сериализации через fetch() теряет имя файла, и серверный multipart-парсер
// падает. Здесь, под jsdom, интересует только UI-поведение — мокаем apiClient на границе.
// ВАЖНО: не писать в этом файле строку "at-vitest-environment" буквально — vitest сканирует
// РАЗМЕТКУ КОММЕНТАРИЕВ ЦЕЛОГО ФАЙЛА на этот паттерн, а не только настоящие директивы;
// один раз уже словил этим багом весь файл в чужом (node, без document) окружении.
function mockScanPhoto(response: ScanPhotoRichResponse) {
  return vi.spyOn(apiClient, "scanPhoto").mockResolvedValue(response);
}

function confidentResponse(): ScanPhotoRichResponse {
  const wine = findWineBySlug("tihaya-buhta-chardonnay-reserve-2023");
  if (!wine) throw new Error("fixture wine missing");
  const { searchTerms: _searchTerms, ...card } = wine;
  return {
    slug: wine.wine_id,
    card,
    confidence: { top1_score: 0.94, gap: 0.31, f1_top1: 0.87, f1_top5: 0.95 },
    ocr_verified: true,
    timing_ms: 780,
    not_in_catalog: false,
    // v0.4.11: поле есть всегда, но уверенный ответ его не рендерит — одна карточка, как было.
    candidates: [
      {
        wine_id: wine.wine_id,
        name: wine.source.name,
        winery_name: wine.source.winery_name,
        region_name: wine.source.region_name,
        image_url: wine.source.image_url,
        source_url: wine.source_url,
        score: 0.94,
      },
    ],
    similar: [],
    analogs: [
      { wine_id: "severny-sklon-krasnostop-2021", name: "Красностоп Крепкий", winery_name: "Усадьба Северный Склон", region_name: "Северный склон" },
    ],
  };
}

function notInCatalogResponse(): ScanPhotoRichResponse {
  return {
    slug: "",
    card: null,
    confidence: { top1_score: 0.79, gap: 0.015, f1_top1: 0.87, f1_top5: 0.95 },
    ocr_verified: false,
    timing_ms: 640,
    not_in_catalog: true,
    // Фото + название + винодельня (contracts/image-scan.md v0.4.11) — WineResultChip.
    candidates: [
      {
        wine_id: "dom-tihaya-buhta-brut-2022",
        name: "Брют Резерв",
        winery_name: "Дом Тихая Бухта",
        region_name: "Тихая бухта",
        image_url: "data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'/>",
        source_url: "https://example.com/wines/dom-tihaya-buhta-brut-2022",
        score: 0.79,
      },
      {
        wine_id: "severny-sklon-riesling-poluslad-2023",
        name: "Рислинг Полусладкий",
        winery_name: "Усадьба Северный Склон",
        region_name: "Северный склон",
        image_url: "data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'/>",
        source_url: "https://example.com/wines/severny-sklon-riesling-poluslad-2023",
        score: 0.76,
      },
    ],
    // Намеренно непусто: доказывает, что UI больше не читает similar для not_in_catalog
    // (заменено candidates), а не просто "забыл" — см. тест ниже "не рендерит similar".
    similar: [{ wine_id: "dom-tihaya-buhta-brut-2022", name: "Брют Резерв", winery_name: "Дом Тихая Бухта", region_name: "Тихая бухта" }],
    analogs: [
      { wine_id: "sokoliny-utes-merlot-cabernet-2020", name: "Мерло-Каберне", winery_name: "Виноградники Соколиный Утёс", region_name: "Тихая бухта" },
    ],
  };
}

/** Гейт not_in_catalog не меняется (v0.4.11, п.5): ниже пола candidates тоже может быть пуст. */
function notInCatalogNoCandidatesResponse(): ScanPhotoRichResponse {
  return {
    slug: "",
    card: null,
    confidence: { top1_score: 0.32, gap: 0.01, f1_top1: 0.87, f1_top5: 0.95 },
    ocr_verified: false,
    timing_ms: 500,
    not_in_catalog: true,
    candidates: [],
    similar: [],
    analogs: [],
  };
}

function WineProbe() {
  const { wineId } = useParams<{ wineId: string }>();
  return <div>WINE_CARD_PROBE:{wineId}</div>;
}

function renderScan() {
  return renderApp(
    <Routes>
      <Route path="/app/scan" element={<ScanScreen />} />
      <Route path="/app/wine/:wineId" element={<WineProbe />} />
      <Route path="/app/chat" element={<div>CHAT_PROBE</div>} />
    </Routes>,
    "/app/scan",
  );
}

function pngFile(name: string) {
  return new File(["fake-photo-bytes"], name, { type: "image/png" });
}

describe("ScanScreen — фото-first (кейс ЛЦТ, contracts/image-scan.md v0.4)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("уверенный матч — ОДНА карточка сразу, без экрана вариантов и без confidence в UI", async () => {
    mockScanPhoto(confidentResponse());
    renderScan();

    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });
    expect(screen.getByText(/ищем по фото/i)).toBeInTheDocument();

    const resultCard = await screen.findByTestId("scan-photo-result");
    expect(within(resultCard).getByText("Шардоне Резерв")).toBeInTheDocument();

    // Никакого списка кандидатов и никакой цифры уверенности (top1_score=0.94 -> "94%")
    // в разметке результата. ABV% (13%) — легитимные данные карточки, их НЕ баним целиком.
    expect(screen.queryByTestId("scan-low-confidence")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scan-not-in-catalog")).not.toBeInTheDocument();
    expect(resultCard.textContent).not.toMatch(/94\s*%/);
  });

  it("аналоги из ответа кликабельны и ведут на карточку соответствующего вина", async () => {
    mockScanPhoto(confidentResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const analogsBlock = await screen.findByTestId("scan-analogs-block");
    fireEvent.click(within(analogsBlock).getByRole("button"));

    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:severny-sklon-krasnostop-2021")).toBeInTheDocument(),
    );
  });

  it("«Спросить сомелье об этом вине» уводит в чат с префиллом (доп-функция после поиска)", async () => {
    mockScanPhoto(confidentResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    await screen.findByTestId("scan-photo-result");
    fireEvent.click(screen.getByRole("button", { name: /спросить сомелье об этом вине/i }));

    await waitFor(() => expect(screen.getByText("CHAT_PROBE")).toBeInTheDocument());
  });

  it("not_in_catalog с candidates — «Возможно, это одно из:» с фото, без выдумки одной карточки (v0.4.11)", async () => {
    mockScanPhoto(notInCatalogResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("unknown-bottle.png")] } });

    const block = await screen.findByTestId("scan-not-in-catalog");
    expect(within(block).getByText(/возможно, это одно из/i)).toBeInTheDocument();

    const candidatesBlock = within(block).getByTestId("scan-candidates-block");
    const candidateButtons = within(candidatesBlock).getAllByRole("button");
    expect(candidateButtons).toHaveLength(2);
    expect(within(candidatesBlock).getByText(/Брют Резерв/)).toBeInTheDocument();
    expect(within(candidatesBlock).getByText(/Рислинг Полусладкий/)).toBeInTheDocument();
    // фото (contracts/image-scan.md v0.4.11: "candidates ... фото, название, винодельня").
    expect(within(candidatesBlock).getAllByRole("img").length).toBeGreaterThanOrEqual(2);

    // similar в ответе непусто (см. notInCatalogResponse), но UI его больше не рендерит —
    // candidates его заменил, честный остаток "такого вина нет" тоже не показан.
    expect(within(block).queryByText(/похожие вина/i)).not.toBeInTheDocument();
    expect(within(block).queryByText(/такого вина в каталоге нет/i)).not.toBeInTheDocument();

    // Честное объяснение выше по тексту тоже упоминает "аналоги из других виноделен" — это
    // ожидаемо (см. scan.notInCatalogMessage), поэтому здесь getAllByText, не getByText.
    expect(within(block).getAllByText(/аналоги из других виноделен/i).length).toBeGreaterThanOrEqual(1);
    expect(within(block).getAllByRole("button").length).toBeGreaterThan(candidateButtons.length);

    expect(screen.queryByTestId("scan-photo-result")).not.toBeInTheDocument();
  });

  it("тап по кандидату ведёт на карточку этого вина", async () => {
    mockScanPhoto(notInCatalogResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("unknown-bottle.png")] } });

    const candidatesBlock = await screen.findByTestId("scan-candidates-block");
    fireEvent.click(within(candidatesBlock).getByText(/Рислинг Полусладкий/));

    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:severny-sklon-riesling-poluslad-2023")).toBeInTheDocument(),
    );
  });

  it("not_in_catalog без кандидатов — честное «такого вина в каталоге нет», без выдумки", async () => {
    mockScanPhoto(notInCatalogNoCandidatesResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("unknown-bottle.png")] } });

    const block = await screen.findByTestId("scan-not-in-catalog");
    expect(within(block).getByText(/такого вина в каталоге нет/i)).toBeInTheDocument();
    expect(within(block).queryByTestId("scan-candidates-block")).not.toBeInTheDocument();
    expect(within(block).queryByText(/возможно, это одно из/i)).not.toBeInTheDocument();
  });

  it("drag-and-drop фото запускает тот же поиск, что и выбор файла", async () => {
    mockScanPhoto(confidentResponse());
    renderScan();
    const dropzone = screen.getByTestId("scan-dropzone");

    fireEvent.drop(dropzone, { dataTransfer: { files: [pngFile("label.png")] } });

    expect(await screen.findByTestId("scan-photo-result")).toBeInTheDocument();
  });

  it("тихая подпись про фото — не чекбокс согласия", () => {
    renderScan();
    expect(screen.getByText(/этикетка крупно, ровно и без бликов/i)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

describe("ScanScreen — текстовый путь (запасной вход)", () => {
  it("уверенное совпадение резолвится в карточку автоматически", async () => {
    renderScan();
    fireEvent.change(screen.getByLabelText(/текст с этикетки/i), {
      target: { value: "Шардоне Резерв" },
    });
    fireEvent.click(screen.getByRole("button", { name: /найти вино/i }));

    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:tihaya-buhta-chardonnay-reserve-2023")).toBeInTheDocument(),
    );
  });

  it("low_confidence показывает список выбора вместо автоперехода", async () => {
    renderScan();
    fireEvent.change(screen.getByLabelText(/текст с этикетки/i), {
      target: { value: "тихая бухта" },
    });
    fireEvent.click(screen.getByRole("button", { name: /найти вино/i }));

    const picker = await screen.findByTestId("scan-low-confidence");
    const items = within(picker).getAllByRole("button");
    expect(items.length).toBeGreaterThanOrEqual(2);

    fireEvent.click(items[0]);
    await waitFor(() => expect(screen.getByText(/WINE_CARD_PROBE:/)).toBeInTheDocument());
  });

  it("текст без совпадений — честное «ничего не нашли», без выдумки", async () => {
    renderScan();
    fireEvent.change(screen.getByLabelText(/текст с этикетки/i), {
      target: { value: "абракадабра непонятно что" },
    });
    fireEvent.click(screen.getByRole("button", { name: /найти вино/i }));

    expect(await screen.findByTestId("scan-no-matches")).toBeInTheDocument();
    // Честная деградация — не выдумываем аналоги, когда стиль не распознан.
    expect(screen.queryByTestId("scan-text-analogs")).not.toBeInTheDocument();
  });

  it("matches пуст, но стиль узнан (B7) — «Похожие российские вина» тем же компонентом карточек", async () => {
    renderScan();
    fireEvent.change(screen.getByLabelText(/текст с этикетки/i), {
      // Живой кейс Вячеслава (17.09, contracts/openapi.yaml analogs/analog_reason):
      // иностранное вино вне каталога, но сорт (рислинг) узнаётся с опечаткой.
      target: { value: "Urban Risling" },
    });
    fireEvent.click(screen.getByRole("button", { name: /найти вино/i }));

    const block = await screen.findByTestId("scan-text-analogs");
    expect(within(block).getByText(/похожие российские вина/i)).toBeInTheDocument();
    expect(within(block).getByText(/urban risling.*вне каталога.*рислинг/i)).toBeInTheDocument();
    expect(screen.queryByTestId("scan-no-matches")).not.toBeInTheDocument();

    fireEvent.click(within(block).getByRole("button"));
    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:severny-sklon-riesling-poluslad-2023")).toBeInTheDocument(),
    );
  });
});
