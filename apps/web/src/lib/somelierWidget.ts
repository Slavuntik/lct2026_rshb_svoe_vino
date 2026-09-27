import { prefersReducedMotion } from "./motion";

/**
 * SomelierCardWidget.tsx рендерится ровно один раз на экран (внутри WineCardContent —
 * WineCardScreen ИЛИ инлайн-результат подтверждённого скана, никогда оба сразу на одной
 * странице) — фиксированный id безопасен, дублирования быть не может.
 */
export const SOMELIER_WIDGET_INPUT_ID = "somelier-widget-question";

/**
 * «Спросить сомелье об этом вине» (WineCardScreen.tsx / ScanScreen.tsx) раньше уводило на
 * отдельный /app/chat с префиллом. Задача тимлида 27.09 (макет Figma): виджет сомелье теперь
 * встроен прямо в карточку вина (WineCardContent → SomelierCardWidget), поэтому кнопка вместо
 * навигации доскролливает до её поля вопроса и ставит туда фокус клавиатуры — раздел
 * «Сомелье» (/app/chat) при этом никуда не делся и остаётся в меню разделов нетронутым.
 *
 * scrollIntoView/scrollTo не реализованы в jsdom (тесты) — вызываем их только если метод
 * реально существует, чтобы не падать в vitest.
 */
export function focusSomelierWidget(): void {
  const input = document.getElementById(SOMELIER_WIDGET_INPUT_ID);
  if (!input) return;
  if (typeof input.scrollIntoView === "function") {
    input.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "center" });
  }
  input.focus();
}
