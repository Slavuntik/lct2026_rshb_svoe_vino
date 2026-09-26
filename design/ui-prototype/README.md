Фото или текст этикетки открывает карточку вина. Распознавание и карточка берутся со стенда `http://89.110.72.101`. Локальный Express нужен для сомелье и для `POST /eval`.

## Что умеет

1. Загрузка этикетки с камеры или из галереи.
2. Поиск по тексту этикетки.
3. Сетка совпадения и карточка вина со стенда.
4. Если вина нет в каталоге стенда — экран «Вино не нашлось» и аналоги из ответа.
5. Цифровой сомелье на локальном каталоге `backend/data/wines.json`.
6. `POST /eval` на локальном сервере: плоский `{"slug":"..."}` для скрипта оценки.

## Куда уходят запросы

Браузер ходит на Vite (`localhost:5173`). Прокси отправляет `/v1` на стенд, `/api` и `/eval` на локальный порт `3001`.

| Действие на экране | Запрос |
|--------------------|--------|
| Фото | `POST /v1/scan/photo`, без токена |
| Текст «Найти вино» | `POST /v1/scan/resolve`, гостевой токен |
| Карточка и похожие | `GET /v1/wines/{id}`, гостевой токен |
| Сомелье «Подобрать» | `POST /api/sommelier` на локальный сервер |
| Скрипт оценки | `POST /eval` на локальный сервер |

Гостевой токен клиент берёт сам: `POST /v1/auth/guest`. Витрина `/v1/shelf/*` и `POST /v1/scan/ocr` не используются.

Локальный `backend/src/matcher.ts` отвечает только на `/api/scan` и `/eval`. Он сравнивает хеш или имя файла с `backend/data`. Экран сканера его не вызывает.

## Стек

| Слой | Технологии |
|------|------------|
| Клиент | React 18, TypeScript, Vite, React Router, папка `web/` |
| Локальный API | Node.js, Express, TypeScript, папка `backend/` |
| Стенд | `http://89.110.72.101` |
| E2E | Playwright, папка `e2e/` |
| Ассеты | `assets/` |

## Быстрый старт

Команды из `apps/web`.

```bash
bash start.sh
```

Скрипт ставит зависимости, если их ещё нет, и поднимает клиент и локальный API. То же самое: `npm install && npm run dev`.

- Сайт: http://localhost:5173
- Локальный API: http://localhost:3001
- Health: http://localhost:3001/api/health

Оценка на локальном матчере:

```bash
curl -s -F "image=@./e2e/fixtures/fanagoria-cabernet.png" http://localhost:3001/eval
# {"slug":"fanagoria-cabernet-sauvignon-2022"}
```

E2E:

```bash
npm run test:e2e
```

## Структура

```
apps/web/            ← эта папка, корень npm
  web/               ← клиент сканера
  backend/           ← локальный API: сомелье, /eval, мок-матчер
  e2e/               ← Playwright
  assets/            ← логотипы, этикетки, бренд
  plan.txt
  start.sh
```

Подробности экранов и полей: [web/README.md](web/README.md), [backend/README.md](backend/README.md).
