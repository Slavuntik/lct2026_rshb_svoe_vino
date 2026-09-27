/**
 * Тонкая обёртка над window.matchMedia для настройки ОС «уменьшить движение».
 * Не бросает исключение, если matchMedia недоступен (часть версий jsdom в тестах) —
 * по умолчанию считаем, что уменьшать движение не нужно. Вынесено отдельно от компонента
 * меню, чтобы тесты могли замокать window.matchMedia и проверить ветку без анимации.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}
