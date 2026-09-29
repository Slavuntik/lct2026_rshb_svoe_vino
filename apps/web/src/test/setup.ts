import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "../mocks/server";
import { resetMockState } from "../mocks/state";

// jsdom does not implement the native dialog API; browser QA covers modal focus.
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  // Без test.globals в vite.config.ts у @testing-library/react нет глобального afterEach,
  // чтобы самому зарегистрировать автоочистку DOM — делаем это явно, иначе разметка одного
  // it() утекает в следующий и ломает getBy*/getByRole на "нашлось несколько элементов".
  cleanup();
  server.resetHandlers();
  resetMockState();
  // setupFiles работает и для тестов с @vitest-environment node (без window/DOM вообще —
  // например, apiClient.scanPhoto.node.test.ts, где нужен настоящий File/FormData Node,
  // а не подмена jsdom), поэтому гард обязателен.
  if (typeof window !== "undefined") {
    window.localStorage.clear();
  }
});

afterAll(() => {
  server.close();
});
