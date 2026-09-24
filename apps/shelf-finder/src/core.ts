import type { Box, Catalog, Detection, Match, Observation, Track } from './types';

export const UNKNOWN: Match = { id: null, score: 0, margin: 0, candidates: [] };
export function iou(a: Box, b: Box): number {
  const intersect = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const area = (x: Box) => Math.max(0, x[2] - x[0]) * Math.max(0, x[3] - x[1]);
  return intersect / (area(a) + area(b) - intersect || 1);
}
export function nms(items: Detection[], threshold = 0.45): Detection[] {
  const kept: Detection[] = [];
  for (const item of [...items].sort((a, b) => b.score - a.score)) if (kept.every(other => iou(item.box, other.box) < threshold)) kept.push(item);
  return kept;
}
export function normalize(values: ArrayLike<number>): number[] {
  const v = Array.from(values); const norm = Math.sqrt(v.reduce((s, x) => s + x * x, 0));
  return norm > 0 ? v.map(x => x / norm) : v;
}
export function rank(embedding: number[], catalog: Catalog, threshold = .9, minMargin = .05): Match {
  const scores = catalog.wines.map(wine => ({ id: wine.id, score: Math.max(-1, ...wine.references.map(reference => reference.reduce((s, x, i) => s + x * embedding[i], 0))) })).sort((a, b) => b.score - a.score);
  const first = scores[0];
  if (!first) return { ...UNKNOWN };
  const margin = first.score - (scores[1]?.score ?? 0);
  return { id: first.score >= threshold && margin >= minMargin ? first.id : null, score: first.score, margin, candidates: scores.slice(0, 3) };
}
export function parseCatalog(value: unknown, model?: string, dimension?: number): Catalog {
  const c = value as Catalog;
  if (!c || c.version !== 1 || !Number.isInteger(c.dimension) || c.dimension < 1 || c.dimension > 4096 || typeof c.embeddingModel !== 'string' || !Array.isArray(c.wines) || c.wines.length > 3000) throw new Error('Неверный формат каталога.');
  if ((model && c.embeddingModel !== model) || (dimension && c.dimension !== dimension)) throw new Error('Каталог создан для другой модели распознавания.');
  const ids = new Set<string>();
  for (const wine of c.wines) {
    if (!wine || typeof wine.id !== 'string' || !wine.id || ids.has(wine.id) || typeof wine.name !== 'string' || !wine.name || !['brand', 'region', 'group'].every(k => typeof wine[k as keyof typeof wine] === 'string') || !Array.isArray(wine.references) || !wine.references.length || wine.references.length > 50) throw new Error('В каталоге неверная или повторяющаяся позиция.');
    ids.add(wine.id);
    for (const ref of wine.references) {
      if (!Array.isArray(ref) || ref.length !== c.dimension || !ref.every(x => typeof x === 'number' && Number.isFinite(x)) || ref.every(x => x === 0)) throw new Error('Неверный вектор эталона.');
    }
    wine.references = wine.references.map(normalize);
  }
  return c;
}
export function tiles(width: number, height: number, dense: boolean): Box[] {
  if (!dense) return [[0, 0, width, height]];
  return [[0, 0, width, height], [0, 0, width * .6, height * .6], [width * .4, 0, width, height * .6], [0, height * .4, width * .6, height], [width * .4, height * .4, width, height]];
}
/** Reset names after a gap, ambiguous evidence or abrupt movement. Each box is matched once. */
export class Tracker {
  private tracks: Track[] = []; private nextId = 1;
  reset() { this.tracks = []; }
  update(observations: Observation[], now: number, required = 3, maxGapMs = 1800): Track[] {
    const previous = this.tracks.filter(t => now - t.lastSeen < maxGapMs);
    const used = new Set<number>();
    this.tracks = observations.map(observation => {
      const old = previous.filter(t => !used.has(t.trackId)).sort((a, b) => iou(b.box, observation.box) - iou(a.box, observation.box))[0];
      const matched = old && iou(old.box, observation.box) >= .4;
      if (matched) used.add(old.trackId);
      const stable = matched && observation.match.id !== null && old.match.id === observation.match.id;
      const seen = stable ? old.seen + 1 : observation.match.id ? 1 : 0;
      return { ...observation, trackId: matched ? old.trackId : this.nextId++, lastSeen: now, seen, confirmed: seen >= required && !observation.tooSmall };
    });
    return this.tracks;
  }
}
