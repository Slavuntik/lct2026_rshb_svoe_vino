import type {
  AnalogsResponse,
  MetricsResponse,
  QuestionsResponse,
  ScanResponse,
  SuggestRequest,
  SuggestResponse,
  WineCard,
} from '#shared/types/api'

const SCAN_TIMEOUT_MS = 60_000
const DEFAULT_TIMEOUT_MS = 20_000

/** Клиент API. Все пути относительны /api — их проксирует server/api/[...path].ts. */
export function useApi() {
  const api = $fetch.create({ baseURL: '/api', timeout: DEFAULT_TIMEOUT_MS })
  const winePath = (slug: string) => `/v1/wines/${encodeURIComponent(slug)}`

  return {
    /**
     * mockStatus учитывается только в режиме моков: выбирает ответ «найдено» или «нет в каталоге».
     * box — рамка, которую указал пользователь, в долях кадра [x1, y1, x2, y2]; с ней сервис не
     * запускает детектор. Именно на автоматическом выборе бутылки теряется больше всего точности.
     */
    scan(image: Blob, filename: string, mockStatus?: 'found' | 'not_found', box?: [number, number, number, number]) {
      const form = new FormData()
      form.append('image', image, filename)
      if (box) form.append('box', box.map((value) => value.toFixed(4)).join(','))
      return api<ScanResponse>('/v1/scan', {
        method: 'POST',
        body: form,
        timeout: SCAN_TIMEOUT_MS,
        query: mockStatus ? { mock: mockStatus } : undefined,
      })
    },
    wine: (slug: string) => api<WineCard>(winePath(slug)),
    analogs: (slug: string, limit = 6) => api<AnalogsResponse>(`${winePath(slug)}/analogs`, { query: { limit } }),
    /** Сводка прогонов для страницы метрик; 404, если сводка ещё не собрана (make report). */
    metrics: () => api<MetricsResponse>('/v1/metrics'),
    questions: () => api<QuestionsResponse>('/v1/sommelier/questions'),
    suggest: (request: SuggestRequest) => api<SuggestResponse>('/v1/sommelier/suggest', { method: 'POST', body: request }),
  }
}
