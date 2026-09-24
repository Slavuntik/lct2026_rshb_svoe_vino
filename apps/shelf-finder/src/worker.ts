/// <reference lib="webworker" />
import type * as ORT from 'onnxruntime-web';
import { nms, normalize, parseCatalog, rank, tiles, UNKNOWN } from './core';
import type { Box, Catalog, Detection, ModelManifest, Observation } from './types';
import { recognitionViews } from './preprocess';
import type { GeometryVerifier } from './geometry';
import { createLearnedSessions } from './model-sessions';
import type { LearnedRecognizer } from './learned';

let ort: typeof ORT;
let detector: ORT.InferenceSession;
let embedder: ORT.InferenceSession;
let catalog: Catalog;
let manifest: ModelManifest;
let geometry: GeometryVerifier | undefined;
let learned: LearnedRecognizer | undefined;
let cancelled = false;
let recognitionBackend = 'wasm';
const canvas = new OffscreenCanvas(640, 640);
const context = canvas.getContext('2d', { willReadFrequently: true })!;

async function checkedAsset(base: string, filename: string): Promise<ArrayBuffer> {
  const url = new URL(filename, base);
  if (url.origin !== new URL(base).origin || !url.pathname.startsWith(new URL(base).pathname)) throw new Error('Модель должна находиться в каталоге приложения.');
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Не найден файл модели: ${filename}. Запустите подготовку моделей.`);
  const bytes = await response.arrayBuffer();
  const expected = manifest.hashes[filename];
  if (expected && crypto.subtle) {
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const hash = Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2, '0')).join('');
    if (hash !== expected) throw new Error(`Повреждён файл ${filename}; подготовьте комплект заново.`);
  }
  return bytes;
}
function tensor(bitmap: ImageBitmap, box: Box, size: number, kind: 'detector' | 'embedding') {
  canvas.width = size; canvas.height = size;
  context.fillStyle = kind === 'detector' ? 'rgb(114,114,114)' : 'white';
  context.fillRect(0, 0, size, size);
  const width = box[2] - box[0], height = box[3] - box[1];
  const siglip = kind === 'embedding' && manifest.preprocessing === 'siglip-full-label';
  const scale = size / (Math.max(width, height) * (kind === 'embedding' && manifest.preprocessing ? 1.08 : 1));
  const dx = (size - width * scale) / 2, dy = (size - height * scale) / 2;
  context.drawImage(bitmap, ...[box[0], box[1], width, height, dx, dy, width * scale, height * scale] as [number, number, number, number, number, number, number, number]);
  const rgba = context.getImageData(0, 0, size, size).data;
  const data = new Float32Array(3 * size * size);
  const mean = siglip ? [.5, .5, .5] : [.485, .456, .406], std = siglip ? [.5, .5, .5] : [.229, .224, .225];
  for (let p = 0; p < size * size; p++) for (let c = 0; c < 3; c++) {
    const value = rgba[p * 4 + c] / 255;
    data[c * size * size + p] = kind === 'embedding' ? (value - mean[c]) / std[c] : value;
  }
  return { input: new ort.Tensor('float32', data, [1, 3, size, size]), scale, dx, dy };
}
async function detect(bitmap: ImageBitmap, dense: boolean): Promise<Detection[]> {
  const detections: Detection[] = [];
  for (const tile of tiles(bitmap.width, bitmap.height, dense)) {
    const { input, scale, dx, dy } = tensor(bitmap, tile, manifest.detectorSize, 'detector');
    const outputs = await detector.run({ [detector.inputNames[0]]: input });
    const output = outputs[detector.outputNames[0]];
    const [batch, channels, count] = output.dims;
    if (batch !== 1 || channels < 5 || channels > 256 || count < channels) throw new Error('Детектор должен выдавать YOLOv8/11 [1, 4 + классы, N], без NMS.');
    const values = output.data as Float32Array;
    for (let i = 0; i < count; i++) {
      const score = values[(4 + manifest.bottleClass) * count + i];
      if (score < .25) continue;
      const cx = values[i], cy = values[count + i], w = values[count * 2 + i], h = values[count * 3 + i];
      const box: Box = [(cx - w / 2 - dx) / scale + tile[0], (cy - h / 2 - dy) / scale + tile[1], (cx + w / 2 - dx) / scale + tile[0], (cy + h / 2 - dy) / scale + tile[1]];
      box[0] = Math.max(tile[0], box[0]); box[1] = Math.max(tile[1], box[1]); box[2] = Math.min(tile[2], box[2]); box[3] = Math.min(tile[3], box[3]);
      if (box[2] > box[0] && box[3] > box[1]) detections.push({ score, box: [box[0] / bitmap.width, box[1] / bitmap.height, box[2] / bitmap.width, box[3] / bitmap.height] });
    }
    input.dispose(); Object.values(outputs).forEach(x => x.dispose());
  }
  return nms(detections).slice(0, 80);
}
async function embed(bitmap: ImageBitmap, box: Box): Promise<number[]> {
  const crop: Box = [box[0] * bitmap.width, box[1] * bitmap.height, box[2] * bitmap.width, box[3] * bitmap.height];
  const views = manifest.preprocessing === 'siglip-full-label' ? recognitionViews(crop, bitmap.width, bitmap.height) : manifest.preprocessing === 'mobilenet-label' ? recognitionViews(crop, bitmap.width, bitmap.height).slice(1) : [crop];
  const combined: number[] = [];
  for (const view of views) {
    const { input } = tensor(bitmap, view, 224, 'embedding');
    try {
      const name = manifest.embeddingOutput ?? embedder.outputNames[0];
      const outputs = await embedder.run({ [embedder.inputNames[0]]: input }, [name]);
      try { combined.push(...normalize(outputs[name].data as Float32Array)); }
      finally { Object.values(outputs).forEach(x => x.dispose()); }
    } finally { input.dispose(); }
  }
  return normalize(combined);
}
self.onmessage = async (event: MessageEvent) => {
  const message = event.data;
  try {
    if (message.type === 'init') {
      const directory = ['siglip', 'mobile', 'siglip-q4', 'local', 'xfeat', 'hybrid'].includes(message.engine) ? `models/${message.engine}/` : 'models/';
      const base = new URL(directory, message.base).href;
      const response = await fetch(new URL('manifest.json', base));
      if (!response.ok) throw new Error('Комплект моделей не подготовлен. Следуйте README приложения или выберите базовое сравнение.');
      manifest = await response.json();
      ort = message.engine === 'siglip-q4' || manifest.localFeatures ? await import('onnxruntime-web/webgpu') : await import('onnxruntime-web/wasm');
      ort.env.wasm.numThreads = manifest.localFeatures && self.crossOriginIsolated ? Math.min(2, navigator.hardwareConcurrency || 1) : 1;
      ort.env.wasm.wasmPaths = new URL('runtime/', message.base).href;
      const detectorBytes = await checkedAsset(base, manifest.detector);
      detector = await ort.InferenceSession.create(detectorBytes, { executionProviders: ['wasm'] });
      const embeddingBytes = await checkedAsset(base, manifest.embedder);
      if (!manifest.localFeatures && manifest.preprocessing === 'siglip-full-label' && 'gpu' in navigator) {
        try { embedder = await ort.InferenceSession.create(embeddingBytes, { executionProviders: ['webgpu', 'wasm'], ...(manifest.localFeatures ? {} : {freeDimensionOverrides: { batch_size: 1, num_channels: 3, height: 224, width: 224 }}) }); recognitionBackend = 'webgpu'; }
        catch { embedder = await ort.InferenceSession.create(embeddingBytes, { executionProviders: ['wasm'] }); }
      } else if (!manifest.localFeatures) embedder = await ort.InferenceSession.create(embeddingBytes, { executionProviders: ['wasm'] });
      const catalogBytes = await checkedAsset(base, manifest.catalog);
      catalog = parseCatalog(JSON.parse(new TextDecoder().decode(catalogBytes)), manifest.embeddingModel, manifest.dimension);
      if (manifest.localFeatures) {
        if (!manifest.matcher || manifest.matcherPoints !== 256 || manifest.matcherLayers !== (manifest.localExtractor === 'xfeat' ? 6 : 5)) throw new Error('Нужен совместимый комплект проверки этикетки на 256 точек.');
        self.postMessage({ type: 'loading', message: 'Загружаем проверку деталей этикетки…' });
        const matcherBytes = await checkedAsset(base, manifest.matcher);
        const local = await createLearnedSessions(ort, {extractor: embeddingBytes, matcher: matcherBytes, retriever: manifest.retriever ? await checkedAsset(base, manifest.retriever) : undefined}, manifest.localExtractor === 'xfeat' ? 64 : 128, 'gpu' in navigator);
        embedder = local.extractor; recognitionBackend = local.backend;
        const {matcher,retriever} = local;
        const descriptorDot = manifest.descriptorDot ? await ort.InferenceSession.create(await checkedAsset(base, manifest.descriptorDot), {executionProviders: ['wasm']}) : undefined;
        const { LearnedRecognizer } = await import('./learned');
        learned = new LearnedRecognizer(ort, embedder, matcher, filename => checkedAsset(base, filename), () => cancelled, retriever, descriptorDot);
        await learned.init(manifest.localFeatures, catalog);
      }
      if (manifest.geometryReferences) {
        const { GeometryVerifier } = await import('./geometry');
        const references = JSON.parse(new TextDecoder().decode(await checkedAsset(base, manifest.geometryReferences)));
        geometry = new GeometryVerifier(base, references); await geometry.init();
      }
      self.postMessage({ type: 'ready', catalog, manifest });
    } else if (message.type === 'catalog') {
      if (learned) { self.postMessage({ type: 'catalogReady', catalog }); return; }
      catalog = parseCatalog(message.catalog, manifest.embeddingModel, manifest.dimension);
      self.postMessage({ type: 'catalogReady', catalog });
    } else if (message.type === 'cancel') {
      cancelled = true;
    } else if (message.type === 'scan') {
      cancelled = false;
      learned?.resetScene();
      const bitmap: ImageBitmap = message.bitmap;
      try {
        const started = performance.now();
        const detections = await detect(bitmap, message.dense);
        const detectionMs = performance.now() - started;
        const observations: Observation[] = [];
        for (let i = 0; i < detections.length; i++) {
          if (cancelled) { self.postMessage({type: 'cancelled', requestId: message.requestId}); return; }
          const detection = detections[i];
          const tooSmall = (detection.box[2] - detection.box[0]) * bitmap.width < 40 || (detection.box[3] - detection.box[1]) * bitmap.height < 80;
          if (learned && !tooSmall) {
            const result = await learned.recognize(bitmap, detection.box);
            observations.push({...detection, embedding: [], tooSmall, match: result.match, geometry: result.evidence, verification: result.verification});
            if (result.match.id) self.postMessage({type: 'partial', requestId: message.requestId, observations});
            self.postMessage({ type: 'progress', requestId: message.requestId, done: i + 1, total: detections.length });
            continue;
          }
          const embedding = tooSmall ? [] : await embed(bitmap, detection.box);
          const match = tooSmall ? { ...UNKNOWN } : rank(embedding, catalog, message.threshold, message.margin, geometry ? 20 : 3);
          const verified = geometry && !tooSmall ? await geometry.verify(bitmap, detection.box, match, catalog) : undefined;
          observations.push({ ...detection, embedding, tooSmall, match: verified?.match ?? match, geometry: verified?.evidence });
          self.postMessage({ type: 'progress', requestId: message.requestId, done: i + 1, total: detections.length });
        }
        if (learned && !cancelled) await learned.refineScene(observations, (done,total) => self.postMessage({type: 'progress', requestId: message.requestId, stage: 'Проверяем повторяющиеся бутылки', done,total}));
        self.postMessage({ type: cancelled ? 'cancelled' : 'result', requestId: message.requestId, observations, elapsed: performance.now() - started, detectionMs, recognitionMs: performance.now() - started - detectionMs, recognitionBackend, threads: ort.env.wasm.numThreads });
      } finally { bitmap.close(); learned?.resetScene(); }
    }
  } catch (error) { self.postMessage({ type: cancelled && message.type === 'scan' ? 'cancelled' : 'error', requestId: message.requestId, message: error instanceof Error ? error.message : String(error) }); }
};
