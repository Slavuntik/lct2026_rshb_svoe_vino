import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { LabelIndex, Wine, WineColor } from "./types.js";

const root = dirname(fileURLToPath(import.meta.url));
const dataDir = join(root, "..", "data");

export const wines: Wine[] = JSON.parse(readFileSync(join(dataDir, "wines.json"), "utf8"));
export const labelIndex: LabelIndex = JSON.parse(
  readFileSync(join(dataDir, "label-index.json"), "utf8"),
);

const bySlug = new Map(wines.map((wine) => [wine.slug, wine]));

export function getWine(slug: string): Wine | undefined {
  const wine = bySlug.get(slug);
  return wine ? presentWine(wine) : undefined;
}

function presentWine(wine: Wine): Wine {
  return {
    ...wine,
    servingTemp: wine.servingTemp ?? servingTemp(wine.color),
    category: wine.category ?? categoryLabel(wine.color),
    vineyardImageUrl: wine.vineyardImageUrl ?? "/brand/vineyard.png",
    pairings: wine.pairings ?? defaultPairings(),
  };
}

function servingTemp(color: WineColor): string {
  if (color === "sparkling") return "6-8";
  if (color === "white" || color === "rose") return "10-12";
  if (color === "orange") return "12-14";
  return "16-18";
}

function categoryLabel(color: WineColor): string {
  const map: Record<WineColor, string> = {
    white: "Белое сухое",
    red: "Красное сухое",
    rose: "Розовое сухое",
    orange: "Оранж сухое",
    sparkling: "Игристое брют",
  };
  return map[color];
}

function defaultPairings(): Wine["pairings"] {
  return [
    { label: "Блюда из мяса", imageUrl: "/brand/dish-meat.png" },
    { label: "Закуски", imageUrl: "/brand/dish-snack.png" },
    { label: "Блюда из рыбы", imageUrl: "/brand/dish-fish.png" },
  ];
}

export function similarWines(slug: string, limit = 4): Wine[] {
  const source = bySlug.get(slug);
  if (!source) return wines.slice(0, limit);

  return wines
    .filter((wine) => wine.slug !== slug)
    .sort((a, b) => scoreSimilar(source, b) - scoreSimilar(source, a))
    .slice(0, limit)
    .map(presentWine);
}

export function similarByColor(color: WineColor, excludeSlug?: string, limit = 4): Wine[] {
  return wines
    .filter((wine) => wine.color === color && wine.slug !== excludeSlug)
    .slice(0, limit);
}

function scoreSimilar(source: Wine, other: Wine): number {
  let score = 0;
  if (source.color === other.color) score += 3;
  if (source.grape === other.grape) score += 3;
  if (source.region.split(".")[0] === other.region.split(".")[0]) score += 2;
  if (source.producer !== other.producer) score += 1;
  return score;
}
