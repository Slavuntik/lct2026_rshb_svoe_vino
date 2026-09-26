import { Capacitor } from "@capacitor/core";
import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";
import { OfflineBanner } from "./components/OfflineBanner";
import { I18nProvider } from "./i18n";
import { track } from "./lib/analytics";
import { AppRouter } from "./router";

function detectPlatform(): "ios" | "android" | "web" {
  const platform = Capacitor.getPlatform();
  return platform === "ios" || platform === "android" ? platform : "web";
}

export default function App() {
  useEffect(() => {
    track("app_open", { platform: detectPlatform() });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <I18nProvider>
      <BrowserRouter>
        <OfflineBanner />
        <AppRouter />
      </BrowserRouter>
    </I18nProvider>
  );
}
