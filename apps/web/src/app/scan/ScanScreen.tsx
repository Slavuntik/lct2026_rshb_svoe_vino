import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { WineCardContent } from "../../components/WineCardContent";
import { WineImage } from "../../components/WineImage";
import { useI18n, type DictionaryPath } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient, ApiRequestError } from "../../lib/apiClient";
import type {
  AnalogStyle,
  AnalogWine,
  DishPairingResponse,
  PairingWineItem,
  ScanCandidateWine,
  ScanMatch,
  ScanPhotoRichResponse,
  ScanResolveResponse,
  WineSource,
} from "../../lib/apiTypes";
import { focusSomelierWidget } from "../../lib/somelierWidget";
import { storage } from "../../lib/storage";
import { AimBox, DEFAULT_FRAME, type Frame } from "./AimBox";

type ResolveOutcome = {
  matches: ScanMatch[];
  lowConfidence: boolean;
  /** B7: заполнено только когда matches пуст — российские аналоги по стилю из текста. */
  analogs: AnalogWine[];
  analogReason: string | null;
} | null;

/**
 * v0.4.11: тот же компонент для similar/analogs (AnalogWine, без фото) и для candidates
 * (ScanCandidateWine, с фото) — "in" различает форму без отдельного пропа-дискриминатора.
 *
 * Плитка каталога (задача тимлида 27.09, придирчивая сверка с макетом Figma): «Найдено по
 * прямому попаданию» → «Нашли ещё N бутылок с похожей этикеткой» и «Если не нашлось» →
 * «Аналоги» в макете — тот же 2-колоночный грид карточек 163px/radius 30, что и экран
 * «Каталог вин» (сверено в Figma напрямую: одинаковый компонент переиспользован дизайнером
 * во всех трёх местах), а не построчный список. Переиспользуем классы `catalog-tile*`
 * (CatalogScreen.tsx) один в один — тот же визуальный компонент, тот же CSS, никакого
 * нового стиля изобретать не пришлось; `.match-item`/`.match-list` остаются для чата
 * (узкий пузырь `.chat-bubble`, где грид с крупными фото не поместится) и карточек блюда.
 */
function WineResultChip({ wine, onClick }: { wine: AnalogWine | ScanCandidateWine; onClick: () => void }) {
  const imageUrl = "image_url" in wine ? wine.image_url : undefined;
  return (
    <button type="button" className="catalog-tile" onClick={onClick}>
      <span className="catalog-tile__image-frame">
        <WineImage src={imageUrl ?? undefined} alt={wine.name} width={110} className="catalog-tile__image" />
      </span>
      <span className="catalog-tile__name">{wine.name}</span>
      {wine.winery_name && <span className="catalog-tile__winery">{wine.winery_name}</span>}
    </button>
  );
}

/**
 * Задача тимлида 22.09 («Что подать» по фото блюда), п.1 UI: «список вин: карточки со
 * ссылкой на внутреннюю карточку /app/wine/:wineId и reason». Клик — внутренняя навигация
 * приложения (goToWine), не href: правило ссылок п.2 (ратифицировано в contracts/
 * post-scan.md v1.1 §5) — внешняя ссылка на портал остаётся только внутри самой карточки
 * вина (WineCardContent), нигде в списках. image_url у PairingWineItem не required
 * (openapi 0.3.4) — WineImage сам деградирует на плейсхолдер при отсутствии/битой ссылке.
 */
function DishWineCard({ wine, onClick }: { wine: PairingWineItem; onClick: () => void }) {
  return (
    <button type="button" className="match-item" onClick={onClick}>
      <span className="match-item__main">
        <WineImage src={wine.image_url ?? undefined} alt={wine.name} width={40} className="match-item__thumb" />
        <span className="stack stack--tight">
          <span>
            {wine.name} · {wine.winery}
          </span>
          <span className="row">
            {wine.color && <span className="badge">{wine.color}</span>}
            {wine.sugar && <span className="badge">{wine.sugar}</span>}
          </span>
          {wine.reason && <span className="text-caption">{wine.reason}</span>}
        </span>
      </span>
    </button>
  );
}

