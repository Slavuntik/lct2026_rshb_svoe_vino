// Типы клиента строго по contracts/openapi.yaml v0.2. Правки контракта — только у оркестратора;
// расхождения фиксируются в reports/c-report.md, а не чинятся молча в коде.

export type ConsentScope = "base" | "profiling" | "geo" | "marketing";

/** Словарь кодов ошибок из шапки openapi.yaml v0.2 — единственный источник, не выдумывать новые. */
export type ApiErrorCode =
  | "validation_error"
  | "invalid_credentials"
  | "unauthorized"
  | "age_restricted"
  | "consent_required"
  | "not_found"
  | "rate_limited"
  | "unknown_event"
  | "llm_unavailable"
  | "not_implemented"
  // клиентские, не из словаря сервера — сеть недоступна / не удалось распарсить ответ
  | "network_error"
  | "unknown_error";

export interface ApiError {
  error: {
    code: string;
    message: string;
  };
}

export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

export interface TokenPair {
  access_token: string;
  expires_in?: number;
}

export interface RegisterPayload {
  email: string;
  password: string;
  birth_date: string; // YYYY-MM-DD
  consent_version: string;
  consent_scopes: ConsentScope[];
}

export interface LoginPayload {
  email: string;
  password: string;
}

/** v0.2: /auth/guest — «Попробовать в браузере» без стены регистрации. */
export interface GuestAuthPayload {
  age_confirmed: boolean;
  consent_version: string;
}

export interface ConsentRecord {
  consent_version: string;
  scope: ConsentScope;
  grant: boolean;
  updated_at: string;
}

export interface ConsentsResponse {
  consents: ConsentRecord[];
}

export interface PostConsentPayload {
  consent_version: string;
  grant: boolean;
  scopes: ConsentScope[];
}

export interface ScanMatch {
  wine_id: string;
  name: string;
  winery_name: string;
  confidence: number;
}

export interface ScanResolveResponse {
  matches: ScanMatch[];
  low_confidence: boolean;
  /**
   * B7 (17.09, 4138171): фолбэк при пустых matches — из текста распознан сорт/стиль
   * (pipeline/ref) -> российские аналоги тем же резолвером, что /v1/analogs. Форма
   * items — как AnalogWine. Опционально по openapi.yaml (в required только matches) —
   * клиент обязан трактовать отсутствие как пусто/null, не как ошибку.
   */
  analogs?: AnalogWine[];
  analog_reason?: string | null;
}

export interface ScanResolvePayload {
  text: string;
  hints?: {
    color?: string;
    winery?: string;
  };
}

export interface SensoryVector {
  sweetness: number;
  acidity: number;
  tannin: number;
  body: number;
  oak: number;
  aromatic_intensity: number;
  bubbles: number;
}

export interface WineSource {
  name: string;
  winery: string;
  winery_name: string;
  region: string;
  region_name: string;
  grapes: string[];
  color: string;
  sugar_category: string;
  color_in_glass: string;
  vintage: number | null;
  abv_percent: number;
  serving_temp_c: [number, number];
  food_pairings: string[];
  description: string;
  public_rating: number | null;
  image_url: string;
  /**
   * v0.4.11 (contracts/image-scan.md): поле ЕСТЬ только у карточки-фолбэка каталога кейса
   * (слага нет в нашем RAG) — её source несёт ровно {name, winery_name, region_name, grapes,
   * color, category, description, image_url}, без sugar_category/vintage/abv_percent/
   * serving_temp_c/food_pairings и т.д. WineCardContent рендерит category как честную замену
   * бейджа sugar_category, когда его нет.
   */
  category?: string;
}

export interface WineDerived {
  style_tags: string[];
  sensory: SensoryVector & { confidence: number };
  reference_style_matches: string[];
}

/**
 * v0.3.6 (openapi.yaml, задача тимлида 23.09 — дефект жюри reports/qa-manual-hack-v16.md §5.1):
 * форма items у GET /wines/{id}.similar_wines. Тот же порядок/слаги, что similar, но слаг без
 * карточки в каталоге в similar_wines НЕ попадает (остаётся только в similar) — длины массивов
 * могут отличаться, similar_wines не индексируем в паре с similar.
 */
