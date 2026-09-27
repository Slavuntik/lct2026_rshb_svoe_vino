import { afterEach, describe, expect, it } from "vitest";
import { focusSomelierWidget, SOMELIER_WIDGET_INPUT_ID } from "./somelierWidget";

describe("focusSomelierWidget — «Спросить сомелье об этом вине» (задача тимлида 27.09)", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("ставит фокус на поле виджета по стабильному id", () => {
    const input = document.createElement("input");
    input.id = SOMELIER_WIDGET_INPUT_ID;
    document.body.appendChild(input);

    focusSomelierWidget();

    expect(document.activeElement).toBe(input);
  });

  it("виджета ещё нет на странице (карточка не догрузилась) — тихо ничего не делает, не падает", () => {
    expect(() => focusSomelierWidget()).not.toThrow();
  });
});
