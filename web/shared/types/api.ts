/**
 * Типы ответов FastAPI-сервиса WineScan (src/winescan/service/app.py).
 * Общие для интерфейса (app/) и режима моков (server/).
 */

export type Sweetness = 'brut_nature' | 'extra_brut' | 'brut' | 'dry' | 'semi_dry' | 'semi_sweet' | 'sweet'

export interface WineAttributes {
  year: number | null
  sweetness: Sweetness | null
  sparkling: boolean | null
  volume_l: number | null
}

export interface WineImageMeta {
  file: string | null
  status: string | null
  width: number | null
  height: number | null
  source_photo_name: string | null
}

/** Карточка вина — строка artifacts/catalog/catalog.jsonl. */
export interface WineCard {
  slug: string
  name: string
  winery: string
  region: string | null
  category: string
  color: string | null
  grapes: string[]
  description: string | null
  attributes: WineAttributes
  image: WineImageMeta
}

export interface ScanCandidate {
  slug: string
  name: string
  score: number
  visual_score: number | null
  local_inliers: number | null
  text_score: number | null
}

export interface ScanConfidence {
  score_top1: number | null
  visual_score_top1: number | null
  margin_top1_top2: number | null
  decision_reason: string
  ocr_text: string
}

/** Тайминги этапов: detect, embed_search, local_match, ocr, total (мс). */
export type ScanTimings = Record<string, number>

export type BoundingBox = [number, number, number, number]

export interface ScanResponse {
  status: 'found' | 'not_found'
  slug: string | null
  card: WineCard | null
  confidence: ScanConfidence
  top5: ScanCandidate[]
  box: BoundingBox | null
  timings_ms: ScanTimings
}

/** Вино с объяснением выбора: аналоги и подборки сомелье. */
export interface ReasonedWine {
  slug: string
  name: string
  winery: string
  score: number
  reasons: string[]
}

export interface AnalogsResponse {
  slug: string
  analogs: ReasonedWine[]
}

export type QuestionId = 'dish' | 'category' | 'sweetness' | 'body'

export interface SommelierOption {
  id: string
  title: string
}

export interface SommelierQuestion {
  id: QuestionId
  title: string
  options: SommelierOption[]
}

export interface QuestionsResponse {
  questions: SommelierQuestion[]
}

export interface SuggestRequest {
  dish?: string
  category?: string
  sweetness?: string
  body?: string
  region?: string
  exclude_slugs?: string[]
}

/** Итог решения «не найдено» в сквозном прогоне сервиса (winescan.eval.scanner_eval). */
export interface MetricsDecision {
  in_catalog: number
  answered_correct: number | null
  answered_wrong: number | null
  rejected: number | null
  precision_of_answers: number | null
  out_of_catalog: number
  out_of_catalog_rejected: number | null
  open_set_accuracy: number | null
}

/** Один прогон из artifacts/eval: поиск (winescan.eval.run) или сквозной прогон сервиса. */
export interface MetricsRun {
  run: string
  split: string | null
  kind: 'сервис' | 'поиск'
  queries: number | null
  top1: number | null
  top5: number | null
  top1_in_phash_group: number | null
  decision: MetricsDecision | null
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  finished_at: string
}

export interface MetricsResponse {
  generated_at: string
  runs: MetricsRun[]
}

export interface SuggestResponse {
  suggestions: ReasonedWine[]
  disclaimer: string
}
