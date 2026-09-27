import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FoodPairingCarousel } from "./FoodPairingCarousel";
import { ru } from "../i18n/ru";
import type { WinePairing } from "../lib/apiTypes";
import { renderApp } from "../test/renderApp";

function pairing(tag: string): WinePairing {
  return { tag, score: null, triggered_rules: [] };
}

/**
 * Карусель «Сочетание с блюдами» (задача тимлида 27.09, макет Figma; волна 2 — фото от
 * Вячеслава). 8 из 9 канонических тегов + сырой портальный тег «Морепродукты» — реальные
 * фото; «Блюда из рыбы» — старое фото первой волны; тег вне словаря (сырой текст портала
 * basis=catalog) — типографская плитка-монограмма, не пустое место и не выдуманная картинка.
 */
describe("FoodPairingCarousel", () => {
  it("канонические теги с фото — правильный src у каждого, alt пуст (тег уже назван текстом рядом)", () => {
    const photoTags: [string, string][] = [
      [ru.scan.dishCategoryFish, "/brand/dish-fish.jpg"],
      [ru.scan.dishCategoryOysters, "/brand/dish-ustritsy.jpg"],
      [ru.scan.dishCategoryCheese, "/brand/dish-syry.jpg"],
      [ru.scan.dishCategoryPoultry, "/brand/dish-ptitsa.jpg"],
      [ru.scan.dishCategorySalads, "/brand/dish-salaty.jpg"],
      [ru.scan.dishCategoryBruschetta, "/brand/dish-brusketty.jpg"],
      [ru.scan.dishCategoryBbq, "/brand/dish-bbq.jpg"],
      [ru.scan.dishCategoryAsian, "/brand/dish-aziatskaya.jpg"],
      [ru.scan.dishCategoryDesserts, "/brand/dish-desert.jpg"],
      // Не канонический тег — частый сырой тег портала (см. FoodPairingCarousel.tsx), та же картинка, что «Устрицы».
      [ru.wineCard.pairingsRawTagSeafood, "/brand/dish-ustritsy.jpg"],
    ];
    renderApp(<FoodPairingCarousel pairings={photoTags.map(([tag]) => pairing(tag))} />);

    for (const [tag, src] of photoTags) {
      const tile = screen.getByText(tag).closest(".food-pairing__tile") as HTMLElement;
      const photo = tile.querySelector("img.food-pairing__photo") as HTMLImageElement;
      expect(photo, `фото тега «${tag}»`).toBeInTheDocument();
      expect(photo).toHaveAttribute("src", src);
      expect(photo).toHaveAttribute("alt", "");
      expect(tile.querySelector(".food-pairing__monogram")).not.toBeInTheDocument();
    }
  });

  it("dish-vypechka.jpg (второй кадр «Выпечка и десерты») никуда не подключён — только desert.jpg", () => {
    const { container } = renderApp(<FoodPairingCarousel pairings={[pairing(ru.scan.dishCategoryDesserts)]} />);
    const photo = container.querySelector("img.food-pairing__photo");
    expect(photo).toHaveAttribute("src", "/brand/dish-desert.jpg");
    expect(container.querySelector('img[src="/brand/dish-vypechka.jpg"]')).not.toBeInTheDocument();
  });

  it("тег вне словаря (сырой текст портала, не «Морепродукты») — типографская плитка первой буквой", () => {
    // "Птица" — реальный сырой food_pairings из mocks/fixtures/wines.ts, не один из 9 тегов.
    renderApp(<FoodPairingCarousel pairings={[pairing("Птица")]} />);
    const tile = screen.getByText("Птица").closest(".food-pairing__tile") as HTMLElement;
    expect(tile.querySelector("img")).not.toBeInTheDocument();
    const monogram = tile.querySelector(".food-pairing__monogram");
    expect(monogram).toHaveTextContent("П");
    expect(monogram).toHaveAttribute("aria-hidden", "true");
  });

  it("фото не загрузилось (onError) — тихий откат на типографскую плитку, не битая иконка браузера", () => {
    renderApp(<FoodPairingCarousel pairings={[pairing(ru.scan.dishCategoryBbq)]} />);
    const tile = screen.getByText(ru.scan.dishCategoryBbq).closest(".food-pairing__tile") as HTMLElement;
    const photo = tile.querySelector("img.food-pairing__photo") as HTMLImageElement;
    fireEvent.error(photo);
    expect(tile.querySelector("img")).not.toBeInTheDocument();
    expect(tile.querySelector(".food-pairing__monogram")).toHaveTextContent("B");
  });

  it("pairings=[] — ничего не рендерит (WinePairingsBlock сам показывает message в этом случае)", () => {
    const { container } = renderApp(<FoodPairingCarousel pairings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("трек — доступный composite-виджет (role=group, aria-roledescription=carousel, подписан заголовком блока)", () => {
    renderApp(<FoodPairingCarousel pairings={[pairing(ru.scan.dishCategoryBbq)]} />);
    const track = screen.getByRole("group", { name: "К чему подать" });
    expect(track).toHaveAttribute("aria-roledescription", "carousel");
    expect(track).toHaveAttribute("tabindex", "0");
  });

  it("одна пара — без точек пагинации (нечего листать)", () => {
    const { container } = renderApp(<FoodPairingCarousel pairings={[pairing(ru.scan.dishCategoryBbq)]} />);
    expect(container.querySelector(".food-pairing__dots")).not.toBeInTheDocument();
  });

  it("стрелки клавиатуры на треке переключают активную точку пагинации (карусель листается с клавиатуры)", () => {
    const { container } = renderApp(
      <FoodPairingCarousel
        pairings={[pairing(ru.scan.dishCategoryOysters), pairing(ru.scan.dishCategoryCheese), pairing(ru.scan.dishCategoryBbq)]}
      />,
    );
    const track = screen.getByRole("group", { name: "К чему подать" });
    const dots = () => Array.from(container.querySelectorAll(".food-pairing__dot"));

    expect(dots()[0]).toHaveClass("food-pairing__dot--active");

    fireEvent.keyDown(track, { key: "ArrowRight" });
    expect(dots()[1]).toHaveClass("food-pairing__dot--active");
    expect(dots()[0]).not.toHaveClass("food-pairing__dot--active");

    fireEvent.keyDown(track, { key: "End" });
    expect(dots()[2]).toHaveClass("food-pairing__dot--active");

    fireEvent.keyDown(track, { key: "Home" });
    expect(dots()[0]).toHaveClass("food-pairing__dot--active");

    // За левую границу — не проваливается в отрицательный индекс.
    fireEvent.keyDown(track, { key: "ArrowLeft" });
    expect(dots()[0]).toHaveClass("food-pairing__dot--active");
  });

  it("точки скрыты от вспомогательных технологий (карусель уже доступна через сам трек), но кликабельны мышью", () => {
    const { container } = renderApp(
      <FoodPairingCarousel pairings={[pairing(ru.scan.dishCategoryOysters), pairing(ru.scan.dishCategoryCheese)]} />,
    );
    const dotsWrap = container.querySelector(".food-pairing__dots");
    expect(dotsWrap).toHaveAttribute("aria-hidden", "true");

    const dots = Array.from(container.querySelectorAll(".food-pairing__dot"));
    fireEvent.click(dots[1]);
    expect(dots[1]).toHaveClass("food-pairing__dot--active");
  });
});
