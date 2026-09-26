import { ru } from "../../i18n/ru";
import type { DishInfo, DishPairingResponse, PairingWineBasis, PairingWineItem } from "../../lib/apiTypes";
import { caseFallbackWines, wines, type WineFixture } from "./wines";

// Мок «Что подать» по фото блюда (задача тимлида 22.09, POST /v1/pairing/dish-photo,
// POST /v1/pairing/dish) — ратифицированный контракт: contracts/post-scan.md v1.1 §4,
// contracts/openapi.yaml v0.3.4 (DishInfo/PairingWineItem/DishPairingResponse). Как и
// остальные мок-фикстуры (styles.ts, chat.ts) — упрощённый стенд-ин, не порт реальной
// модели/движка правил, но форма ответа и статусные нюансы (§4.4) — по контракту буквально.
//
// 9 категорий — буквально `portal_tag_defaults` из pipeline/ref/food_pairing_rules.yaml
// (contracts/post-scan.md v1.1 §4.0: "category — строго один из 9 тегов… значение вне этого
// списка нигде не возникает как легальное"). Значения берём из ru.ts (единственный источник
// строк, ScanScreen.tsx рендерит те же ключи через t()) — не дублируем литералы, чтобы
// список не мог разъехаться.
export const DISH_CATEGORIES = [
  ru.scan.dishCategoryOysters,
  ru.scan.dishCategoryCheese,
  ru.scan.dishCategoryFish,
  ru.scan.dishCategoryPoultry,
  ru.scan.dishCategorySalads,
  ru.scan.dishCategoryBruschetta,
  ru.scan.dishCategoryBbq,
  ru.scan.dishCategoryAsian,
  ru.scan.dishCategoryDesserts,
] as const;

export type DishCategory = (typeof DISH_CATEGORIES)[number];

function toPairingWine(wine: WineFixture, reason: string, basis: PairingWineBasis): PairingWineItem {
  return {
    wine_id: wine.wine_id,
    name: wine.source.name,
    winery: wine.source.winery_name,
    color: wine.source.color,
    sugar: wine.source.sugar_category,
    image_url: wine.source.image_url,
    reason,
    basis,
  };
}

function dishInfo(partial: Partial<DishInfo> & Pick<DishInfo, "source">): DishInfo {
  return { name: null, category: null, alternatives: [], ingredients: [], ...partial };
}

// По 1–2 вина на категорию из уже существующих фикстур (mocks/fixtures/wines.ts) — никаких
// новых вымышленных вин, только новые "reason"-тексты под конкретное блюдо. Один намеренно —
// позиция каталога КЕЙСА (case-shato-yuzhny-sklon-saperavi-2019, source_url на vino-svoe.ru,
// категория "Сыры"): доказывает "правило ссылок" (contracts/post-scan.md v1.1 §5) и на этом
// новом списке — клик ведёт на НАШУ внутреннюю карточку (WineCardContent), внешняя ссылка —
// только оттуда. ≤2 вина на категорию здесь всегда из разных виноделен — не нарушает лимит
// "не более 2 на одну винодельню" (§4.3) даже без явной группировки в моке.
const CATEGORY_WINES: Record<DishCategory, PairingWineItem[]> = {
  "Устрицы": [
    toPairingWine(wines[3], "Тонкие пузырьки и чёткая кислотность освежают устрицы, не перебивая вкус.", "catalog"),
  ],
  "Сыры": [
    toPairingWine(wines[1], "Плотные танины и пряный финиш держатся рядом с твёрдыми сырами.", "catalog"),
    toPairingWine(caseFallbackWines[0], "Плотная структура и ежевичные тона не теряются рядом с выдержанными сырами.", "rules"),
  ],
  "Блюда из рыбы": [toPairingWine(wines[0], "Мягкая кислотность и маслянистое тело не забивают рыбу.", "catalog")],
  "Блюда из птицы": [
    toPairingWine(wines[4], "Живая кислотность и ароматика хорошо держат птицу в специях.", "rules"),
  ],
  "Салаты": [toPairingWine(wines[2], "Лёгкое тело и освежающая кислотность не спорят с зеленью.", "catalog")],
  "Брускетты": [toPairingWine(wines[3], "Пузырьки и кислотность чистят нёбо между брускеттами.", "rules")],
  "BBQ": [
    toPairingWine(wines[1], "Плотные танины выдержат жирность мяса с углей, специи гриля не потеряются.", "catalog"),
    toPairingWine(wines[5], "Бархатистые танины и нотки какао не теряются рядом с дымом гриля.", "rules"),
  ],
  "Азиатская кухня": [
    toPairingWine(wines[4], "Лёгкая сладость гасит остроту, кислотность держит вкус не приторным.", "catalog"),
  ],
  "Выпечка и десерты": [toPairingWine(wines[4], "Мёд и белый персик рифмуются со сладкой выпечкой.", "catalog")],
};

