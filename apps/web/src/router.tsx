import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useI18n } from "./i18n";

// Оболочка приложения загружается отдельным чанком; стартовый экран — сканер.
const AppShell = lazy(() => import("./app/AppShell"));

function RouteFallback() {
  const { t } = useI18n();
  return (
    <div className="screen container">
      <p>{t("common.loading")}</p>
    </div>
  );
}

export function AppRouter() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/" element={<Navigate to="/app/scan" replace />} />
        <Route path="/app/*" element={<AppShell />} />
        <Route path="*" element={<Navigate to="/app/scan" replace />} />
      </Routes>
    </Suspense>
  );
}
