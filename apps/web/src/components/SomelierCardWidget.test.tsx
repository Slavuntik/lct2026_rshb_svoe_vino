import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { SomelierCardWidget } from "./SomelierCardWidget";
import { apiClient } from "../lib/apiClient";
import { SOMELIER_WIDGET_INPUT_ID } from "../lib/somelierWidget";
import { renderApp } from "../test/renderApp";

function WineProbe() {
  const { wineId } = useParams<{ wineId: string }>();
  return <div>WINE_CARD_PROBE:{wineId}</div>;
}

/** Статический путь для хоста виджета (не /app/wine/:wineId) — иначе он же, будучи такой же
 * по форме динамического сегмента, перехватил бы переход по клику на цитату вместо WineProbe:
 * react-router не различает два роута по имени параметра, только по структуре пути. */
function renderWidget(wineId: string) {
  return renderApp(
    <Routes>
      <Route path="/app/somelier-widget-under-test" element={<SomelierCardWidget wineId={wineId} />} />
      <Route path="/app/wine/:wineId" element={<WineProbe />} />
    </Routes>,
    "/app/somelier-widget-under-test",
  );
}

function ask(question: string) {
  fireEvent.change(screen.getByPlaceholderText(/например: с чем подать это вино/i), { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: /^спросить$/i }));
}

/**
 * Задача тимлида 27.09 (макет Figma «Цифровой сомелье»): виджет сомелье внутри карточки
 * вина. Поведение обязано быть тем же, что ChatScreen.tsx (contracts/openapi.yaml v0.3.5,
 * agents/BOARD.md — wine_id первым запросом), только источник wine_id — проп, не
 * navigation state (виджет не требует перехода на отдельный экран).
 */
describe("SomelierCardWidget", () => {
  it("до первого вопроса показывает заголовок и подсказку, поле ввода помечено стабильным id для focusSomelierWidget()", () => {
    renderWidget("tihaya-buhta-chardonnay-reserve-2023");

    expect(screen.getByRole("heading", { name: "Цифровой сомелье" })).toBeInTheDocument();
    expect(screen.getByText(/спросите про вкус/i)).toBeInTheDocument();

    const input = screen.getByPlaceholderText(/например: с чем подать это вино/i);
    expect(input).toHaveAttribute("id", SOMELIER_WIDGET_INPUT_ID);
  });

  it("отправка пустого вопроса ничего не делает — кнопка отправки задизейблена", () => {
    const chatSpy = vi.spyOn(apiClient, "chat");
    renderWidget("tihaya-buhta-chardonnay-reserve-2023");

    expect(screen.getByRole("button", { name: /^спросить$/i })).toBeDisabled();
    expect(chatSpy).not.toHaveBeenCalled();
  });

  it("первый вопрос уносит wine_id — ответ детерминированно про это вино, с цитатой, ведущей на его же внутреннюю карточку", async () => {
    renderWidget("tihaya-buhta-chardonnay-reserve-2023");

    ask("Что скажете?");

    const answer = await screen.findByText(/Шардоне Резерв/);
    const bubble = answer.closest(".chat-bubble--assistant") as HTMLElement;
    expect(bubble.textContent).toMatch(/\[1\]/);

    const citation = within(bubble).getByRole("button", { name: /^\[1\]/ });
    fireEvent.click(citation);

    await waitFor(() =>
      expect(screen.getByText("WINE_CARD_PROBE:tihaya-buhta-chardonnay-reserve-2023")).toBeInTheDocument(),
    );
  });

  it("второй вопрос того же виджета уже без wine_id — при несовпадении ключевых слов честный отказ, не выдуманный ответ", async () => {
    const chatSpy = vi.spyOn(apiClient, "chat");
    renderWidget("tihaya-buhta-chardonnay-reserve-2023");

    ask("Что скажете?");
    await screen.findByText(/Шардоне Резерв/);
    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy.mock.calls[0][0].wine_id).toBe("tihaya-buhta-chardonnay-reserve-2023");

    ask("бла-бла-бла ничего похожего на ключевые слова фикстур");
    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(2));
    expect(chatSpy.mock.calls[1][0].wine_id).toBeUndefined();

    expect(await screen.findByTestId("somelier-widget-refusal")).toHaveTextContent(/честно/i);
  });

  it("сетевая ошибка — честное сообщение внутри виджета, не вечная тишина", async () => {
    vi.spyOn(apiClient, "chat").mockRejectedValue(new Error("network down"));
    renderWidget("tihaya-buhta-chardonnay-reserve-2023");

    ask("Что скажете?");

    expect(await screen.findByTestId("somelier-widget-refusal")).toHaveTextContent(/не удалось получить ответ/i);
  });
});
