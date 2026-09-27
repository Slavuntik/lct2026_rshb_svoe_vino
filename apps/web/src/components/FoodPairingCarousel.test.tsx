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
 * Карусель «Сочетание с блюдами» (задача тимлида 27.09, макет Figma). Фото есть ровно у
 * одного из 9 тегов (см. FoodPairingCarousel.tsx — обоснование, почему остальные два
 * доступных кадра сознательно не привязаны ни к одному тегу); все прочие — типографская
 * плитка-монограмма, не пустое место и не выдуманная картинка.
 */
describe("FoodPairingCarousel", () => {
  it("«Блюда из рыбы» — реальное фото; тег без своей фотографии — типографская плитка первой буквой", () => {
    renderApp(
      <FoodPairingCarousel pairings={[pairing(ru.scan.dishCategoryFish), pairing(ru.scan.dishCategoryCheese)]} />,
    );

    const fishTile = screen.getByText(ru.scan.dishCategoryFish).closest(".food-pairing__tile") as HTMLElement;
    const photo = fishTile.querySelector("img.food-pairing__photo") as HTMLImageElement;
    expect(photo).toBeInTheDocument();
    expect(photo).toHaveAttribute("src", "/brand/dish-fish.jpg");
    // Тег уже назван текстом рядом — декоративное фото не дублирует его в alt.
    expect(photo).toHaveAttribute("alt", "");

    const cheeseTile = screen.getByText(ru.scan.dishCategoryCheese).closest(".food-pairing__tile") as HTMLElement;
    expect(cheeseTile.querySelector("img")).not.toBeInTheDocument();
    const monogram = cheeseTile.querySelector(".food-pairing__monogram");
    expect(monogram).toHaveTextContent("С");
    expect(monogram).toHaveAttribute("aria-hidden", "true");
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
