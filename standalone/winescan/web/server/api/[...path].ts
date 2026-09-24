/**
 * Единая точка входа API для интерфейса: /api/** -> ${NUXT_API_BASE}/**.
 * Проксирование на стороне Nuxt убирает CORS, а адрес бэкенда читается из runtimeConfig при запуске.
 * При NUXT_PUBLIC_MOCK=1 отвечает фикстурами из web/mocks (см. server/utils/mock-api.ts).
 */
import { isMockEnabled } from '#shared/utils/mock'

export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig(event)
  const path = getRouterParam(event, 'path') ?? ''

  if (isMockEnabled(config.public.mock)) {
    return handleMockRequest(event, path)
  }

  const target = `${config.apiBase.replace(/\/+$/, '')}/${path}${getRequestURL(event).search}`
  try {
    return await proxyRequest(event, target)
  } catch (error) {
    // бэкенд не запущен или недоступен: отдаём ошибку в формате FastAPI, текст покажет интерфейс
    console.error(`[api-proxy] ${event.method} ${target}:`, error instanceof Error ? error.message : error)
    setResponseStatus(event, 502)
    return { detail: 'сервис распознавания недоступен' }
  }
})
