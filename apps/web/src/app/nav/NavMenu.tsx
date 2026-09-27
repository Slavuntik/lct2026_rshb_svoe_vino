import { useEffect, useRef } from "react";
import { NavLink } from "react-router-dom";
import { useI18n } from "../../i18n";
import { prefersReducedMotion } from "../../lib/motion";
import { CloseIcon } from "./icons";
import { useFocusTrap } from "./useFocusTrap";

interface NavItem {
  to: string;
  label: string;
}

interface NavMenuProps {
  panelId: string;
  shelfAvailable: boolean;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
  onClose: () => void;
}

/**
 * Выезжающая панель разделов (задача тимлида 27.09, agents/frontend-nav-transfer):
 * монтируется только пока меню открыто (AppNav держит `{menuOpen && <NavMenu .../>}`) —
 * это и есть анимация появления (CSS @keyframes на .nav-menu__backdrop/.nav-menu__panel,
 * которая — в отличие от transition — запускается уже на вставку узла в DOM, лишний JS
 * для тайминга не нужен) и одновременно естественная граница фокус-ловушки: пока панель
 * не смонтирована, её пунктов просто нет в DOM/accessibility-дереве.
 *
 * a11y: role="dialog" + aria-modal, Esc и клик по подложке закрывают (useFocusTrap ловит
 * Esc, backdrop.onClick — клик вне, панель останавливает всплытие), фокус уходит на кнопку
 * закрытия при открытии и возвращается на кнопку-триггер (шапка) при любом закрытии —
 * см. cleanup эффекта ниже. «Витрина» — строго по shelfAvailable, который считает
 * lib/shelfAvailability.ts (пропс сюда, логика самого чека не дублируется).
 */
export function NavMenu({ panelId, shelfAvailable, triggerRef, onClose }: NavMenuProps) {
  const { t } = useI18n();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const reducedMotion = prefersReducedMotion();

  useFocusTrap(panelRef, true, onClose);

  useEffect(() => {
    closeButtonRef.current?.focus();
    return () => {
      triggerRef.current?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- монтаж/размонтаж ровно раз на открытие
  }, []);

  const items: NavItem[] = [
    { to: "/app/scan", label: t("nav.scan") },
    // Задача тимлида 27.09: последний пункт «есть в Figma, нет у нас» — домашний экран
    // «Каталог вин» в макете, у нас сканер остаётся главным действием (решение тимлида),
    // поэтому раздел — только здесь, в меню; вариант «сделать домашним» разобран в отчёте.
    { to: "/app/catalog", label: t("nav.catalog") },
    ...(shelfAvailable ? [{ to: "/app/shelf", label: t("nav.shelf") }] : []),
    { to: "/app/chat", label: t("nav.chat") },
    { to: "/app/taste", label: t("nav.taste") },
    { to: "/app/profile", label: t("nav.profile") },
  ];

  return (
    <div
      className="nav-menu__backdrop"
      data-testid="nav-menu-backdrop"
      style={reducedMotion ? { animation: "none" } : undefined}
      onClick={onClose}
    >
      <div
        ref={panelRef}
        id={panelId}
        className="nav-menu__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="nav-menu-title"
        data-testid="nav-menu-panel"
        style={reducedMotion ? { animation: "none" } : undefined}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="nav-menu__head">
          <h2 id="nav-menu-title">{t("nav.menuTitle")}</h2>
          <button
            type="button"
            ref={closeButtonRef}
            className="nav-menu__close"
            aria-label={t("nav.menuClose")}
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </div>

        <ul className="nav-menu__list">
          {items.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                className={({ isActive }) => `nav-menu__link${isActive ? " nav-menu__link--active" : ""}`}
                onClick={onClose}
              >
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>

        <nav className="nav-menu__legal" aria-label={t("landing.footerLegalTitle")}>
          <a href="/legal/privacy.html" target="_blank" rel="noopener noreferrer" onClick={onClose}>
            {t("landing.legalPrivacyLink")}
          </a>
          <a href="/legal/consent.html" target="_blank" rel="noopener noreferrer" onClick={onClose}>
            {t("landing.legalConsentLink")}
          </a>
          <a href="/legal/terms.html" target="_blank" rel="noopener noreferrer" onClick={onClose}>
            {t("landing.legalTermsLink")}
          </a>
        </nav>
      </div>
    </div>
  );
}
