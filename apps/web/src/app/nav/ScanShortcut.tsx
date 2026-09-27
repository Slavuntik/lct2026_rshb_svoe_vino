import { Link, useLocation } from "react-router-dom";
import { useI18n } from "../../i18n";
import { ScanIcon } from "./icons";

/**
 * Быстрый доступ к скану — решение тимлида по нижней панели вкладок (agents/frontend-nav-
 * transfer): сэндвич-меню стало единственной ПОЛНОЙ навигацией (все разделы + юр-ссылки),
 * а этот плавающий ярлык — не вторая навигация, а ускоритель ровно одного, самого частого
 * действия (сканирование — ядро кейса). Не рендерится на самом /app/scan (незачем ссылаться
 * на текущий экран) и не является <nav>/списком разделов, поэтому не конкурирует с меню:
 * это переход, а не панель вкладок. Подробное обоснование — reports/frontend-nav-transfer.md.
 */
export function ScanShortcut() {
  const { t } = useI18n();
  const location = useLocation();

  if (location.pathname === "/app/scan") return null;

  return (
    <Link to="/app/scan" className="scan-shortcut" aria-label={t("nav.scanShortcutLabel")}>
      <ScanIcon />
      <span aria-hidden="true">{t("nav.scan")}</span>
    </Link>
  );
}
