/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Свой Сомелье — веб-клиент.
// VITE_API_MODE=mock|real переключает источник данных (см. src/lib/env.ts).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
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
