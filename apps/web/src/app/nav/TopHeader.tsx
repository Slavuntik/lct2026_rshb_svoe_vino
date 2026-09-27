import { Link } from "react-router-dom";
import { useI18n } from "../../i18n";
import { MenuIcon, SearchIcon } from "./icons";

interface TopHeaderProps {
  menuOpen: boolean;
  menuButtonRef: React.RefObject<HTMLButtonElement | null>;
  menuPanelId: string;
  onToggleMenu: () => void;
  onSearch: () => void;
}

/**
 * Шапка /app/*: марка слева, поиск + сэндвич-меню справа, скруглённая плашка —
 * перенос компоновки из design/ui-prototype (App.tsx: .nav/.nav-pill/.nav-actions),
 * задача тимлида 27.09 (agents/frontend-nav-transfer). Марка — «Своё Вино» (РСХБ):
 * решение тимлида по ходу задачи (кейс делается для банка, соответствие их
 * фирменному стилю — отдельный критерий; лого — файл кейса, скопирован статикой
 * в public/brand, код на design/ui-prototype не ссылается). Тема (в т.ч. portal.css)
 * красит эту разметку только через переменные — className не меняется.
 */
export function TopHeader({ menuOpen, menuButtonRef, menuPanelId, onToggleMenu, onSearch }: TopHeaderProps) {
  const { t } = useI18n();

  return (
    <header className="app-header">
      <div className="app-header__pill">
        <Link to="/app/scan" className="app-header__brand" aria-label={t("nav.brandHome")}>
          <img src="/brand/logo-svoe-vino.svg" alt={t("nav.brandAlt")} width={160} height={40} />
        </Link>
        <div className="app-header__actions">
          <button type="button" className="app-header__icon-btn" aria-label={t("nav.searchLabel")} onClick={onSearch}>
            <SearchIcon />
          </button>
          <button
            type="button"
            ref={menuButtonRef}
            className="app-header__icon-btn app-header__menu-btn"
            aria-label={menuOpen ? t("nav.menuCollapse") : t("nav.menuOpen")}
            aria-expanded={menuOpen}
            aria-controls={menuPanelId}
            onClick={onToggleMenu}
          >
            <MenuIcon />
          </button>
        </div>
      </div>
    </header>
  );
}
