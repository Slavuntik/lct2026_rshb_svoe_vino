import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient, ApiRequestError } from "../../lib/apiClient";
import type { DishPairingResponse, ScanPhotoRichResponse } from "../../lib/apiTypes";
import { findWineBySlug } from "../../mocks/fixtures/wines";
import { storage } from "../../lib/storage";
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

/**
 * Задача тимлида 22.09 (reports/qa-auto-field-photos.md, reports/ml-eng-ml3.md): полный
 * top-5 без явного лидера — типичный след кадра целой полки (много бутылок в объективе) —
 * должен показать подсказку scan.candidatesManyHint. Пять записей строим циклом — сами
 * значения не важны для теста, важна только длина массива.
 */
function notInCatalogFiveCandidatesResponse(): ScanPhotoRichResponse {
  return {
    slug: "",
    card: null,
    confidence: { top1_score: 0.83, gap: 0.004, f1_top1: 0.87, f1_top5: 0.95 },
    ocr_verified: false,
    timing_ms: 710,
    not_in_catalog: true,
    candidates: Array.from({ length: 5 }, (_, i) => ({
      wine_id: `shelf-candidate-${i}`,
      name: `Кандидат с полки ${i + 1}`,
      winery_name: "Тестовая Винодельня",
      region_name: "Тестовый регион",
      image_url: "data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'/>",
      source_url: `https://example.com/wines/shelf-candidate-${i}`,
      score: 0.83 - i * 0.01,
    })),
    similar: [],
    analogs: [],
  };
}

/**
 * «Что подать» по фото блюда (задача тимлида 22.09) — status=food по умолчанию, схема
 * буквально по брифу тимлида (contracts/post-scan.md v1.1 ещё не ратифицирован architect'ом
 * на момент реализации). overrides позволяет собрать remaining статусы точечно в тестах.
 */
function pairingFoodResponse(overrides: Partial<DishPairingResponse> = {}): DishPairingResponse {
  return {
    status: "food",
    dish: {
      name: "Стейк рибай на гриле",
      category: "BBQ",
      // contracts/post-scan.md v1.1 §4.1: alternatives — ДРУГИЕ теги из тех же 9, не
      // альтернативные названия блюда.
      alternatives: ["Блюда из птицы"],
      ingredients: ["говядина", "розмарин"],
      source: "vlm",
    },
    wines: [
      {
        wine_id: "severny-sklon-krasnostop-2021",
        name: "Красностоп Крепкий",
        winery: "Усадьба Северный Склон",
        color: "красное",
        sugar: "сухое",
        image_url: "data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'/>",
        reason: "Плотные танины выдержат жирность мяса с углей.",
        basis: "catalog",
      },
    ],
    message: null,
    timing_ms: 640,
    ...overrides,
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
      <Route path="/app/profile" element={<div>PROFILE_PROBE</div>} />
      <Route path="/app/taste" element={<div>TASTE_PROBE</div>} />
    </Routes>,
    "/app/scan",
  );
}

function pngFile(name: string) {
  return new File(["fake-photo-bytes"], name, { type: "image/png" });
}

/**
 * confidentResponse() несёт вино с одним сортом ("Шардоне") — resolveTasteAnalogs (§2.1) шлёт
 * его как есть в /analogs; фикстурные стили (mocks/fixtures/styles.ts) не резолвят голое
 * "Шардоне" ни к одному стилю (сознательно: они завязаны на многословные синонимы), поэтому
 * без явного мока постAnalogs «Похоже по вкусу» у ЭТОЙ фикстуры всегда уходит в 404 —
 * ждём здесь, что блок успел осесть в терминальное состояние, прежде чем тест завершится
 * (иначе поздний .then() после cleanup() шумит предупреждением act() в соседних тестах).
 */
