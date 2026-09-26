import { wines } from "./catalog.js";
import type { SommelierRequest, SommelierResponse, Wine, WineColor } from "./types.js";

const COLOR_BY_DISH: Record<string, WineColor[]> = {
  steak: ["red"],
  fish: ["white", "sparkling"],
  poultry: ["white", "rose"],
  cheese: ["red", "orange", "white"],
  picnic: ["rose", "sparkling", "white"],
  aperitif: ["sparkling", "white", "rose"],
};

const SWEET_OK = new Set(["off-dry", "any"]);

export function advise(input: SommelierRequest): SommelierResponse {
  const colors = COLOR_BY_DISH[input.dish] ?? ["red", "white", "rose", "sparkling"];
  const budget = budgetCap(input.budget);

  const ranked = wines
    .filter((wine) => colors.includes(wine.color))
    .filter((wine) => (budget == null ? true : (wine.price ?? 0) <= budget))
    .filter((wine) => (SWEET_OK.has(input.sweetness) ? true : wine.color !== "sparkling" || input.occasion !== "dinner"))
    .sort((a, b) => (b.roskachestvoRating ?? 0) - (a.roskachestvoRating ?? 0))
    .slice(0, 3);

  const picked = ranked.length ? ranked : wines.slice(0, 3);

  return {
    wines: picked,
    rationale: reason(input, picked[0]),
  };
}

function budgetCap(budget: string): number | null {
  if (budget === "low") return 800;
  if (budget === "mid") return 1500;
  if (budget === "high") return null;
  return null;
}

function reason(input: SommelierRequest, wine: Wine): string {
  const occasion =
    input.occasion === "dinner"
      ? "к ужину"
      : input.occasion === "gift"
        ? "в подарок"
        : "к случаю";
  return `${wine.producer} «${wine.name}» — ${occasion}. ${wine.pairing}.`;
}
