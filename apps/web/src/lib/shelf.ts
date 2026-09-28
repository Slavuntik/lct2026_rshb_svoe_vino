import { storage } from './storage';
import { translate } from '../i18n/translate';
const t = (key: Parameters<typeof translate>[1], vars?: Parameters<typeof translate>[2]) => translate('ru', key, vars);

export interface ShelfMatch {
  box: [number, number, number, number];
  wineId: string;
  alternativeWineIds: string[];
}
export interface ShelfScan {
  matches: ShelfMatch[];
  warnings: string[];
  image: {width:number; height:number};
}

export async function prepareShelfPhoto(file: File): Promise<{blob:Blob; width:number; height:number}> {
  if (file.size > 20 * 1024 * 1024) throw new Error(t('shelf.fileSizeError'));
  const bitmap = await createImageBitmap(file);
  try {
    if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height > 32_000_000) throw new Error(t('shelf.resolutionError'));
    const scale = Math.min(1, 1920 / Math.max(bitmap.width, bitmap.height));
    // Keep original pixels when resizing/conversion is unnecessary: another JPEG
    // encoding can erase the tiny label features needed for geometric verification.
    if (scale === 1 && ['image/jpeg', 'image/png'].includes(file.type)) {
      return {blob:file, width:bitmap.width, height:bitmap.height};
    }
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      b => b ? resolve(b) : reject(new Error(t('shelf.prepareError'))), 'image/jpeg', .94));
    return {blob, width:canvas.width, height:canvas.height};
  } finally { bitmap.close(); }
}

export async function scanShelf(blob: Blob, signal: AbortSignal): Promise<ShelfScan> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, {once:true});
  if (signal.aborted) controller.abort();
  let timer = setTimeout(abort, 15_000);
  try {
    const health = await fetch('/v1/shelf/health', {signal:controller.signal, credentials:'same-origin'});
    const config = await health.json();
    if (!health.ok || config.ready !== true) throw new Error(t('shelf.notReady'));
    clearTimeout(timer);
    const seconds = Number.isInteger(config.scanTimeoutSeconds) && config.scanTimeoutSeconds >= 15 && config.scanTimeoutSeconds <= 600 ? config.scanTimeoutSeconds : 300;
    timer = setTimeout(abort, seconds * 1000);
    const body = new FormData(); body.append('image', blob, 'shelf.jpg');
    const token = storage.getAccessToken();
    const headers: HeadersInit = token ? {Authorization: `Bearer ${token}`} : {};
    const options = {signal:controller.signal, credentials:'same-origin' as const, headers};
    const response = await fetch(config.asyncJobs === true ? '/v1/shelf/jobs' : '/v1/shelf/scan', {method:'POST', body, ...options});
    const check = (status: number) => {
      if (status < 200 || status >= 300) throw new Error(status === 503 ? t('shelf.serviceBusy') : status === 401 ? t('shelf.unauthorized') : t('shelf.scanError', {status}));
    };
    check(response.status);
    if (config.asyncJobs !== true) return parseShelfScan(await response.json());
    const job = await response.json();
    if (response.status !== 202 || typeof job.jobId !== 'string' || !/^[a-f0-9]{32}$/.test(job.jobId)) throw new Error(t('shelf.invalidResult'));
    for (;;) {
      await new Promise<void>((resolve, reject) => {
        const stopped = () => { clearTimeout(wait); reject(new DOMException('Aborted', 'AbortError')); };
        const wait = setTimeout(() => { controller.signal.removeEventListener('abort', stopped); resolve(); }, 1000);
        if (controller.signal.aborted) stopped();
        else controller.signal.addEventListener('abort', stopped, {once:true});
      });
      const poll = await fetch(`/v1/shelf/jobs/${job.jobId}`, options);
      check(poll.status);
      const result = await poll.json();
      if (result.state === 'running') continue;
      if (result.state !== 'done' && result.state !== 'failed') throw new Error(t('shelf.invalidResult'));
      check(result.status);
      return parseShelfScan(result.result);
    }
  } catch (error) {
    if (controller.signal.aborted && !signal.aborted) throw new Error(t('shelf.timeout'));
    throw error;
  } finally { clearTimeout(timer); signal.removeEventListener('abort', abort); }
}

export function parseShelfScan(value: unknown): ShelfScan {
  const r = value as ShelfScan;
  if (!r || !r.image || !Number.isInteger(r.image.width) || !Number.isInteger(r.image.height) || r.image.width <= 0 || r.image.height <= 0 || !Array.isArray(r.matches) || r.matches.length > 80) throw new Error(t('shelf.invalidResult'));
  for (const m of r.matches) {
    if (!m || typeof m.wineId !== 'string' || !Array.isArray(m.box) || m.box.length !== 4 || !m.box.every(v=>Number.isFinite(v)&&v>=0&&v<=1) || m.box[0]>=m.box[2] || m.box[1]>=m.box[3] || (m.alternativeWineIds !== undefined && (!Array.isArray(m.alternativeWineIds) || !m.alternativeWineIds.every(id=>typeof id==='string')))) throw new Error(t('shelf.invalidCoordinates'));
  }
  return {image:r.image, matches:r.matches.map(m=>({...m,alternativeWineIds:m.alternativeWineIds??[]})),warnings:Array.isArray(r.warnings)?r.warnings.filter(w=>typeof w==='string'):[]};
}
