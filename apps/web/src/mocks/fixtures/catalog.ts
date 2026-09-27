import type { CatalogItem } from "../../lib/apiTypes";
import { bottlePlaceholderDataUri } from "./image";
import { caseFallbackWines, wines } from "./wines";

/**
 * Мок GET /v1/catalog (contracts/openapi.yaml v0.3.7, задача тимлида 27.09) — НЕ порт боевого
 * `case_catalog.json` (2103 позиции, зона backend): детерминированная фикстура для dev/тестов,
 * тот же приём, что mocks/fixtures/{chat,dishPairing}.ts. Реальные 6 вин + 1 фолбэк каталога
 * кейса (уже существующие фикстуры, чтобы клик по плитке вёл на настоящую карточку в mock-
 * режиме) + 50 синтетических — хватает проверить постраничность (лимит по умолчанию 24 →
 * 2-3 страницы), поиск, фильтры и заглушку без превью (contracts/openapi.yaml: 49 из 2103 без
 * файла на боевом каталоге — здесь каждая 5-я синтетическая позиция).
 */
const REAL_ITEMS: CatalogItem[] = [...wines, ...caseFallbackWines].map((wine) => ({
  wine_id: wine.wine_id,
  name: wine.source.name,
  winery: wine.source.winery_name ?? null,
  color: wine.source.color ?? null,
  sugar: wine.source.sugar_category ?? null,
  image_url: wine.source.image_url ?? null,
}));

const SYNTH_WINERIES = ["Дом Кубанских Холмов", "Усадьба Тамань", "Шато Крымский Берег", "Винодельня Утриш"];
const SYNTH_COLORS = ["белое", "красное", "розовое"];
const SYNTH_SUGARS = ["сухое", "полусухое", "полусладкое"];
const SYNTH_HEX = ["#C9A227", "#7D2A3C", "#D98A96"];

function syntheticItem(index: number): CatalogItem {
  const bucket = index % SYNTH_COLORS.length;
  return {
    wine_id: `synthetic-catalog-wine-${index}`,
    name: `Тестовое вино ${index}`,
    winery: SYNTH_WINERIES[index % SYNTH_WINERIES.length],
    color: SYNTH_COLORS[bucket],
    sugar: SYNTH_SUGARS[index % SYNTH_SUGARS.length],
    // Каждая 5-я — без превью (v0.3.7: image_url=null, позиция не выбрасывается из выдачи).
    image_url: index % 5 === 0 ? null : bottlePlaceholderDataUri(SYNTH_HEX[bucket]),
  };
}

const SYNTH_ITEMS: CatalogItem[] = Array.from({ length: 50 }, (_, i) => syntheticItem(i + 1));

// Порядок как в контракте — по имени (casefold), тай-брейк по wine_id — детерминизм постранички.
export const CATALOG_FIXTURE: CatalogItem[] = [...REAL_ITEMS, ...SYNTH_ITEMS].sort((a, b) => {
  const byName = a.name.toLowerCase().localeCompare(b.name.toLowerCase(), "ru");
  return byName !== 0 ? byName : a.wine_id.localeCompare(b.wine_id);
});
