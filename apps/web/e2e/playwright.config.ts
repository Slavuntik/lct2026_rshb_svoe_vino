import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  use: {
    baseURL: "http://localhost:5173",
    viewport: { width: 390, height: 844 },
  },
  webServer: [
    {
      command: "npm run dev -w backend",
      port: 3001,
      reuseExistingServer: true,
    },
    {
      command: "npm run dev -w frontend",
      port: 5173,
      reuseExistingServer: true,
    },
  ],
});
