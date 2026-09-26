import type { AnalogStyle } from "../../lib/apiTypes";
import { wines } from "./wines";

// Мини-реестр эталонных стилей для мока /analogs (contracts/openapi.yaml v0.2).
// В бою resolve_style — fuzzy-поиск агента A по contracts/rag-interface.md; здесь —
// наивное совпадение по синонимам, только чтобы сцена «аналог импортного» жила без бэкенда.
interface StyleFixture extends AnalogStyle {
  synonyms: string[];
}

export const styles: StyleFixture[] = [
  { slug: "prosecco", name: "Просекко", country: "Италия", synonyms: ["просекко", "prosecco", "игристое итальянское"] },
  { slug: "chablis", name: "Шабли", country: "Франция", synonyms: ["шабли", "chablis", "бургундское белое", "шардоне из бургундии"] },
  {
    slug: "provence-rose",
    name: "Прованское розе",
    country: "Франция",
    synonyms: ["прованс", "провансаль", "rose", "розе", "прованское розе"],
  },
  {
    slug: "mosel-riesling-kabinett",
    name: "Мозельский Рислинг Кабинет",
    country: "Германия",
    synonyms: ["рислинг", "riesling", "мозель", "kabinett", "немецкий рислинг"],
  },
  {
    slug: "bordeaux-right-bank",
    name: "Бордо (правый берег)",
    country: "Франция",
    synonyms: ["бордо", "bordeaux", "мерло купаж", "правый берег", "кьянти", "chianti"],
  },
];

function normalize(text: string): string {
  return text.toLowerCase().replace(/[^\wа-яё\s]/gi, " ").trim();
}

/** Наивный аналог rag.resolve_style: ищем первый стиль, чей синоним встречается в запросе. */
export function resolveStyle(query: string): StyleFixture | null {
  const normalized = normalize(query);
  if (!normalized) return null;
  let best: { style: StyleFixture; length: number } | null = null;
  for (const style of styles) {
    for (const synonym of style.synonyms) {
      const normalizedSynonym = normalize(synonym);
      if (normalizedSynonym && normalized.includes(normalizedSynonym)) {
        if (!best || normalizedSynonym.length > best.length) {
          best = { style, length: normalizedSynonym.length };
        }
      }
    }
  }
  return best?.style ?? null;
}

export function winesForStyle(styleSlug: string) {
  return wines.filter((wine) => wine.derived.reference_style_matches.includes(styleSlug));
}

export function popularStyleNames(): string[] {
  return styles.map((style) => style.name);
}

/**
 * v0.3.6 (openapi.yaml, задача тимлида 23.09, аудит architect — тот же класс дефекта, что
 * similar_wines): GET /taste/profile.top_styles_named — {slug,name,country} тем же порядком,
 * что top_styles. Неизвестный слаг (не из этого мини-реестра) — пропущен, остаётся только в
 * top_styles (тот же приём, что similarWinesFor в fixtures/wines.ts).
 */
export function styleNamesFor(slugs: string[]): AnalogStyle[] {
  return slugs
    .map((slug) => styles.find((style) => style.slug === slug))
    .filter((style): style is StyleFixture => Boolean(style))
    .map(({ slug, name, country }) => ({ slug, name, country }));
}