// 9 категорий блюд — те же ключи, что `portal_tag_defaults` в pipeline/ref/
// food_pairing_rules.yaml (mocks/fixtures/dishPairing.ts::DISH_CATEGORIES зеркалит эти же
// строки из словаря ru.ts, единый источник — см. комментарий там). Значение, уходящее в
// POST /v1/pairing/dish как "category", — это САМ переведённый текст чипа, не отдельный слаг.
const DISH_CATEGORY_KEYS: DictionaryPath[] = [
  "scan.dishCategoryOysters",
  "scan.dishCategoryCheese",
  "scan.dishCategoryFish",
  "scan.dishCategoryPoultry",
  "scan.dishCategorySalads",
  "scan.dishCategoryBruschetta",
  "scan.dishCategoryBbq",
  "scan.dishCategoryAsian",
  "scan.dishCategoryDesserts",
];

/**
 * Чипы категорий блюда — три вызова: (1) ручной выбор без фото и (2) статус "unsure" без
 * догадки — все 9 тегов (categories не задан); (3) alternatives (и у "food", и у "unsure"
 * с слабой догадкой) — ИМЕННО переданный список, не все 9. `alternatives` в контракте —
 * это 0–3 ДРУГИХ тега из тех же 9 portal_tag_defaults (НЕ альтернативные названия блюда,
 * contracts/post-scan.md v1.1 §4.1/§4.4) — чипы одной природы с ручным выбором, поэтому один
 * общий компонент и один и тот же POST /v1/pairing/dish {category} без поля dish.
 */
function DishCategoryChips({ categories, onSelect }: { categories?: string[]; onSelect: (category: string) => void }) {
  const { t } = useI18n();
  const items = categories ?? DISH_CATEGORY_KEYS.map((key) => t(key));
  return (
    <div className="row" data-testid="dish-category-chips">
      {items.map((category) => (
        <button key={category} type="button" className="chip" onClick={() => onSelect(category)}>
          {category}
        </button>
      ))}
    </div>
  );
}

type TasteAnalogsState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; style: AnalogStyle; wines: AnalogWine[] }
  | { status: "failed"; message: string };

type ScanMode = "bottle" | "dish";

/**
 * Мелкие иконки переключателя «Бутылка | Блюдо» (перенос дизайна из design/ui-prototype,
 * см. reports/frontend-design-transfer.md). fill="currentColor" — свои же токены: чип
 * наследует цвет текста и в обычном, и в выбранном (aria-pressed) состоянии, хардкода нет.
 * Силуэт бутылки — тот же path, что заглушка WineImage.tsx (components/WineImage.tsx),
 * контур блюда — из design/ui-prototype/assets/brand/icon-bowl.svg (перерисован в currentColor).
 */
function BottleModeIcon() {
  return (
    <svg viewBox="0 0 120 320" width={9} height={24} aria-hidden="true" className="chip__icon">
      <path
        d="M50 10h20v40l14 24v220a10 10 0 0 1-10 10H46a10 10 0 0 1-10-10V74l14-24z"
        fill="currentColor"
      />
    </svg>
  );
}

