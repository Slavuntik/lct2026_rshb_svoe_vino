import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import { CONSENT_VERSION } from "../../lib/consent";
import { storage } from "../../lib/storage";
import { AppNav } from "../nav/AppNav";
import { ScanScreen } from "../scan/ScanScreen";

type ScreenStatus = "idle" | "submitting" | "error";

/** Подтверждение 18+ поверх сканера; гостевая сессия создаётся только по кнопке. */
export function OnboardingScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const submittingRef = useRef(false);
  const [status, setStatus] = useState<ScreenStatus>("idle");

  useEffect(() => {
    track("onboarding_started", {});
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    const preventCancel = (event: Event) => event.preventDefault();
    dialog?.addEventListener("cancel", preventCancel);
    if (dialog && !dialog.open) dialog.showModal();
    dialog?.querySelector<HTMLElement>("h1")?.focus({ preventScroll: true });
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      dialog?.removeEventListener("cancel", preventCancel);
      dialog?.close();
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  async function handleConfirm() {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setStatus("submitting");
    try {
      const token = await apiClient.registerGuest({ age_confirmed: true, consent_version: CONSENT_VERSION });
      storage.setAccessToken(token.access_token);
      storage.setAccountKind("guest");
      storage.setConsentScopes(["base"]);
      storage.setOnboardingComplete(true);
      track("consent_granted", { version: CONSENT_VERSION, scope: "base" });
      track("onboarding_completed", { scopes: ["base"] });
      navigate("/app/scan", { replace: true });
    } catch {
      setStatus("error");
    } finally {
      submittingRef.current = false;
    }
  }

  return (
    <>
      <div className="age-gate-preview" inert aria-hidden="true">
        <AppNav shelfAvailable={false} />
        <div className="age-gate-preview__content"><ScanScreen /></div>
      </div>
      <dialog ref={dialogRef} className="age-gate-sheet" aria-modal="true"
        aria-labelledby="age-gate-title" aria-describedby="age-gate-description"
        onCancel={(event) => event.preventDefault()}
        onKeyDown={(event) => { if (event.key === "Escape") event.preventDefault(); }}>
        <header>
          <h1 id="age-gate-title" tabIndex={-1} className="age-gate-sheet__title">18+</h1>
          <p id="age-gate-description">{t("onboarding.confirmMessage")}</p>
        </header>

        {status === "error" && <p className="field__error" role="alert">{t("onboarding.registerFailed")}</p>}
        <button type="button" className="btn btn--primary" onClick={handleConfirm} disabled={status === "submitting"}>
          {status === "submitting" ? t("onboarding.submitting") : t("onboarding.confirm")}
        </button>
      </dialog>
    </>
  );
}
