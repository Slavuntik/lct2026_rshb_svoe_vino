import { Capacitor } from "@capacitor/core";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../../test/renderApp";
import { ScanScreen } from "./ScanScreen";

// Ocr — Capacitor Proxy(registerPlugin): подменяем модуль целиком (см. lib/nativeOcr.test.ts).
const recognizeTextMock = vi.fn();
vi.mock("svoy-somelye-ocr-plugin", () => ({
  Ocr: { recognizeText: (...args: unknown[]) => recognizeTextMock(...args) },
}));

function WineProbe() {
  const { wineId } = useParams<{ wineId: string }>();
  return <div>WINE_CARD_PROBE:{wineId}</div>;
}

function renderScan() {
  return renderApp(
    <Routes>
      <Route path="/app/scan" element={<ScanScreen />} />
      <Route path="/app/wine/:wineId" element={<WineProbe />} />
    </Routes>,
    "/app/scan",
  );
}

describe("ScanScreen — текстовый путь (равноправный со сканом по фото)", () => {
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

    // Выбор из списка ведёт на карточку выбранного вина.
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
  });
});

describe("ScanScreen — фото: ветвление native/web (блокер ревью 02, п.2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    recognizeTextMock.mockReset();
  });

  it("на iOS фото идёт через нативный OCR-плагин -> /scan/resolve, минуя /scan/ocr", async () => {
    vi.spyOn(Capacitor, "getPlatform").mockReturnValue("ios");
    recognizeTextMock.mockResolvedValue({ text: "Шардоне Резерв", confidence: 0.92 });

    renderScan();

    // На iOS чекбокса согласия на отправку фото на сервер нет вообще — фото никуда не уходит.
    expect(screen.queryByText(/согласен.*отправить фото на сервер/i)).not.toBeInTheDocument();

    const file = new File(["label-bytes"], "label.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /распознать по фото/i }));

    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:tihaya-buhta-chardonnay-reserve-2023")).toBeInTheDocument(),
    );
    expect(recognizeTextMock).toHaveBeenCalledTimes(1);
  });

  it("на вебе /scan/ocr всегда 501 -> честное предложение ввести текст, без generic-ошибки", async () => {
    vi.spyOn(Capacitor, "getPlatform").mockReturnValue("web");
    renderScan();

    const file = new File(["label-bytes"], "label.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText(/фото этикетки/i), { target: { files: [file] } });
    fireEvent.click(screen.getByLabelText(/согласен.*отправить фото на сервер/i));
    fireEvent.click(screen.getByRole("button", { name: /распознать по фото/i }));

    expect(await screen.findByText(/введите текст с этикетки/i)).toBeInTheDocument();
    expect(screen.queryByText("Что-то пошло не так. Попробуйте ещё раз.")).not.toBeInTheDocument();
    expect(recognizeTextMock).not.toHaveBeenCalled();
  });
});
