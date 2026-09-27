import { useEffect, useState } from "react";

export type ShelfAvailability = "pending" | "available" | "unavailable";

// Health-эндпоинт сервиса витрин (apps/shelf-finder) — вне contracts/openapi.yaml по
// решению архитектора (reports/architect-post-merge-review.md §1, "Согласованность"),
// тот же префикс /v1/shelf, что уже проксирует apps/web/vite.config.ts (server.proxy)
// на dev. Держать в синхроне с ShelfScreen.tsx (/shelf-ui, /v1/shelf) и мок-обработчиком
// в src/mocks/handlers.ts.
export const SHELF_HEALTH_PATH = "/v1/shelf/health";
const DEFAULT_TIMEOUT_MS = 1500;
export const SHELF_RETRY_MS = 5000;
const READY_TTL_MS = 30000;

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
 * проверяем HTTP-статус, Content-Type и ready/state/catalogSize/busy в JSON;
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
    const body: unknown = await response.json();
    if (!body || typeof body !== "object") return "unavailable";
    const health = body as Record<string, unknown>;
    return health.ready === true && health.state === "ready"
      && Number.isInteger(health.catalogSize) && (health.catalogSize as number) >= 0
      && typeof health.busy === "boolean" ? "available" : "unavailable";
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
let cached: { value: ShelfAvailability; expires: number } | null = null;
let generation = 0;

/** Share pending probes and cache results briefly, including transient failures. */
export function checkShelfAvailability(): Promise<ShelfAvailability> {
  if (inFlight) return inFlight;
  if (cached && Date.now() < cached.expires) return Promise.resolve(cached.value);
  const current = generation;
  const request = probeShelfHealth().then(value => {
    if (current === generation) {
      cached = { value, expires: Date.now() + (value === "available" ? READY_TTL_MS : SHELF_RETRY_MS) };
    }
    return value;
  }).finally(() => { if (inFlight === request) inFlight = null; });
  inFlight = request;
  return request;
}

/** Reset module state between tests; stale pending probes cannot overwrite it. */
export function resetShelfAvailabilityForTests(): void {
  generation++;
  inFlight = null;
  cached = null;
}

/** Recover from warmup/network failures without requiring a page reload. */
export function useShelfAvailability(): ShelfAvailability {
  const [state, setState] = useState<ShelfAvailability>("pending");
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      const result = await checkShelfAvailability();
      if (!cancelled) {
        setState(result);
        timer = setTimeout(refresh, SHELF_RETRY_MS);
      }
    };
    void refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);
  return state;
}