async function waitForTasteAnalogsSettled() {
  await waitFor(() => {
    expect(screen.queryByText(/подбираем вина по вкусу/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/подбираем, к чему подать/i)).not.toBeInTheDocument();
  });
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

    await waitForTasteAnalogsSettled();
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

  it("«Спросить сомелье об этом вине» — задача тимлида 27.09 (макет Figma): фокусирует виджет сомелье, встроенный в инлайн-результат, вместо перехода на /app/chat", async () => {
    mockScanPhoto(confidentResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const resultCard = await screen.findByTestId("scan-photo-result");
    const questionInput = within(resultCard).getByPlaceholderText(/например: с чем подать это вино/i);
    expect(questionInput).not.toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: /спросить сомелье об этом вине/i }));

    // Виджет уже встроен в инлайн-результат (WineCardContent → SomelierCardWidget) — CHAT_PROBE
    // не появляется, перехода на /app/chat не происходит вовсе.
    expect(questionInput).toHaveFocus();
    expect(screen.queryByText("CHAT_PROBE")).not.toBeInTheDocument();
  });

  it("«Спросить сомелье об этом вине» — первый запрос /v1/chat уносит wine_id отсканированного вина через встроенный виджет (задача тимлида 22.09/27.09)", async () => {
    const chatSpy = vi.spyOn(apiClient, "chat").mockResolvedValue(undefined);
    mockScanPhoto(confidentResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const resultCard = await screen.findByTestId("scan-photo-result");
    fireEvent.click(screen.getByRole("button", { name: /спросить сомелье об этом вине/i }));

    const questionInput = within(resultCard).getByPlaceholderText(/например: с чем подать это вино/i);
    fireEvent.change(questionInput, { target: { value: "С чем подать это вино?" } });
    fireEvent.click(within(resultCard).getByRole("button", { name: /^спросить$/i }));

    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy.mock.calls[0][0].wine_id).toBe("tihaya-buhta-chardonnay-reserve-2023");
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

  it("not_in_catalog с 5 кандидатами (полный top-5) — подсказка снять одну бутылку крупнее (задача 22.09)", async () => {
    mockScanPhoto(notInCatalogFiveCandidatesResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("shelf.png")] } });

    const candidatesBlock = await screen.findByTestId("scan-candidates-block");
    expect(within(candidatesBlock).getAllByRole("button")).toHaveLength(5);
    expect(within(candidatesBlock).getByText(/попробуйте снять одну бутылку крупнее/i)).toBeInTheDocument();
  });

  it("not_in_catalog с < 5 кандидатами — подсказку не показываем (не всякая неуверенность — полка)", async () => {
    mockScanPhoto(notInCatalogResponse());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("unknown-bottle.png")] } });

    const candidatesBlock = await screen.findByTestId("scan-candidates-block");
    expect(within(candidatesBlock).getAllByRole("button")).toHaveLength(2);
    expect(within(candidatesBlock).queryByText(/попробуйте снять одну бутылку крупнее/i)).not.toBeInTheDocument();
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
    await waitForTasteAnalogsSettled();
  });

  it("тихая подпись про фото — не чекбокс согласия, просит одну бутылку в центре кадра (задача 22.09)", () => {
    renderScan();
    expect(screen.getByText(/одна бутылка в центре кадра/i)).toBeInTheDocument();
    expect(screen.getByText(/этикетка крупно, ровно и без бликов/i)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

describe("ScanScreen — «Похоже по вкусу» (contracts/post-scan.md v1.0 §2, POST /v1/analogs)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("успех /analogs — стиль и похожие вина видны", async () => {
    mockScanPhoto(confidentResponse());
    vi.spyOn(apiClient, "postAnalogs").mockResolvedValue({
      style: { slug: "chablis", name: "Шабли", country: "Франция" },
      wines: [
        {
          wine_id: "severny-sklon-krasnostop-2021",
          name: "Красностоп Крепкий",
          winery_name: "Усадьба Северный Склон",
          region_name: "Северный склон",
        },
      ],
    });
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const block = await screen.findByTestId("scan-taste-analogs-block");
    expect(await within(block).findByText(/по стилю «шабли»/i)).toBeInTheDocument();
    expect(within(block).getByText(/Красностоп Крепкий/)).toBeInTheDocument();
  });

  it("404 на всех попытках — виден message из ответа /analogs, не generic-ошибка", async () => {
    mockScanPhoto(confidentResponse());
    vi.spyOn(apiClient, "postAnalogs").mockRejectedValue(
      new ApiRequestError(404, "not_found", "Не нашли похожий стиль. Популярные: Просекко, Шабли."),
    );
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const block = await screen.findByTestId("scan-taste-analogs-block");
    expect(await within(block).findByText(/не нашли похожий стиль/i)).toBeInTheDocument();
    expect(within(block).queryByText(/что-то пошло не так/i)).not.toBeInTheDocument();
  });

  it("сетевая ошибка (не 404) — явное состояние, не generic-тост и не тишина", async () => {
    mockScanPhoto(confidentResponse());
    vi.spyOn(apiClient, "postAnalogs").mockRejectedValue(new Error("network down"));
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const block = await screen.findByTestId("scan-taste-analogs-block");
    expect(await within(block).findByText(/что-то пошло не так/i)).toBeInTheDocument();
  });

  it("гость — CTA «Пройти вкусовой паспорт» ведёт на профиль (апгрейд токена), не на 403", async () => {
    storage.setAccountKind("guest");
    mockScanPhoto(confidentResponse());
    vi.spyOn(apiClient, "postAnalogs").mockResolvedValue({
      style: { slug: "chablis", name: "Шабли", country: "Франция" },
      wines: [],
    });
    const profileSpy = vi.spyOn(apiClient, "getTasteProfile");
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const ctaButton = await screen.findByRole("button", { name: /пройти вкусовой паспорт/i });
    fireEvent.click(ctaButton);

    await waitFor(() => expect(screen.getByText("PROFILE_PROBE")).toBeInTheDocument());
    // Гостю /taste/profile честно отдаёт 403 consent_required — клиент даже не пробует
    // (не тратит запрос на заведомый отказ), поэтому и не может показать его как ошибку экрана.
    expect(profileSpy).not.toHaveBeenCalled();
    expect(screen.queryByText(/что-то пошло не так/i)).not.toBeInTheDocument();
  });

  it("зарегистрированный аккаунт — CTA ведёт на экран вкусового паспорта", async () => {
    storage.setAccountKind("registered");
    mockScanPhoto(confidentResponse());
    vi.spyOn(apiClient, "postAnalogs").mockResolvedValue({
      style: { slug: "chablis", name: "Шабли", country: "Франция" },
      wines: [],
    });
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile("label.png")] } });

    const ctaButton = await screen.findByRole("button", { name: /пройти вкусовой паспорт/i });
    fireEvent.click(ctaButton);

    await waitFor(() => expect(screen.getByText("TASTE_PROBE")).toBeInTheDocument());
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

