/**
 * Режим моков (NUXT_PUBLIC_MOCK=1): те же пути и форматы, что у FastAPI-сервиса, но данные из web/mocks/.
 * Фикстуры сгенерированы из реального каталога и детерминированного кода аналогов и сомелье (см. README).
 */
import { readFile } from 'node:fs/promises'
import { basename, extname, resolve } from 'node:path'
import type { H3Event } from 'h3'
import type { QuestionsResponse, ReasonedWine, ScanResponse, Sweetness, WineCard } from '#shared/types/api'

interface MockData {
  wines: Record<string, WineCard>
  analogs: Record<string, ReasonedWine[]>
  /** ключ «блюдо|цвет|стиль» -> ранжированный список (фильтр по сладости применяется здесь) */
  suggest: Record<string, ReasonedWine[]>
  questions: QuestionsResponse
  scanFound: ScanResponse
  scanNotFound: ScanResponse
}

const DISCLAIMER = 'Информация о винах каталога. 18+. Чрезмерное употребление алкоголя вредит вашему здоровью.'
const SCAN_DELAY_MS = 700 // чтобы в демо было видно состояние загрузки
const SUGGEST_DELAY_MS = 250
const DEFAULT_UPLOADS_DIR = '../data/raw/prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads'
const IMAGE_TYPES: Record<string, string> = {
  '.webp': 'image/webp',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
}
const BOTTLE_PLACEHOLDER_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 360" width="120" height="360"><path fill="#efe3c4" d="M48 12h24v70c0 18 24 30 24 60v196a12 12 0 0 1-12 12H36a12 12 0 0 1-12-12V142c0-30 24-42 24-60z"/><rect x="30" y="190" width="60" height="80" rx="6" fill="#fdf9ed"/></svg>`

let cache: Promise<MockData> | null = null

function loadMocks(): Promise<MockData> {
  cache ??= (async () => {
    const storage = useStorage('assets:mocks')
    const read = async <T>(name: string): Promise<T> => {
      const value = await storage.getItem<T>(name)
      if (value === null || value === undefined) throw new Error(`нет фикстуры mocks/${name}`)
      return value
    }
    const [wines, analogs, suggest, questions, scanFound, scanNotFound] = await Promise.all([
      read<MockData['wines']>('wines.json'),
      read<MockData['analogs']>('analogs.json'),
      read<MockData['suggest']>('suggest.json'),
      read<QuestionsResponse>('questions.json'),
      read<ScanResponse>('scan_found.json'),
      read<ScanResponse>('scan_not_found.json'),
    ])
    return { wines, analogs, suggest, questions, scanFound, scanNotFound }
  })().catch((error: unknown) => {
    cache = null
    throw error
  })
  return cache
}

const sleep = (ms: number) => new Promise<void>((done) => setTimeout(done, ms))

/** Ошибка в формате FastAPI: {"detail": "..."}. */
function fail(event: H3Event, statusCode: number, detail: string) {
  setResponseStatus(event, statusCode)
  return { detail }
}

export async function handleMockRequest(event: H3Event, path: string): Promise<unknown> {
  const data = await loadMocks()
  let segments: string[]
  try {
    segments = path.split('/').filter(Boolean).map((segment) => decodeURIComponent(segment))
  } catch {
    return fail(event, 400, 'некорректный путь')
  }
  const route = segments.join('/')
  const method = event.method

  if (method === 'GET' && route === 'health') return { status: 'ok', wines: Object.keys(data.wines).length, mock: true }
  if (method === 'POST' && route === 'v1/scan') return mockScan(event, data)
  if (method === 'GET' && route === 'v1/sommelier/questions') return data.questions
  if (method === 'POST' && route === 'v1/sommelier/suggest') return mockSuggest(event, data)

  const [version, resource, slug, action] = segments
  if (method === 'GET' && version === 'v1' && resource === 'wines' && slug && segments.length <= 4) {
    const card = data.wines[slug]
    if (!card) return fail(event, 404, 'вино не найдено (в моках только часть каталога)')
    if (!action) return card
    if (action === 'image') return mockImage(event, card)
    if (action === 'analogs') {
      const limit = Math.max(1, Math.min(Number(getQuery(event).limit) || 6, 20))
      const analogs = data.analogs[slug] ?? approximateAnalogs(card, data.wines)
      return { slug, analogs: analogs.slice(0, limit) }
    }
  }
  return fail(event, 404, 'Not Found')
}

async function mockScan(event: H3Event, data: MockData) {
  const parts = await readMultipartFormData(event)
  const image = parts?.find((part) => part.name === 'image')
  if (!image?.data?.length) return fail(event, 422, 'поле image обязательно')
  await sleep(SCAN_DELAY_MS)
  // в демо ответ выбирается переключателем на экране сканера (?mock=not_found)
  return getQuery(event).mock === 'not_found' ? data.scanNotFound : data.scanFound
}

