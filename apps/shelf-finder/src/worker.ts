/// <reference lib="webworker" />
import * as ort from 'onnxruntime-web/wasm';
import { nms, normalize, parseCatalog, rank, tiles, UNKNOWN } from './core';
import type { Box, Catalog, Detection, ModelManifest, Observation } from './types';

let detector: ort.InferenceSession;
let embedder: ort.InferenceSession;
let catalog: Catalog;
let manifest: ModelManifest;
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
  const scale = Math.min(size / width, size / height);
  const dx = (size - width * scale) / 2, dy = (size - height * scale) / 2;
  context.drawImage(bitmap, ...[box[0], box[1], width, height, dx, dy, width * scale, height * scale] as [number, number, number, number, number, number, number, number]);
  const rgba = context.getImageData(0, 0, size, size).data;
  const data = new Float32Array(3 * size * size);
  const mean = [.485, .456, .406], std = [.229, .224, .225];
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
  const { input } = tensor(bitmap, crop, 224, 'embedding');
  const outputs = await embedder.run({ [embedder.inputNames[0]]: input });
  const vector = normalize(outputs[embedder.outputNames[0]].data as Float32Array);
  input.dispose(); Object.values(outputs).forEach(x => x.dispose());
  return vector;
}
self.onmessage = async (event: MessageEvent) => {
  const message = event.data;
  try {
    if (message.type === 'init') {
      const base = new URL('models/', message.base).href;
      const response = await fetch(new URL('manifest.json', base));
      if (!response.ok) throw new Error('Комплект моделей не подготовлен. См. README приложения: scripts/prepare.py.');
      manifest = await response.json();
      ort.env.wasm.numThreads = 1;
      ort.env.wasm.wasmPaths = new URL('runtime/', message.base).href;
      const detectorBytes = await checkedAsset(base, manifest.detector);
      detector = await ort.InferenceSession.create(detectorBytes, { executionProviders: ['wasm'] });
      const embeddingBytes = await checkedAsset(base, manifest.embedder);
      embedder = await ort.InferenceSession.create(embeddingBytes, { executionProviders: ['wasm'] });
      const catalogBytes = await checkedAsset(base, manifest.catalog);
      catalog = parseCatalog(JSON.parse(new TextDecoder().decode(catalogBytes)), manifest.embeddingModel, manifest.dimension);
      self.postMessage({ type: 'ready', catalog, manifest });
    } else if (message.type === 'catalog') {
      catalog = parseCatalog(message.catalog, manifest.embeddingModel, manifest.dimension);
      self.postMessage({ type: 'catalogReady', catalog });
    } else if (message.type === 'scan') {
      const bitmap: ImageBitmap = message.bitmap;
      try {
        const started = performance.now();
        const detections = await detect(bitmap, message.dense);
        const detectionMs = performance.now() - started;
        const observations: Observation[] = [];
        for (let i = 0; i < detections.length; i++) {
          const detection = detections[i];
          const tooSmall = (detection.box[2] - detection.box[0]) * bitmap.width < 40 || (detection.box[3] - detection.box[1]) * bitmap.height < 80;
          const embedding = tooSmall ? [] : await embed(bitmap, detection.box);
          observations.push({ ...detection, embedding, tooSmall, match: tooSmall ? { ...UNKNOWN } : rank(embedding, catalog, message.threshold, message.margin) });
          self.postMessage({ type: 'progress', requestId: message.requestId, done: i + 1, total: detections.length });
        }
        self.postMessage({ type: 'result', requestId: message.requestId, observations, elapsed: performance.now() - started, detectionMs, recognitionMs: performance.now() - started - detectionMs });
      } finally { bitmap.close(); }
    }
  } catch (error) { self.postMessage({ type: 'error', requestId: message.requestId, message: error instanceof Error ? error.message : String(error) }); }
};
