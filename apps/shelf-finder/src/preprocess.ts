import type { Box } from './types';

/** Mirror WineScan's query_view: 3% context, then a lower-label view for tall bottles. */
export function recognitionViews(box: Box, width: number, height: number): Box[] {
  const [x0, y0, x1, y1] = box;
  const mx = (x1 - x0) * .03, my = (y1 - y0) * .03;
  const full: Box = [Math.round(Math.max(0, x0 - mx)), Math.round(Math.max(0, y0 - my)), Math.round(Math.min(width, x1 + mx)), Math.round(Math.min(height, y1 + my))];
  const w = full[2] - full[0], h = full[3] - full[1];
  const label: Box = h / Math.max(w, 1) < 1.8 ? [...full] : [full[0], full[1] + Math.round(h * .40), full[2], full[1] + Math.round(h * .97)];
  return [full, label];
}
