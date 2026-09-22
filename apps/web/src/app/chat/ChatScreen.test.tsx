import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { setAnalyticsSink, type AnalyticsEvent } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import type { ChatStreamEvent } from "../../lib/apiTypes";
import { renderApp } from "../../test/renderApp";
import { ChatScreen } from "./ChatScreen";

function sendMessage(text: string) {
  fireEvent.change(screen.getByPlaceholderText(/например: что взять к стейку/i), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /спросить/i }));
}

function WineProbe() {
  const { wineId } = useParams<{ wineId: string }>();
  return <div>WINE_CARD_PROBE:{wineId}</div>;
}

/** Правило ссылок (задача тимлида 22.09): цитата-вино теперь внутренняя навигация, не href —
 * нужен реальный роут /app/wine/:wineId, чтобы проверить, что клик реально туда доводит
 * (тот же приём, что renderScan() в ScanScreen.test.tsx). */
function renderChat() {
  return renderApp(
    <Routes>
      <Route path="/app/chat" element={<ChatScreen />} />
      <Route path="/app/wine/:wineId" element={<WineProbe />} />
    </Routes>,
    "/app/chat",
  );
}

/** Задача тимлида 22.09: тот же роут, но location.state несёт {prefillMessage, wineId} — как
 * реально приходит из ScanScreen.tsx/WineCardScreen.tsx «Спросить сомелье об этом вине». */
function renderChatWithWineState(wineId: string, prefillMessage: string) {
  return renderApp(
    <Routes>
      <Route path="/app/chat" element={<ChatScreen />} />
      <Route path="/app/wine/:wineId" element={<WineProbe />} />
    </Routes>,
    { pathname: "/app/chat", state: { prefillMessage, wineId } },
  );
}

describe("ChatScreen — SSE-парсер и честные отказы", () => {
  it("собирает token/citation/done и показывает цитаты с номерами [n]", async () => {
    renderChat();

    sendMessage("что взять к стейку");

    const answerText = await screen.findByText(/Красностоп Крепкий/);
    // "[1]" в JSX рендерится как отдельные текстовые узлы ("[", "1", "] …") — сравниваем
    // склеенный textContent контейнера, а не полагаемся на поэлементный getByText.
    const bubble = answerText.closest(".chat-bubble--assistant");
    await waitFor(() => expect(bubble?.textContent).toMatch(/\[1\]/));
    expect(bubble?.textContent).toMatch(/\[2\]/);

    // Фидбек появляется только после done (когда есть answer_id).
    await waitFor(() => expect(screen.getByRole("button", { name: "Да" })).toBeInTheDocument());
  });

  it("правило ссылок (задача тимлида 22.09, п.2): цитата-вино (wine_id) ведёт на внутреннюю карточку через навигацию приложения, не на vino-svoe", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderChat();
      sendMessage("что взять к стейку");

      const answerText = await screen.findByText(/Красностоп Крепкий/);
      const bubble = answerText.closest(".chat-bubble--assistant") as HTMLElement;
      const citationButtons = await within(bubble).findAllByRole("button", { name: /^\[\d+\]/ });
      expect(citationButtons.length).toBeGreaterThanOrEqual(2);

      // Никакой <a href> на портал среди цитат — только наша карточка (правило ссылок).
      expect(within(bubble).queryAllByRole("link")).toHaveLength(0);

      fireEvent.click(citationButtons[0]);

      await waitFor(() =>
        expect(screen.getByText("WINE_CARD_PROBE:severny-sklon-krasnostop-2021")).toBeInTheDocument(),
      );
      // Клик больше не «переход на первоисточник» (это теперь внутренняя карточка,
      // не внешний портал) — source_link_clicked не должен эмититься с этого бейджа.
      expect(events.find((e) => e.name === "source_link_clicked")).toBeUndefined();
    } finally {
      restore();
    }
  });

  it("v0.3.2 + правило ссылок (22.09): цитата-статья (chunk_id, без wine_id) уходит в «Источники» и остаётся внешней ссылкой — как раньше", async () => {
    // Настоящий стриминг: показанные токены не отозвать, поэтому если модель закончила
    // ответ без единого [n], сервер досылает citation перед done (контекст промпта —
    // и есть источник). Эмулируем эту последовательность напрямую через apiClient.chat,
    // не завязываясь на конкретный мок-сценарий в mocks/handlers.ts. Citation — статья
    // (chunk_id, БЕЗ wine_id): у неё нет своей внутренней карточки, поэтому, в отличие от
    // цитаты-вина выше, правило ссылок эту ссылку не трогает — остаётся внешним <a href>.
    const chatSpy = vi.spyOn(apiClient, "chat").mockImplementation(async (_payload, onEvent) => {
      const events: ChatStreamEvent[] = [
        { type: "token", text: "Ответ без единой цитатной пометки в тексте вообще." },
        {
          type: "citation",
          n: 1,
          chunk_id: "article-tannin-guide-3",
          url: "https://vino-svoe.ru/articles/tannin-guide",
          quote: "Танины смягчаются при выдержке в дубе",
        },
        { type: "done", answer_id: "mock-answer-orphan" },
      ];
      for (const event of events) onEvent(event);
    });

    try {
      renderChat();
      sendMessage("что-нибудь посоветуй");

      await screen.findByText(/без единой цитатной пометки/);

      const sourcesBlock = await screen.findByTestId("chat-sources-block");
      expect(within(sourcesBlock).getByText(/источники/i)).toBeInTheDocument();

      const link = within(sourcesBlock).getByRole("link");
      expect(link).toHaveAttribute("href", "https://vino-svoe.ru/articles/tannin-guide");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link.getAttribute("rel")).toMatch(/noopener/);
      // И никакой внутренней кнопки-цитаты рядом — это не вино, ей неоткуда взяться.
      expect(within(sourcesBlock).queryByRole("button", { name: /^\[\d+\]/ })).not.toBeInTheDocument();
    } finally {
      chatSpy.mockRestore();
    }
  });

  it("пустая выдача ретривера — честный refusal, а не выдумка", async () => {
    renderChat();

    sendMessage("совершенно нерелевантный набор слов без совпадений");

    const refusal = await screen.findByTestId("chat-refusal");
    expect(refusal.textContent).toMatch(/честно/i);
    expect(refusal.textContent).toMatch(/не нашли достаточно надёжных источников/i);
  });

  it("режим «аналог импортного»: POST /analogs вместо /chat, результат кликабелен", async () => {
    renderChat();

    fireEvent.click(screen.getByRole("button", { name: /аналог импортного/i }));
    fireEvent.change(screen.getByPlaceholderText(/люблю просекко/i), { target: { value: "люблю Просекко" } });
    fireEvent.click(screen.getByRole("button", { name: /найти аналог/i }));

    const result = await screen.findByTestId("analog-result");
    expect(result.textContent).toMatch(/Просекко/);
  });

  it("нераспознанный стиль — 404 с подсказкой стилей из message, без падения экрана", async () => {
    renderChat();

    fireEvent.click(screen.getByRole("button", { name: /аналог импортного/i }));
    fireEvent.change(screen.getByPlaceholderText(/люблю просекко/i), { target: { value: "зюмбревокс тарабарский" } });
    fireEvent.click(screen.getByRole("button", { name: /найти аналог/i }));

    const notFound = await screen.findByTestId("analog-not-found");
    expect(notFound.textContent).toMatch(/популярные/i);
  });
});

