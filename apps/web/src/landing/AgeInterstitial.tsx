import { useI18n } from "../i18n";

interface AgeInterstitialProps {
  denied: boolean;
  onConfirm: () => void;
  onDeny: () => void;
  onReconsider: () => void;
}

/** 18+ гейт лендинга: показывается один раз, ack — в localStorage (см. lib/storage.ts). */
export function AgeInterstitial({ denied, onConfirm, onDeny, onReconsider }: AgeInterstitialProps) {
  const { t } = useI18n();

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="age-gate-title">
      <div className="modal stack">
        <h2 id="age-gate-title">{t("landing.ageGateTitle")}</h2>
        {denied ? (
          <>
            <p>{t("landing.ageGateDenied")}</p>
            <button type="button" className="btn btn--ghost" onClick={onReconsider}>
              {t("common.back")}
            </button>
          </>
        ) : (
          <>
            <p>{t("landing.ageGateMessage")}</p>
            <div className="row">
              <button type="button" className="btn btn--primary" onClick={onConfirm}>
                {t("landing.ageGateYes")}
              </button>
              <button type="button" className="btn btn--ghost" onClick={onDeny}>
                {t("landing.ageGateNo")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
