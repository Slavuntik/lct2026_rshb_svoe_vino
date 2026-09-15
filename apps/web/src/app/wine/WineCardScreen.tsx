import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { WineCardContent } from "../../components/WineCardContent";
import { useI18n } from "../../i18n";
import { track, type EventPropsMap } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import type { WineCardResponse } from "../../lib/apiTypes";

type FromSource = EventPropsMap["wine_card_viewed"]["from"];

function isFromSource(value: unknown): value is FromSource {
  return value === "scan" || value === "chat" || value === "similar" || value === "swipe";
}

/** Экран 3/6 — карточка вина: source-поля + derived + ссылка на первоисточник + похожие. */
export function WineCardScreen() {
  const { t } = useI18n();
  const { wineId } = useParams<{ wineId: string }>();
  const location = useLocation();
  const navigate = useNavigate();

  const [wine, setWine] = useState<WineCardResponse | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  const locationState = location.state as { from?: unknown } | null;
  const from: FromSource = isFromSource(locationState?.from) ? locationState.from : "scan";

  useEffect(() => {
    if (!wineId) return;
    let cancelled = false;
    setStatus("loading");
    apiClient
      .getWine(wineId)
      .then((data) => {
        if (cancelled) return;
        setWine(data);
        setStatus("ready");
        track("wine_card_viewed", { wine_id: data.wine_id, from });
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
    // from сознательно не в зависимостях: событие должно уйти один раз на факт открытия карточки,
    // а не при каждой смене "источника перехода" (его и не бывает без смены wineId).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wineId]);

  function handleSimilarClick(id: string) {
    navigate(`/app/wine/${encodeURIComponent(id)}`, { state: { from: "similar" } });
  }

  function handleAskSomelier() {
    if (!wine) return;
    navigate("/app/chat", {
      state: { prefillMessage: t("chat.prefillAskAboutWine", { name: wine.source.name, winery: wine.source.winery_name }) },
    });
  }

  if (status === "loading") {
    return (
      <div className="screen container">
        <p>{t("wineCard.loading")}</p>
      </div>
    );
  }

  if (status === "error" || !wine) {
    return (
      <div className="screen container stack">
        <p>{t("wineCard.notFound")}</p>
        <button type="button" className="btn btn--ghost" onClick={() => navigate("/app/scan")}>
          {t("wineCard.backToScan")}
        </button>
      </div>
    );
  }

  return (
    <div className="screen container stack">
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => navigate("/app/scan")}>
        {t("wineCard.backToScan")}
      </button>

      <WineCardContent wine={wine} titleAs="h1" />

      {wine.similar && wine.similar.length > 0 && (
        <div className="stack">
          <h2>{t("wineCard.similarTitle")}</h2>
          <div className="row">
            {wine.similar.map((id) => (
              <button key={id} type="button" className="chip" onClick={() => handleSimilarClick(id)}>
                {id}
              </button>
            ))}
          </div>
        </div>
      )}

      <button type="button" className="btn btn--secondary" onClick={handleAskSomelier}>
        {t("wineCard.askSomelier")}
      </button>
    </div>
  );
}
