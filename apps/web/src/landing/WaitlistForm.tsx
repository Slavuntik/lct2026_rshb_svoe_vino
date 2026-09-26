import { useState, type FormEvent } from "react";
import { useI18n } from "../i18n";
import { track } from "../lib/analytics";
import { apiClient } from "../lib/apiClient";
import { CONSENT_VERSION } from "../lib/consent";

type Status = "idle" | "submitting" | "success" | "error";

export function WaitlistForm() {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [validationError, setValidationError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!consent) {
      setValidationError(t("landing.waitlistConsentError"));
      return;
    }
    if (!/^\S+@\S+\.\S+$/.test(email)) {
      setValidationError(t("landing.waitlistEmailError"));
      return;
    }
    setValidationError(null);
    setStatus("submitting");
    try {
      await apiClient.postWaitlist({ email, consent_version: CONSENT_VERSION });
      setStatus("success");
      track("waitlist_joined", {});
    } catch {
      setStatus("error");
    }
  }

  if (status === "success") {
    return (
      <div className="card stack" data-testid="waitlist-success">
        <h2>{t("landing.waitlistTitle")}</h2>
        <p className="badge badge--ok">{t("landing.waitlistSuccess")}</p>
      </div>
    );
  }

  return (
    <form className="card stack" onSubmit={handleSubmit} noValidate aria-label={t("landing.waitlistTitle")}>
      <h2>{t("landing.waitlistTitle")}</h2>
      <p className="text-small">{t("landing.waitlistSubtitle")}</p>
      <label className="field">
        <span className="field__label">{t("landing.waitlistEmailLabel")}</span>
        <input
          className="field__input"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="email"
          required
        />
      </label>
      <label className="checkbox-row">
        <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
        <span>{t("landing.waitlistConsentLabel")}</span>
      </label>
      <p className="text-caption">
        {t("landing.waitlistLegalHint")}{" "}
        <a href="/legal/privacy.html" target="_blank" rel="noopener noreferrer">
          {t("landing.legalPrivacyLink")}
        </a>
        {" · "}
        <a href="/legal/consent.html" target="_blank" rel="noopener noreferrer">
          {t("landing.legalConsentLink")}
        </a>
      </p>
      {validationError && <p className="field__error">{validationError}</p>}
      {status === "error" && <p className="field__error">{t("landing.waitlistError")}</p>}
      <button type="submit" className="btn btn--primary btn--block" disabled={!consent || status === "submitting"}>
        {t("landing.waitlistSubmit")}
      </button>
    </form>
  );
}
