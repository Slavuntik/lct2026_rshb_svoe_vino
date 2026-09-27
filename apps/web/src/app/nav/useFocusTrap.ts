import { useEffect } from "react";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Ловушка фокуса для модального выезжающего меню (contracts не участвуют — чисто клиентская
 * a11y-механика). Пока active=true: Tab/Shift+Tab не уводят фокус за пределы containerRef,
 * Escape зовёт onEscape (само закрытие и возврат фокуса на кнопку-триггер решает вызывающий
 * компонент — см. NavMenu). Список фокусируемых элементов пересчитывается на каждое нажатие
 * Tab: состав меню статичен (пункты + юр-ссылки), но пересчёт дешёвый и не полагается на
 * порядок рендера.
 */
export function useFocusTrap(
  containerRef: React.RefObject<HTMLElement | null>,
  active: boolean,
  onEscape: () => void,
): void {
  useEffect(() => {
    if (!active) return;

    function focusableElements(): HTMLElement[] {
      const container = containerRef.current;
      if (!container) return [];
      return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onEscape();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusableElements();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (!items.includes(active as HTMLElement)) {
        // Фокус оказался вне контейнера (например, из-за программного .focus() снаружи) —
        // возвращаем внутрь, а не отпускаем наружу молча.
        event.preventDefault();
        first.focus();
      }
    }

    // capture: true — перехватываем раньше обработчиков конкретных полей внутри меню.
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [active, containerRef, onEscape]);
}
