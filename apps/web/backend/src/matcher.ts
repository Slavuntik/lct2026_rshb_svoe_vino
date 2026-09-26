import { createHash } from "node:crypto";
import { getWine, labelIndex, similarByColor, similarWines, wines } from "./catalog.js";
import type { Candidate, ScanResult, Wine, WineColor } from "./types.js";

const COLORS: WineColor[] = ["red", "white", "rose", "orange", "sparkling"];

export function matchLabel(filename: string, buffer: Buffer): ScanResult {
  const hash = createHash("sha256").update(buffer).digest("hex");
  const normalized = normalizeName(filename);

  const byHash = labelIndex.byHash[hash];
  if (byHash) return exact(byHash, 0.94);

  const byFile = labelIndex.byFilename[normalized] ?? labelIndex.byFilename[filename];
  if (byFile) return exact(byFile, 0.91);

  const bySlugInName = wines.find(
    (wine) =>
      normalized.includes(wine.slug) ||
      normalized.includes(normalizeName(wine.producer)) ||
      normalized.includes(normalizeName(wine.name)),
  );
  if (bySlugInName && looksLikeLabelName(normalized, bySlugInName)) {
    return exact(bySlugInName.slug, 0.88);
  }

  return unknown(buffer);
}

export function bestSlug(result: ScanResult): string {
  if (result.wine) return result.wine.slug;
  if (result.candidates[0]) return result.candidates[0].slug;
  if (result.similar[0]) return result.similar[0].slug;
  return wines[0].slug;
}

function exact(slug: string, top1: number): ScanResult {
  const wine = getWine(slug);
  if (!wine) return unknown(Buffer.from(slug));

  const others = similarWines(slug, 4).map((item, index) => ({
    slug: item.slug,
    score: round(top1 - 0.45 - index * 0.08),
  }));

  const candidates: Candidate[] = [{ slug, score: top1 }, ...others];
  const f1Top5 = round(Math.min(0.99, top1 + 0.05));

  return {
    found: true,
    wine,
    confidence: { f1Top1: top1, f1Top5 },
    candidates,
    similar: [],
  };
}

function unknown(buffer: Buffer): ScanResult {
  const seed = buffer.length + buffer[0] + (buffer[buffer.length - 1] ?? 0);
  const color = COLORS[seed % COLORS.length];
  const similar = similarByColor(color, undefined, 6);
  const fallback = similar.length ? similar : wines.slice(0, 4);

  return {
    found: false,
    wine: null,
    confidence: { f1Top1: 0.18, f1Top5: 0.44 },
    candidates: fallback.map((wine, index) => ({
      slug: wine.slug,
      score: round(0.22 - index * 0.03),
    })),
    similar: fallback,
  };
}

function looksLikeLabelName(filename: string, wine: Wine): boolean {
  return (
    filename.includes(wine.slug) ||
    (filename.includes(normalizeName(wine.producer)) &&
      filename.includes(normalizeName(wine.name.split(" ")[0] ?? "")))
  );
}

function normalizeName(value: string): string {
  return value
    .toLowerCase()
    .replace(/^.*[/\\]/, "")
    .replace(/\s+/g, "-");
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
