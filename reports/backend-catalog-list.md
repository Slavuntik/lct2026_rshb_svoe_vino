# backend: GET /v1/catalog — плитка каталога вин (27.09)

## Проверено на переиспользование — не подошло
`taste/candidates` — колода свайпа (гостю недоступна, разнообразие/исключение просмотренного, не
постраничный обход); `analogs` — один стиль → top-12, не список каталога; `case-thumbs` — статика,
переиспользована для `image_url`. Новый метод нужен.

## Реализация
Источник — ТОТ ЖЕ кэш карточек, что `dish_pairing.py` строит для подбора к блюду (прогрет в фоне
при старте). Добавил `dish_pairing.get_catalog_cards()` (публичная обёртка) + свой кэш
`_catalog_items()` поверх (плоские карточки, сортировка один раз); на запрос — только фильтр+срез.
`image_url` — НЕ `source.image_url` напрямую: у 1978/2103 слагов это внешний CDN организатора
(всегда живой), у ~125 — наш `/case-thumbs/…`, но записан безусловно, без проверки файла. Добавил
`case_catalog.has_thumb(slug)` (проверка файла на диске) — плитка всегда через него, `null` без
файла, вино не выбрасывается. Факт: **49 из 2103** без файла, не 44 из брифа (разошлось на
несколько шт., не критично). `color`/`sugar` — сравнение `.casefold()` с обеих сторон: у слагов
без настоящего RAG цвет хранится с большой буквы, без casefold фильтр их бы молча не находил.
Файлы: `app/routers/catalog.py` (новый), `app/schemas.py` (+CatalogItem/Response),
`dish_pairing.py`, `rag/case_catalog.py` (+has_thumb), `main.py` (роутер+тег). Авторизация —
`get_current_principal` (гость и юзер), как `/wines/{id}` и `/analogs`. Новых env нет — лимиты
через `Query(ge=/le=)`, по образцу `/taste/candidates`.

## Цифры (реальный CASE_DATA_DIR, 2103 слага)
Сборка+сортировка кэша: 6.6 мс (разово). `GET /v1/catalog` тёплый, полный HTTP-путь, 50 повторов:
**p50 1.5 мс, p95 2.4 мс** без фильтров; с `q`+`color`: p50 1.7, p95 4.2 мс. На два порядка ниже
требования "десятки миллисекунд". Воспроизвести: `apps/api/.venv/bin/python -m pytest -q
apps/api/tests/test_catalog.py`.

## Тесты
`test_catalog.py` (22, новый): постраничность без дублей/пропусков, лимит по умолчанию, поиск
имя/винодельня, фильтры (+регресс на большую букву), пустая страница/каталог, offset за
пределами, неизвестные параметры не роняют, невалидные limit/offset → 422, гость допущен, без
токена — 401, детерминизм. + `test_rag_case_catalog.py` (+4, has_thumb), `test_dish_pairing.py`
(+1). Полный прогон: **607 passed, 12 skipped** (было 580/12) — зелёный.

## Контракт (architect — ратификация, openapi.yaml не правил)
```yaml
  /catalog:
    get:
      summary: Каталог вин кейса — постраничная плитка для главного экрана
      description: >
        2103 позиции постранично: limit (дефолт 24, макс 100), offset; q — поиск по названию/
        винодельне; color/sugar — точные фильтры (везде регистронезависимо). Порядок — по имени,
        тай-брейк по wine_id: детерминирован. image_url — null без файла превью (вино не
        выбрасывается). Bearer-токен (гость или пользователь), как /wines/{id} и /analogs.
      parameters:
        - { name: limit, in: query, schema: { type: integer, default: 24, minimum: 1, maximum: 100 } }
        - { name: offset, in: query, schema: { type: integer, default: 0, minimum: 0 } }
        - { name: q, in: query, schema: { type: string, maxLength: 200 } }
        - { name: color, in: query, schema: { type: string } }
        - { name: sugar, in: query, schema: { type: string } }
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                required: [wines, total, limit, offset]
                properties:
                  wines:
                    type: array
                    items:
                      type: object
                      required: [wine_id, name]
                      properties:
                        wine_id: { type: string }
                        name: { type: string }
                        winery: { type: string, nullable: true }
                        color: { type: string, nullable: true }
                        sugar: { type: string, nullable: true }
                        image_url: { type: string, nullable: true }
                  total: { type: integer, description: "после q/color/sugar, до limit/offset" }
                  limit: { type: integer }
                  offset: { type: integer }
```
До ратификации держу временное исключение в `test_openapi_contract.py`
(`_KNOWN_UNDOCUMENTED_EXTRA_PATHS = {"/v1/catalog"}`, приём как раньше у `/v1/eval/predict`).

## Вопросы тимлиду
1. `image_url: null` (не `""`) при отсутствии превью — как у соседних схем. Ок?
2. Реальных слагов без превью — 49, не 44 — не блокер, просто фикс числа.
