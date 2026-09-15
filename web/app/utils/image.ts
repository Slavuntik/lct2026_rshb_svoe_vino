export interface UploadInfo {
  width: number | null
  height: number | null
  bytes: number
  resized: boolean
}

export interface PreparedUpload {
  blob: Blob
  filename: string
  info: UploadInfo
}

// Сервис всё равно уменьшает кадр до 1600 px по длинной стороне, поэтому фото 12 Мп
// уменьшаем в браузере до 2048 px: кадр остаётся целым, а загрузка по мобильной сети — в разы быстрее.
const MAX_SIDE = 2048
const MAX_BYTES_WITHOUT_RESIZE = 3 * 1024 * 1024

/** Готовит фото к отправке: всё фото целиком, при необходимости уменьшенное (с учётом EXIF-поворота). */
export async function prepareUpload(file: File): Promise<PreparedUpload> {
  const original: PreparedUpload = {
    blob: file,
    filename: file.name || 'photo.jpg',
    info: { width: null, height: null, bytes: file.size, resized: false },
  }
  if (typeof createImageBitmap !== 'function') return original

  let bitmap: ImageBitmap | null = null
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
    const { width, height } = bitmap
    const scale = Math.min(1, MAX_SIDE / Math.max(width, height))
    if (scale === 1 && file.size <= MAX_BYTES_WITHOUT_RESIZE) {
      return { ...original, info: { ...original.info, width, height } }
    }
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(width * scale)
    canvas.height = Math.round(height * scale)
    const context = canvas.getContext('2d')
    if (!context) return original
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, 'image/jpeg', 0.92))
    if (!blob) return original
    return {
      blob,
      filename: 'photo.jpg',
      info: { width: canvas.width, height: canvas.height, bytes: blob.size, resized: true },
    }
  } catch {
    // формат не декодируется браузером (например, HEIC) — отправляем как есть, сервис разберётся
    return original
  } finally {
    bitmap?.close()
  }
}
