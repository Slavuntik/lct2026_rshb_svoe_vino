import { expect, it } from 'vitest';
import { parseServerMatches } from '../src/server-client';
const result = { requestId: 'a', pipelineVersion: 'v', catalogVersion: 'c', detectedCount: 1, image: {width: 100, height: 100}, matches: [{ box: [.1, .2, .8, .9], wineId: 'wine', name: 'Wine' }], timingsMs: {processing: 100}, warnings: [] };
it('maps only server accepted matches to the existing drawing interface', () => {
  expect(parseServerMatches(result, new Set(['wine']))[0].match.id).toBe('wine');
  expect(parseServerMatches({...result, matches: []}, new Set(['wine']))).toEqual([]);
});
it('rejects untrusted IDs and invalid frame coordinates', () => {
  expect(() => parseServerMatches(result, new Set())).toThrow();
  for (const box of [[-1,0,1,1],[.8,.2,.1,.9],[0,0,NaN,1]]) expect(() => parseServerMatches({...result,matches:[{...result.matches[0],box}]},new Set(['wine']))).toThrow();
});
