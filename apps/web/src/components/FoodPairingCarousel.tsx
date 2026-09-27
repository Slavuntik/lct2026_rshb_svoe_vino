import { useRef, useState, type KeyboardEvent, type UIEvent } from "react";
import { useI18n } from "../i18n";
import { ru } from "../i18n/ru";
import { prefersReducedMotion } from "../lib/motion";
import type { WinePairing } from "../lib/apiTypes";

/**
 * Фото для тегов гастропар (задача тимлида 27.09, макет Figma «Сочетание с блюдами» на
 * карточке вина). Картинок на все 9 тегов `pipeline/ref/food_pairing_rules.yaml`
 * (`portal_tag_defaults`, те же 9, что `ru.scan.dishCategory*` — contracts/post-scan.md v1.2
 * §4.0) у нас нет — реально доступна ровно ОДНА, однозначно узнаваемая: design/ui-prototype/
 * assets/brand/dish-fish.png (лосось на тарелке → «Блюда из рыбы», перенесена в
 * public/brand/dish-fish.jpg, см. reports/frontend-card-widgets.md).
 *
 * dish-meat.png/dish-snack.png сознательно НЕ привязаны ни к одному тегу — та же причина, по
 * которой их уже отклонили для чипов режима «Блюдо» на сканере (решение тимлида 27.09,
 * reports/frontend-design-transfer.md: «каждая изображает ровно одно блюдо, мясо/птица/BBQ
 * пересекаются, сопоставление ненадёжно»). dish-meat.png — светлое жареное филе с овощным
 * гарниром: с равным основанием могло бы изображать «Блюда из птицы» ИЛИ «BBQ», выбор одного
 * из двух был бы выдумкой, не переносом макета. dish-snack.png — овощная нарезка без хлеба и
 * заправки: не «Салаты» (нет заправки/смешивания) и не «Брускетты» (нет хлеба). Остальные 8
 * тегов — типографская плитка (см. FoodPairingTile ниже), а не пустое место.
 *
 * Ключи словаря — ЗНАЧЕНИЯ `ru.scan.dishCategory*` (то же поле, что заполняет API в
 * WinePairing.tag), а не литералы кириллицы: src/test/i18n-hardcoded-strings.test.ts запрещает
 * кириллицу вне i18n/mocks/test, а mocks/fixtures/dishPairing.ts уже задаёт этот же приём.
 */
const TAG_PHOTO: Partial<Record<string, string>> = {
  [ru.scan.dishCategoryFish]: "/brand/dish-fish.jpg",
};

function monogramLetter(tag: string): string {
  return tag.trim().charAt(0).toUpperCase() || "?";
}

function FoodPairingTile({ tag }: { tag: string }) {
  const photo = TAG_PHOTO[tag];
  return (
    <div className="food-pairing__tile" data-testid="food-pairing-tile">
      {photo ? (
        // Декоративное фото — тег уже назван текстом ниже, дублировать его в alt не нужно
        // (тот же приём, что миниатюры сорта/региона в WineCardContent.tsx: alt="").
        <img src={photo} alt="" className="food-pairing__photo" />
      ) : (
        // Типографская плитка (нет своей фотографии) — кружок-монограмма первой буквой тега
        // на токенах --accent-soft/--accent, тот же контрастный дуэт, что .btn--secondary.
        <span className="food-pairing__monogram" aria-hidden="true">
          {monogramLetter(tag)}
        </span>
      )}
      <span className="food-pairing__label">{tag}</span>
    </div>
  );
}

interface FoodPairingCarouselProps {
  pairings: WinePairing[];
}

/**
 * Карусель фото блюд (Figma «Сочетание с блюдами», задача тимлида 27.09, ответ на
 * `GET /wines/{id}/pairings` — contracts/post-scan.md v1.2 §1, до 3 тегов).
 *
 * Листается свайпом (нативный `overflow-x:auto` — жест браузера/тачпада, отдельный JS не
 * нужен) и с клавиатуры: сам трек — один composite-виджет (`role="group"`,
 * `aria-roledescription="carousel"`, единственный таб-стоп), стрелки листают на плитку.
 * Точки снизу — чисто визуальная подсказка позиции (`aria-hidden`, не второй способ
 * управления для скринридера — им уже служит сам трек).
 * Уважает «уменьшить движение»: `prefersReducedMotion()` → `behavior:"auto"` вместо "smooth".
 */
export function FoodPairingCarousel({ pairings }: FoodPairingCarouselProps) {
  const { t } = useI18n();
  const trackRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);

  function scrollToIndex(index: number) {
    const track = trackRef.current;
    if (!track) return;
    const clamped = Math.max(0, Math.min(index, pairings.length - 1));
    setActive(clamped);
    const tile = track.children[clamped];
    if (tile instanceof HTMLElement && typeof track.scrollTo === "function") {
      track.scrollTo({ left: tile.offsetLeft, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        scrollToIndex(active + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        scrollToIndex(active - 1);
        break;
      case "Home":
        event.preventDefault();
        scrollToIndex(0);
        break;
      case "End":
        event.preventDefault();
        scrollToIndex(pairings.length - 1);
        break;
      default:
        break;
    }
  }

  // Свайп/тач-скролл двигает трек напрямую (браузер), сюда прилетает обычный "scroll" —
  // подхватываем точки под фактическую позицию, чтобы жест и клавиатура не расходились.
  function handleScroll(event: UIEvent<HTMLDivElement>) {
    const track = event.currentTarget;
    let closest = 0;
    let bestDistance = Infinity;
    Array.from(track.children).forEach((child, index) => {
      if (!(child instanceof HTMLElement)) return;
      const distance = Math.abs(child.offsetLeft - track.scrollLeft);
      if (distance < bestDistance) {
        bestDistance = distance;
        closest = index;
      }
    });
    setActive(closest);
  }

  if (pairings.length === 0) return null;

  return (
    <div className="food-pairing">
      <div
        ref={trackRef}
        className="food-pairing__track"
        role="group"
        aria-roledescription="carousel"
        aria-label={t("wineCard.pairingsTitle")}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        onScroll={handleScroll}
      >
        {pairings.map((pairing) => (
          <FoodPairingTile key={pairing.tag} tag={pairing.tag} />
        ))}
      </div>
      {pairings.length > 1 && (
        <div className="food-pairing__dots" aria-hidden="true">
          {pairings.map((pairing, index) => (
            <button
              key={pairing.tag}
              type="button"
              tabIndex={-1}
              className={`food-pairing__dot${index === active ? " food-pairing__dot--active" : ""}`}
              onClick={() => scrollToIndex(index)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
