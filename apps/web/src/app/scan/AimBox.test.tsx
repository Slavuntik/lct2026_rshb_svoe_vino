import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AimBox, DEFAULT_FRAME } from "./AimBox";

/**
 * Прицел под jsdom. Раскладки здесь нет (clientWidth всегда 0), поэтому проверять
 * перетаскивание в пикселях бессмысленно — это проверялось бы против пустого layout, а не
 * против поведения. Проверяем то, что от компонента реально зависит: рамка и ручки на месте,
 * подпись доступна, и отсутствие раскладки не роняет обработчики.
 *
 * Содержательный сценарий — «кнопка по рамке отправляет доли кадра в API» — проверяется на
 * уровне экрана (ScanScreen.aim.test.tsx), где виден весь путь до клиента.
 */

describe("AimBox — прицел пользователя", () => {
  it("рисует рамку и четыре угловые ручки", () => {
    render(<AimBox previewUrl="blob:photo" frame={DEFAULT_FRAME} onFrameChange={vi.fn()} label="Рамка" />);

    expect(screen.getByTestId("aim-frame")).toBeInTheDocument();
    for (const grip of ["nw", "ne", "sw", "se"]) {
      expect(screen.getByTestId(`aim-grip-${grip}`)).toBeInTheDocument();
    }
  });

  it("рамка подписана для скринридера", () => {
    render(<AimBox previewUrl="blob:photo" frame={DEFAULT_FRAME} onFrameChange={vi.fn()} label="Рамка вокруг бутылки" />);

    expect(screen.getByRole("group", { name: "Рамка вокруг бутылки" })).toBeInTheDocument();
  });

  it("без раскладки перетаскивание ничего не меняет и не падает", () => {
    const onFrameChange = vi.fn();
    render(<AimBox previewUrl="blob:photo" frame={DEFAULT_FRAME} onFrameChange={onFrameChange} label="Рамка" />);

    const frame = screen.getByTestId("aim-frame");
    frame.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    frame.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 40, clientY: 40 }));
    frame.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }));

    expect(onFrameChange).not.toHaveBeenCalled();
  });
});
