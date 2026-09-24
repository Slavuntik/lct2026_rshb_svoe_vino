import { describe, expect, it } from 'vitest';
import { recognitionViews } from '../src/preprocess';

describe('recognition views', () => {
  it('adds context and focuses the second view on the label of a tall bottle', () => {
    expect(recognitionViews([100, 100, 200, 500], 960, 1280)).toEqual([[97, 88, 203, 512], [97, 258, 203, 499]]);
  });
  it('keeps crops within the photo and does not cut short packages', () => {
    expect(recognitionViews([0, 0, 100, 100], 100, 100)).toEqual([[0, 0, 100, 100], [0, 0, 100, 100]]);
  });
});
