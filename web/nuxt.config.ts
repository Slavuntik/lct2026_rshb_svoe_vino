import { fileURLToPath } from 'node:url'

// Сканер вин: мобильный интерфейс модуля каталога «Своё вино».
// Все запросы фронта идут на /api/**, их обрабатывает server/api/[...path].ts:
// проксирует на NUXT_API_BASE (адрес задаётся при запуске, без пересборки) или отвечает фикстурами при NUXT_PUBLIC_MOCK=1.
export default defineNuxtConfig({
  compatibilityDate: '2026-09-01',
  devtools: { enabled: false },
  telemetry: false,

  css: ['~/assets/css/main.css'],

  app: {
    head: {
      htmlAttrs: { lang: 'ru' },
      titleTemplate: '%s · Сканер вин',
      meta: [
        { name: 'viewport', content: 'width=device-width, initial-scale=1, viewport-fit=cover' },
        { name: 'theme-color', content: '#fefdfa' },
        { name: 'format-detection', content: 'telephone=no' },
        {
          name: 'description',
          content: 'Распознавание российских вин по фото этикетки: карточка из каталога, аналоги других виноделен и подбор к блюду.',
        },
      ],
      link: [{ rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    },
  },

  runtimeConfig: {
    // NUXT_API_BASE — адрес FastAPI-сервиса WineScan
    apiBase: 'http://127.0.0.1:8080',
    // NUXT_MOCK_UPLOADS_DIR — папка с эталонными фото для режима моков (по умолчанию ../data/raw/.../uploads)
    mockUploadsDir: '',
    public: {
      // NUXT_PUBLIC_MOCK=1 — данные из web/mocks, Python-сервис не нужен
      mock: '',
    },
  },

  nitro: {
    // фикстуры попадают в сборку сервера и читаются лениво только в режиме моков
    serverAssets: [{ baseName: 'mocks', dir: fileURLToPath(new URL('./mocks', import.meta.url)) }],
  },

  typescript: { strict: true },
})
