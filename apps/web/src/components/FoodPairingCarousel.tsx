import { useRef, useState, type KeyboardEvent, type UIEvent } from "react";
import { useI18n } from "../i18n";
import { ru } from "../i18n/ru";
import { prefersReducedMotion } from "../lib/motion";
import type { WinePairing } from "../lib/apiTypes";

/**
 * Фото для тегов гастропар (задача тимлида 27.09, макет Figma «Сочетание с блюдами» на
 * карточке вина; волна 2 — фото от Вячеслава прилетели тем же днём). 8 из 9 канонических
 * тегов `pipeline/ref/food_pairing_rules.yaml` (`portal_tag_defaults`, те же 9, что
 * `ru.scan.dishCategory*` — contracts/post-scan.md v1.2 §4.0) закрыты кадрами
 * `public/brand/dish-{ustritsy,syry,ptitsa,salaty,brusketty,bbq,aziatskaya,vypechka}.jpg`
 * (600×600, тарелка по центру на белом — уже в едином стиле друг с другом). Из двух кадров
 * «Выпечка и десерты» (vypechka — круассаны, desert — чизкейк с ягодами) выбран `desert.jpg`:
 * пятно соуса даёт больше контраста на маленькой плитке 60px, `dish-vypechka.jpg` остаётся в
 * public/brand про запас, но никуда не подключён (задача тимлида — «второй не подключать»).
 *
 * «Блюда из рыбы» — по-прежнему старое `dish-fish.jpg` (108×108, из первой волны, другой
 * стиль/размер) — тимлид попросит у Вячеслава кадр в новом стиле; замена сводится к тому же
 * пути `/brand/dish-fish.jpg`, кода трогать не придётся.
 *
 * `ru.wineCard.pairingsRawTagSeafood` («Морепродукты») — НЕ один из 9 канонических тегов, а
 * частый СЫРОЙ тег портала уровня basis=catalog (contracts/post-scan.md §1, встречается в
 * mocks/fixtures/wines.ts) — покрыт тем же фото устриц по прямому указанию тимлида («устрицы и
 * морепродукты» — оба про морепродукты, картинка одна).
 *
 * Ключи словаря — ЗНАЧЕНИЯ `ru.scan.dishCategory*`/`ru.wineCard.pairingsRawTagSeafood` (те же
 * поля, что уже заполняет API в WinePairing.tag), а не литералы кириллицы: src/test/
 * i18n-hardcoded-strings.test.ts запрещает кириллицу вне i18n/mocks/test.
 *
 * Монограмма-заглушка (FoodPairingTile ниже) НЕ удалена: она остаётся для любого тега вне
 * этого словаря (basis=catalog — сырой текст портала шире 9 тегов, например «Птица», «Твёрдые
 * сыры») и для случая, если файл фото не загрузился (onError у <img> ниже).
 */
const TAG_PHOTO: Partial<Record<string, string>> = {
  [ru.scan.dishCategoryFish]: "/brand/dish-fish.jpg",
  [ru.scan.dishCategoryOysters]: "/brand/dish-ustritsy.jpg",
  [ru.wineCard.pairingsRawTagSeafood]: "/brand/dish-ustritsy.jpg",
  [ru.scan.dishCategoryCheese]: "/brand/dish-syry.jpg",
  [ru.scan.dishCategoryPoultry]: "/brand/dish-ptitsa.jpg",
  [ru.scan.dishCategorySalads]: "/brand/dish-salaty.jpg",
  [ru.scan.dishCategoryBruschetta]: "/brand/dish-brusketty.jpg",
  [ru.scan.dishCategoryBbq]: "/brand/dish-bbq.jpg",
  [ru.scan.dishCategoryAsian]: "/brand/dish-aziatskaya.jpg",
  [ru.scan.dishCategoryDesserts]: "/brand/dish-desert.jpg",
};

function monogramLetter(tag: string): string {
  return tag.trim().charAt(0).toUpperCase() || "?";
}

function FoodPairingTile({ tag }: { tag: string }) {
  const photo = TAG_PHOTO[tag];
  const [photoFailed, setPhotoFailed] = useState(false);
  const showPhoto = photo && !photoFailed;
  return (
    <div className="food-pairing__tile" data-testid="food-pairing-tile">
      {showPhoto ? (
        // Мягкая подложка --bg под фото (задача тимлида 27.09): белая тарелка кадра на кремовом
        // --surface плитки иначе выглядит вклеенной заплаткой — кольцо той же плотности, что
        // фон страницы, снимает жёсткий стык. Декоративно — тег уже назван текстом ниже.
        <span className="food-pairing__photo-frame">
          <img src={photo} alt="" className="food-pairing__photo" onError={() => setPhotoFailed(true)} />
        </span>
      ) : (
        // Типографская плитка (нет своей фотографии ИЛИ фото не загрузилось) — кружок-
        // монограмма первой буквой тега на токенах --accent-soft/--accent, тот же контрастный
        // дуэт, что .btn--secondary.
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
