import { useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { NavMenu } from "./NavMenu";
import { ScanShortcut } from "./ScanShortcut";
import { TopHeader } from "./TopHeader";

const SEARCH_HASH = "#scan-text-search";

interface AppNavProps {
  shelfAvailable: boolean;
}

/**
 * Владелец состояния навигации /app/*: собирает шапку, выезжающее меню и ярлык скана.
 * Задача тимлида 27.09 (agents/frontend-nav-transfer.md, контракт не меняется — чисто
 * клиентская навигация). Единая точка открытия/закрытия меню и её единственный побочный
 * эффект — сброс при смене маршрута (переход по пункту меню уже закрывает его сам, это —
 * страховка на кнопку «назад» браузера/системный жест).
 */
export function AppNav({ shelfAvailable }: AppNavProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  function handleSearch() {
    setMenuOpen(false);
    navigate(`/app/scan${SEARCH_HASH}`);
  }

  return (
    <>
      <TopHeader
        menuOpen={menuOpen}
        menuButtonRef={menuButtonRef}
        menuPanelId={panelId}
        onToggleMenu={() => setMenuOpen((open) => !open)}
        onSearch={handleSearch}
      />
      {menuOpen && (
        <NavMenu
          panelId={panelId}
          shelfAvailable={shelfAvailable}
          triggerRef={menuButtonRef}
          onClose={() => setMenuOpen(false)}
        />
      )}
      <ScanShortcut />
    </>
  );
}
