/** Понятный пользователю текст ошибки запроса к API (ofetch FetchError, ответ FastAPI {"detail": ...}). */
export function apiErrorMessage(error: unknown): string {
  const e = (error ?? {}) as { statusCode?: number; status?: number; data?: unknown; message?: string }
  const status = e.statusCode ?? e.status
  const detail = extractDetail(e.data)

  if (!status) {
    if (/timeout|aborted/i.test(e.message ?? '')) return 'Сервер долго не отвечает. Попробуйте ещё раз.'
    return 'Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.'
  }
  if (status === 502 || status === 503 || status === 504) {
    return 'Сервис распознавания сейчас недоступен. Попробуйте позже.'
  }
  if (status === 413) return 'Фото слишком большое. Выберите другое фото.'
  if (status >= 400 && status < 500) {
    return detail ? `${upperFirst(detail)}.` : 'Не удалось обработать запрос.'
  }
  return 'На сервере произошла ошибка. Попробуйте ещё раз.'
}

function extractDetail(data: unknown): string | null {
  if (typeof data === 'string') return data || null
  if (!data || typeof data !== 'object') return null
  const record = data as Record<string, unknown>
  if (typeof record.detail === 'string') return record.detail
  if (record.data) return extractDetail(record.data)
  return typeof record.message === 'string' ? record.message : null
}
