import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useShelfAvailability } from "../lib/shelfAvailability";
import { storage } from "../lib/storage";
import { AppNav } from "./nav/AppNav";
import { ChatScreen } from "./chat/ChatScreen";
import { MetricsScreen } from "./metrics/MetricsScreen";
import { OnboardingScreen } from "./onboarding/OnboardingScreen";
import { ProfileScreen } from "./profile/ProfileScreen";
import { ScanScreen } from "./scan/ScanScreen";
import { ShelfScreen } from "./shelf/ShelfScreen";
import { ShelfUnavailableScreen } from "./shelf/ShelfUnavailableScreen";
import { TastePassportScreen } from "./taste/TastePassportScreen";
import { WineCardScreen } from "./wine/WineCardScreen";

function RequireOnboarding({ children }: { children: ReactNode }) {
  if (!storage.isOnboardingComplete()) {
    return <Navigate to="/app/onboarding" replace />;
  }
  return <>{children}</>;
}

/**
 * Оболочка приложения, включая отдельный экран витрин.
 * Онбординг вне навигации и вне гейта (это и есть сам гейт).
 */
export default function AppShell() {
  const location = useLocation();
  const onOnboarding = location.pathname.startsWith("/app/onboarding");
  const showNav = storage.isOnboardingComplete() && !onOnboarding;
  // Один живой чек на всё приложение (src/lib/shelfAvailability.ts) — не блокирует этот
  // рендер: "pending" ведёт себя как "unavailable" и в навигации, и в самом роуте, пока
  // проверка не подтвердит сервис витрин живым.
  const shelfAvailability = useShelfAvailability();
  const shelfAvailable = shelfAvailability === "available";

  return (
    <div className="app-shell">
      {showNav && <AppNav shelfAvailable={shelfAvailable} />}
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
            path="shelf"
            element={
              <RequireOnboarding>
                {shelfAvailable ? <ShelfScreen /> : <ShelfUnavailableScreen />}
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
          {/* v0.4.10: метрики распознавания — числа последнего прогона оценки. Экран намеренно
              вне меню разделов (app/nav/NavMenu.tsx): оно рассчитано на пользовательские
              разделы, а это страница для демонстрации и проверки, доступная по прямой ссылке
              /app/metrics. */}
          <Route path="metrics" element={<MetricsScreen />} />
          <Route index element={<Navigate to={storage.isOnboardingComplete() ? "scan" : "onboarding"} replace />} />
          <Route path="*" element={<Navigate to="/app" replace />} />
        </Routes>
      </div>
    </div>
  );
}
