import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";
import { track } from "../lib/analytics";
import { apiClient } from "../lib/apiClient";
import { CONSENT_VERSION } from "../lib/consent";
import { storage } from "../lib/storage";
import { AgeInterstitial } from "./AgeInterstitial";
import { WaitlistForm } from "./WaitlistForm";

// Каркас лендинга (агент C, волна 1): оффер, три фичи, waitlist, 18+ интерстициал, дисклеймер.
// Копирайт и юр-страницы доводит агент D (agents/D-landing.md) поверх этого каркаса —
// см. ORCHESTRATION.md про секвенирование зон в apps/web/src/landing/.
type CtaStatus = "idle" | "loading" | "error";

export function LandingPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [ageGateOpen, setAgeGateOpen] = useState(false);
  const [ageDenied, setAgeDenied] = useState(false);
  const [ctaStatus, setCtaStatus] = useState<CtaStatus>("idle");

  async function enterAppAsGuest() {
    setCtaStatus("loading");
    try {
      const token = await apiClient.registerGuest({ age_confirmed: true, consent_version: CONSENT_VERSION });
      storage.setAccessToken(token.access_token);
      storage.setAccountKind("guest");
      // Возраст и базовое согласие уже подтверждены гостевым входом — экран онбординга
      // из /app в этом пути не нужен, он остаётся для прямых заходов на /app.
      storage.setOnboardingComplete(true);
      navigate("/app");
    } catch {
      setCtaStatus("error");
    }
  }

  function handleTryClick() {
    if (storage.hasSeenLandingAgeGate()) {
      void enterAppAsGuest();
    } else {
      setAgeDenied(false);
      setAgeGateOpen(true);
    }
  }

  function handleAgeConfirm() {
    storage.setSeenLandingAgeGate();
    setAgeGateOpen(false);
    void enterAppAsGuest();
  }

  function handleAgeDeny() {
    track("age_gate_failed", {});
    setAgeDenied(true);
  }

  return (
    <div className="screen">
      {ageGateOpen && (
        <AgeInterstitial
          denied={ageDenied}
          onConfirm={handleAgeConfirm}
          onDeny={handleAgeDeny}
          onReconsider={() => setAgeDenied(false)}
        />
      )}

      <header className="container landing-hero">
        <h1>{t("landing.heroTitle")}</h1>
        <p className="screen__subtitle">{t("landing.heroSubtitle")}</p>
        <div className="stack stack--tight" style={{ alignItems: "center" }}>
          <button type="button" className="btn btn--primary" onClick={handleTryClick} disabled={ctaStatus === "loading"}>
            {ctaStatus === "loading" ? t("landing.ctaTryLoading") : t("landing.ctaTry")}
          </button>
          {ctaStatus === "error" && <p className="field__error">{t("landing.ctaTryError")}</p>}
        </div>
      </header>

      <main className="container">
        <section className="landing-features" aria-label={t("nav.scan")}>
          <article className="feature-card">
            <h3>{t("landing.featureScanTitle")}</h3>
            <p>{t("landing.featureScanText")}</p>
          </article>
          <article className="feature-card">
            <h3>{t("landing.featureChatTitle")}</h3>
            <p>{t("landing.featureChatText")}</p>
          </article>
          <article className="feature-card">
            <h3>{t("landing.featureAnalogTitle")}</h3>
            <p>{t("landing.featureAnalogText")}</p>
          </article>
        </section>

        <WaitlistForm />
      </main>

      <footer className="landing-footer container">
        <p>{t("landing.footerDisclaimer")}</p>
      </footer>
    </div>
  );
}