describe("ChatScreen — wine_id в первом запросе /v1/chat (задача тимлида 22.09)", () => {
  it("обычный чат без перехода от вина — запрос /v1/chat уходит без поля wine_id", async () => {
    const chatSpy = vi.spyOn(apiClient, "chat").mockResolvedValue(undefined);
    renderChat();

    sendMessage("что взять к стейку");

    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy.mock.calls[0][0].wine_id).toBeUndefined();
  });

  it("wine_id из location.state уходит РОВНО первым запросом — второй вопрос диалога уже без него", async () => {
    const chatSpy = vi.spyOn(apiClient, "chat").mockResolvedValue(undefined);
    renderChatWithWineState("severny-sklon-krasnostop-2021", "Расскажи про Красностоп Крепкий от Усадьба Северный Склон");

    sendMessage("Расскажи про Красностоп Крепкий");
    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy.mock.calls[0][0].wine_id).toBe("severny-sklon-krasnostop-2021");

    sendMessage("а что ещё есть в таком стиле?");
    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(2));
    expect(chatSpy.mock.calls[1][0].wine_id).toBeUndefined();
  });

  it("режим «аналог импортного» не расходует wine_id — он остаётся для следующего вопроса в режиме «Вопрос»", async () => {
    const chatSpy = vi.spyOn(apiClient, "chat").mockResolvedValue(undefined);
    const analogSpy = vi.spyOn(apiClient, "postAnalogs");
    renderChatWithWineState("severny-sklon-krasnostop-2021", "Расскажи про Красностоп Крепкий от Усадьба Северный Склон");

    // POST /analogs — не /v1/chat, "первый запрос чата" ещё не случился.
    fireEvent.click(screen.getByRole("button", { name: /аналог импортного/i }));
    fireEvent.change(screen.getByPlaceholderText(/люблю просекко/i), { target: { value: "люблю Просекко" } });
    fireEvent.click(screen.getByRole("button", { name: /найти аналог/i }));
    await waitFor(() => expect(analogSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^вопрос$/i }));
    sendMessage("Расскажи про Красностоп Крепкий");

    await waitFor(() => expect(chatSpy).toHaveBeenCalledTimes(1));
    expect(chatSpy.mock.calls[0][0].wine_id).toBe("severny-sklon-krasnostop-2021");
  });
});
