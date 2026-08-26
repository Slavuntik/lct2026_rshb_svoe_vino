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
