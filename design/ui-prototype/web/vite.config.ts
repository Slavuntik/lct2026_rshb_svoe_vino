import react from "@vitejs/plugin-react";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  publicDir: resolve(root, "../assets"),
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:3001",
      "/eval": "http://localhost:3001",
      "/v1": { target: "http://89.110.72.101", changeOrigin: true },
    },
  },
});
