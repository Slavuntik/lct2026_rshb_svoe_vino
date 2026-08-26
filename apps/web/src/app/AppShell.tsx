import type { ReactNode } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useI18n } from "../i18n";
import { storage } from "../lib/storage";
import { ChatScreen } from "./chat/ChatScreen";
import { OnboardingScreen } from "./onboarding/OnboardingScreen";
import { ProfileScreen } from "./profile/ProfileScreen";
import { ScanScreen } from "./scan/ScanScreen";
import { TastePassportScreen } from "./taste/TastePassportScreen";
import { WineCardScreen } from "./wine/WineCardScreen";

function RequireOnboarding({ children }: { children: ReactNode }) {
  if (!storage.isOnboardingComplete()) {
    return <Navigate to="/app/onboarding" replace />;
  }
  return <>{children}</>;
}

function BottomNav() {
  const { t } = useI18n();
  const items: Array<{ to: string; label: string }> = [
    { to: "/app/scan", label: t("nav.scan") },
    { to: "/app/chat", label: t("nav.chat") },
    { to: "/app/taste", label: t("nav.taste") },
    { to: "/app/profile", label: t("nav.profile") },
  ];
  return (
    <nav className="app-nav row" aria-label={t("common.appName")}>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) => `app-nav__link${isActive ? " app-nav__link--active" : ""}`}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * Оболочка шести экранов /app. Маршруты — ровно шесть: onboarding, scan, wine/:id, chat,
 * taste, profile. Онбординг вне навигации и вне гейта (это и есть сам гейт).
 */
export default function AppShell() {
  const location = useLocation();
  const onOnboarding = location.pathname.startsWith("/app/onboarding");
  const showNav = storage.isOnboardingComplete() && !onOnboarding;

  return (
    <div className="app-shell">
      <div className="container">
        <Routes>
          <Route path="onboarding" element={<OnboardingScreen />} />
          <Route
            path="scan"
            element={
              <RequireOnboarding>
                <ScanScreen />
              </RequireOnboarding>
            }
          />
          <Route
            path="wine/:wineId"
            element={
              <RequireOnboarding>
                <WineCardScreen />
              </RequireOnboarding>
            }
          />
          <Route
            path="chat"
            element={
              <RequireOnboarding>
                <ChatScreen />
              </RequireOnboarding>
            }
          />
          <Route
            path="taste"
            element={
              <RequireOnboarding>
                <TastePassportScreen />
              </RequireOnboarding>
            }
          />
          <Route
            path="profile"
            element={
              <RequireOnboarding>
                <ProfileScreen />
              </RequireOnboarding>
            }
          />
          <Route index element={<Navigate to={storage.isOnboardingComplete() ? "scan" : "onboarding"} replace />} />
          <Route path="*" element={<Navigate to="/app" replace />} />
        </Routes>
      </div>
      {showNav && <BottomNav />}
    </div>
  );
}
