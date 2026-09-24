import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/browser', timeout: 180_000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:5180', headless: true, viewport: { width: 1280, height: 900 } },
  webServer: { command: 'npm run dev -- --host 127.0.0.1', url: 'http://127.0.0.1:5180', reuseExistingServer: false, timeout: 30_000 },
});
