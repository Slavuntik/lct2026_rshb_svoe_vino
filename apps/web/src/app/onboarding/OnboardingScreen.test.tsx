import { fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../../test/renderApp";
import { apiClient } from "../../lib/apiClient";
import { CONSENT_VERSION } from "../../lib/consent";
import { storage } from "../../lib/storage";
import { OnboardingScreen } from "./OnboardingScreen";

function renderOnboarding() {
  return renderApp(
    <Routes>
      <Route path="/app/onboarding" element={<OnboardingScreen />} />
      <Route path="/app/scan" element={<div>SCAN_SCREEN_PROBE</div>} />
    </Routes>,
    "/app/onboarding",
  );
}

describe("OnboardingScreen — подтверждение 18+", () => {
  it("до подтверждения блокирует сканер и не создаёт гостя", () => {
    const guest = vi.spyOn(apiClient, "registerGuest");
    try {
      renderOnboarding();
      expect(screen.getByRole("dialog", { name: "18+" })).toBeInTheDocument();
      expect(document.querySelector(".age-gate-preview")).toHaveAttribute("inert");
      expect(screen.queryByRole("button", { name: "Сканировать" })).not.toBeInTheDocument();
      expect(storage.isOnboardingComplete()).toBe(false);
      expect(guest).not.toHaveBeenCalled();
      const cancel = new Event("cancel", { cancelable: true });
      fireEvent(screen.getByRole("dialog"), cancel);
      expect(cancel.defaultPrevented).toBe(true);
    } finally { guest.mockRestore(); }
  });

  it("подтверждает возраст и сохраняет только базовое согласие без выдуманной даты рождения", async () => {
    const guest = vi.spyOn(apiClient, "registerGuest");
    try {
      renderOnboarding();
      fireEvent.click(screen.getByRole("button", { name: "Подтверждаю" }));
      expect(await screen.findByText("SCAN_SCREEN_PROBE")).toBeInTheDocument();
      expect(guest).toHaveBeenCalledWith({ age_confirmed: true, consent_version: CONSENT_VERSION });
      expect(storage.isOnboardingComplete()).toBe(true);
      expect(storage.getConsentScopes()).toEqual(["base"]);
      expect(storage.getAccountKind()).toBe("guest");
      expect(storage.getBirthDate()).toBeNull();
      expect(document.body.style.overflow).not.toBe("hidden");
    } finally { guest.mockRestore(); }
  });

  it("при ошибке остаётся закрытым, позволяет повторить и блокирует повторный клик во время запроса", async () => {
    const guest = vi.spyOn(apiClient, "registerGuest").mockRejectedValueOnce(new Error("offline"));
    try {
      renderOnboarding();
      const confirm = screen.getByRole("button", { name: "Подтверждаю" });
      fireEvent.click(confirm);
      fireEvent.click(confirm);
      expect(confirm).toBeDisabled();
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(guest).toHaveBeenCalledTimes(1);
      expect(storage.isOnboardingComplete()).toBe(false);
      expect(confirm).toBeEnabled();
      fireEvent.click(confirm);
      expect(await screen.findByText("SCAN_SCREEN_PROBE")).toBeInTheDocument();
    } finally { guest.mockRestore(); }
  });
});
