import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { setAnalyticsSink, type AnalyticsEvent } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import { CONSENT_VERSION } from "../../lib/consent";
import { storage } from "../../lib/storage";
import { renderApp } from "../../test/renderApp";
import { ProfileScreen } from "./ProfileScreen";

async function loginAsGuest() {
  const token = await apiClient.registerGuest({ age_confirmed: true, consent_version: CONSENT_VERSION });
  storage.setAccessToken(token.access_token);
  storage.setAccountKind("guest");
}

function renderProfile() {
  return renderApp(
    <Routes>
      <Route path="/app/profile" element={<ProfileScreen />} />
      <Route path="/" element={<div>LANDING_PROBE</div>} />
    </Routes>,
    "/app/profile",
  );
}

describe("ProfileScreen — мои данные", () => {
  beforeEach(async () => {
    await loginAsGuest();
  });

  it("включение скоупа шлёт consent_granted{version,scope}", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderProfile();
      const profilingCheckbox = screen.getByLabelText(/вкусовой профиль/i);
      fireEvent.click(profilingCheckbox);

      await waitFor(() =>
        expect(events.some((e) => e.name === "consent_granted" && (e.props as { scope: string }).scope === "profiling")).toBe(
          true,
        ),
      );
    } finally {
      restore();
    }
  });

  it("выгрузка данных шлёт data_export_requested и показывает подтверждение", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderProfile();
      fireEvent.click(screen.getByRole("button", { name: /скачать мои данные/i }));

      await waitFor(() => expect(events.some((e) => e.name === "data_export_requested")).toBe(true));
      expect(await screen.findByText(/файл сформирован/i)).toBeInTheDocument();
    } finally {
      restore();
    }
  });

  it("апгрейд гостя реально показывает сообщение об успехе, а не только меняет бейдж (регрессия из e2e агента F)", async () => {
    // Онбординг всегда пишет дату рождения перед тем, как выдать гостевой токен —
    // без неё мок /auth/register честно отдаёт 403 age_restricted (birth_date="").
    storage.setBirthDate("1990-01-01");
    renderProfile();

    fireEvent.change(screen.getByLabelText(/e-mail/i), { target: { value: "guest@example.com" } });
    fireEvent.change(screen.getByLabelText(/пароль/i), { target: { value: "supersecret1" } });
    fireEvent.click(screen.getByRole("button", { name: /создать аккаунт/i }));

    // Раньше setAccountKind("registered") и setUpgradeStatus("done") прилетали одним
    // рендером — форма (и сообщение внутри неё) размонтировались раньше, чем
    // пользователь успевал их увидеть; единственным сигналом оставалась смена бейджа.
    await waitFor(() => expect(screen.getByText(/полный аккаунт/i)).toBeInTheDocument());
    expect(screen.getByText(/аккаунт создан/i)).toBeInTheDocument();
  });

  it("удаление аккаунта требует подтверждения, затем чистит хранилище и уходит на лендинг", async () => {
    const events: AnalyticsEvent[] = [];
    const restore = setAnalyticsSink((event) => events.push(event));
    try {
      renderProfile();

      fireEvent.click(screen.getByRole("button", { name: /удалить аккаунт и все данные/i }));
      // Модалка подтверждения — до неё запрос не улетает.
      expect(events.some((e) => e.name === "account_delete_requested")).toBe(false);

      fireEvent.click(screen.getByRole("button", { name: /да, удалить всё/i }));

      await waitFor(() => expect(screen.getByText("LANDING_PROBE")).toBeInTheDocument());
      expect(events.some((e) => e.name === "account_delete_requested")).toBe(true);
      expect(storage.getAccessToken()).toBeNull();
    } finally {
      restore();
    }
  });
});
