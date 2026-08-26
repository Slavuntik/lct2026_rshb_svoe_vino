import type { CapacitorConfig } from "@capacitor/cli";

// bundle id — ПЛЕЙСХОЛДЕР (agents/C-client.md): сменить на боевой на publish-Mac перед
// сборкой в App Store Connect. webDir — билд apps/web (npm run build:web в этой же папке).
const config: CapacitorConfig = {
  appId: "ru.svoysomelye.app",
  appName: "Свой Сомелье",
  webDir: "../web/dist",
  ios: {
    contentInset: "always",
  },
  server: {
    // androidScheme/iosScheme по умолчанию — https, чтобы Service Worker/MSW из веб-кода
    // (см. apps/web/src/lib/mockBootstrap.ts) не упирался в file://-специфичные ограничения.
    androidScheme: "https",
  },
};

export default config;
