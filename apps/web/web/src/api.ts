import type { ScanResult, SommelierRequest, SommelierResponse, Wine, WineColor, WinePairing } from "./types";

const base = import.meta.env.VITE_API_URL ?? "";

const dishImages = ["/brand/dish-meat.png", "/brand/dish-snack.png", "/brand/dish-fish.png"];

type StandWine = {
  wine_id?: string;
  slug?: string;
  name?: string;
  winery_name?: string | null;
  winery?: string | null;
  region_name?: string | null;
  image_url?: string | null;
  public_rating?: number | null;
  score?: number;
};

type StandScan = {
  slug?: string | null;
  not_in_catalog?: boolean;
  card?: {
    wine_id?: string;
    name?: string;
    winery_name?: string;
    image_url?: string;
    source?: Record<string, unknown>;
  } | null;
  similar?: StandWine[];
  analogs?: StandWine[];
  candidates?: StandWine[];
};

type StandResolve = {
  matches?: Array<{ wine_id: string; name: string; winery_name: string; confidence: number }>;
  low_confidence?: boolean;
  analogs?: StandWine[];
};

type StandWineCard = {
  wine_id: string;
  source: Record<string, unknown>;
  similar_wines?: StandWine[];
};

let guestToken: Promise<string> | null = null;

async function parse<T>(res: Promise<Response>): Promise<T> {
  const response = await res;
  if (!response.ok) {
    throw new Error(`api_${response.status}`);
  }
  return (await response.json()) as T;
}

