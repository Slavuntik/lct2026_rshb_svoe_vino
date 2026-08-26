import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "../mocks/server";
import { resetMockState } from "../mocks/state";

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
  window.localStorage.clear();
});

afterAll(() => {
  server.close();
});
