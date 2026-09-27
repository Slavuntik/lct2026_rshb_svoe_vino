import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n, type DictionaryPath } from "../../i18n";
import { isAdult } from "../../lib/age";
import { track } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import type { ConsentScope } from "../../lib/apiTypes";
import { CONSENT_VERSION, OPTIONAL_CONSENT_SCOPES } from "../../lib/consent";
import { storage } from "../../lib/storage";

const SCOPE_COPY: Record<ConsentScope, { label: DictionaryPath; hint: DictionaryPath }> = {
  base: { label: "onboarding.consentBaseLabel", hint: "onboarding.consentBaseHint" },
  profiling: { label: "onboarding.consentProfilingLabel", hint: "onboarding.consentProfilingHint" },
  geo: { label: "onboarding.consentGeoLabel", hint: "onboarding.consentGeoHint" },
  marketing: { label: "onboarding.consentMarketingLabel", hint: "onboarding.consentMarketingHint" },
};

type ScreenStatus = "idle" | "submitting" | "error";

/**
 * Экран 1/6 — онбординг: гейт 18+ по дате рождения + согласия по скоупам.
 * Технически логинит гостя через POST /auth/guest (v0.2) — полноценная регистрация
 * (email/пароль) переносится на профиль, когда гость решает завести аккаунт.
 */
export function OnboardingScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [birthDate, setBirthDate] = useState("");
  const [scopes, setScopes] = useState<Record<ConsentScope, boolean>>({
    base: true,
    profiling: false,
    geo: false,
    marketing: false,
  });
  const [birthDateError, setBirthDateError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [status, setStatus] = useState<ScreenStatus>("idle");

  useEffect(() => {
    track("onboarding_started", {});
  }, []);

  function toggleScope(scope: ConsentScope) {
    if (scope === "base") return; // обязательный скоуп, не отключается на этом экране
    setScopes((prev) => ({ ...prev, [scope]: !prev[scope] }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!birthDate) {
      setBirthDateError(t("onboarding.birthDateError"));
      return;
    }
    if (!isAdult(birthDate)) {
      track("age_gate_failed", {});
      setDenied(true);
      return;
    }

    setBirthDateError(null);
    setStatus("submitting");
    try {
      const token = await apiClient.registerGuest({ age_confirmed: true, consent_version: CONSENT_VERSION });
      storage.setAccessToken(token.access_token);
      storage.setAccountKind("guest");
      storage.setBirthDate(birthDate);

      const grantedOptional = OPTIONAL_CONSENT_SCOPES.filter((scope) => scopes[scope]);
      if (grantedOptional.length > 0) {
        await apiClient.postConsent({ consent_version: CONSENT_VERSION, grant: true, scopes: grantedOptional });
        for (const scope of grantedOptional) {
          track("consent_granted", { version: CONSENT_VERSION, scope });
        }
      }
      track("consent_granted", { version: CONSENT_VERSION, scope: "base" });

      const allGranted: ConsentScope[] = ["base", ...grantedOptional];
      storage.setConsentScopes(allGranted);
      storage.setOnboardingComplete(true);
      track("onboarding_completed", { scopes: allGranted });
      navigate("/app/scan");
    } catch {
      setStatus("error");
    }
  }

  if (denied) {
    return (
      <div className="screen container stack" data-testid="age-denied">
        <h1>{t("onboarding.ageDeniedTitle")}</h1>
        <p>{t("onboarding.ageDeniedMessage")}</p>
      </div>
    );
  }

  return (
    <div className="screen container">
      <header className="screen__header">
        <h1 className="screen__title">{t("onboarding.stepAgeTitle")}</h1>
        <p className="screen__subtitle">{t("onboarding.stepAgeSubtitle")}</p>
      </header>

      {/* Задача тимлида 27.09 («Остальные экраны», живой просмотр): линия-разделитель между
          возрастным гейтом и согласиями была в старом стиле, не в духе безрамочной карточки
          вина — заменена отступом. Две группы вместо одного плоского .stack — единственный
          способ дать ИМЕННО между ними больше воздуха, чем внутри каждой (.stack.gap
          одинаков для всех прямых детей): form.stack--loose (36px) снаружи, обычный .stack
          (16px) внутри каждой группы. */}
      <form className="stack stack--loose" onSubmit={handleSubmit} noValidate>
        <div className="stack">
          <label className="field">
            <span className="field__label">{t("onboarding.birthDateLabel")}</span>
            <input
              className="field__input"
              type="date"
              value={birthDate}
              onChange={(event) => setBirthDate(event.target.value)}
              max={new Date().toISOString().slice(0, 10)}
            />
          </label>
          {birthDateError && <p className="field__error">{birthDateError}</p>}
        </div>

        <div className="stack">
          <h2 className="card-heading">{t("onboarding.consentsTitle")}</h2>
          <p className="text-small">{t("onboarding.consentsSubtitle")}</p>

          <div className="stack stack--tight">
            <div className="checkbox-row">
              <input type="checkbox" checked readOnly aria-readonly="true" />
              <span>
                {t(SCOPE_COPY.base.label)}
                <br />
                <span className="text-caption">{t(SCOPE_COPY.base.hint)}</span>
              </span>
            </div>
            {OPTIONAL_CONSENT_SCOPES.map((scope) => (
              <label className="checkbox-row" key={scope}>
                <input type="checkbox" checked={scopes[scope]} onChange={() => toggleScope(scope)} />
                <span>
                  {t(SCOPE_COPY[scope].label)}
                  <br />
                  <span className="text-caption">{t(SCOPE_COPY[scope].hint)}</span>
                </span>
              </label>
            ))}
          </div>
        </div>

        {status === "error" && <p className="field__error">{t("onboarding.registerFailed")}</p>}

        <button type="submit" className="btn btn--primary btn--block" disabled={status === "submitting"}>
          {status === "submitting" ? t("onboarding.submitting") : t("onboarding.finish")}
        </button>
        <p className="text-caption">{t("onboarding.disclaimer")}</p>
      </form>
    </div>
  );
}
