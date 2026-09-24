import { describe, expect, it } from 'vitest';
import { selectDetailMatch } from '../src/local-policy';
const evidence = (id: string, inliers: number, matches = 40, coverage = 0.15) => ({
  id,
  inliers,
  matches,
  coverage
});
describe('geometric refusal policy', () => {
  it('accepts distributed support with a clear lead', () => {
    expect(selectDetailMatch([evidence('known', 32), evidence('other', 12)])).toBe('known');
  });
  it('leaves weak, ambiguous and concentrated matches unmarked', () => {
    expect(selectDetailMatch([evidence('weak', 12)])).toBeNull();
    expect(selectDetailMatch([evidence('a', 32), evidence('b', 28)])).toBeNull();
    expect(selectDetailMatch([evidence('tiny-patch', 32, 40, 0.001)])).toBeNull();
    expect(selectDetailMatch([evidence('outliers', 25, 100)])).toBeNull();
  });
  it('requires stronger inlier ratio when using an automatically confirmed repeat', () => {
    expect(selectDetailMatch([evidence('repeat', 18, 22)], true)).toBe('repeat');
    expect(selectDetailMatch([evidence('repeat', 18, 40)], true)).toBeNull();
    expect(selectDetailMatch([evidence('repeat', 18, 22)])).toBeNull();
  });
  it('rejects empty and invalid evidence', () => {
    expect(selectDetailMatch([])).toBeNull();
    expect(selectDetailMatch([evidence('invalid', NaN)])).toBeNull();
    expect(selectDetailMatch([evidence('invalid', 30, 20)])).toBeNull();
  });
});

describe('label projection', () => {
  it('accepts ordinary upright geometry and rejects reflections and extreme slants', async () => {
    const { plausibleLabelProjection: valid } = await import('../src/local-policy');
    expect(valid([1, 0, 0, 0, 1, 0, 0, 0, 1], [160, 512], [160, 512])).toBe(true);
    expect(valid([-1, 0, 160, 0, 1, 0, 0, 0, 1], [160, 512], [160, 512])).toBe(false);
    expect(valid([1, 0, 0, 3, 1, 0, 0, 0, 1], [160, 512], [160, 512])).toBe(false);
    expect(valid([0.01, 0, 0, 0, 0.01, 0, 0, 0, 1], [160, 512], [160, 512])).toBe(false);
    expect(valid([1, 0, 0, 0, 1, 0, 0, 0, 0], [160, 512], [160, 512])).toBe(false);
  });
});

it('requires a second descriptor model to corroborate a proposed identity', async () => {
  const { independentAgreement } = await import('../src/local-policy');
  expect(independentAgreement(evidence('agreement', 25, 50))).toBe(true);
  expect(independentAgreement(evidence('accidental', 13, 68))).toBe(false);
  expect(independentAgreement(evidence('inconsistent', 20, 100))).toBe(false);
  expect(independentAgreement(evidence('tiny', 25, 50, 0.001))).toBe(false);
});
