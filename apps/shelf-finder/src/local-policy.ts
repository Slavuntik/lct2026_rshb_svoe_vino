export interface DetailEvidence {
  id: string;
  inliers: number;
  matches: number;
  coverage: number;
}

/** Conservative rejection; geometric support is not a probability or exact-vintage proof. */
export function selectDetailMatch(evidence: DetailEvidence[], repeated = false): string | null {
  if (!evidence.length) return null;
  const ordered = [...evidence].sort((a, b) => b.inliers - a.inliers);
  const best = ordered[0];
  if (![best.inliers, best.matches, best.coverage].every(Number.isFinite)) return null;
  if (best.matches < best.inliers || best.inliers < (repeated ? 16 : 20)) return null;
  if (best.inliers / Math.max(best.matches, 1) < (repeated ? 0.5 : 0.35) || best.coverage < 0.025)
    return null;
  if (best.inliers - (ordered[1]?.inliers ?? 0) < 8) return null;
  return best.id;
}

/** Upright shelf labels must stay upright, convex and reasonably sized after projection. */
export function plausibleLabelProjection(
  h: ArrayLike<number>,
  reference: [number, number],
  query: [number, number]
): boolean {
  const [rw, rh] = reference,
    [qw, qh] = query;
  const corners = [
    [rw * 0.1, rh * 0.35],
    [rw * 0.9, rh * 0.35],
    [rw * 0.9, rh * 0.97],
    [rw * 0.1, rh * 0.97]
  ].map(([x, y]) => {
    const z = h[6] * x + h[7] * y + h[8];
    return [(h[0] * x + h[1] * y + h[2]) / z, (h[3] * x + h[4] * y + h[5]) / z];
  });
  if (!corners.flat().every(Number.isFinite)) return false;
  if (corners.some(([x, y]) => x < -0.5 * qw || x > 1.5 * qw || y < -0.25 * qh || y > 1.5 * qh))
    return false;
  const crosses = corners.map((p, i) => {
    const n = corners[(i + 1) % 4],
      nn = corners[(i + 2) % 4];
    return (n[0] - p[0]) * (nn[1] - n[1]) - (n[1] - p[1]) * (nn[0] - n[0]);
  });
  if (!crosses.every((v) => v > 0)) return false;
  for (const [a, b] of [
    [0, 1],
    [3, 2]
  ]) {
    const dx = corners[b][0] - corners[a][0],
      dy = corners[b][1] - corners[a][1];
    if (dx <= 0 || Math.abs(dy) > dx) return false;
  }
  const area =
    corners.reduce(
      (s, p, i) => s + p[0] * corners[(i + 1) % 4][1] - p[1] * corners[(i + 1) % 4][0],
      0
    ) / 2;
  return area > qw * qh * 0.496 * 0.25 && area < qw * qh * 0.496 * 4;
}

/** Different learned descriptors must corroborate the same catalog image. */
export function independentAgreement(evidence: DetailEvidence): boolean {
  return (
    [evidence.inliers, evidence.matches, evidence.coverage].every(Number.isFinite) &&
    evidence.inliers >= 16 &&
    evidence.matches >= evidence.inliers &&
    evidence.inliers / Math.max(1, evidence.matches) >= 0.25 &&
    evidence.coverage >= 0.025
  );
}