function token(): Promise<string> {
  if (!guestToken) {
    guestToken = parse<{ access_token: string }>(
      fetch(`${base}/v1/auth/guest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ age_confirmed: true, consent_version: "v1" }),
      }),
    )
      .then((body) => body.access_token)
      .catch((error) => {
        guestToken = null;
        throw error;
      });
  }
  return guestToken;
}

function thumb(id: string): string {
  return `${base}/v1/case-thumbs/${encodeURIComponent(id)}.webp`;
}

function asWine(item: StandWine, fallbackId = ""): Wine {
  const id = item.wine_id || item.slug || fallbackId;
  return {
    slug: id,
    name: item.name || "Вино",
    producer: item.winery_name || item.winery || "",
    region: item.region_name || "",
    grape: "",
    year: 0,
    color: "red",
    description: "",
    roskachestvoRating: ratingOf(item.public_rating),
    pairing: "",
    alcohol: 0,
    volume: 750,
    price: null,
    imageUrl: item.image_url || (id ? thumb(id) : "/labels/bottle.png"),
  };
}

function fromCard(card: NonNullable<StandScan["card"]>): StandWine {
  const source = card.source ?? {};
  const rating = source.public_rating;
  return {
    wine_id: card.wine_id,
    name: card.name || textValue(source.name),
    winery_name: card.winery_name || textValue(source.winery_name) || textValue(source.winery),
    region_name: textValue(source.region_name),
    image_url: card.image_url || textValue(source.image_url),
    public_rating: typeof rating === "number" ? rating : null,
  };
}

function ratingOf(value: number | null | undefined): number | null {
  if (typeof value !== "number") return null;
  return value <= 5 ? value * 20 : value;
}

function mapScan(body: StandScan): ScanResult {
  const found = Boolean(body.slug) && !body.not_in_catalog;
  const pool = [...(body.candidates ?? []), ...(body.similar ?? []), ...(body.analogs ?? [])];
  const heroSource = body.card?.wine_id
    ? fromCard(body.card)
    : pool.find((item) => (item.wine_id || item.slug) === body.slug) ?? pool[0];
  const hero = found && heroSource ? asWine(heroSource, body.slug || "") : null;
  const similar = (body.not_in_catalog ? body.similar?.length ? body.similar : body.analogs : body.similar?.length ? body.similar : body.candidates) ?? [];
  return {
    found,
    wine: hero,
    confidence: { f1Top1: 0, f1Top5: 0 },
    candidates: [],
    similar: similar
      .map((item) => asWine(item))
      .filter((item) => item.slug && item.slug !== hero?.slug)
      .slice(0, 6),
  };
}

function mapResolve(body: StandResolve): ScanResult {
  const matches = body.matches ?? [];
  const found = matches.length > 0 && !body.low_confidence;
  const hero = found ? asWine(matches[0]) : null;
  const rest = (found ? matches.slice(1) : matches).map((item) => asWine(item));
  const analogs = (body.analogs ?? []).map((item) => asWine(item));
  return {
    found,
    wine: hero,
    confidence: { f1Top1: matches[0]?.confidence ?? 0, f1Top5: 0 },
    candidates: [],
    similar: [...rest, ...analogs].filter((item) => item.slug !== hero?.slug).slice(0, 6),
  };
}

function capitalize(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function textValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return "";
}

function mapColor(raw: string): WineColor {
  const value = raw.toLowerCase();
  if (value.includes("крас")) return "red";
  if (value.includes("роз")) return "rose";
  if (value.includes("оран")) return "orange";
  if (value.includes("игр")) return "sparkling";
  return "white";
}

function mapCard(body: StandWineCard): Wine {
  const source = body.source;
  const grapes = Array.isArray(source.grapes) ? source.grapes.map(String) : [];
  const temp = source.serving_temp_c;
  const serving = Array.isArray(temp) ? temp.join("-") : textValue(temp);
  const foods = Array.isArray(source.food_pairings) ? source.food_pairings.map(String) : [];
  const pairings: WinePairing[] = foods.slice(0, 3).map((label, index) => ({
    label,
    imageUrl: dishImages[index % dishImages.length],
  }));
  const publicRating = typeof source.public_rating === "number" ? source.public_rating : null;
  const colorName = textValue(source.color);
  const sugar = textValue(source.sugar_category);
  return {
    slug: body.wine_id,
    name: textValue(source.name) || "Вино",
    producer: textValue(source.winery_name),
    region: textValue(source.region_name),
    grape: grapes.join(", "),
    year: typeof source.vintage === "number" ? source.vintage : 0,
    color: mapColor(colorName),
    description: textValue(source.description),
    roskachestvoRating: publicRating == null ? null : publicRating <= 5 ? publicRating * 20 : publicRating,
    pairing: foods.join(", "),
    alcohol: typeof source.abv_percent === "number" ? source.abv_percent : 0,
    volume: 750,
    price: null,
    imageUrl: textValue(source.image_url) || thumb(body.wine_id),
    servingTemp: serving,
    category: [capitalize(colorName), sugar].filter(Boolean).join(" "),
    vineyardImageUrl: "/brand/vineyard.png",
    pairings: pairings.length ? pairings : undefined,
  };
}

export function scanLabel(file: File, signal?: AbortSignal): Promise<ScanResult> {
  const body = new FormData();
  body.append("image", file);
  return parse<StandScan>(fetch(`${base}/v1/scan/photo`, { method: "POST", body, signal })).then(mapScan);
}

export function resolveText(text: string, signal?: AbortSignal): Promise<ScanResult> {
  return token().then((access) =>
    parse<StandResolve>(
      fetch(`${base}/v1/scan/resolve`, {
        method: "POST",
        signal,
        headers: { Authorization: `Bearer ${access}`, "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      }),
    ).then(mapResolve),
  );
}

export function getWine(slug: string): Promise<Wine> {
  return token().then((access) =>
    parse<StandWineCard>(
      fetch(`${base}/v1/wines/${encodeURIComponent(slug)}`, {
        headers: { Authorization: `Bearer ${access}` },
      }),
    ).then(mapCard),
  );
}

export function getSimilar(slug: string): Promise<Wine[]> {
  return token().then((access) =>
    parse<StandWineCard>(
      fetch(`${base}/v1/wines/${encodeURIComponent(slug)}`, {
        headers: { Authorization: `Bearer ${access}` },
      }),
    ).then((body) => (body.similar_wines ?? []).map((item) => asWine(item)).filter((item) => item.slug !== slug)),
  );
}

export function askSommelier(payload: SommelierRequest): Promise<SommelierResponse> {
  return parse(
    fetch(`${base}/api/sommelier`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}
