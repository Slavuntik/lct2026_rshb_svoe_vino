import { useEffect, useState } from "react";
import { FoodPairingCarousel } from "./FoodPairingCarousel";
import { SensoryVectorView } from "./SensoryVectorView";
import { SomelierCardWidget } from "./SomelierCardWidget";
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

/**
 * Круглая метка-«миниатюра» третьей строки фактов («Категория и цвет» — задача тимлида
 * 27.09, придирчивая сверка с Figma): в макете там своя фотография-миниатюра, как у сорта/
 * региона, но у нас нет третьего фото-ассета под неё (только thumb-grape.jpg/thumb-region.jpg,
 * заводить новый файл без утверждённого кадра — выдумывать материал). Вместо фото — закрашенный
 * кружок того же размера/посадки (.fact-thumb-icon рядом с .fact-thumb в global.css): честная
 * заглушка «тут признак цвета», не случайная фотография не по теме.
 */
function CategoryColorIcon() {
  return (
    <svg viewBox="0 0 24 24" width={14} height={14} aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill="var(--accent)" />
    </svg>
  );
}

/**
 * Иконка «миска с паром» у заголовка «К чему подать» — перенос макета Figma (карточка вина,
 * секция «Сочетание с блюдами», задача тимлида 27.09, reports/frontend-card-widgets.md).
 * Тот же контур, что DishModeIcon в ScanScreen.tsx (design/ui-prototype/assets/brand/
 * icon-bowl.svg) — файлы не шарят локальные иконки друг с другом (см. AbvIcon/ServingTempIcon
 * выше), поэтому здесь свой экземпляр, currentColor наследует var(--on-accent) из кружка.
 */
function PairingIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} aria-hidden="true">
      <path
        d="M16.8946 1.55305C16.6476 1.05906 16.0469 0.858805 15.5529 1.10577C15.0589 1.35274 14.8587 1.9534 15.1057 2.44739C15.4155 3.06715 15.2947 3.69869 15.0299 4.75769L15.0063 4.85195C14.7709 5.78878 14.4383 7.1126 15.1057 8.44743C15.3527 8.94141 15.9533 9.14164 16.4473 8.89465C16.9413 8.64766 17.1415 8.04698 16.8945 7.55301C16.5846 6.93325 16.7055 6.30172 16.9702 5.24274L16.9939 5.14841C17.2293 4.21161 17.5619 2.88784 16.8946 1.55305Z"
        fill="currentColor"
      />
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M1 12C1 15.2469 2.63797 17.6068 4.99902 19.4904V22C4.99902 22.5523 5.44673 23 5.99902 23H18C18.5522 23 18.9998 22.5525 19 22.0003L19.0008 19.4906C21.362 17.6069 23 15.2469 23 12C23 11.4477 22.5523 11 22 11H2C1.44772 11 1 11.4477 1 12ZM6.33842 18C4.51237 16.5679 3.35682 14.9888 3.0701 13H20.9299C20.6432 14.9888 19.4876 16.5679 17.6616 18H6.33842ZM6.99902 20H17.0007L17.0003 21H6.99902V20Z"
        fill="currentColor"
      />
      <path
        d="M11.5529 1.10577C12.0469 0.858805 12.6476 1.05906 12.8945 1.55305C13.5619 2.88785 13.2292 4.21163 12.9938 5.14843L12.9701 5.24277C12.7054 6.30175 12.5845 6.93327 12.8944 7.55301C13.1414 8.04698 12.9412 8.64766 12.4472 8.89465C11.9532 9.14164 11.3525 8.94141 11.1055 8.44743C10.4381 7.11258 10.7708 5.78875 11.0062 4.85193L11.0298 4.75767C11.2946 3.69866 11.4155 3.06714 11.1056 2.44739C10.8587 1.9534 11.0589 1.35274 11.5529 1.10577Z"
        fill="currentColor"
      />
      <path
        d="M8.89472 1.55305C8.64775 1.05906 8.04709 0.858805 7.5531 1.10577C7.05911 1.35274 6.85886 1.9534 7.10583 2.44739C7.4157 3.06721 7.29488 3.69879 7.03024 4.75778L7.00661 4.85202C6.7713 5.78886 6.43881 7.11264 7.1062 8.44743C7.35319 8.94141 7.95387 9.14164 8.44784 8.89465C8.94182 8.64766 9.14205 8.04698 8.89506 7.55301C8.58515 6.9332 8.70595 6.30162 8.97058 5.24266L8.99423 5.14834C9.22953 4.21152 9.56202 2.88779 8.89472 1.55305Z"
        fill="currentColor"
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
      <div className="food-pairing__header">
        <span className="food-pairing__icon" aria-hidden="true">
          <PairingIcon />
        </span>
        <h2 className="card-heading">{t("wineCard.pairingsTitle")}</h2>
      </div>
      {state.status === "loading" && <p className="text-small">{t("wineCard.pairingsLoading")}</p>}
      {state.status === "failed" && <p className="field__error">{t("common.errorGeneric")}</p>}
      {state.status === "empty" && <p className="text-small">{state.message}</p>}
      {state.status === "ready" && (
        <>
          {sourceCaption && <p className="text-caption">{t(sourceCaption)}</p>}
          {/* Карусель фото/типографских плиток — задача тимлида 27.09 (макет Figma), заменяет
              прежний ряд текстовых .chip на FoodPairingCarousel.tsx (см. там же обоснование
              выбора фото по тегу и границы "не выдумывать"). */}
          <FoodPairingCarousel pairings={state.pairings} />
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
        {/* Заголовочный блок (задача тимлида 27.09, вторая правка после прямого просмотра
            макета и стенда тимлидом): в Figma «карточка вина» сверху название, под ним
            винодельня и плашка — и только НИЖЕ крупное фото бутылки; весь этот блок выровнен
            по центру, не по левому краю. Было наоборот (фото → название, всё по левому краю) —
            и порядок, и выравнивание расходились с макетом, это правится здесь. */}
        <div className="wine-card-header">
          <TitleTag className="screen__title">{source.name}</TitleTag>
          <p className="screen__subtitle">
            {source.winery_name} · {source.region_name}
          </p>
        </div>

        <WineImage src={source.image_url} alt={source.name} width={96} className="wine-card-image" />

        {/* Порядок блоков карточки (задача тимлида 27.09, придирчивая сверка с Figma «карточка
            вина»): факты → фото виноградника → плитки характеристик → описание — буквально
            как в макете (ранее описание стояло сразу после бейджей, ДО фото/фактов/плиток —
            расхождение с макетом, не осознанное отступление, здесь исправлено).
            Порядок строк факт-блока — Регион → Сорт → Категория и цвет, как в макете (было
            Сорт → Регион). Прежние отдельные чипы «цвет / сахарная категория / винтаж» под
            названием — вторая правка тимлида (прямой просмотр макета и стенда, 27.09): в
            Figma этой строки чипов нет вовсе, их место — третья строка факт-блока ниже
            («Категория и цвет», значение — цвет+сахарная категория одной фразой, как в
            макете "Белое сухое"). Год урожая (vintage) в макете отдельно не показан —
            выдумывать для него новую строку не стали (нет образца), обычно виден в названии.*/}
        <dl className="stack stack--tight">
          {/* .wine-fact-row — на самом <dt>, не на обёртке: иначе getByText(...).closest("div")
              в WineCardContent.test.tsx находит новый div вместо исходного row--between и
              перестаёт видеть соседний <dd> (см. reports/frontend-design-transfer.md). */}
          <div className="row row--between">
            <dt className="text-small wine-fact-row">
              <img src="/brand/thumb-region.jpg" alt="" className="fact-thumb" />
              {t("wineCard.regionLabel")}
            </dt>
            <dd>{source.region_name}</dd>
          </div>
          <div className="row row--between">
            <dt className="text-small wine-fact-row">
              <img src="/brand/thumb-grape.jpg" alt="" className="fact-thumb" />
              {t("wineCard.grapesLabel")}
            </dt>
            <dd>{source.grapes.join(", ")}</dd>
          </div>
          <div className="row row--between">
            <dt className="text-small wine-fact-row">
              <span className="fact-thumb-icon" aria-hidden="true">
                <CategoryColorIcon />
              </span>
              {t("wineCard.categoryColorLabel")}
            </dt>
            {/* sugar_category (обычная карточка) — только слово сахарной категории ("сухое"),
                цвет к нему приписываем сами. category (карточка-фолбэк каталога кейса, когда
                sugar_category нет вовсе, v0.4.11) — ГОТОВАЯ фраза "цвет+категория" ("красное
                сухое", видно на фикстуре WineCardContent.test.tsx) — приписывать цвет второй
                раз нельзя, иначе на экране дублируется слово. */}
            <dd>{source.sugar_category ? `${source.color} ${source.sugar_category}` : (source.category ?? source.color)}</dd>
          </div>
        </dl>

        {/* Виноградник — декоративная фотография переноса дизайна (design/ui-prototype/assets/
            brand/vineyard.png, сжато под reports/frontend-design-transfer.md). Один и тот же
            снимок для любой карточки — это настроение/воздух между текстом, а не иллюстрация
            конкретного региона, поэтому alt="" (декоративная, не информативная картинка). */}
        <img src="/brand/vineyard.jpg" alt="" className="wine-vineyard" />

        {/* Плитки температуры подачи / крепости — Figma «карточка вина»: 2 колонки, значок-
            кружок + подпись + крупное значение, порядок температура→крепость (задача тимлида
            27.09, reports/frontend-figma-restyle.md). Раньше это были строки dt/dd внутри dl
            рядом с сортом/регионом — вынесены в отдельный блок (не строка таблицы, а плитка
            с крупным числом), поэтому здесь обычные span/p, не dt/dd вне своего <dl>.
            WineCardContent.test.tsx ищет ближайший <div> к тексту "подача" и проверяет, что
            тот же div содержит "°C" — .spec-tile ниже как раз такой общий div-предок. */}
        {(source.abv_percent != null || source.serving_temp_c) && (
          <div className="spec-tiles">
            {source.serving_temp_c && (
              <div className="spec-tile">
                <span className="icon-badge" aria-hidden="true">
                  <ServingTempIcon />
                </span>
                <span className="spec-tile__label">{t("wineCard.servingTempLabel")}</span>
                <p className="spec-tile__value text-mono">
                  {source.serving_temp_c[0]}–{source.serving_temp_c[1]}°C
                </p>
              </div>
            )}
            {source.abv_percent != null && (
              <div className="spec-tile">
                <span className="icon-badge" aria-hidden="true">
                  <AbvIcon />
                </span>
                <span className="spec-tile__label">{t("wineCard.abvLabel")}</span>
                <p className="spec-tile__value text-mono">{source.abv_percent}%</p>
              </div>
            )}
          </div>
        )}

        <p>{source.description}</p>

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
          {/* .card-heading (global.css) — задача тимлида 27.09, блокировка снята после того как
             второй агент закончил спец-плитки и ярлык скана: заголовок секции внутри карточки
             мельче страничного h1, та же правка, что на профиле/паспорте вкуса/онбординге.
             Композиция карточки (порядок блоков, паддинги) не менялась — только размер текста. */}
          <h2 className="card-heading">{t("wineCard.sensoryTitle")}</h2>
          <SensoryVectorView vector={derived.sensory} />
        </div>
      )}

      <WinePairingsBlock wineId={wine.wine_id} />

      {/* Виджет сомелье встроен прямо в карточку (задача тимлида 27.09, макет Figma «Цифровой
          сомелье») — компактный аналог отдельного /app/chat, см. SomelierCardWidget.tsx. */}
      <SomelierCardWidget wineId={wine.wine_id} />
    </>
  );
}