export interface SimilarWineItem {
  wine_id: string;
  name: string;
  winery?: string | null;
  image_url?: string | null;
}

export interface WineCardResponse {
  wine_id: string;
  source: WineSource;
  derived: WineDerived;
  source_url: string;
  /** DEPRECATED (openapi 0.3.6) — голые слаги без имени/винодельни. Запасной путь рендера,
   * когда similar_wines пуст или отсутствует (contracts/openapi.yaml, reports/backend-similar-wines.md). */
  similar?: string[];
  similar_wines?: SimilarWineItem[];
}

/**
 * v0.3.3 (contracts/post-scan.md v1.0, contracts/openapi.yaml): GET /wines/{wine_id}/pairings —
 * гастропары к распознанному вину. `basis` честно называет источник — catalog (как в
 * source.food_pairings, score всегда null), sensory (derived.sensory через мини-DSL правил),
 * heuristic (цвет + ключевые слова name/description — обычный случай карточки-фолбэка каталога
 * кейса при сканировании), unavailable (даже color пуст). Контракт: pairings=[] ⟺ message
 * непусто (§1, правило скоринга №5) — клиент обязан показать message, а не молчаливую пустоту.
 */
export type WinePairingsBasis = "catalog" | "sensory" | "heuristic" | "unavailable";

export interface WinePairingRuleHit {
  id?: string;
  explain?: string;
}

export interface WinePairing {
  tag: string;
  /** null при basis=catalog (чужой нескорингованный текст портала) — openapi.yaml. */
  score?: number | null;
  /** [] при basis=catalog — openapi.yaml. */
  triggered_rules?: WinePairingRuleHit[];
}

export interface WinePairingsResponse {
  wine_id: string;
  basis: WinePairingsBasis;
  /** Не более output_contract.top_n=3 (food_pairing_rules.yaml, число не меняем). */
  pairings: WinePairing[];
  message?: string | null;
}

/**
 * «Что подать» по фото блюда — задача тимлида 22.09, `POST /v1/pairing/dish-photo`
 * (multipart, поле `image`) и `POST /v1/pairing/dish` (JSON). Контракт RATIFIED в процессе
 * этой же волны — `contracts/post-scan.md` v1.1 §4, `contracts/openapi.yaml` v0.3.4
 * (`components.schemas.DishInfo/PairingWineItem/DishPairingResponse`) — имена типов ниже
 * зеркалят эти схемы буквально (кроме `DishPairingPayload`, у запроса нет именованной схемы
 * в openapi, только инлайновый `type: object` — суффикс "Payload" ради единообразия с
 * остальными *Payload в этом файле, ScanResolvePayload/AnalogsPayload/…).
 *
 * Известное расхождение контракта с ТЕКУЩИМ кодом backend (`apps/api/app/schemas.py`,
 * read-only для нас, не чиним): `DishInfo.name` там `str = ""` (не `str | null`, как в
 * ратифицированной схеме) — backend писал реализацию до ратификации v1.1 (см. докстринг
 * `apps/api/app/routers/pairing.py`, "реализовано по брифу буквально… расхождения см.
 * reports/backend-dish-photo.md"). Типы здесь — по контракту (nullable), рендер в
 * ScanScreen.tsx защищается от ОБОИХ вариантов (`||`, не `??`, на пустую строку).
 * Аналогично `winery`/`color`/`sugar` у backend `str | None`, у контракта — required
 * non-null: типы здесь по контракту, DishWineCard в ScanScreen.tsx уже рендерит их
 * условно (`wine.color && …`), так что расхождение не проявится как пустой бейдж/крэш.
 */
export type DishPairingStatus = "food" | "not_food" | "bottle" | "unsure";

