import type { ScanConfidence, Sweetness, WineCard } from '#shared/types/api'

/** Сладость по-русски — те же подписи, что у сомелье и аналогов в бэкенде. */
export const SWEETNESS_LABELS: Record<Sweetness, string> = {
  brut_nature: 'брют натюр',
  extra_brut: 'экстра брют',
  brut: 'брют',
  dry: 'сухое',
  semi_dry: 'полусухое',
  semi_sweet: 'полусладкое',
  sweet: 'сладкое',
}

export function upperFirst(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

export function sweetnessLabel(value: Sweetness | null | undefined): string | null {
  return value ? (SWEETNESS_LABELS[value] ?? null) : null
}

/** «Красное сухое», «Игристое белое брют». */
export function categoryLine(card: WineCard): string {
  const category = card.category?.trim() || 'Вино'
  const sweetness = sweetnessLabel(card.attributes?.sweetness)
  if (card.attributes?.sparkling) {
    return ['Игристое', category.toLowerCase(), sweetness].filter(Boolean).join(' ')
  }
  return [category, sweetness].filter(Boolean).join(' ')
}

// Градиент красного взят со страницы вина портала; для остальных цветов — предположение в той же манере.
const CATEGORY_SWATCHES: Record<string, string> = {
  Красное: 'linear-gradient(180deg, #A51B3A 0%, #741D30 100%)',
  Белое: 'linear-gradient(180deg, #F4E6B0 0%, #DCC27A 100%)',
  Розовое: 'linear-gradient(180deg, #F6C1C0 0%, #E08A8E 100%)',
  Оранжевое: 'linear-gradient(180deg, #F5C27E 0%, #DD8F45 100%)',
}

export function categorySwatch(category: string | null | undefined): string {
  return (category && CATEGORY_SWATCHES[category]) || 'linear-gradient(180deg, #E4DFDB 0%, #C2BFBC 100%)'
}

/**
 * Уровень уверенности для бейджа. Сервис уже решил «найдено» (визуальный порог), здесь только подсказка:
 * если второй кандидат почти так же близок (маленький отрыв), просим сверить название.
 * Порог 0.03 — допущение интерфейса, в бэкенде min_margin не задан.
 */
export const HIGH_CONFIDENCE_MARGIN = 0.03
export type ConfidenceLevel = 'high' | 'medium'

export function confidenceLevel(confidence: ScanConfidence): ConfidenceLevel {
  const margin = confidence.margin_top1_top2
  if (margin === null || margin === undefined) return 'high' // единственный кандидат — сравнивать не с чем
  return margin >= HIGH_CONFIDENCE_MARGIN ? 'high' : 'medium'
}

export function formatScore(value: number | null | undefined, digits = 3): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

export function formatMs(value: number): string {
  return `${Math.round(value).toLocaleString('ru-RU')} мс`
}

export function formatVolume(liters: number): string {
  return `${String(liters).replace('.', ',')} л`
}

export function wineUrl(slug: string): string {
  return `/wine/${encodeURIComponent(slug)}`
}

export function wineImageUrl(slug: string): string {
  return `/api/v1/wines/${encodeURIComponent(slug)}/image`
}
