import type { BoundingBox, ScanCandidate, ScanConfidence, ScanTimings } from '#shared/types/api'
import type { UploadInfo } from './image'

/** Данные уверенности последнего скана — показываются на карточке, если пришли со сканера. */
export interface ScanContext {
  at: number
  confidence: ScanConfidence
  top5: ScanCandidate[]
  timings_ms: ScanTimings
  box: BoundingBox | null
  upload: UploadInfo | null
}

const PREFIX = 'winescan:scan:'

export function saveScanContext(slug: string, context: ScanContext): void {
  try {
    sessionStorage.setItem(PREFIX + slug, JSON.stringify(context))
  } catch {
    // приватный режим или запрет хранилища: карточка откроется без бейджа уверенности
  }
}

export function loadScanContext(slug: string): ScanContext | null {
  try {
    const raw = sessionStorage.getItem(PREFIX + slug)
    return raw ? (JSON.parse(raw) as ScanContext) : null
  } catch {
    return null
  }
}