/** `alternatives` — 0–3 ДРУГИХ тега из тех же 9 `portal_tag_defaults` (НЕ альтернативные
 * названия блюда) — и у status=food (модель не была уверена между категориями), и у
 * status=unsure (best-effort слабая догадка перед отказом; пустой массив ⟹ UI показывает
 * все 9 тегов, contracts/post-scan.md v1.1 §4.4). */
export interface DishInfo {
  name: string | null;
  category: string | null;
  alternatives: string[];
  ingredients: string[];
  source: "vlm" | "vlm_local" | "zero_shot" | "user" | "none";
}

/** «catalog» — вино подобрано по его каталожным food_pairings; «rules» — через движок
 * food_pairing_rules.yaml в обратном направлении (post-scan.md §4.3), тот же честный принцип
 * источника, что basis у WinePairing выше. Пока не рендерится в UI (см. отчёт, тот же
 * минимализм, что pairing.score/triggered_rules у WinePairing). */
export type PairingWineBasis = "catalog" | "rules";

export interface PairingWineItem {
  wine_id: string;
  name: string;
  /** Отображаемое имя винодельни (`source.winery_name` на backend, НЕ слаг `source.winery`) —
   * поле в ответе называется "winery", не "winery_name", буквально по контракту. */
  winery: string;
  color: string;
  sugar: string;
  /** Не в required у openapi (nullable, может отсутствовать) — в отличие от остальных полей. */
  image_url?: string | null;
  /** Всегда дословный текст без LLM: шаблон (catalog) либо rules[].explain (rules). */
  reason: string;
  basis: PairingWineBasis;
}

export interface DishPairingResponse {
  status: DishPairingStatus;
  /** ВСЕГДА объект (не null) — при not_food/bottle/unsure у него просто name=category=null. */
  dish: DishInfo;
  /** Максимум 6, не более 2 на одну винодельню (post-scan.md §4.3). [] ⟺ message непусто. */
  wines: PairingWineItem[];
  message?: string | null;
  timing_ms: number;
}

/** POST /v1/pairing/dish: category — строго один из 9 тегов portal_tag_defaults (иначе 400
 * validation_error); dish — свободный текст названия блюда, опционально. */
export interface DishPairingPayload {
  category: string;
  dish?: string;
}

/**
 * v0.4 (contracts/image-scan.md, кейс ЛЦТ): POST /v1/scan/photo, rich-режим (без ?flat=1).
 * Форма `card` контрактом не специфицирована явно — принимаю как у GET /wines/{id}
 * (WineCardResponse), это самая естественная форма "карточки" в системе; зафиксировано
 * как допущение в reports/c-report.md. confidence приходит для API/метрик (ТЗ кейса:
 * "виден отрыв лидера — в API"), в UI НЕ показывается умышленно (тоже требование ТЗ).
 */
export interface ScanPhotoConfidence {
  top1_score: number;
  gap: number;
  f1_top1: number;
  f1_top5: number;
}

/**
 * v0.4.11 (contracts/image-scan.md, разбор чата кейса 21.09): top-5 схлопнутых позиций
 * ANN-поиска по убыванию score, обогащённые данными карточки (наш каталог, иначе фолбэк
 * из каталога кейса). Показываются при not_in_catalog=true как «Возможно, это одно из:» —
 * приватная проверка кейса содержит только вина каталога, поэтому именно этот список, а не
 * абсолютный score, отвечает на вопрос «какое из них». score в UI не рендерится (тот же
 * принцип, что confidence.top1_score — честная неуверенность живёт в самом факте показа
 * списка, не в цифрах).
 */
export interface ScanCandidateWine {
  wine_id: string;
  name: string;
  winery_name: string;
  region_name: string;
  image_url: string;
  source_url: string;
  score: number;
}

export interface ScanPhotoRichResponse {
  slug: string;
  card: WineCardResponse | null;
  confidence: ScanPhotoConfidence;
  ocr_verified: boolean;
  timing_ms: number;
  not_in_catalog: boolean;
  /** v0.4.11: поле есть всегда (не только при not_in_catalog) — см. ScanCandidateWine. */
  candidates: ScanCandidateWine[];
  similar: AnalogWine[];
  analogs: AnalogWine[];
}

