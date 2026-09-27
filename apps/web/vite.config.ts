/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Строкой, не импортом из src/lib/env.ts: этот файл выполняется Vite/esbuild вне рантайма
// приложения, где import.meta.env ещё не существует — импорт модуля, читающего его в теле
// функции, безопасен только пока никто её не зовёт отсюда; проще и надёжнее не рисковать.
// Держать в синхроне с API_BASE_PATH в src/lib/env.ts.
const API_BASE_PATH = "/v1";

// Свой Сомелье — веб-клиент.
// VITE_API_MODE=mock|real переключает источник данных (см. src/lib/env.ts).
export default defineConfig({
  plugins: [react()],
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
