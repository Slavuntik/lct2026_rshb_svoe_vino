import { SensoryVectorView } from "./SensoryVectorView";
import { WineImage } from "./WineImage";
import { useI18n } from "../i18n";
import { track } from "../lib/analytics";
import type { WineCardResponse } from "../lib/apiTypes";

interface WineCardContentProps {
  wine: WineCardResponse;
  /** WineCardScreen — заголовок страницы (h1); инлайн-результат скана — подзаголовок (h2). */
  titleAs?: "h1" | "h2";
}

/**
 * "Внутренности" карточки вина — общие для WineCardScreen (переход по /app/wine/:id) и
 * инлайн-результата фотоскана (кейс ЛЦТ: карточка появляется сразу на экране скана, без
 * отдельного перехода — см. ScanScreen.tsx). Вынесено, чтобы не дублировать разметку.
 */
export function WineCardContent({ wine, titleAs = "h1" }: WineCardContentProps) {
  const { t } = useI18n();
  const { source, derived } = wine;
  const TitleTag = titleAs;

  function handleSourceLinkClick() {
    track("source_link_clicked", { wine_id: wine.wine_id });
  }

  return (
    <>
      <div className="card stack">
        <WineImage src={source.image_url} alt={source.name} width={96} className="wine-card-image" />
        <TitleTag className="screen__title">{source.name}</TitleTag>
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
    </>
  );
}
