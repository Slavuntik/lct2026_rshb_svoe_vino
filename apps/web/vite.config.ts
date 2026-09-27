/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readFileSync, readdirSync } from "node:fs";

// Строкой, не импортом из src/lib/env.ts: этот файл выполняется Vite/esbuild вне рантайма
// приложения, где import.meta.env ещё не существует — импорт модуля, читающего его в теле
// функции, безопасен только пока никто её не зовёт отсюда; проще и надёжнее не рисковать.
// Держать в синхроне с API_BASE_PATH в src/lib/env.ts.
const API_BASE_PATH = "/v1";

// Static legal pages use the same sources as both bundled frontends.
// An explicit allowlist keeps the dev middleware from exposing arbitrary files.
type LegalAsset = { file: URL; type: string; rewriteFonts?: boolean };
function getLegalAssets() {
const legalAssets = new Map<string, LegalAsset>([
  ["/legal/tokens.css", {file:new URL("./src/styles/tokens.css", import.meta.url), type:"text/css; charset=utf-8"}],
  ["/legal/fonts.css", {file:new URL("./src/styles/fonts.css", import.meta.url), type:"text/css; charset=utf-8", rewriteFonts:true}],
]);
for (const name of readdirSync(new URL("./src/assets/fonts/", import.meta.url))) {
  if (!/\.(woff2|txt)$/.test(name)) continue;
  legalAssets.set(`/legal/fonts/${name}`, {file:new URL(`./src/assets/fonts/${name}`, import.meta.url),
    type:name.endsWith(".woff2")?"font/woff2":"text/plain; charset=utf-8"});
}
  return legalAssets;
}
function readLegalAsset(asset: LegalAsset) {
  const content = readFileSync(asset.file);
  return asset.rewriteFonts ? content.toString("utf8").replaceAll("../assets/fonts/", "./fonts/") : content;
}

// Свой Сомелье — веб-клиент.
// VITE_API_MODE=mock|real переключает источник данных (см. src/lib/env.ts).
export default defineConfig({
  plugins: [react(), {
    name: "shared-legal-design-assets",
    configureServer(server) {
      const legalAssets = getLegalAssets();
      server.middlewares.use((request, response, next) => {
        const asset = legalAssets.get(request.url?.split("?")[0] ?? "");
        if (!asset) return next();
        response.setHeader("Content-Type", asset.type);
        response.end(readLegalAsset(asset));
      });
    },
    generateBundle() {
      for (const [path, asset] of getLegalAssets()) {
        this.emitFile({ type: "asset", fileName: path.slice(1), source: readLegalAsset(asset) });
      }
    },
  }],
  // OCR-плагин подключён file:-зависимостью (симлинк в apps/shell), и его импорт
  // @capacitor/core иначе разрешается из apps/shell/node_modules — в CI его нет
  // (там ставится только apps/web). dedupe + paths в tsconfig.app.json заставляют
  // брать собственную копию веба; вдобавок это исключает две копии ядра в бандле.
  resolve: { dedupe: ["@capacitor/core"] },
  server: {
    port: 5173,
    headers: {
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Embedder-Policy": "require-corp",
    },
    // VITE_API_MODE=real: транспорт до локального agents/B (uvicorn на :8000 по его брифу).
    // В mock-режиме прокси попросту не используется — MSW перехватывает fetch раньше,
    // до сетевого уровня, так что этот блок безвреден и в mock, и когда бэкенда ещё нет.
    proxy: {
      "/v1/shelf": {
        target: process.env.VINCHIK_SHELF_URL || "http://127.0.0.1:8086",
        changeOrigin: true,
        timeout: 330_000,
        proxyTimeout: 330_000,
      },
      "/shelf-ui": {
        target: process.env.VINCHIK_SHELF_URL || "http://127.0.0.1:8086",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/shelf-ui(?=\/|\?|$)/, "/").replace(/^\/\//, "/"),
      },
      [API_BASE_PATH]: {
        target: process.env.VINCHIK_API_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    restoreMocks: true,
    environmentOptions: {
      jsdom: {
        url: "http://localhost:3000/",
      },
    },
  },
});
