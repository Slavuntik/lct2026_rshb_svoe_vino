# Frontend — сканер «Своё Вино»

Mobile-first веб-модуль. Отдельное нативное приложение не нужно.

## Стек

React 18 + TypeScript + Vite + React Router. Стили — `src/styles.css`. Главный экран совпадает с макетом Figma (узел `3:2008`): фон `#FEFDFA`, шапка `#F9F2EB`, кнопки `#AB494F`, шрифты Playfair Display / Inter / Ubuntu.

## Экраны

| Роут | Назначение |
|------|------------|
| `/` | Сканер: «Сканировать» открывает камеру, «Загрузить фото» — галерею. Пока файл уходит на `POST /v1/scan/photo`, полноэкранный экран «Рассматриваем этикетку» с отменой. «Найти вино» вызывает `POST /v1/scan/resolve` и показывает «Читаем название», тоже с отменой. |
| `/match/:slug` | Прямое совпадение: плитка вина и сетка похожих бутылок |
| `/wine/:slug` | Карточка вина: `GET /v1/wines/:id` — описание, подача, гастропара, сомелье |
| `/not-found` | «Вино не нашлось» и аналоги из других виноделен |
| `/sommelier` | Цифровой сомелье (retention) |

После успешного скана открывается сетка совпадения. Плитка ведёт на карточку вина.

## Как фронт говорит с API

Сканер и карточка ходят на стенд через прокси Vite `/v1` → `http://89.110.72.101`. Сомелье пока на локальном `/api`. Гостевой токен (`POST /v1/auth/guest`) запрашивается сам для текста и карточки. Фото скана токен не требует. Витрина (`/v1/shelf/*`) и `POST /v1/scan/ocr` на стенде не используются.

### `POST /v1/scan/photo`

`multipart/form-data`, поле `image`. Ответ стенда приводится к `ScanResult`.

### `POST /v1/scan/resolve`

JSON `{ "text": "..." }` с `Authorization: Bearer`. Прямое совпадение — если есть matches и `low_confidence` ложь.

### `GET /v1/wines/:id`

Карточка: `source` (название, винодельня, регион, сорт, цвет, сахар, крепость, температура, гастропара, описание, рейтинг) и `similar_wines`. Превью без своего фото: `GET /v1/case-thumbs/{id}.webp`.

Форма, в которую это складывается на экране (`ScanResult`):

```ts
{
  found: boolean
  wine: Wine | null
  confidence: { f1Top1: number; f1Top5: number }
  candidates: Array<{ slug: string; score: number }>
  similar: Wine[]
}
```

`confidence` и `candidates` на экране не рисуем.

### `POST /api/sommelier`

```ts
// запрос
{ occasion: string; dish: string; sweetness: string; budget: string }

// ответ
{ wines: Wine[]; rationale: string }
```

## Тип `Wine`

Совпадает с каталогом бэкенда:

| Поле | Тип | Смысл |
|------|-----|--------|
| `slug` | string | стабильный id, то что ждёт скрипт оценки |
| `name` | string | название на этикетке |
| `producer` | string | винодельня |
| `region` | string | регион / ЗГУ |
| `grape` | string | сорт / ассамбляж |
| `year` | number | винтаж |
| `color` | `red` \| `white` \| `rose` \| `orange` \| `sparkling` | тип |
| `description` | string | короткое описание |
| `roskachestvoRating` | number \| null | рейтинг Роскачества (0–100) |
| `pairing` | string | к чему подать |
| `alcohol` | number | % об. |
| `volume` | number | мл |
| `price` | number \| null | ₽, если есть |
| `imageUrl` | string | фото бутылки / этикетки |
| `servingTemp` | string | температура подачи со стенда, например `10-12` |
| `category` | string | «Белое сухое» и т.п. |
| `vineyardImageUrl` | string | фото терруара |
| `pairings` | `{ label, imageUrl }[]` | гастропара на карточке |

## Ассеты

Логотипы и картинки класть в корневой `assets/`:

- `assets/logos/` — логотип портала, иконка сканера
- `assets/labels/` — референсные этикетки (индексирует бэкенд)
- `assets/brand/` — прочие графические материалы

Vite отдаёт их как `/logos/...`, `/labels/...`, `/brand/...`.

## Запуск

```bash
# из корня репозитория
npm run dev:frontend

# или
cd frontend && npm run dev
```

Порт: `5173`. Прокси API настроен в `vite.config.ts`.
