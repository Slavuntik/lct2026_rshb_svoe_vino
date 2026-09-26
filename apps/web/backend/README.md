# Backend — API сканера

Express + TypeScript. Каталог — JSON. Модель CV заменена детерминированным матчером.

Интерфейс сканера и карточки ходит на стенд `http://89.110.72.101` (`/v1/scan/photo`, `/v1/scan/resolve`, `/v1/wines/{id}`). Этот локальный сервер остаётся для `/eval` и цифрового сомелье.

## Данные

Источник: `data/wines.json`. Отдельной БД нет (бритва Оккама). Когда понадобится — таблица `wines` с теми же колонками.

### Каталог `Wine`

| Поле | Тип | Описание |
|------|-----|----------|
| slug | string PK | `fanagoria-cabernet-sauvignon-2022` |
| name | string | Каберне Совиньон |
| producer | string | Фанагория |
| region | string | Кубань. Таманский полуостров |
| grape | string | Каберне Совиньон |
| year | number | 2022 |
| color | enum | red / white / rose / orange / sparkling |
| description | string | текст карточки |
| roskachestvoRating | number? | рейтинг Роскачества |
| pairing | string | гастрономическая пара |
| alcohol | number | градус |
| volume | number | мл |
| price | number? | цена, ₽ |
| imageUrl | string | путь к изображению |

### Индекс этикеток `data/label-index.json`

```json
{
  "byHash": {
    "<sha256>": "fanagoria-cabernet-sauvignon-2022"
  },
  "byFilename": {
    "fanagoria-cabernet.png": "fanagoria-cabernet-sauvignon-2022"
  }
}
```

Референсные файлы этикеток класть в `assets/labels/`. После добавления — дописать hash/имя в индекс (или пересчитать `npm run index-labels`, когда скрипт появится).

## Контракты API

| Метод | Путь | Вход | Выход |
|-------|------|------|--------|
| GET | `/api/health` | — | `{ ok: true }` |
| POST | `/api/scan` | `multipart/form-data` поле `image` | `ScanResult`, ответ не раньше чем через 3 с (мок обработки модели) |
| POST | `/eval` | то же | `{"slug":"wine-slug"}` |
| GET | `/api/wines` | — | `Wine[]` |
| GET | `/api/wines/:slug` | slug | `Wine` + мок карточки: `servingTemp`, `category`, `vineyardImageUrl`, `pairings` |
| GET | `/api/wines/:slug/similar` | slug | `Wine[]` |
| POST | `/api/sommelier` | JSON анкета | `{ wines, rationale }` |

### `ScanResult`

```json
{
  "found": true,
  "wine": { "slug": "fanagoria-cabernet-sauvignon-2022", "...": "..." },
  "confidence": { "f1Top1": 0.93, "f1Top5": 0.98 },
  "candidates": [
    { "slug": "fanagoria-cabernet-sauvignon-2022", "score": 0.93 },
    { "slug": "sober-bash-cabernet-2021", "score": 0.41 }
  ],
  "similar": []
}
```

Если этикетка неизвестна:

```json
{
  "found": false,
  "wine": null,
  "confidence": { "f1Top1": 0.18, "f1Top5": 0.44 },
  "candidates": [],
  "similar": [ { "slug": "..." } ]
}
```

### Скрипт оценки

Организатор шлёт фото одно за другим. Ответ **только**:

```json
{"slug":"wine-slug"}
```

Всегда один лучший slug (даже если уверенность низкая — берём top-1 из кандидатов или ближайший аналог). Время ответа скрипт меряет сам.

## Как устроен мок

`src/matcher.ts`:

1. SHA-256 буфера → `label-index.byHash`
2. имя файла → `byFilename` или вхождение slug/названия
3. иначе `found=false`, аналоги по цвету/региону из байтов файла (детерминированно)

F1 top-1 / top-5 — расчётные метрики отрыва: при точном попадании top-1 высокий и заметно выше остальных; при промахе — низкий.

**Заменить на реальную модель:** оставить сигнатуру `matchLabel(file) → ScanResult`.

## Запуск

```bash
# из корня
npm run dev:backend

# или
cd backend && npm run dev
```

Порт: `3001` (`PORT`). Лимит файла: 8 МБ.