function DishModeIcon() {
  return (
    <svg viewBox="0 0 24 24" width={14} height={14} aria-hidden="true" className="chip__icon">
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

/**
 * v0.3.3 (contracts/post-scan.md v1.0 §2.1): «Похоже по вкусу» — POST /v1/analogs
 * (существующая ручка "аналог импортного", без нового API) по данным уже полученной
 * карточки: сорта через запятую, иначе название; при 404 и >1 сорте — один повторный
 * запрос по первому сорту (часто резолвится лучше купажа целиком, resolve_style — rapidfuzz
 * против имён 146 стилей). Не путать с scan.analogsTitle — тот блок приходит готовым в
 * ответе /scan/photo, этот — отдельный запрос по стилю данного вина.
 */
async function resolveTasteAnalogs(source: WineSource) {
  const grapes = source.grapes ?? [];
  const query = grapes.length > 0 ? grapes.join(", ") : source.name;
  try {
    return await apiClient.postAnalogs({ query });
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found" && grapes.length > 1) {
      return apiClient.postAnalogs({ query: grapes[0] });
    }
    throw error;
  }
}

/**
 * Экран 2/6 — скан этикетки. Кейс ЛЦТ («Своё Вино», РСХБ.Цифра): фото — ПЕРВИЧНЫЙ путь
 * (CV-поиск по фото, POST /v1/scan/photo, contracts/image-scan.md v0.4), текст — запасной
 * вход через прежний /scan/resolve. Результат фото — ОДНА карточка сразу, без экрана
 * вариантов; confidence в ответе есть (для API/метрик по ТЗ), в UI не показывается умышленно.
 *
 * Раньше (ревью 02) фото на iOS уходило через нативный OCR-плагин в текст, на вебе — в
 * /scan/ocr (501). Кейс меняет механизм: CV-поиск по самому изображению происходит на
 * сервере для ЛЮБОЙ платформы, поэтому эта ветка убрана — apps/shell/plugins/ocr-plugin
 * остаётся в дереве (компилируется, есть тесты), просто здесь больше не используется.
 *
 * Задача тимлида 22.09 («Что подать» по фото блюда): переключатель «Бутылка | Блюдо» —
 * точка входа этого же экрана, камера/загрузка общие (handlePhotoSelected ветвится по
 * scanMode). status="bottle" от ручки блюда — мостик обратно в уже готовый bottle-сценарий
 * тем же файлом (handleScanAsBottle шлёт то же фото в /v1/scan/photo).
 */
export function ScanScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  // --- режим: бутылка (по умолчанию) | блюдо ---
  const [scanMode, setScanMode] = useState<ScanMode>("bottle");

  // --- фото ---
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [photoStatus, setPhotoStatus] = useState<"idle" | "searching" | "error">("idle");
  const [result, setResult] = useState<ScanPhotoRichResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Уточнение рамкой предлагается после первого поиска, только в режиме бутылки.
  const [aiming, setAiming] = useState(false);
  const [frame, setFrame] = useState<Frame>(DEFAULT_FRAME);

  // --- текст (запасной путь, только режим "бутылка") ---
  const [text, setText] = useState("");
  const [textError, setTextError] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "resolving" | "error">("idle");
  const [outcome, setOutcome] = useState<ResolveOutcome>(null);
  const textSearchRef = useRef<HTMLTextAreaElement>(null);

  // Иконка поиска в шапке (app/nav/TopHeader.tsx) ведёт сюда через navigate("/app/scan#scan-
  // text-search") — задача тимлида 27.09, п.(3). Реагируем на location.hash, а не только на
  // монтаж: с этого экрана уже можно быть, повторный клик по иконке поиска не размонтирует
  // ScanScreen, а react-router всё равно даёт новый location.key на каждый navigate(), даже
  // если итоговый путь+хэш совпадают с текущими — этим и ловим повторный клик.
  useEffect(() => {
    if (location.hash === "#scan-text-search") {
      textSearchRef.current?.focus();
    }
  }, [location.hash, location.key]);

  // --- «Что подать» по фото блюда (задача тимлида 22.09) ---
  // loadingPhoto/loadingCategory разведены задачей тимлида 23.09 (qa-manual-hack-v16.md §2):
  // один "loading" на оба пути давал лоадер "Распознаём блюдо…" даже когда фото не было вовсе
  // (ручной чип категории — обычный запрос /v1/pairing/dish, без единого пикселя на входе).
  const [dishStatus, setDishStatus] = useState<"idle" | "loadingPhoto" | "loadingCategory" | "error">("idle");
  const [dishResult, setDishResult] = useState<DishPairingResponse | null>(null);

  // --- «Похоже по вкусу» (contracts/post-scan.md v1.0 §2) ---
  const [tasteAnalogs, setTasteAnalogs] = useState<TasteAnalogsState>({ status: "idle" });
  const [inYourTaste, setInYourTaste] = useState(false);

  useEffect(() => {
    // Ревокаем object URL превью при смене фото/размонтировании — не копим память.
    return () => {
      if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
    };
  }, [photoPreviewUrl]);

  function goToWine(wineId: string, from: "scan") {
    navigate(`/app/wine/${encodeURIComponent(wineId)}`, { state: { from } });
  }

  async function runBottleScan(selected: File, box?: [number, number, number, number]) {
    setOutcome(null);
    setResult(null);
    setDishResult(null);
    setDishStatus("idle");
    setAiming(false);
    setPhotoStatus("searching");
    track("scan_started", { mode: "web_upload", framed: box !== undefined });

    try {
      const response = await apiClient.scanPhoto(selected, box);
      setResult(response);
      setPhotoStatus("idle");
      track("scan_resolved", {
        matched: !response.not_in_catalog,
        confidence: response.confidence.top1_score,
        wine_id: response.card?.wine_id,
      });
      if (!response.not_in_catalog && response.card) {
        track("wine_card_viewed", { wine_id: response.card.wine_id, from: "scan" });
      }
    } catch {
      setPhotoStatus("error");
    }
  }

  async function runDishPhotoScan(selected: File) {
    setOutcome(null);
    setResult(null);
    setPhotoStatus("idle");
    setDishResult(null);
    setDishStatus("loadingPhoto");
    track("scan_started", { mode: "web_upload" });

    try {
      const response = await apiClient.pairingDishPhoto(selected);
      setDishResult(response);
      setDishStatus("idle");
    } catch {
      setDishStatus("error");
    }
  }

  async function handlePhotoSelected(selected: File) {
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(selected);
    });
    setPhotoFile(selected);
    setFrame(DEFAULT_FRAME);
    setAiming(false);
    if (scanMode === "dish") {
      await runDishPhotoScan(selected);
    } else {
      await runBottleScan(selected);
    }
  }

  function handleFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (selected) void handlePhotoSelected(selected);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) void handlePhotoSelected(dropped);
  }

  function handleResetPhoto() {
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setPhotoFile(null);
    setResult(null);
    setDishResult(null);
    setPhotoStatus("idle");
    setAiming(false);
    setDishStatus("idle");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  /** Переключатель «Бутылка | Блюдо»: смена намерения — честный сброс текущего результата,
   * камера/загрузка остаются теми же элементами (не пересоздаются). */
  function handleModeChange(mode: ScanMode) {
    if (mode === scanMode) return;
    setScanMode(mode);
    handleResetPhoto();
  }

  /** status="bottle" от /v1/pairing/dish-photo: «Похоже на бутылку — отсканировать?» — то же
   * фото, без повторной загрузки, уходит в уже готовый и покрытый тестами сценарий бутылки. */
  async function handleScanAsBottle() {
    if (!photoFile) return;
    setScanMode("bottle");
    await runBottleScan(photoFile);
  }

  /** Чипы alternatives (и у "food", и у "unsure" со слабой догадкой) и категории (unsure без
   * догадки / ручной выбор без фото) — один и тот же запрос POST /v1/pairing/dish {category}.
   * `dish` (свободный текст) в payload намеренно не передаём: alternatives в контракте — это
   * ДРУГИЕ теги категории (contracts/post-scan.md v1.1 §4.1), не альтернативные названия
   * блюда, а свободного текстового поля для ручного ввода названия в этом UI нет (только
   * чипы) — DishPairingPayload.dish остаётся для будущей итерации, если она появится. */
  async function submitDishCorrection(category: string) {
    setDishResult(null);
    setDishStatus("loadingCategory");
    try {
      const response = await apiClient.pairingDish({ category });
      setDishResult(response);
      setDishStatus("idle");
    } catch {
      setDishStatus("error");
    }
  }

  /**
   * Задача тимлида 27.09 (макет Figma): виджет сомелье встроен прямо в инлайн-результат
   * скана (WineCardContent → SomelierCardWidget, рендерится строкой выше) — вместо перехода
   * на отдельный /app/chat кнопка доскролливает до поля вопроса и ставит туда фокус
   * (lib/somelierWidget.ts). wine_id первого вопроса виджет берёт из своего пропа
   * (contracts/openapi.yaml v0.3.5) — навигационный префилл больше не нужен здесь.
   */
  function handleAskSomelierAboutResult() {
    if (!result?.card) return;
    focusSomelierWidget();
  }

  function handleResolveResult(resolved: ScanResolveResponse) {
    const best = resolved.matches[0];
    track("scan_resolved", {
      matched: resolved.matches.length > 0,
      confidence: best?.confidence ?? 0,
      wine_id: best?.wine_id,
    });
    if (resolved.matches.length === 0) {
      setOutcome({
        matches: [],
        lowConfidence: true,
        analogs: resolved.analogs ?? [],
        analogReason: resolved.analog_reason ?? null,
      });
      return;
    }
    if (!resolved.low_confidence) {
      goToWine(best.wine_id, "scan");
      return;
    }
    setOutcome({ matches: resolved.matches, lowConfidence: true, analogs: [], analogReason: null });
  }

  async function handleTextSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!text.trim()) {
      setTextError(t("scan.textEmptyError"));
      return;
    }
    setTextError(null);
    setOutcome(null);
    setStatus("resolving");
    track("scan_started", { mode: "text" });
    try {
      const resolved = await apiClient.scanResolve({ text: text.trim() });
      setStatus("idle");
      handleResolveResult(resolved);
    } catch {
      setStatus("error");
    }
  }

  const confidentCard = result && !result.not_in_catalog && result.card ? result.card : null;
  // dishResult.dish ВСЕГДА объект (контракт), но name/category внутри него — nullable;
  // foodDish!=null означает только "показываем блок еды", не "name/category точно заполнены".
  const foodDish = dishResult && dishResult.status === "food" ? dishResult.dish : null;

  useEffect(() => {
    if (!confidentCard) {
      setTasteAnalogs({ status: "idle" });
      setInYourTaste(false);
      return;
    }
    let cancelled = false;
    setTasteAnalogs({ status: "loading" });
    setInYourTaste(false);
    resolveTasteAnalogs(confidentCard.source)
      .then((response) => {
        if (cancelled) return;
        setTasteAnalogs({ status: "ready", style: response.style, wines: response.wines });
        track("analog_requested", { style_slug: response.style.slug });
        // Пометка «в вашем вкусе» — опционально (contracts/post-scan.md §2.2, не критерий
        // приёмки), только для не-гостя: гостю /taste/profile честно отдаёт 403
        // consent_required, поэтому даже не пробуем — не тратим запрос на заведомый отказ.
        if (storage.getAccountKind() === "guest") return;
        apiClient
          .getTasteProfile()
          .then((profile) => {
            if (!cancelled) setInYourTaste(profile.top_styles.includes(response.style.slug));
          })
          .catch(() => {
            // Нет полного согласия/сеть недоступна — молча не показываем пометку,
            // это необязательное украшение, а не факт, который нужно объяснять ошибкой.
          });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof ApiRequestError ? error.message : t("common.errorGeneric");
        setTasteAnalogs({ status: "failed", message });
      });
    return () => {
      cancelled = true;
    };
    // t/track стабильно не влияют на идентичность эффекта — перезапуск нужен только на
    // смену самой распознанной карточки.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [confidentCard]);

  /** contracts/post-scan.md §2.3: гостю — на апгрейд токена (не дохлый 403 экрана паспорта). */
  function handleTastePassportCta() {
    if (storage.getAccountKind() === "guest") {
      navigate("/app/profile");
    } else {
      navigate("/app/taste");
    }
  }

  return (
    <div className="screen container stack">
      <header className="screen__header">
        {/* Решение тимлида 27.09: заголовок/подзаголовок — та же недоделка, что была в
            иллюстрации дропзоны (bottle-текст «Скан этикетки»/«...этикетку...» просачивался
            в режим «Блюдо»), только выше на экране. */}
        <h1 className="screen__title">{scanMode === "dish" ? t("scan.dishTitle") : t("scan.title")}</h1>
        <p className="screen__subtitle">{scanMode === "dish" ? t("scan.dishSubtitle") : t("scan.subtitle")}</p>
      </header>

      <div className="row" data-testid="scan-mode-toggle">
        <button
          type="button"
          className="chip"
          aria-pressed={scanMode === "bottle"}
          onClick={() => handleModeChange("bottle")}
        >
          <BottleModeIcon />
          {t("scan.modeBottleLabel")}
        </button>
        <button type="button" className="chip" aria-pressed={scanMode === "dish"} onClick={() => handleModeChange("dish")}>
          <DishModeIcon />
          {t("scan.modeDishLabel")}
        </button>
      </div>

      <div
        className={`dropzone${dragActive ? " dropzone--active" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        data-testid="scan-dropzone"
      >
        {photoPreviewUrl && !aiming && (
          <img src={photoPreviewUrl} alt={t("scan.uploadChosen", { name: photoFile?.name ?? "" })} className="dropzone__preview" />
        )}
        {/* Иллюстрация переноса дизайна (design/ui-prototype/assets/brand/scanner.svg) — только
            для режима «Бутылка» и только пока фото не выбрано (декоративная подсказка «сюда
            наводим камеру»). Решение тимлида 27.09: в «Блюдо» эта же иконка бутылки в прицеле
            смотрелась недоделкой (не про то), а dish-meat/dish-fish/dish-snack.png из прототипа
            не подошли — каждая изображает ровно одно конкретное блюдо (мясо/рыбу/овощной
            перекус), фото-стиль вдобавок спорит с линейной иконографией остального экрана
            (scanner.svg, значки режимов, %/термометр на карточке вина) — единой на все блюда
            картинки нет, поэтому режим «Блюдо» остаётся текстовым, без картинки. */}
        {!photoPreviewUrl && scanMode === "bottle" && (
          <img src="/brand/scanner.svg" alt="" width={64} height={65} className="dropzone__illustration" />
        )}
        <p>{dragActive ? t("scan.dropHintActive") : t("scan.dropHint")}</p>
        <p className="text-small">{t("scan.dropOrChoose")}</p>
        <label className="field">
          {/* Задача тимлида 23.09 (qa-manual-hack-v16.md §2): зона загрузки общая для обоих
              режимов, но подпись поля — своя для «Блюдо», не бутылочное «Фото этикетки». */}
          <span className="visually-hidden">{scanMode === "dish" ? t("scan.dishPhotoLabel") : t("scan.photoLabel")}</span>
          {/* capture=environment — подсказка мобильным браузерам открыть камеру сразу */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            onChange={handleFileInputChange}
            className="visually-hidden"
          />
        </label>
        <button type="button" className="btn btn--primary" onClick={() => fileInputRef.current?.click()}>
          {t("scan.choosePhoto")}
        </button>
        <p className="text-caption" style={{ marginTop: "var(--space-3)" }}>
          {scanMode === "dish" ? t("scan.dishPhotoCaption") : t("scan.quietPhotoCaption")}
        </p>
      </div>

      {scanMode === "bottle" && photoStatus === "searching" && <p className="text-small">{t("scan.photoSearching")}</p>}
      {scanMode === "bottle" && photoStatus === "error" && <p className="field__error">{t("scan.photoError")}</p>}
      {/* loadingPhoto — реальное фотораспознавание (VLM/CV), loadingCategory — обычный запрос
          по тегу без единого фото; отдельные подписи не путают пользователя ложным "распознаём"
          (qa-manual-hack-v16.md §2: "Распознаём блюдо…" мелькало и на ручном выборе категории). */}
      {scanMode === "dish" && dishStatus === "loadingPhoto" && <p className="text-small">{t("scan.dishSearching")}</p>}
      {scanMode === "dish" && dishStatus === "loadingCategory" && <p className="text-small">{t("scan.dishCategoryLoading")}</p>}
      {scanMode === "dish" && dishStatus === "error" && <p className="field__error">{t("scan.dishError")}</p>}
      {/* v0.4.10: уточнение рамкой. Предлагается после ответа — первый поиск уже прошёл по
          всему кадру, а прицел нужен там, где в кадре несколько бутылок одной серии: выбор
          нужной бутылки, а не качество поиска, и есть главный резерв точности у полки. */}
      {scanMode === "bottle" && photoFile && photoPreviewUrl && photoStatus !== "searching" && (result || photoStatus === "error") && (
        <div className="stack" data-testid="scan-aim-offer">
          {!aiming && (
            <button type="button" className="btn btn--secondary" onClick={() => setAiming(true)}>
              {t("scan.aimTitle")}
            </button>
          )}
          {aiming && (
            <div className="stack" data-testid="scan-aim">
              <p className="text-small">{t("scan.aimHint")}</p>
              <AimBox
                previewUrl={photoPreviewUrl}
                frame={frame}
                onFrameChange={setFrame}
                label={t("scan.aimFrameLabel")}
              />
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => void runBottleScan(photoFile, [frame.x1, frame.y1, frame.x2, frame.y2])}
              >
                {t("scan.aimScanFramed")}
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => setAiming(false)}>
                {t("scan.aimScanWhole")}
              </button>
            </div>
          )}
        </div>
      )}

      {scanMode === "bottle" && confidentCard && (
        <div className="stack" data-testid="scan-photo-result">
          <WineCardContent wine={confidentCard} titleAs="h2" />
          <button type="button" className="btn btn--secondary" onClick={handleAskSomelierAboutResult}>
            {t("scan.askSomelierAboutThis")}
          </button>
          {result && result.analogs.length > 0 && (
            <div className="stack" data-testid="scan-analogs-block">
              <h2>{t("scan.analogsTitle")}</h2>
              <div className="catalog-grid">
                {result.analogs.map((wine) => (
                  <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                ))}
              </div>
            </div>
          )}

          <div className="stack" data-testid="scan-taste-analogs-block">
            <h2>{t("scan.tasteAnalogsTitle")}</h2>
            {tasteAnalogs.status === "loading" && <p className="text-small">{t("scan.tasteAnalogsLoading")}</p>}
            {tasteAnalogs.status === "failed" && <p className="text-small">{tasteAnalogs.message}</p>}
            {tasteAnalogs.status === "ready" && (
              <>
                <p className="text-small">
                  {t("scan.tasteAnalogsStyleFound", { style: tasteAnalogs.style.name })}{" "}
                  {inYourTaste && <span className="badge">{t("scan.tasteAnalogsInYourTaste")}</span>}
                </p>
                {tasteAnalogs.wines.length > 0 && (
                  <div className="catalog-grid">
                    {tasteAnalogs.wines.map((wine) => (
                      <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                    ))}
                  </div>
                )}
              </>
            )}
            <button type="button" className="btn btn--ghost" onClick={handleTastePassportCta}>
              {t("scan.tastePassportCta")}
            </button>
          </div>

          <button type="button" className="btn btn--ghost" onClick={handleResetPhoto}>
            {t("scan.newPhoto")}
          </button>
        </div>
      )}

      {scanMode === "bottle" && result && result.not_in_catalog && (
        <div className="card stack" data-testid="scan-not-in-catalog">
          {result.candidates.length > 0 ? (
            <div className="stack" data-testid="scan-candidates-block">
              <h2>{t("scan.candidatesTitle")}</h2>
              <p className="text-small">{t("scan.candidatesSubtitle")}</p>
              {/* 22.09: полный top-5 без явного лидера — типичный след кадра целой полки
                  (reports/qa-auto-field-photos.md), где нарезка на бутылку в бой не пошла
                  (reports/ml-eng-ml3.md); при < 5 кандидатах подсказку не показываем. */}
              {result.candidates.length === 5 && <p className="text-small">{t("scan.candidatesManyHint")}</p>}
              <div className="catalog-grid">
                {result.candidates.map((wine) => (
                  <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                ))}
              </div>
            </div>
          ) : (
            <>
              <h2>{t("scan.notInCatalogTitle")}</h2>
              <p>{t("scan.notInCatalogMessage")}</p>
            </>
          )}
          {result.analogs.length > 0 && (
            <>
              <p className="field__label">{t("scan.analogsTitle")}</p>
              <div className="catalog-grid">
                {result.analogs.map((wine) => (
                  <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                ))}
              </div>
            </>
          )}
          <button type="button" className="btn btn--ghost" onClick={handleResetPhoto}>
            {t("scan.newPhoto")}
          </button>
        </div>
      )}

      {scanMode === "dish" && dishResult && (
        <div className="stack" data-testid="dish-result">
          {foodDish && (
            <div className="card stack" data-testid="dish-food-result">
              {/* name — nullable (zero_shot не даёт названия, только категорию по образу
                  фото; на 22.09 backend вдобавок иногда шлёт "" вместо null — контракт ещё
                  не догнан реализацией, см. apiTypes.ts) — || ловит оба случая, не только null. */}
              <h2>{foodDish.name || foodDish.category || t("scan.dishUnnamedFallback")}</h2>
              {foodDish.category && (
                <div className="row">
                  <span className="badge">{foodDish.category}</span>
                </div>
              )}
              {foodDish.alternatives.length > 0 && (
                <div className="stack stack--tight" data-testid="dish-alternatives">
                  <p className="field__label">{t("scan.dishAlternativesTitle")}</p>
                  <DishCategoryChips categories={foodDish.alternatives} onSelect={submitDishCorrection} />
                </div>
              )}
              <div className="stack" data-testid="dish-wines-block">
                <p className="field__label">{t("scan.dishWinesTitle")}</p>
                {dishResult.wines.length > 0 ? (
                  <div className="match-list">
                    {dishResult.wines.map((wine) => (
                      <DishWineCard key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                    ))}
                  </div>
                ) : (
                  <p className="text-small">{dishResult.message || t("scan.dishNoWinesFallback")}</p>
                )}
              </div>
            </div>
          )}

          {dishResult.status === "not_food" && (
            <div className="card stack" data-testid="dish-not-food">
              <h2>{t("scan.dishNotFoodTitle")}</h2>
              <p>{dishResult.message || t("scan.dishNotFoodFallback")}</p>
            </div>
          )}

          {dishResult.status === "bottle" && (
            <div className="card stack" data-testid="dish-bottle-hint">
              <p>{dishResult.message || t("scan.dishBottleFallback")}</p>
              <button type="button" className="btn btn--primary" onClick={handleScanAsBottle}>
                {t("scan.dishBottleCta")}
              </button>
            </div>
          )}

          {dishResult.status === "unsure" && (
            <div className="card stack" data-testid="dish-unsure">
              <h2>{t("scan.dishUnsureTitle")}</h2>
              {dishResult.message && <p className="text-small">{dishResult.message}</p>}
              {/* contracts/post-scan.md v1.1 §4.4: alternatives непуст — модель успела дать
                  слабую догадку, показываем именно её; пуст — честный фолбэк на все 9 тегов. */}
              <DishCategoryChips
                categories={dishResult.dish.alternatives.length > 0 ? dishResult.dish.alternatives : undefined}
                onSelect={submitDishCorrection}
              />
            </div>
          )}

          <button type="button" className="btn btn--ghost" onClick={handleResetPhoto}>
            {t("scan.dishNewPhoto")}
          </button>
        </div>
      )}

      <p className="text-small" style={{ textAlign: "center" }}>
        {t("scan.orDivider")}
      </p>

      {scanMode === "bottle" ? (
        <form className="stack card" onSubmit={handleTextSubmit}>
          <p className="field__label">{t("scan.textFallbackTitle")}</p>
          <label className="field">
            <span className="field__label">{t("scan.textLabel")}</span>
            <textarea
              id="scan-text-search"
              ref={textSearchRef}
              className="field__textarea"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder={t("scan.textPlaceholder")}
            />
          </label>
          {textError && <p className="field__error">{textError}</p>}
          <button type="submit" className="btn btn--ghost" disabled={status === "resolving"}>
            {t("scan.textSubmit")}
          </button>
        </form>
      ) : (
        // Задача тимлида 22.09: «ручной выбор категории без фото тоже работает» — тот же
        // POST /v1/pairing/dish, что и чипы unsure/alternatives выше, доступен независимо
        // от того, был ли вообще загружен снимок блюда.
        <div className="stack card" data-testid="dish-manual-category">
          <p className="field__label">{t("scan.dishManualCategoryTitle")}</p>
          <DishCategoryChips onSelect={submitDishCorrection} />
        </div>
      )}

      {scanMode === "bottle" && status === "resolving" && <p className="text-small">{t("scan.resolving")}</p>}
      {scanMode === "bottle" && status === "error" && <p className="field__error">{t("common.errorGeneric")}</p>}

      {scanMode === "bottle" && outcome && outcome.matches.length === 0 && outcome.analogs.length > 0 && (
        <div className="card stack" data-testid="scan-text-analogs">
          <h2>{t("scan.analogsFoundTitle")}</h2>
          {outcome.analogReason && <p className="text-small">{outcome.analogReason}</p>}
          <div className="catalog-grid">
            {outcome.analogs.map((wine) => (
              <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
            ))}
          </div>
        </div>
      )}

      {scanMode === "bottle" && outcome && outcome.matches.length === 0 && outcome.analogs.length === 0 && (
        <div className="card stack" data-testid="scan-no-matches">
          <h2>{t("scan.noMatchesTitle")}</h2>
          <p>{t("scan.noMatchesMessage")}</p>
        </div>
      )}

      {scanMode === "bottle" && outcome && outcome.matches.length > 0 && (
        <div className="card stack" data-testid="scan-low-confidence">
          <h2>{t("scan.lowConfidenceTitle")}</h2>
          <p className="text-small">{t("scan.lowConfidenceSubtitle")}</p>
          <div className="match-list">
            {outcome.matches.map((match) => (
              <button key={match.wine_id} type="button" className="match-item" onClick={() => goToWine(match.wine_id, "scan")}>
                <span>
                  {match.name} · {match.winery_name}
                </span>
                <span className="badge">{t("scan.confidenceLabel", { percent: Math.round(match.confidence * 100) })}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
