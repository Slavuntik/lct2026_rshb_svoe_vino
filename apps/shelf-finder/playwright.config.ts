import { defineConfig } from '@playwright/test';
const port = Number(process.env.SHELF_TEST_PORT || 5180);
export default defineConfig({
  testDir: './tests/browser', timeout: 180_000, workers: 1,
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true, viewport: { width: 1280, height: 900 } },
  webServer: { command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`, url: `http://127.0.0.1:${port}`, reuseExistingServer: false, timeout: 30_000 },
});
