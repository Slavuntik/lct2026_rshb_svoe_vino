# backend: similar_wines (GET /wines/{id}) + top_styles_named (GET /taste/profile)

Задача тимлида 23.09 (дефект жюри, reports/qa-manual-hack-v16.md п.5.1) + добавка тимлида
в ходе работы (аудит architect, тот же класс дефекта у GET /taste/profile.top_styles).
Контракт уже ратифицирован architect (openapi 0.3.6) — `contracts/openapi.yaml` не правил.

## 1. similar_wines

`app/rag/cards.py::build_wine_card()` — единственный построитель карточки (GET /wines/{id}
И `card` в rich-скане). `similar` (голые слаги) как был; добавлен `similar_wines:
[{wine_id,name,winery,image_url}]`, тот же порядок. **Без новых запросов к ретриверу**:
`retriever.similar()` и раньше отдавал `Candidate` с полным `meta["source"]`, код брал только
`.id` и выбрасывал остальное — теперь source переиспользуется на месте (`_similar_wine_items`).
Слаг без пригодного `name` — пропущен в similar_wines, остаётся в similar (тот же приём, что
хотфикс `AnalogsWineItem` в routers/scan.py). Пустой similar -> пустой similar_wines. `card` в
`/scan/photo` получил поле автоматически (один построитель, регресс-тест обновлён).

## 2. top_styles_named (добавка тимлида)

`app/routers/taste.py::_resolve_style_names()`: `top_styles` (≤3 слага стиля) -> один вызов
`retriever.list_reference_styles(top_n=500)` (с запасом больше 146 стилей
`pipeline/ref/reference_styles.yaml`, т.к. реальный `list_popular()` — top-N по частоте в
каталоге, не весь справочник) -> словарь slug->style -> `top_styles_named:
[{slug,name,country}]` (переиспользован `AnalogsStyle`, форма 1:1 с `list_reference_styles()`).
Неизвестный слаг — пропущен, остаётся в top_styles. Пустой top_styles -> пустой список без
обращения к ретриверу.

Заодно: `apps/api/app/main.py::API_VERSION` 0.3.3 -> 0.3.6 (контракт этой версии полностью
реализован этой волной; `tests/test_docs.py` синхронизирован).

## Замер (similar_wines, GET /wines/{id}, тёплый кэш, x2 прогона, Mac)

- Прямой вызов `build_wine_card()` (MockRetriever, N=20000): p50 ДО 0.00217 мс -> ПОСЛЕ
  0.00229 мс (+0.00012 мс — шум измерения, никаких новых обращений к ретриверу нет).
- Полный `GET /v1/wines/{id}` через TestClient (N=2000): p50 ДО 1.22–1.77 мс -> ПОСЛЕ
  1.50–2.04 мс (+~0.25–0.3 мс — сериализация нового вложенного списка, не логика).
  Реальный Qdrant-бэкенд не трогал: ORCHESTRATION.md (22.09, находки qa-auto) — второй процесс
  на `packages/rag/data/qdrant` тихо ломает соседей; `get_by_id`/`similar` там же документированы
  как "прямой lookup в payload-кэше, без похода в Qdrant" (packages/rag/rag/base.py), так что
  зависимости от бэкенда у этой стоимости нет — MockRetriever достаточно представителен.
  Скрипт: `/tmp/.../scratchpad/bench_similar_wines.py` (сессионный scratch, не в репозитории).

## Тесты (apps/api/tests, все на MockRetriever)

`test_wines.py`: enrichment полей, порядок (2 элемента, belye-peski-sauvignon-blanc), пустой
similar, неизвестный слаг (monkeypatch `retriever.similar` — "вернуть" вино без имени) не
ломает ответ (200) и не попадает в similar_wines; exact-key-set тест фолбэка каталога кейса
обновлён. `test_scan_photo.py`: exact-key-set `card` обновлён. `test_taste.py`: top_styles_named
заполнен и совпадает по слагу/имени, пустой top_styles -> пустой список, неизвестный стиль
(monkeypatch `list_reference_styles` -> []) не ломает ответ. `test_docs.py`: версия 0.3.6.

`cd apps/api && .venv/bin/python -m pytest -q` -> **539 passed, 12 skipped** (skip —
`RUN_CV_INTEGRATION`/`RUN_RAG_INTEGRATION`, не трогал, как и раньше).

## Риски / предложения

Контракт для обеих фич уже ратифицирован (0.3.6) — правок не предлагаю. `AnalogsStyle.country`
не nullable (как и в /analogs) — если у стиля когда-нибудь не окажется country в
reference_styles.yaml, будет 500 при резолве; тот же риск уже есть у /analogs сегодня, не новый.
`_STYLE_CATALOG_TOP_N=500` — константа с запасом, не подписана на реальный размер справочника;
если он вырастет за 500, тихо деградирует как "стиль не резолвлен" (не упадёт), но стоит держать
в уме при следующей правке `pipeline/ref/reference_styles.yaml`.
