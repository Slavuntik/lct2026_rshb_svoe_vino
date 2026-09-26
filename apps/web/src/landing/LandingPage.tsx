import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";
import { track } from "../lib/analytics";
import { apiClient } from "../lib/apiClient";
import { CONSENT_VERSION } from "../lib/consent";
import { storage } from "../lib/storage";
import { AgeInterstitial } from "./AgeInterstitial";
import { useLandingSeo } from "./useLandingSeo";
import { WaitlistForm } from "./WaitlistForm";
import { WineRoadsTeaser } from "./WineRoadsTeaser";

// Каркас лендинга (агент C, волна 1): оффер, три фичи, waitlist, 18+ интерстициал, дисклеймер.
// Копирайт, тизер «Винных дорог», SEO-теги и юр-ссылки поверх каркаса — агент D
// (agents/D-landing.md), см. ORCHESTRATION.md про секвенирование зон в apps/web/src/landing/.
type CtaStatus = "idle" | "loading" | "error";

export function LandingPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [ageGateOpen, setAgeGateOpen] = useState(false);
  const [ageDenied, setAgeDenied] = useState(false);
  const [ctaStatus, setCtaStatus] = useState<CtaStatus>("idle");

  useLandingSeo();

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
          <p className="text-caption">{t("landing.ctaTrust")}</p>
        </div>
      </header>

      <main className="container">
        <h2 id="landing-features-heading">{t("landing.sectionFeaturesTitle")}</h2>
        <section className="landing-features" aria-labelledby="landing-features-heading">
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

        <WineRoadsTeaser />

        <WaitlistForm />
      </main>

      <footer className="landing-footer container stack stack--tight">
        <nav className="row" aria-label={t("landing.footerLegalTitle")}>
          <a href="/legal/privacy.html" target="_blank" rel="noopener noreferrer">
            {t("landing.legalPrivacyLink")}
          </a>
          <a href="/legal/consent.html" target="_blank" rel="noopener noreferrer">
            {t("landing.legalConsentLink")}
          </a>
          <a href="/legal/terms.html" target="_blank" rel="noopener noreferrer">
            {t("landing.legalTermsLink")}
          </a>
        </nav>
        <p>{t("landing.footerDisclaimer")}</p>
      </footer>
    </div>
  );
}
