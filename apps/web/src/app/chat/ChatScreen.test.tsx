import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { setAnalyticsSink, type AnalyticsEvent } from "../../lib/analytics";
import { renderApp } from "../../test/renderApp";
import { ChatScreen } from "./ChatScreen";

function sendMessage(text: string) {
  fireEvent.change(screen.getByPlaceholderText(/например: что взять к стейку/i), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /спросить/i }));
}

describe("ChatScreen — SSE-парсер и честные отказы", () => {
  it("собирает token/citation/done и показывает цитаты с номерами [n]", async () => {
    renderApp(<ChatScreen />, "/app/chat");

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

  it("цитаты кликабельны и ведут на первоисточник — «правило каталога №5» (хвост ревью 02)", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderApp(<ChatScreen />, "/app/chat");
      sendMessage("что взять к стейку");

      const answerText = await screen.findByText(/Красностоп Крепкий/);
      const bubble = answerText.closest(".chat-bubble--assistant") as HTMLElement;
      await waitFor(() => expect(within(bubble).getAllByRole("link").length).toBeGreaterThanOrEqual(2));

      const links = within(bubble).getAllByRole("link");
      expect(links[0]).toHaveAttribute("href", "https://example.com/wines/severny-sklon-krasnostop-2021");
      expect(links[0]).toHaveAttribute("target", "_blank");
      expect(links[0].getAttribute("rel")).toMatch(/noopener/);

      fireEvent.click(links[0]);
      const clicked = events.find((e) => e.name === "source_link_clicked");
      expect(clicked?.props).toMatchObject({ wine_id: "severny-sklon-krasnostop-2021" });
    } finally {
      restore();
    }
  });

  it("пустая выдача ретривера — честный refusal, а не выдумка", async () => {
    renderApp(<ChatScreen />, "/app/chat");

    sendMessage("совершенно нерелевантный набор слов без совпадений");

    const refusal = await screen.findByTestId("chat-refusal");
    expect(refusal.textContent).toMatch(/честно/i);
    expect(refusal.textContent).toMatch(/не нашли достаточно надёжных источников/i);
  });

  it("режим «аналог импортного»: POST /analogs вместо /chat, результат кликабелен", async () => {
    renderApp(<ChatScreen />, "/app/chat");

    fireEvent.click(screen.getByRole("button", { name: /аналог импортного/i }));
    fireEvent.change(screen.getByPlaceholderText(/люблю просекко/i), { target: { value: "люблю Просекко" } });
    fireEvent.click(screen.getByRole("button", { name: /найти аналог/i }));

    const result = await screen.findByTestId("analog-result");
    expect(result.textContent).toMatch(/Просекко/);
  });

  it("нераспознанный стиль — 404 с подсказкой стилей из message, без падения экрана", async () => {
    renderApp(<ChatScreen />, "/app/chat");

    fireEvent.click(screen.getByRole("button", { name: /аналог импортного/i }));
    fireEvent.change(screen.getByPlaceholderText(/люблю просекко/i), { target: { value: "зюмбревокс тарабарский" } });
    fireEvent.click(screen.getByRole("button", { name: /найти аналог/i }));

    const notFound = await screen.findByTestId("analog-not-found");
    expect(notFound.textContent).toMatch(/популярные/i);
  });
});