/**
 * v0.4 (contracts/image-scan.md): GET /v1/metrics/scan — сводка последнего прогона оценки.
 * Все поля необязательные: до первого прогона сервис честно отдаёт null вместо выдуманных
 * чисел, и экран показывает это как «замер ещё не проводился», а не как ноль.
 */
export interface ScanMetricsResponse {
  index_version: string | null;
  f1_top1: number | null;
  f1_top5: number | null;
  match_rate: number | null;
  eval_set: string | null;
  measured_at: string | null;
}

// budget_rub_max выпилен в v0.2 — цен в каталоге vines нет вовсе (ревью 01, блокер 4).
export type ChatFilters = Partial<{
  color: string;
  sugar: string;
  region: string;
}>;

export interface ChatPayload {
  message: string;
  filters?: ChatFilters;
  /**
   * v0.3.5 (задача тимлида 22.09, openapi 0.3.5 — architect оформляет параллельно): слаг вина,
   * ТОЛЬКО первый запрос диалога после перехода «Спросить сомелье об этом вине»
   * (ScanScreen.tsx/WineCardScreen.tsx) — гарантирует ответ про то самое отсканированное/
   * открытое вино вместо текстового поиска по префиллу (91.8% попаданий — вина-близнецы из
   * одной серии путаются). Нет слага — поле не шлём (ChatScreen.tsx::initialWineIdRef).
   */
  wine_id?: string;
}

export interface ChatFeedbackPayload {
  answer_id: string;
  verdict: "up" | "down";
}

export type ChatStreamEvent =
  | { type: "token"; text: string }
  // n — порядковый номер цитаты, совпадает с маркером [n] в тексте ответа (v0.2).
  | { type: "citation"; n: number; wine_id?: string; chunk_id?: string; url?: string; quote?: string }
  | { type: "done"; answer_id: string }
  | { type: "refusal"; reason: string };

/** v0.2: сцена «аналог импортного» — детерминированный путь, не через LLM. */
export interface AnalogsPayload {
  query: string;
  filters?: Partial<{ region: string; sugar: string }>;
}

export interface AnalogStyle {
  slug: string;
  name: string;
  country: string;
}

export interface AnalogWine {
  wine_id: string;
  name: string;
  winery_name: string;
  region_name: string;
}

export interface AnalogsResponse {
  style: AnalogStyle;
  wines: AnalogWine[];
}

export type SwipeVerdict = "like" | "dislike" | "skip";

export interface SwipePayload {
  wine_id: string;
  verdict: SwipeVerdict;
}

export interface TasteProfileResponse {
  vector: SensoryVector;
  /** DEPRECATED (openapi 0.3.6) — голые слаги эталонных стилей, без имени/страны. Запасной
   * путь рендера, когда top_styles_named пуст или отсутствует (тот же класс дефекта и то же
   * решение, что similar/similar_wines — reports/backend-similar-wines.md, аудит architect). */
  top_styles: string[];
  /** v0.3.6 — форма как у list_reference_styles()/AnalogStyle; тот же порядок, что top_styles,
   * неизвестный слаг стиля пропущен (остаётся только в top_styles). */
  top_styles_named?: AnalogStyle[];
  swipes_count: number;
}

/** v0.2.2: GET /taste/candidates — колода для свайпов (пробел нашёл сам, см. c-report.md). */
export interface TasteCandidateWine {
  wine_id: string;
  name: string;
  winery_name: string;
  region_name?: string;
  color?: string;
  image_url?: string;
}

export interface TasteCandidatesResponse {
  wines: TasteCandidateWine[];
}

export interface WaitlistPayload {
  email: string;
  consent_version: string;
}

export interface DataExportResponse {
  [key: string]: unknown;
}

/** v0.2: POST /events — единственный транспорт для клиентских событий (contracts/events.md). */
export interface EventPayload {
  name: string;
  props: Record<string, unknown>;
}
