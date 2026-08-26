export type ApiMode = "mock" | "real";

/**
 * VITE_API_MODE=mock|real — единственный переключатель источника данных.
 * По умолчанию mock: бэкенда (agents/B-api) ещё нет, приложение обязано жить без него.
 */
export function getApiMode(): ApiMode {
  const raw = import.meta.env.VITE_API_MODE;
  return raw === "real" ? "real" : "mock";
}

export function isMockMode(): boolean {
  return getApiMode() === "mock";
}

/** Базовый путь API — контракт живёт под /v1 (contracts/openapi.yaml servers[0]). */
export const API_BASE_PATH = "/v1";
