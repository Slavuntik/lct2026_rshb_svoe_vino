import { useEffect, useState } from "react";

export type ShelfAvailability = "pending" | "available" | "unavailable";

// Health-эндпоинт сервиса витрин (apps/shelf-finder) — вне contracts/openapi.yaml по
// решению архитектора (reports/architect-post-merge-review.md §1, "Согласованность"),
// тот же префикс /v1/shelf, что уже проксирует apps/web/vite.config.ts (server.proxy)
// на dev. Держать в синхроне с ShelfScreen.tsx (/shelf-ui, /v1/shelf) и мок-обработчиком
// в src/mocks/handlers.ts.
export const SHELF_HEALTH_PATH = "/v1/shelf/health";
const DEFAULT_TIMEOUT_MS = 1500;

function healthUrl(): string {
  // VITE_SHELF_UI_URL — необязательная переменная сборки: если задан отдельный origin для
  // сервиса витрин, используем его; иначе — тот же относительный путь на своём origin, что
  // сейчас неявно использует раздел (ShelfScreen грузит /shelf-ui с того же origin).
  const base = (import.meta.env.VITE_SHELF_UI_URL as string | undefined)?.trim();
  if (!base) return SHELF_HEALTH_PATH;
  return `${base.replace(/\/+$/, "")}${SHELF_HEALTH_PATH}`;
}

/**
 * Лёгкий живой запрос к сервису витрин с коротким таймаутом (по умолчанию 1.5 с).
 * Важно (reports/architect-post-merge-review.md §3): в проде nginx без локейшна для
 * /shelf-ui и /v1/shelf отдаёт try_files-фолбэк — наш же index.html с кодом 200. Поэтому
 * критерий живости — не просто response.ok, а ok И Content-Type: application/json;
 * фолбэк и любая наша страница — всегда text/html.
 */
export async function probeShelfHealth(timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<ShelfAvailability> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(healthUrl(), {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const contentType = response.headers.get("content-type") ?? "";
    if (!response.ok || !contentType.includes("application/json")) {
      return "unavailable";
    }
    return "available";
  } catch {
    // Таймаут (abort), сеть недоступна, CORS-блокировка — во всех случаях честно
    // "недоступен", без выброса наружу: проверка не должна ронять загрузку приложения
    // или засорять консоль.
    return "unavailable";
  } finally {
    clearTimeout(timer);
  }
}

let inFlight: Promise<ShelfAvailability> | null = null;

/** Один запрос на весь жизненный цикл вкладки — повторные вызовы отдают тот же промис. */
export function checkShelfAvailability(): Promise<ShelfAvailability> {
  if (!inFlight) {
    inFlight = probeShelfHealth();
  }
  return inFlight;
}

/** Только для тестов: сбрасывает кеш между it(), иначе второй тест унаследует результат первого. */
export function resetShelfAvailabilityForTests(): void {
  inFlight = null;
}

/**
 * Реактивное состояние проверки: "pending" до ответа, дальше "available"/"unavailable".
 * Не блокирует рендер — запрос уходит в фоне, состояние обновляется по готовности.
 */
export function useShelfAvailability(): ShelfAvailability {
  const [state, setState] = useState<ShelfAvailability>("pending");
  useEffect(() => {
    let cancelled = false;
    checkShelfAvailability().then((result) => {
      if (!cancelled) setState(result);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}
