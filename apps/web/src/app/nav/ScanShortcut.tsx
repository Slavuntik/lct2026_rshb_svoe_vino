import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useI18n } from "../../i18n";
import { prefersReducedMotion } from "../../lib/motion";
import { ScanIcon } from "./icons";

const SCROLL_HIDE_THRESHOLD_PX = 4; // фильтр дрожания тачпада/инерции — не прячем от шума в 1px
const TOP_SAFE_ZONE_PX = 24; // у самого верха страницы ярлык всегда виден (нечего перекрывать)

/**
 * Быстрый доступ к скану — решение тимлида по нижней панели вкладок (agents/frontend-nav-
 * transfer): сэндвич-меню стало единственной ПОЛНОЙ навигацией (все разделы + юр-ссылки),
 * а этот плавающий ярлык — не вторая навигация, а ускоритель ровно одного, самого частого
 * действия (сканирование — ядро кейса). Не рендерится на самом /app/scan (незачем ссылаться
 * на текущий экран) и не является <nav>/списком разделов, поэтому не конкурирует с меню:
 * это переход, а не панель вкладок. Подробное обоснование — reports/frontend-nav-transfer.md.
 *
 * Задача тимлида 27.09 (второй заход, придирчивая сверка): на карточке вина ярлык наезжал на
 * фото виноградника (position:fixed — перекрывает ЛЮБОЙ контент в своём углу, независимо от
 * прокрутки, включая самый первый экран без единого скролла — см. reports/frontend-layout-
 * audit.md). Два решения, не одно:
 * 1. Скрыт на /app/wine/* — не только на самом /app/scan: у карточки вина УЖЕ есть свой явный
 *    «К сканеру» вверху экрана (WineCardScreen.tsx), плавающий ярлык там чисто избыточен и
 *    именно там наезжал на фото — та же логика, что уже применена к /app/scan.
 * 2. На остальных экранах (каталог/сомелье/вкус/профиль/витрина) — прячется при прокрутке
 *    ВНИЗ (чтение контента важнее ускорителя) и возвращается при прокрутке вверх или у
 *    самого верха страницы (жест «хочу назад» либо ещё нечего перекрывать). Это не решает
 *    100% случаев (первый экран любой страницы теоретически может закончиться ровно на
 *    ярлыке), но многократно сокращает время наложения на реальный контент при чтении —
 *    универсальный приём, не список экранов-исключений, которые придётся помнить на будущее.
 */
export function ScanShortcut() {
  const { t } = useI18n();
  const location = useLocation();
  const [hidden, setHidden] = useState(false);
  const lastYRef = useRef(0);

  const suppressed = location.pathname === "/app/scan" || location.pathname.startsWith("/app/wine/");

  useEffect(() => {
    // Новый экран — ярлык снова виден (иначе унаследованная "спрятанность" с прошлой длинной
    // прокрутки могла бы скрыть его на заведомо коротком экране, где скроллить некуда).
    setHidden(false);
    lastYRef.current = window.scrollY;
  }, [location.pathname]);

  useEffect(() => {
    if (suppressed) return undefined;
    // Без requestAnimationFrame-троттлинга нарочно: браузер и так диспетчерит "scroll" не
    // чаще кадра в большинстве современных реализаций, а jsdom (тесты) rAF не поддерживает
    // без pretendToBeVisual — усложнять ради экономии на редких лишних сеттерах не стоило.
    function onScroll() {
      const y = window.scrollY;
      const lastY = lastYRef.current;
      if (y <= TOP_SAFE_ZONE_PX) {
        setHidden(false);
      } else if (y - lastY > SCROLL_HIDE_THRESHOLD_PX) {
        setHidden(true);
      } else if (lastY - y > SCROLL_HIDE_THRESHOLD_PX) {
        setHidden(false);
      }
      lastYRef.current = y;
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [suppressed]);

  if (suppressed) return null;

  const className = `scan-shortcut${hidden ? " scan-shortcut--hidden" : ""}`;

  return (
    <Link
      to="/app/scan"
      className={className}
      aria-label={t("nav.scanShortcutLabel")}
      aria-hidden={hidden}
      tabIndex={hidden ? -1 : undefined}
      style={prefersReducedMotion() ? { transition: "none" } : undefined}
    >
      <ScanIcon />
      <span aria-hidden="true">{t("nav.scan")}</span>
    </Link>
  );
}