export function isDishCategory(value: string): value is DishCategory {
  return (DISH_CATEGORIES as readonly string[]).includes(value);
}

/** POST /v1/pairing/dish-photo — детерминировано по имени файла, тот же приём, что /scan/photo.
 * `dish` — ВСЕГДА объект (contracts/openapi.yaml 0.3.4 DishPairingResponse.dish не nullable),
 * у not_food/bottle/unsure просто name=category=null. */
export function buildDishPhotoResponse(filename: string): DishPairingResponse {
  const lower = filename.toLowerCase();
  if (lower.includes("notfood")) {
    return {
      status: "not_food",
      dish: dishInfo({ source: "vlm" }),
      wines: [],
      message: "На фото не похоже на блюдо — попробуйте другой кадр.",
      timing_ms: 420,
    };
  }
  if (lower.includes("bottle")) {
    return {
      status: "bottle",
      dish: dishInfo({ source: "vlm" }),
      wines: [],
      message: "Похоже, на фото бутылка вина, а не блюдо.",
      timing_ms: 380,
    };
  }
  // "unsure-guess" — модель успела дать слабую догадку (alternatives непуст) до отказа: UI
  // должен предложить именно ЭТИ теги, а не сразу все 9 (contracts/post-scan.md v1.1 §4.4).
  if (lower.includes("unsure-guess")) {
    return {
      status: "unsure",
      dish: dishInfo({ source: "zero_shot", alternatives: [ru.scan.dishCategoryPoultry, ru.scan.dishCategorySalads] }),
      wines: [],
      message: "Не уверены, что за блюдо на фото — похоже на один из вариантов ниже.",
      timing_ms: 480,
    };
  }
  if (lower.includes("unsure")) {
    return {
      status: "unsure",
      dish: dishInfo({ source: "none" }),
      wines: [],
      message: "Не уверены, что за блюдо на фото — уточните категорию.",
      timing_ms: 510,
    };
  }
  return {
    status: "food",
    dish: {
      name: "Стейк рибай на гриле",
      category: ru.scan.dishCategoryBbq,
      // 0-3 ДРУГИХ тега из тех же 9 (не альтернативные названия блюда) — post-scan.md §4.1 п.1.
      alternatives: [ru.scan.dishCategoryPoultry, ru.scan.dishCategoryAsian],
      ingredients: ["говядина", "розмарин", "чёрный перец"],
      source: "vlm",
    },
    wines: CATEGORY_WINES.BBQ,
    message: null,
    timing_ms: 640,
  };
}

/** POST /v1/pairing/dish — ручной выбор категории и исправление по чипам alternatives/unsure.
 * Категория вне 9 тегов сюда не долетает — handlers.ts отбраковывает её 400 validation_error
 * ДО вызова этой функции (contracts/post-scan.md v1.1 §4.2), поэтому здесь `category` уже
 * сужен до `DishCategory`. */
export function buildDishCategoryResponse(category: DishCategory, dish?: string): DishPairingResponse {
  return {
    status: "food",
    dish: {
      // dish.source="user" всегда (§4.2); свободный текст не разбирается на ингредиенты —
      // пустая строка/отсутствие трактуются как "имени нет", а не как название "категория".
      name: dish?.trim() || null,
      category,
      alternatives: [],
      ingredients: [],
      source: "user",
    },
    wines: CATEGORY_WINES[category],
    message: null,
    timing_ms: 90,
  };
}