async function mockImage(event: H3Event, card: WineCard) {
  const file = card.image?.file
  if (file) {
    const dir = useRuntimeConfig(event).mockUploadsDir || resolve(process.cwd(), DEFAULT_UPLOADS_DIR)
    try {
      const body = await readFile(resolve(dir, basename(file)))
      setResponseHeader(event, 'content-type', IMAGE_TYPES[extname(file).toLowerCase()] ?? 'application/octet-stream')
      setResponseHeader(event, 'cache-control', 'public, max-age=86400')
      return body
    } catch {
      // эталонных фото нет локально (данные кейса не входят в репозиторий) — отдаём заглушку
    }
  }
  setResponseHeader(event, 'content-type', 'image/svg+xml; charset=utf-8')
  return BOTTLE_PLACEHOLDER_SVG
}

async function mockSuggest(event: H3Event, data: MockData) {
  const body = await readBody<Record<string, unknown> | null>(event).catch(() => null)
  const text = (key: string): string => {
    const value = body?.[key]
    return typeof value === 'string' ? value : ''
  }
  const dish = text('dish')
  if (!dish) return fail(event, 400, 'в режиме моков нужно выбрать блюдо')
  const dishes = data.questions.questions.find((question) => question.id === 'dish')?.options ?? []
  if (!dishes.some((option) => option.id === dish)) return fail(event, 400, `неизвестное блюдо: ${dish}`)

  const rawExclude = body?.exclude_slugs
  const exclude = new Set(Array.isArray(rawExclude) ? rawExclude.filter((s): s is string => typeof s === 'string') : [])
  const sweetness = text('sweetness')
  const pool = data.suggest[`${dish}|${text('category')}|${text('body')}`] ?? []

  const suggestions: ReasonedWine[] = []
  const wineries = new Set<string>()
  for (const suggestion of pool) {
    if (exclude.has(suggestion.slug) || wineries.has(suggestion.winery)) continue
    if (sweetness && data.wines[suggestion.slug]?.attributes.sweetness !== sweetness) continue
    suggestions.push(suggestion)
    wineries.add(suggestion.winery)
    if (suggestions.length === 3) break
  }
  await sleep(SUGGEST_DELAY_MS)
  return { suggestions, disclaimer: DISCLAIMER }
}

// Упрощённая копия правил src/winescan/product/analogs.py (без TF-IDF по описанию) —
// для карточек, аналоги которых не попали в фикстуры.
const SWEETNESS_ORDER: Sweetness[] = ['brut_nature', 'extra_brut', 'brut', 'dry', 'semi_dry', 'semi_sweet', 'sweet']
const SWEETNESS_RU: Record<Sweetness, string> = {
  brut_nature: 'брют натюр',
  extra_brut: 'экстра брют',
  brut: 'брют',
  dry: 'сухое',
  semi_dry: 'полусухое',
  semi_sweet: 'полусладкое',
  sweet: 'сладкое',
}
const GENERIC_GRAPES = new Set(['белые сорта винограда', 'красные сорта винограда', 'розовые сорта винограда', 'купаж', 'белые сорта', 'красные сорта'])

function approximateAnalogs(base: WineCard, wines: Record<string, WineCard>): ReasonedWine[] {
  const baseGrapes = new Set(base.grapes.map((g) => g.toLowerCase()).filter((g) => !GENERIC_GRAPES.has(g)))
  const results: ReasonedWine[] = []
  for (const other of Object.values(wines)) {
    if (other.slug === base.slug || other.winery === base.winery || other.category !== base.category) continue
    if (Boolean(base.attributes.sparkling) !== Boolean(other.attributes.sparkling)) continue
    let score = 1
    const reasons = [`тот же цвет: ${base.category.toLowerCase()}`]

    const grapes = new Set(other.grapes.map((g) => g.toLowerCase()).filter((g) => !GENERIC_GRAPES.has(g)))
    if (baseGrapes.size && grapes.size) {
      const shared = other.grapes.filter((g) => baseGrapes.has(g.toLowerCase()))
      const union = new Set([...baseGrapes, ...grapes]).size
      if (shared.length) {
        score += (3 * shared.length) / union
        reasons.push(`сорт: ${shared.join(', ')}`)
      }
    }
    const a = base.attributes.sweetness
    const b = other.attributes.sweetness
    if (a && b) {
      const distance = Math.abs(SWEETNESS_ORDER.indexOf(a) - SWEETNESS_ORDER.indexOf(b))
      if (distance === 0) {
        score += 2
        reasons.push(`сладость: ${SWEETNESS_RU[b]}`)
      } else if (distance === 1) {
        score += 0.7
      }
    }
    if (other.attributes.sparkling) reasons.push('игристое')
    if (other.region && other.region === base.region) {
      score += 1
      reasons.push(`регион: ${other.region}`)
    }
    results.push({ slug: other.slug, name: other.name, winery: other.winery, score: Math.round(score * 1000) / 1000, reasons })
  }
  results.sort((x, y) => y.score - x.score)
  const chosen: ReasonedWine[] = []
  const wineries = new Set<string>()
  for (const analog of results) {
    if (wineries.has(analog.winery)) continue
    chosen.push(analog)
    wineries.add(analog.winery)
    if (chosen.length === 20) break
  }
  return chosen
}
