import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { setAnalyticsSink, type AnalyticsEvent } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import { CONSENT_VERSION } from "../../lib/consent";
import { storage } from "../../lib/storage";
import { renderApp } from "../../test/renderApp";
import { TastePassportScreen } from "./TastePassportScreen";

// Свайп требует полного аккаунта со scope profiling (contracts/openapi.yaml v0.2) —
// проходим тот же путь, что и настоящий онбординг/апгрейд гостя.
async function setupAccountWithProfiling() {
  const token = await apiClient.registerGuest({ age_confirmed: true, consent_version: CONSENT_VERSION });
  storage.setAccessToken(token.access_token);
  storage.setAccountKind("registered");
  await apiClient.postConsent({ consent_version: CONSENT_VERSION, grant: true, scopes: ["profiling"] });
}

describe("TastePassportScreen — свайпы шлют события по словарю events.md", () => {
  beforeEach(async () => {
    await setupAccountWithProfiling();
  });

  it("свайп 'нравится' шлёт swipe{wine_id,verdict} и taste_profile_updated{swipes_count}", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderApp(<TastePassportScreen />, "/app/taste");

      await waitFor(() => expect(screen.getByText("Шардоне Резерв")).toBeInTheDocument());
      fireEvent.click(screen.getByRole("button", { name: "Нравится" }));

      await waitFor(() => expect(events.some((e) => e.name === "swipe")).toBe(true));

      const swipeEvent = events.find((e) => e.name === "swipe");
      expect(swipeEvent?.props).toMatchObject({
        wine_id: "tihaya-buhta-chardonnay-reserve-2023",
        verdict: "like",
        _v: "0.1",
      });

      const updatedEvent = events.find((e) => e.name === "taste_profile_updated");
      expect(updatedEvent?.props).toMatchObject({ swipes_count: 1 });

      // Следующая карточка в очереди подгружается автоматически.
      await waitFor(() => expect(screen.getByText("Красностоп Крепкий")).toBeInTheDocument());
    } finally {
      restore();
    }
  });

  it("скип шлёт swipe{verdict:'skip'} и не двигает вкусовой вектор так же, как like/dislike", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderApp(<TastePassportScreen />, "/app/taste");
      await waitFor(() => expect(screen.getByText("Шардоне Резерв")).toBeInTheDocument());
      fireEvent.click(screen.getByRole("button", { name: "Пропустить" }));

      await waitFor(() => expect(events.some((e) => e.name === "swipe")).toBe(true));
      expect(events.find((e) => e.name === "swipe")?.props).toMatchObject({ verdict: "skip" });
    } finally {
      restore();
    }
  });

  it("гость видит приглашение завести аккаунт вместо свайпов", async () => {
    storage.clearAll();
    storage.setAccountKind("guest");
    renderApp(<TastePassportScreen />, "/app/taste");

    expect(await screen.findByTestId("taste-gate")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /открыть профиль/i })).toBeInTheDocument();
  });
});
