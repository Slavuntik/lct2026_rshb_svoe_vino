import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "../../lib/apiClient";
import type { ScanPhotoRichResponse } from "../../lib/apiTypes";
import { buildDishPhotoResponse } from "../../mocks/fixtures/dishPairing";
import { findWineBySlug } from "../../mocks/fixtures/wines";
import { renderApp } from "../../test/renderApp";
import { ScanScreen } from "./ScanScreen";

/**
 * Уточнение рамкой (contracts/image-scan.md v0.4.10).
 *
 * Порядок здесь принципиален: первый поиск идёт по всему кадру сразу, как и раньше, — лишний
 * шаг на каждом скане раздражал бы, а в обычном кадре бутылка одна. Прицел предлагается ПОСЛЕ
 * ответа: он нужен там, где в кадре несколько бутылок одной серии, и тогда выбор нужной
 * бутылки — главный резерв точности — переходит от детектора к пользователю.
 */

function response(): ScanPhotoRichResponse {
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
    candidates: [],
    similar: [],
    analogs: [],
  };
}

function renderScan() {
  return renderApp(
    <Routes>
      <Route path="/app/scan" element={<ScanScreen />} />
      <Route path="/app/wine/:wineId" element={<div>WINE_PROBE</div>} />
    </Routes>,
    "/app/scan",
  );
}

function pngFile() {
  return new File(["fake-photo-bytes"], "label.png", { type: "image/png" });
}

describe("ScanScreen — уточнение рамкой", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("первый поиск идёт по всему кадру: рамка не запрашивается заранее", async () => {
    const scan = vi.spyOn(apiClient, "scanPhoto").mockResolvedValue(response());
    renderScan();

    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile()] } });

    await screen.findByTestId("scan-photo-result");
    expect(scan).toHaveBeenCalledTimes(1);
    expect(scan.mock.calls[0][1]).toBeUndefined();
  });

  it("после ответа можно обвести бутылку и переискать — в API уходят доли кадра", async () => {
    const scan = vi.spyOn(apiClient, "scanPhoto").mockResolvedValue(response());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile()] } });
    await screen.findByTestId("scan-photo-result");

    fireEvent.click(screen.getByRole("button", { name: /покажите нужную бутылку/i }));
    expect(await screen.findByTestId("aim-frame")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /искать по рамке/i }));

    await waitFor(() => expect(scan).toHaveBeenCalledTimes(2));
    const box = scan.mock.calls[1][1];
    expect(box).toHaveLength(4);
    expect(box?.every((value) => value >= 0 && value <= 1)).toBe(true);
  });

  it("«искать по всему кадру» закрывает прицел, не отправляя рамку", async () => {
    const scan = vi.spyOn(apiClient, "scanPhoto").mockResolvedValue(response());
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile()] } });
    await screen.findByTestId("scan-photo-result");

    fireEvent.click(screen.getByRole("button", { name: /покажите нужную бутылку/i }));
    fireEvent.click(screen.getByRole("button", { name: /искать по всему кадру/i }));

    await waitFor(() => expect(screen.queryByTestId("aim-frame")).not.toBeInTheDocument());
    expect(scan).toHaveBeenCalledTimes(1);
  });

  it("прицел сбрасывается при переходе к блюду и не мешает новому фото", async () => {
    const scan = vi.spyOn(apiClient, "scanPhoto").mockResolvedValue(response());
    const dish = vi.spyOn(apiClient, "pairingDishPhoto").mockResolvedValue(buildDishPhotoResponse("dish.png"));
    renderScan();
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile()] } });
    await screen.findByTestId("scan-photo-result");
    fireEvent.click(screen.getByRole("button", { name: /покажите нужную бутылку/i }));
    expect(screen.getByTestId("aim-frame")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^блюдо$/i }));
    expect(screen.queryByTestId("scan-aim-offer")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/фото блюда/i), { target: { files: [pngFile()] } });
    await screen.findByTestId("dish-food-result");
    expect(dish).toHaveBeenCalledTimes(1);
    expect(scan).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("scan-aim-offer")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^бутылка$/i }));
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [pngFile()] } });
    await screen.findByTestId("scan-photo-result");
    expect(scan.mock.calls[1][1]).toBeUndefined();
    expect(screen.queryByTestId("aim-frame")).not.toBeInTheDocument();
  });

});
