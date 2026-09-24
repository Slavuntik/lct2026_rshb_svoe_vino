import { describe, expect, it } from 'vitest';
import { iou, nms, normalize, parseCatalog, rank, tiles, Tracker, UNKNOWN } from '../src/core';
import type { Catalog, Observation } from '../src/types';
const catalog: Catalog = { version: 1, embeddingModel: 'test', dimension: 2, wines: [
  { id: 'a', name: 'A', brand: '', region: '', group: '', references: [[1, 0], [.99, .01]] },
  { id: 'b', name: 'B', brand: '', region: '', group: '', references: [[0, 1]] },
] };
const observation = (id: string | null, x = 0): Observation => ({ box: [x, 0, x + .2, .7], score: .9, embedding: [1, 0], tooSmall: false, match: { ...UNKNOWN, id } });
describe('catalog and open set matching', () => {
  it('ranks SKU rather than treating two references of one SKU as competing products', () => { const m = rank([1, 0], catalog); expect(m.id).toBe('a'); expect(m.margin).toBe(1); });
  it('rejects unfamiliar and ambiguous crops', () => { expect(rank([-.7, -.7], catalog).id).toBeNull(); expect(rank(normalize([1, 1]), catalog, .6, .1).id).toBeNull(); });
  it('rejects incompatible dimensions, duplicate IDs and NaN before inference', () => {
    expect(() => parseCatalog(catalog, 'other')).toThrow();
    expect(() => parseCatalog({ ...catalog, wines: [catalog.wines[0], catalog.wines[0]] })).toThrow();
    expect(() => parseCatalog({ ...catalog, wines: [{ ...catalog.wines[0], references: [[NaN, 0]] }] })).toThrow();
    expect(() => parseCatalog(catalog, 'test', 3)).toThrow();
  });
  it('normalizes imported references', () => { const c = parseCatalog({ ...catalog, wines: [{ ...catalog.wines[0], references: [[5, 0]] }] }); expect(c.wines[0].references[0]).toEqual([1, 0]); });
});
describe('geometry and temporal confirmation', () => {
  it('deduplicates overlapping tiles but keeps adjacent bottles', () => { const boxes = [{ box: [0, 0, .2, .8] as const, score: .9 }, { box: [.01, 0, .21, .8] as const, score: .8 }, { box: [.3, 0, .5, .8] as const, score: .7 }]; expect(nms(boxes.map(b => ({ ...b, box: [...b.box] }))).length).toBe(2); expect(iou([0, 0, 0, 0], [0, 0, 0, 0])).toBe(0); });
  it('dense tiles overlap and stay inside the original image', () => { expect(tiles(960, 1280, true)).toHaveLength(5); for (const b of tiles(960, 1280, true)) expect(b[2] <= 960 && b[3] <= 1280).toBe(true); });
  it('requires repeated evidence and resets on unknown or changed SKU', () => { const t = new Tracker(); expect(t.update([observation('a')], 0)[0].confirmed).toBe(false); t.update([observation('a')], 100); expect(t.update([observation('a')], 200)[0].confirmed).toBe(true); expect(t.update([observation(null)], 300)[0].confirmed).toBe(false); expect(t.update([observation('b')], 400)[0].seen).toBe(1); });
  it('does not reuse an identity after movement or a long gap', () => { const t = new Tracker(); const first = t.update([observation('a')], 0)[0]; expect(t.update([observation('a', .7)], 200)[0].trackId).not.toBe(first.trackId); expect(t.update([observation('a', .7)], 5000)[0].seen).toBe(1); });
  it('supports slow inference without counting stale frames as new observations', () => { const t = new Tracker(); t.update([observation('a')], 0, 3, 6000); t.update([observation('a')], 5000, 3, 6000); expect(t.update([observation('a')], 10000, 3, 6000)[0].confirmed).toBe(true); });
  it('does not assign one track to two detections', () => { const t = new Tracker(); t.update([observation('a')], 0); const next = t.update([observation('a'), observation('a', .01)], 100); expect(new Set(next.map(x => x.trackId)).size).toBe(2); });
});
