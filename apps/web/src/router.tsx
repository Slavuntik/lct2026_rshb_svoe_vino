import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useI18n } from "./i18n";
import { LandingPage } from "./landing/LandingPage";

// Код-сплит: шесть экранов /app (+ MSW через lib/analytics и bootstrap) не должны попадать
// в первый чанк лендинга — иначе Lighthouse на "/" платит за код, который там не выполняется.
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
        <Route path="/" element={<LandingPage />} />
        <Route path="/app/*" element={<AppShell />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
