import type { Catalog, ModelManifest, Observation, ScanResult } from './types';

type Message = { type: string; requestId?: number; bitmap?: ImageBitmap };
interface ServerResponse {
  requestId: string; pipelineVersion: string; catalogVersion: string;
  detectedCount: number; image: { width: number; height: number };
  matches: { box: number[]; wineId: string; name: string }[];
  timingsMs: { processing: number; detection?: number; recognition?: number };
  warnings: string[];
}

/** Same interface as the local worker; no model downloads in server mode. */
export class ServerClient {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private active?: AbortController;
  private generation = 0;
  private wineIds = new Set<string>();
  private catalogVersion = '';
  constructor(private base = '/v1/shelf') { this.base = base.replace(/\/$/, ''); }
  private emit(data: unknown) { this.onmessage?.({ data } as MessageEvent); }
  private async json(path: string, options: RequestInit, signal: AbortSignal) {
    const response = await fetch(`${this.base}${path}`, { ...options, signal, credentials: 'same-origin' });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : response.status === 503 ? 'Сервер занят или прогревается. Повторите подключение через несколько секунд.' : `Ошибка сервера: ${response.status}`);
    return body;
  }
  postMessage(message: Message, _transfer?: Transferable[]) {
    if (message.type === 'cancel') {
      const requestId = this.currentRequest;
      this.generation++; this.active?.abort();
      this.emit({ type: 'cancelled', requestId });
      return;
    }
    if (message.type === 'catalog') return;
    void this.run(message);
  }
  private currentRequest?: number;
  private async run(message: Message) {
    this.active?.abort(); const controller = new AbortController(); this.active = controller;
    const generation = ++this.generation; this.currentRequest = message.requestId;
    const timer = setTimeout(() => controller.abort(), 45_000);
    try {
      if (message.type === 'init') {
        const health = await this.json('/health', {}, controller.signal);
        if (!health?.ready) throw new Error('Сервер ещё не готов');
        const remote = await this.json('/catalog', {}, controller.signal);
        if (typeof remote.catalogVersion !== 'string' || !Array.isArray(remote.wines)) throw new Error('Неверный ответ каталога');
        this.catalogVersion = remote.catalogVersion;
        const catalog: Catalog = { version: 1, dimension: 1, embeddingModel: `server-${remote.catalogVersion}`, wines: remote.wines.map((wine: Record<string, unknown>) => ({ ...wine, references: [[1]] })) };
        this.wineIds = new Set(catalog.wines.map(w => w.id));
        const manifest = { embeddingModel: catalog.embeddingModel, dimension: 1, localFeatures: 'server' } as ModelManifest;
        if (generation === this.generation) this.emit({ type: 'ready', catalog, manifest });
      } else if (message.type === 'scan' && message.bitmap) {
        const started = performance.now(), bitmap = message.bitmap;
        const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
        canvas.getContext('2d')!.drawImage(bitmap, 0, 0); bitmap.close();
        const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(b => b ? resolve(b) : reject(new Error('Не удалось подготовить фото')), 'image/jpeg', .95));
        const form = new FormData(); form.append('image', blob, 'shelf.jpg');
        const response: ServerResponse = await this.json('/scan', { method: 'POST', body: form }, controller.signal);
        if (response.image?.width !== canvas.width || response.image?.height !== canvas.height) throw new Error('Сервер вернул другой размер изображения');
        if (response.catalogVersion !== this.catalogVersion) throw new Error('Каталог сервера обновился. Перезагрузите страницу.');
        const observations = parseServerMatches(response, this.wineIds);
        const result: ScanResult & Record<string, unknown> = {
          type: 'result', requestId: message.requestId!, observations, elapsed: performance.now() - started,
          detectionMs: response.timingsMs.detection ?? 0, recognitionMs: response.timingsMs.recognition ?? response.timingsMs.processing,
          recognitionBackend: 'server', serverRequestId: response.requestId, pipelineVersion: response.pipelineVersion,
          serverProcessingMs: response.timingsMs.processing, detectedCount: response.detectedCount
        };
        if (generation === this.generation) this.emit(result);
      }
    } catch (error) {
      message.bitmap?.close();
      if (generation === this.generation) this.emit({ type: 'error', requestId: message.requestId, message: controller.signal.aborted ? 'Сервер не ответил за 45 секунд. Повторите попытку.' : error instanceof Error ? error.message : String(error) });
    } finally { clearTimeout(timer); }
  }
  terminate() { this.generation++; this.active?.abort(); }
}

export function parseServerMatches(response: ServerResponse, ids: Set<string>): Observation[] {
  if (!Number.isInteger(response.detectedCount) || response.detectedCount < 0 || response.detectedCount > 80 || !Array.isArray(response.matches) || response.matches.length > 80 || !Number.isFinite(response.timingsMs?.processing) || response.timingsMs.processing < 0) throw new Error('Неверный результат сервера');
  return response.matches.map(match => {
    const box = match.box;
    if (!ids.has(match.wineId) || !Array.isArray(box) || box.length !== 4 || !box.every(v => Number.isFinite(v) && v >= 0 && v <= 1) || box[0] >= box[2] || box[1] >= box[3]) throw new Error('Неверные координаты или название в ответе сервера');
    return { box: box as Observation['box'], score: 0, embedding: [], tooSmall: false, match: { id: match.wineId, score: 0, margin: 0, candidates: [] } };
  });
}
