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

export interface WineCardResponse {
  wine_id: string;
  source: WineSource;
  derived: WineDerived;
  source_url: string;
  similar?: string[];
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

// budget_rub_max выпилен в v0.2 — цен в каталоге vines нет вовсе (ревью 01, блокер 4).
export type ChatFilters = Partial<{
  color: string;
  sugar: string;
  region: string;
}>;

export interface ChatPayload {
  message: string;
  filters?: ChatFilters;
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
  top_styles: string[];
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