describe("ScanScreen — «Что подать» по фото блюда (переключатель «Бутылка | Блюдо», задача тимлида 22.09)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function switchToDish() {
    fireEvent.click(screen.getByRole("button", { name: "Блюдо" }));
  }

  it("переключатель по умолчанию — «Бутылка» нажата, «Блюдо» — нет", () => {
    renderScan();
    expect(screen.getByRole("button", { name: "Бутылка" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Блюдо" })).toHaveAttribute("aria-pressed", "false");
  });

  it("status=food — имя блюда, чип категории, вина-карточки со ссылкой на внутреннюю карточку и reason", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue(pairingFoodResponse());
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("dish.png")] } });

    expect(screen.getByText(/распознаём блюдо/i)).toBeInTheDocument();

    const foodBlock = await screen.findByTestId("dish-food-result");
    expect(within(foodBlock).getByText("Стейк рибай на гриле")).toBeInTheDocument();
    expect(within(foodBlock).getByText("BBQ")).toBeInTheDocument();

    const winesBlock = within(foodBlock).getByTestId("dish-wines-block");
    expect(within(winesBlock).getByText(/Красностоп Крепкий/)).toBeInTheDocument();
    expect(within(winesBlock).getByText(/Плотные танины выдержат жирность мяса/)).toBeInTheDocument();
    // Правило ссылок (задача тимлида 22.09, п.2): ни одной внешней ссылки в списке вин
    // подбора к блюду — только внутренняя навигация, как во всех остальных списках.
    expect(within(winesBlock).queryAllByRole("link")).toHaveLength(0);

    fireEvent.click(within(winesBlock).getByText(/Красностоп Крепкий/));
    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:severny-sklon-krasnostop-2021")).toBeInTheDocument(),
    );
  });

  it("status=food, dish.name пуст (zero_shot/backend расхождение) — заголовок падает на категорию, не пустую строку", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue(
      pairingFoodResponse({ dish: { name: "", category: "BBQ", alternatives: [], ingredients: [], source: "zero_shot" } }),
    );
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("dish.png")] } });

    const foodBlock = await screen.findByTestId("dish-food-result");
    expect(within(foodBlock).getByRole("heading", { name: "BBQ" })).toBeInTheDocument();
  });

  it("чип alternatives (ДРУГАЯ категория, не имя блюда) уходит в POST /pairing/dish без поля dish", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue(pairingFoodResponse());
    const dishSpy = vi.spyOn(apiClient, "pairingDish").mockResolvedValue(
      pairingFoodResponse({
        dish: { name: null, category: "Блюда из птицы", alternatives: [], ingredients: [], source: "user" },
      }),
    );
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("dish.png")] } });

    const foodBlock = await screen.findByTestId("dish-food-result");
    fireEvent.click(within(foodBlock).getByRole("button", { name: "Блюда из птицы" }));

    // Ровно {category}, БЕЗ поля dish — alternatives не несёт название блюда (контракт).
    await waitFor(() => expect(dishSpy).toHaveBeenCalledWith({ category: "Блюда из птицы" }));
    expect(await screen.findByRole("heading", { name: "Блюда из птицы" })).toBeInTheDocument();
  });

  it("status=not_food — честное сообщение, без вин и без выдумки", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue({
      status: "not_food",
      dish: { name: null, category: null, alternatives: [], ingredients: [], source: "vlm" },
      wines: [],
      message: "На фото не похоже на блюдо — попробуйте другой кадр.",
      timing_ms: 300,
    });
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("notfood.png")] } });

    const block = await screen.findByTestId("dish-not-food");
    expect(within(block).getByText(/на фото не похоже на блюдо/i)).toBeInTheDocument();
    expect(screen.queryByTestId("dish-wines-block")).not.toBeInTheDocument();
  });

  it("status=bottle — CTA «Похоже на бутылку — отсканировать?» шлёт ТО ЖЕ фото в /v1/scan/photo", async () => {
    const photoFile = pngFile("actually-a-bottle.png");
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue({
      status: "bottle",
      dish: { name: null, category: null, alternatives: [], ingredients: [], source: "vlm" },
      wines: [],
      message: "Похоже, на фото бутылка вина, а не блюдо.",
      timing_ms: 300,
    });
    const scanPhotoSpy = mockScanPhoto(confidentResponse());
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [photoFile] } });

    const cta = await screen.findByRole("button", { name: /похоже на бутылку/i });
    fireEvent.click(cta);

    await waitFor(() => expect(scanPhotoSpy).toHaveBeenCalledTimes(1));
    expect(scanPhotoSpy.mock.calls[0][0]).toBe(photoFile);
    expect(await screen.findByTestId("scan-photo-result")).toBeInTheDocument();
    // Переключатель отражает фактический переход в режим «Бутылка».
    expect(screen.getByRole("button", { name: "Бутылка" })).toHaveAttribute("aria-pressed", "true");
  });

  it("status=unsure без догадки (alternatives=[]) — чипы всех 9 категорий, выбор уходит в POST /pairing/dish", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue({
      status: "unsure",
      dish: { name: null, category: null, alternatives: [], ingredients: [], source: "none" },
      wines: [],
      message: "Не уверены, что за блюдо на фото — уточните категорию.",
      timing_ms: 300,
    });
    const dishSpy = vi.spyOn(apiClient, "pairingDish").mockResolvedValue(pairingFoodResponse());
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("unsure.png")] } });

    const unsureBlock = await screen.findByTestId("dish-unsure");
    const chips = within(unsureBlock).getByTestId("dish-category-chips");
    expect(within(chips).getAllByRole("button")).toHaveLength(9);

    fireEvent.click(within(chips).getByRole("button", { name: "BBQ" }));
    await waitFor(() => expect(dishSpy).toHaveBeenCalledWith({ category: "BBQ" }));
    expect(await screen.findByTestId("dish-food-result")).toBeInTheDocument();
  });

  it("status=unsure со слабой догадкой (alternatives непуст) — чипы ИМЕННО этих тегов, не все 9 (contracts/post-scan.md v1.1 §4.4)", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue({
      status: "unsure",
      dish: {
        name: null,
        category: null,
        alternatives: ["Блюда из птицы", "Салаты"],
        ingredients: [],
        source: "zero_shot",
      },
      wines: [],
      message: "Не уверены, что за блюдо на фото — похоже на один из вариантов ниже.",
      timing_ms: 480,
    });
    const dishSpy = vi.spyOn(apiClient, "pairingDish").mockResolvedValue(pairingFoodResponse());
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("unsure-guess.png")] } });

    const unsureBlock = await screen.findByTestId("dish-unsure");
    const chips = within(unsureBlock).getByTestId("dish-category-chips");
    expect(within(chips).getAllByRole("button")).toHaveLength(2);
    expect(within(chips).getByRole("button", { name: "Блюда из птицы" })).toBeInTheDocument();
    expect(within(chips).getByRole("button", { name: "Салаты" })).toBeInTheDocument();

    fireEvent.click(within(chips).getByRole("button", { name: "Салаты" }));
    await waitFor(() => expect(dishSpy).toHaveBeenCalledWith({ category: "Салаты" }));
  });

  it("ручной выбор категории без фото — тоже работает", async () => {
    const dishSpy = vi.spyOn(apiClient, "pairingDish").mockResolvedValue(pairingFoodResponse());
    renderScan();
    switchToDish();

    const manualBlock = await screen.findByTestId("dish-manual-category");
    expect(within(manualBlock).getAllByRole("button")).toHaveLength(9);
    fireEvent.click(within(manualBlock).getByRole("button", { name: "Сыры" }));

    await waitFor(() => expect(dishSpy).toHaveBeenCalledWith({ category: "Сыры" }));
    expect(await screen.findByTestId("dish-food-result")).toBeInTheDocument();
  });

  it("режим «Блюдо» — свои подсказки загрузки, не унаследованные от «Бутылка» (qa-manual-hack-v16.md §2, регресс)", () => {
    renderScan();
    switchToDish();

    expect(screen.getByLabelText(/фото блюда/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/фото этикетки/i)).not.toBeInTheDocument();
    // Бутылочный совет про этикетку в кадре не должен просачиваться в режим «Блюдо».
    expect(screen.queryByText(/одна бутылка в центре кадра/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/этикетка крупно/i)).not.toBeInTheDocument();
  });

  it("заголовок и подзаголовок экрана — свои для «Блюдо», переключаются туда и обратно (регресс тимлида 27.09)", () => {
    renderScan();
    expect(screen.getByRole("heading", { name: "Скан этикетки" })).toBeInTheDocument();
    expect(screen.getByText(/наведите камеру на этикетку/i)).toBeInTheDocument();

    switchToDish();
    expect(screen.getByRole("heading", { name: "Что подать к блюду" })).toBeInTheDocument();
    expect(screen.getByText(/сфотографируйте блюдо/i)).toBeInTheDocument();
    // Бутылочные заголовок/подзаголовок не должны просачиваться в режим «Блюдо».
    expect(screen.queryByText(/^скан этикетки$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/наведите камеру на этикетку/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Бутылка" }));
    expect(screen.getByRole("heading", { name: "Скан этикетки" })).toBeInTheDocument();
    expect(screen.getByText(/наведите камеру на этикетку/i)).toBeInTheDocument();
    expect(screen.queryByText(/что подать к блюду/i)).not.toBeInTheDocument();
  });

  it("ручной выбор категории — лоадер свой, не «Распознаём блюдо» (без фото лоадер этот текст видеть не должен, qa-manual-hack-v16.md §2, регресс)", async () => {
    vi.spyOn(apiClient, "pairingDish").mockResolvedValue(pairingFoodResponse());
    renderScan();
    switchToDish();

    const manualBlock = await screen.findByTestId("dish-manual-category");
    fireEvent.click(within(manualBlock).getByRole("button", { name: "Сыры" }));

    // Проверяем СИНХРОННО, до разрешения промиса pairingDish — тот же приём, что и для
    // фото-пути ниже: React уже поставил dishStatus="loadingCategory" в этом же тике.
    expect(screen.getByText(/подбираем вина под категорию/i)).toBeInTheDocument();
    expect(screen.queryByText(/распознаём блюдо/i)).not.toBeInTheDocument();

    await screen.findByTestId("dish-food-result");
    expect(screen.queryByText(/подбираем вина под категорию/i)).not.toBeInTheDocument();
  });

  it("сетевая ошибка при распознавании блюда — явное состояние, не тишина", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockRejectedValue(new Error("network down"));
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("dish.png")] } });

    expect(await screen.findByText(/не удалось распознать блюдо/i)).toBeInTheDocument();
  });

  it("переключение обратно на «Бутылка» сбрасывает результат распознавания блюда", async () => {
    vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue(pairingFoodResponse());
    renderScan();
    switchToDish();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile("dish.png")] } });
    await screen.findByTestId("dish-food-result");

    fireEvent.click(screen.getByRole("button", { name: "Бутылка" }));

    expect(screen.queryByTestId("dish-food-result")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dish-manual-category")).not.toBeInTheDocument();
  });

  it("режим «Бутылка» не затронут: текстовый фолбэк по-прежнему на месте, без блюда-переключения", () => {
    renderScan();
    expect(screen.getByLabelText(/текст с этикетки/i)).toBeInTheDocument();
    expect(screen.queryByTestId("dish-manual-category")).not.toBeInTheDocument();
  });
});
