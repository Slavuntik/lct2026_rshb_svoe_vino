import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { SensoryVectorView } from "../../components/SensoryVectorView";
import { WineImage } from "../../components/WineImage";
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

  function handleSourceLinkClick() {
    if (wine) track("source_link_clicked", { wine_id: wine.wine_id });
  }

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

  const { source, derived } = wine;

  return (
    <div className="screen container stack">
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => navigate("/app/scan")}>
        {t("wineCard.backToScan")}
      </button>

      <div className="card stack">
        <WineImage src={source.image_url} alt={source.name} width={96} className="wine-card-image" />
        <h1 className="screen__title">{source.name}</h1>
        <p className="screen__subtitle">
          {source.winery_name} · {source.region_name}
        </p>

        <div className="row">
          <span className="badge">{source.color}</span>
          <span className="badge">{source.sugar_category}</span>
          {source.vintage && <span className="badge text-mono">{source.vintage}</span>}
        </div>

        <p>{source.description}</p>

        <dl className="stack stack--tight">
          <div className="row row--between">
            <dt className="text-small">{t("wineCard.grapesLabel")}</dt>
            <dd>{source.grapes.join(", ")}</dd>
          </div>
          <div className="row row--between">
            <dt className="text-small">{t("wineCard.regionLabel")}</dt>
            <dd>{source.region_name}</dd>
          </div>
          <div className="row row--between">
            <dt className="text-small">{t("wineCard.abvLabel")}</dt>
            <dd className="text-mono">{source.abv_percent}%</dd>
          </div>
          <div className="row row--between">
            <dt className="text-small">{t("wineCard.servingTempLabel")}</dt>
            <dd className="text-mono">
              {source.serving_temp_c[0]}–{source.serving_temp_c[1]}°C
            </dd>
          </div>
        </dl>

        <div>
          <p className="field__label">{t("wineCard.foodPairingsLabel")}</p>
          <div className="row">
            {source.food_pairings.map((pairing) => (
              <span key={pairing} className="chip">
                {pairing}
              </span>
            ))}
          </div>
        </div>

        <a
          className="btn btn--ghost"
          href={wine.source_url}
          target="_blank"
          rel="noreferrer noopener"
          onClick={handleSourceLinkClick}
        >
          {t("wineCard.sourceLink")}
        </a>
      </div>

      <div className="card stack">
        <h2>{t("wineCard.sensoryTitle")}</h2>
        <SensoryVectorView vector={derived.sensory} />
      </div>

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
