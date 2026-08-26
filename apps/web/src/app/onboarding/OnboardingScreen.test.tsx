import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { renderApp } from "../../test/renderApp";
import { setAnalyticsSink, type AnalyticsEvent } from "../../lib/analytics";
import { storage } from "../../lib/storage";
import { OnboardingScreen } from "./OnboardingScreen";

function isoYearsAgo(years: number): string {
  const date = new Date();
  date.setFullYear(date.getFullYear() - years);
  return date.toISOString().slice(0, 10);
}

function renderOnboarding() {
  return renderApp(
    <Routes>
      <Route path="/app/onboarding" element={<OnboardingScreen />} />
      <Route path="/app/scan" element={<div>SCAN_SCREEN_PROBE</div>} />
    </Routes>,
    "/app/onboarding",
  );
}

describe("OnboardingScreen — гейт 18+", () => {
  it("без даты рождения не пускает дальше: показывает ошибку и остаётся на форме", () => {
    renderOnboarding();
    fireEvent.click(screen.getByRole("button", { name: /начать пробовать/i }));

    expect(screen.getByText(/укажите дату рождения/i)).toBeInTheDocument();
    expect(screen.queryByText("SCAN_SCREEN_PROBE")).not.toBeInTheDocument();
    expect(storage.isOnboardingComplete()).toBe(false);
  });

  it("до 18 лет — блокирует честным экраном отказа и шлёт age_gate_failed", () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderOnboarding();
      const dateInput = screen.getByLabelText(/дата рождения/i);
      fireEvent.change(dateInput, { target: { value: isoYearsAgo(10) } });
      fireEvent.click(screen.getByRole("button", { name: /начать пробовать/i }));

      expect(screen.getByTestId("age-denied")).toBeInTheDocument();
      expect(screen.queryByText("SCAN_SCREEN_PROBE")).not.toBeInTheDocument();
      expect(events.some((e) => e.name === "age_gate_failed")).toBe(true);
      expect(storage.isOnboardingComplete()).toBe(false);
    } finally {
      restore();
    }
  });

  it("совершеннолетний со скоупами: скоупы уходят в API-клиент и онбординг завершается", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderOnboarding();

      fireEvent.change(screen.getByLabelText(/дата рождения/i), { target: { value: isoYearsAgo(30) } });
      fireEvent.click(screen.getByLabelText(/вкусовой профиль/i));
      fireEvent.click(screen.getByRole("button", { name: /начать пробовать/i }));

      await waitFor(() => expect(screen.getByText("SCAN_SCREEN_PROBE")).toBeInTheDocument());

      expect(storage.isOnboardingComplete()).toBe(true);
      expect(storage.getConsentScopes()).toEqual(expect.arrayContaining(["base", "profiling"]));
      expect(storage.getAccountKind()).toBe("guest");

      const completed = events.find((e) => e.name === "onboarding_completed");
      expect(completed?.props).toMatchObject({ scopes: expect.arrayContaining(["base", "profiling"]) });
      expect(events.some((e) => e.name === "consent_granted" && (e.props as { scope: string }).scope === "profiling")).toBe(
        true,
      );
    } finally {
      restore();
    }
  });
});
