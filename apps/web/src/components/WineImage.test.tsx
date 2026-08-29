import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WineImage } from "./WineImage";

// Ревью 03, риск 1: image_url — хотлинк на vino-svoe.ru без гарда; недоступный чужой хост
// в кадре демо = сломанная иконка браузера. onError обязан подменить <img> на плейсхолдер.
describe("WineImage — гард от битых хотлинков (ревью 03)", () => {
  it("onError на <img> подменяет её на плейсхолдер", () => {
    render(<WineImage src="https://vino-svoe.ru/broken.jpg" alt="Шардоне Резерв" />);

    const img = screen.getByRole("img", { name: "Шардоне Резерв" });
    expect(img.tagName).toBe("IMG");
    expect(screen.queryByTestId("wine-image-placeholder")).not.toBeInTheDocument();

    fireEvent.error(img);

    expect(screen.queryByRole("img", { name: "Шардоне Резерв" })?.tagName).not.toBe("IMG");
    const placeholder = screen.getByTestId("wine-image-placeholder");
    expect(placeholder).toBeInTheDocument();
    expect(placeholder.tagName.toLowerCase()).toBe("svg");
  });

  it("без src сразу показывает плейсхолдер, не дожидаясь ошибки загрузки", () => {
    render(<WineImage alt="Без картинки" />);
    expect(screen.getByTestId("wine-image-placeholder")).toBeInTheDocument();
  });
});
