import { useEffect, useState } from "react";
import { SensoryVectorView } from "./SensoryVectorView";
import { WineImage } from "./WineImage";
import { useI18n, type DictionaryPath } from "../i18n";
import { track } from "../lib/analytics";
import { apiClient } from "../lib/apiClient";
import type { WineCardResponse, WinePairing, WinePairingsBasis } from "../lib/apiTypes";

interface WineCardContentProps {
  wine: WineCardResponse;
  /** WineCardScreen — заголовок страницы (h1); инлайн-результат скана — подзаголовок (h2). */
  titleAs?: "h1" | "h2";
}

/**
 * v0.4.11 (contracts/image-scan.md): source_url и наших вин, и карточки-фолбэка каталога
 * кейса указывает на vino-svoe.ru — условие по хосту ссылки, а не по происхождению карточки.
 * На кривой/пустой source_url — молча false (общая ссылка «Первоисточник» всё равно рабочая).
 */
function isVinoSvoeUrl(sourceUrl: string): boolean {
  try {
    const host = new URL(sourceUrl).hostname.toLowerCase();
    return host === "vino-svoe.ru" || host.endsWith(".vino-svoe.ru");
  } catch {
    return false;
  }
}

type PairingsState =
  | { status: "loading" }
  | { status: "ready"; pairings: WinePairing[]; basis: WinePairingsBasis }
  | { status: "empty"; message: string }
  | { status: "failed" };

const PAIRINGS_SOURCE_CAPTION: Partial<Record<WinePairingsBasis, DictionaryPath>> = {
  catalog: "wineCard.pairingsSourceCatalog",
  sensory: "wineCard.pairingsSourceSensory",
  heuristic: "wineCard.pairingsSourceHeuristic",
};

/**
 * v0.3.3 (contracts/post-scan.md v1.0, задача тимлида 22.09 п.1а): «К чему подать» —
 * GET /wines/{id}/pairings, три уровня basis с честной подписью источника (фронт не гадает,
 * откуда теги — catalog/sensory/heuristic каждый несёт свою подпись). Контракт: pairings=[] ⟺
 * message непусто — показываем message текстом (это покрывает и basis=unavailable), не молчим
 * и не прячем блок молча. Сетевая ошибка — отдельное явное состояние (common.errorGeneric),
 * не вечный "Подбираем…". Один и тот же блок для WineCardScreen и инлайн-результата скана —
 * WineCardContent уже общий код-путь для обоих экранов, отдельного дублирования не нужно.
 *
 * Решение тимлида (22.09, после отчёта frontend): старый статический рендер
 * source.food_pairings ("Сочетания") убран из карточки — этот блок при basis=catalog
 * показывает тот же список с честной подписью источника, второй копии не нужно. Раньше
 * architect фиксировал в contracts/post-scan.md §1 "новый эндпоинт не подменяет этот
 * рендер" — тимлид снял этот запрет отдельным сообщением, контракт не редактировался
 * (не моя зона), но фактическое поведение UI уже соответствует новому решению.
 */
function WinePairingsBlock({ wineId }: { wineId: string }) {
  const { t } = useI18n();
  const [state, setState] = useState<PairingsState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    apiClient
      .getWinePairings(wineId)
      .then((response) => {
        if (cancelled) return;
        if (response.pairings.length > 0) {
          setState({ status: "ready", pairings: response.pairings, basis: response.basis });
        } else {
          setState({ status: "empty", message: response.message || t("wineCard.pairingsEmptyFallback") });
        }
      })
      .catch(() => {
        if (!cancelled) setState({ status: "failed" });
      });
    return () => {
      cancelled = true;
    };
    // t() сознательно не в зависимостях — не перезапрашиваем на смену локали "на лету"
    // (её сейчас и не бывает), только на смену самого вина.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wineId]);

  const sourceCaption = state.status === "ready" ? PAIRINGS_SOURCE_CAPTION[state.basis] : undefined;

  return (
    <div className="card stack" data-testid="wine-pairings-block">
      <h2>{t("wineCard.pairingsTitle")}</h2>
      {state.status === "loading" && <p className="text-small">{t("wineCard.pairingsLoading")}</p>}
      {state.status === "failed" && <p className="field__error">{t("common.errorGeneric")}</p>}
      {state.status === "empty" && <p className="text-small">{state.message}</p>}
      {state.status === "ready" && (
        <>
          {sourceCaption && <p className="text-caption">{t(sourceCaption)}</p>}
          <div className="row">
            {state.pairings.map((pairing) => (
              <span key={pairing.tag} className="chip">
                {pairing.tag}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * "Внутренности" карточки вина — общие для WineCardScreen (переход по /app/wine/:id) и
 * инлайн-результата фотоскана (кейс ЛЦТ: карточка появляется сразу на экране скана, без
 * отдельного перехода — см. ScanScreen.tsx). Вынесено, чтобы не дублировать разметку.
 *
 * Защитный рендер (v0.4.11): карточка-фолбэк каталога кейса (слага нет в нашем RAG) несёт
 * ТОЛЬКО {name, winery_name, region_name, grapes, color, category, description, image_url}
 * в source и буквально {} в derived — уже, чем наш обычный WineCardResponse. apiClient
 * доверяет payload'у без рантайм-валидации (apiClient.ts: `payload as T`), поэтому именно
 * здесь, на границе рендера, поля вне этого подмножества показываются только если реально
 * пришли — без выдумки нулей/дефолтов и без падения экрана.
 */
export function WineCardContent({ wine, titleAs = "h1" }: WineCardContentProps) {
  const { t } = useI18n();
  const { source, derived } = wine;
  const TitleTag = titleAs;
  const sourceLinkLabel = isVinoSvoeUrl(wine.source_url) ? t("wineCard.sourceLinkVinoSvoe") : t("wineCard.sourceLink");

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
          {/* sugar_category отсутствует у карточки-фолбэка каталога кейса (v0.4.11) — category её честная замена, когда она есть. */}
          {source.sugar_category && <span className="badge">{source.sugar_category}</span>}
          {source.category && <span className="badge">{source.category}</span>}
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
          {source.abv_percent != null && (
            <div className="row row--between">
              <dt className="text-small">{t("wineCard.abvLabel")}</dt>
              <dd className="text-mono">{source.abv_percent}%</dd>
            </div>
          )}
          {source.serving_temp_c && (
            <div className="row row--between">
              <dt className="text-small">{t("wineCard.servingTempLabel")}</dt>
              <dd className="text-mono">
                {source.serving_temp_c[0]}–{source.serving_temp_c[1]}°C
              </dd>
            </div>
          )}
        </dl>

        <a
          className="btn btn--ghost"
          href={wine.source_url}
          target="_blank"
          rel="noreferrer noopener"
          onClick={handleSourceLinkClick}
        >
          {sourceLinkLabel}
        </a>
      </div>

      {derived.sensory && (
        <div className="card stack">
          <h2>{t("wineCard.sensoryTitle")}</h2>
          <SensoryVectorView vector={derived.sensory} />
        </div>
      )}

      <WinePairingsBlock wineId={wine.wine_id} />
    </>
  );
}
