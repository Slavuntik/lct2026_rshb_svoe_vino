"""Same conservative geometric acceptance rules as the browser hybrid pipeline."""

import math
import numpy as np


def select(evidence, repeated=False):
    ordered = sorted(evidence, key=lambda e: e["inliers"], reverse=True)
    if not ordered:
        return None
    best = ordered[0]
    if not all(math.isfinite(best[k]) for k in ("inliers", "matches", "coverage")):
        return None
    if best["matches"] < best["inliers"] or best["inliers"] < (16 if repeated else 20):
        return None
    if (
        best["inliers"] / max(best["matches"], 1) < (0.5 if repeated else 0.35)
        or best["coverage"] < 0.025
    ):
        return None
    if best["inliers"] - (ordered[1]["inliers"] if len(ordered) > 1 else 0) < 8:
        return None
    return best["id"]


def independent(e):
    return (
        all(math.isfinite(e[k]) for k in ("inliers", "matches", "coverage"))
        and e["matches"] >= e["inliers"] >= 16
        and e["inliers"] / max(e["matches"], 1) >= 0.25
        and e["coverage"] >= 0.025
    )


def plausible(h, reference, query):
    rw, rh = reference
    qw, qh = query
    corners = (
        np.array(
            [
                [0.1 * rw, 0.35 * rh, 1],
                [0.9 * rw, 0.35 * rh, 1],
                [0.9 * rw, 0.97 * rh, 1],
                [0.1 * rw, 0.97 * rh, 1],
            ]
        )
        @ h.T
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        c = corners[:, :2] / corners[:, 2:]
    if (
        not np.isfinite(c).all()
        or np.any(c[:, 0] < -0.5 * qw)
        or np.any(c[:, 0] > 1.5 * qw)
        or np.any(c[:, 1] < -0.25 * qh)
        or np.any(c[:, 1] > 1.5 * qh)
    ):
        return False
    edges = np.roll(c, -1, axis=0) - c
    following = np.roll(edges, -1, axis=0)
    if not np.all(edges[:, 0] * following[:, 1] - edges[:, 1] * following[:, 0] > 0):
        return False
    for a, b in [(0, 1), (3, 2)]:
        dx, dy = c[b] - c[a]
        if dx <= 0 or abs(dy) > dx:
            return False
    area = np.sum(c[:, 0] * np.roll(c[:, 1], -1) - c[:, 1] * np.roll(c[:, 0], -1)) / 2
    return bool(qw * qh * 0.496 * 0.25 < area < qw * qh * 0.496 * 4)


def reference_ambiguities(index, hashes):
    """Exact equality in both feature galleries cannot identify one SKU."""
    groups = {}
    for slug in index["ids"]:
        key = tuple(
            hashes[index[field][slug]]
            for field in ("references", "verificationReferences")
        )
        groups.setdefault(key, []).append(slug)
    return {
        slug: [other for other in group if other != slug]
        for group in groups.values()
        if len(group) > 1
        for slug in group
    }


def select_strong(evidence):
    """A rescue needs label support as well as the normal geometric margin."""
    choice = select(evidence)
    return (
        choice
        if any(
            e["id"] == choice
            and e.get("labelInliers", 0) >= 16
            and e["inliers"] >= 24
            and e["inliers"] / max(e["matches"], 1) >= 0.4
            for e in evidence
        )
        else None
    )
