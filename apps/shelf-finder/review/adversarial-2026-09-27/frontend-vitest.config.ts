import { defineConfig } from '../../../web/node_modules/vitest/dist/config.js'
import { fileURLToPath } from 'node:url'
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: { include: ['frontend-health.test.ts'], environment: 'node' },
})
