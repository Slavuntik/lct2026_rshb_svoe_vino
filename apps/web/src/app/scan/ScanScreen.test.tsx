import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useParams } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { renderApp } from "../../test/renderApp";
import { ScanScreen } from "./ScanScreen";

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
