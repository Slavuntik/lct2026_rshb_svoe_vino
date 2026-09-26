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

/**
 * Мелкие цветные иконки-бейджи у характеристик вина — перенос дизайна из
 * design/ui-prototype/assets/brand (icon-percent.svg/icon-thermometer.svg), см.
 * reports/frontend-design-transfer.md. Прототип перепутал местами подписи (иконка процента
 * стояла у температуры подачи, термометра — у крепости в про центах) — здесь сопоставление
 * исправлено: процент → крепость (abv_percent), термометр → температура подачи.
 * fill="var(--bg)" — тот же приём контраста, что .btn--primary (белый в светлой теме,
 * тёмный в тёмной, потому что --accent в тёмной теме нарочно светлее), а не хардкод "white".
 */
function AbvIcon() {
  return (
    <svg viewBox="0 0 24 24" width={14} height={14} aria-hidden="true">
      <path
        d="M18.5547 2.16806C19.0142 2.47441 19.1384 3.09528 18.832 3.55481L6.83203 21.5548C6.52568 22.0143 5.90481 22.1385 5.44528 21.8322C4.98576 21.5258 4.86158 20.9049 5.16793 20.4454L17.1679 2.44541C17.4743 1.98588 18.0952 1.8617 18.5547 2.16806Z"
        fill="var(--bg)"
      />
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M7 11C9.20914 11 11 9.20914 11 7C11 4.79086 9.20914 3 7 3C4.79086 3 3 4.79086 3 7C3 9.20914 4.79086 11 7 11ZM7 9C8.10457 9 9 8.10457 9 7C9 5.89543 8.10457 5 7 5C5.89543 5 5 5.89543 5 7C5 8.10457 5.89543 9 7 9Z"
        fill="var(--bg)"
      />
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M21 17C21 19.2091 19.2091 21 17 21C14.7909 21 13 19.2091 13 17C13 14.7909 14.7909 13 17 13C19.2091 13 21 14.7909 21 17ZM19 17C19 18.1046 18.1046 19 17 19C15.8954 19 15 18.1046 15 17C15 15.8954 15.8954 15 17 15C18.1046 15 19 15.8954 19 17Z"
        fill="var(--bg)"
      />
    </svg>
  );
}

function ServingTempIcon() {
  return (
    <svg viewBox="0 0 24 24" width={14} height={14} aria-hidden="true">
      <path d="M7 18C7.55228 18 8 17.5523 8 17C8 16.4477 7.55228 16 7 16C6.44772 16 6 16.4477 6 17C6 17.5523 6.44772 18 7 18Z" fill="var(--bg)" />
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M21.2071 2.79301C19.988 1.57387 18.0096 1.5795 16.7974 2.80557L7.66375 12.0439C7.44626 12.015 7.22467 12.0001 7 12.0001C4.23858 12.0001 2 14.2387 2 17.0001C2 19.7615 4.23858 22.0001 7 22.0001C9.76142 22.0001 12 19.7615 12 17.0001C12 16.7754 11.9851 16.5539 11.9562 16.3364L21.1945 7.20267C22.4206 5.99049 22.4262 4.01215 21.2071 2.79301ZM18.2197 4.2117C18.6521 3.77429 19.358 3.77228 19.7929 4.20722C20.2278 4.64217 20.2258 5.34797 19.7884 5.78043L16.3557 9.17423H13.3134L18.2197 4.2117ZM11.336 11.1742H14.3328L10.1709 15.289C9.91792 15.5391 9.81687 15.9048 9.90555 16.2494C9.96703 16.4883 10 16.7396 10 17.0001C10 18.657 8.65685 20.0001 7 20.0001C5.34315 20.0001 4 18.657 4 17.0001C4 15.3433 5.34315 14.0001 7 14.0001C7.26051 14.0001 7.51187 14.0331 7.75074 14.0946C8.09528 14.1832 8.46099 14.0822 8.71112 13.8292L11.336 11.1742Z"
        fill="var(--bg)"
      />
    </svg>
  );
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

        {/* Виноградник — декоративная фотография переноса дизайна (design/ui-prototype/assets/
            brand/vineyard.png, сжато под reports/frontend-design-transfer.md). Один и тот же
            снимок для любой карточки — это настроение/воздух между текстом, а не иллюстрация
            конкретного региона, поэтому alt="" (декоративная, не информативная картинка). */}
        <img src="/brand/vineyard.jpg" alt="" className="wine-vineyard" />

        <dl className="stack stack--tight">
          <div className="row row--between">
            {/* .wine-fact-row — на самом <dt>, не на обёртке: иначе getByText(...).closest("div")
                в WineCardContent.test.tsx находит новый div вместо исходного row--between и
                перестаёт видеть соседний <dd> (см. reports/frontend-design-transfer.md). */}
            <dt className="text-small wine-fact-row">
              <img src="/brand/thumb-grape.jpg" alt="" className="fact-thumb" />
              {t("wineCard.grapesLabel")}
            </dt>
            <dd>{source.grapes.join(", ")}</dd>
          </div>
          <div className="row row--between">
            <dt className="text-small wine-fact-row">
              <img src="/brand/thumb-region.jpg" alt="" className="fact-thumb" />
              {t("wineCard.regionLabel")}
            </dt>
            <dd>{source.region_name}</dd>
          </div>
          {source.abv_percent != null && (
            <div className="row row--between">
              <dt className="text-small wine-fact-row">
                <span className="icon-badge" aria-hidden="true">
                  <AbvIcon />
                </span>
                {t("wineCard.abvLabel")}
              </dt>
              <dd className="text-mono">{source.abv_percent}%</dd>
            </div>
          )}
          {source.serving_temp_c && (
            <div className="row row--between">
              <dt className="text-small wine-fact-row">
                <span className="icon-badge" aria-hidden="true">
                  <ServingTempIcon />
                </span>
                {t("wineCard.servingTempLabel")}
              </dt>
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
