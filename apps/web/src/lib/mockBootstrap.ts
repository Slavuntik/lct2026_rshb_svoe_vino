import { isMockMode } from "./env";

let readyPromise: Promise<void> | null = null;

/**
 * Поднимает MSW (динамический import — отдельный чанк, см. Lighthouse-заметку в c-report.md)
 * и возвращает ОДИН И ТОТ ЖЕ промис при повторных вызовах. Вызывается дважды с разными целями:
 *  - main.tsx стартует его сразу, но НЕ ждёт перед рендером — первый пейнт лендинга не должен
 *    платить за загрузку мок-слоя;
 *  - apiClient дожидается его перед КАЖДЫМ реальным запросом — на случай (в теории) клика
 *    быстрее, чем успел стартовать воркер.
 */
export function ensureMocksReady(): Promise<void> {
  // Под vitest перехват уже делает msw/node (src/mocks/server.ts + test/setup.ts) —
  // грузить ещё и браузерный воркер (навигатор.serviceWorker под jsdom) незачем и небезопасно.
  if (!isMockMode() || import.meta.env.MODE === "test") return Promise.resolve();
  if (!readyPromise) {
    readyPromise = import("../mocks/browser")
      .then(async ({ worker }) => {
        await worker.start({ onUnhandledRequest: "bypass" });
      })
      .catch((error: unknown) => {
        // Мок не поднялся — не блокируем приложение: honest network_error из apiClient
        // лучше белого экрана. См. main.tsx.
        console.error("[mocks] failed to start MSW worker", error);
      });
  }
  return readyPromise;
}
