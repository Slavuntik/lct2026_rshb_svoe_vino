# qa-auto — приёмка пост-скана (22.09): гастропары + «Похоже по вкусу»
Коммиты: `e32ae1d` (backend), `30f15f6`+`097a326` (frontend). Кандидат в `hack-v10`.
Контракт: `contracts/post-scan.md` v1.0. Вердикт — в конце.
## 1. Тесты
`cd apps/api && .venv/bin/pytest -q`: первые 2 прогона (до `b194919`) — 345 passed, 1 failed
(`test_scan_photo_fusion_ml1_winery_alias.py`, незакоммиченный на тот момент WIP ml-engineer
в `app/cv/`, вне пост-скана) + post-summary `libc++abi recursive_mutex` (exit 134). Пока шла
эта приёмка, ml-engineer закоммитил фикс (`14362ab`/`2757c07`/`b194919`, не трогает
`food_pairing.py`/`routers/wines.py`/apps/web) — финальный прогон на актуальном HEAD: **346
passed, 11 skipped, 0 failed**, без крэша. `cd apps/web && npm test && npm run typecheck &&
npm run build`: **17 файлов/82 теста**, typecheck/build зелёные — совпадает с
`reports/frontend-post-scan.md` (apps/web этой волной ml-engineer не затронут).
## 2. Живой API — 30 вин `GET /v1/wines/{id}/pairings`
Стенд/CPU-путь: `RAG_PROVIDER=real RAG_MODE=embedded`, `CV_FUSION=1
CV_FUSION_TEXT_SOURCE=ocr CV_OCR_ENGINE=rapid CV_FUSION_CROPS=2`, без VLM/шлюза, порт 8775.
Свои копии без `.lock` — `packages/cv/data-d1` **и** `packages/rag/data` (embedded qdrant
второго тоже эксклюзивный однопроцессный: без копии — 500 «already accessed by another
instance», конфликт с чужим живым API на :8774; решение сверх брифа, см. вопрос тимлиду).
10 наш каталог / 10 каталог кейса вне RAG / 10 из part1+part2 (seed 20260922). **29/30 →
200, 1/30 → 500.** basis по 200-ответам: catalog=18, heuristic=11, sensory=0 (см. дефект),
unavailable=0. Неизвестный id → 404 `not_found`, тело как у `GET /wines/{id}` — контракт
соблюдён. Тайминг 200-ответов: p50=1.9 мс, p95=6.6 мс, max=8.3 мс.
**Дефект (needs-work):** `chateau-de-talu-uroki-frantsuzskogo-krasnostop-krasnostop-
anapskiy-krasnoe-suhoe-145` → 500. `derived.sensory` непуст, но 3 оси (`sweetness`,
`acidity`, `aromatic_intensity`) — ключи со значением `null` (битая запись каталога, 1 из
1978). `food_pairing.py:324 build_sensory_wine_vector()` проверяет `axis in sensory`, не
`is not None` → `TypeError: float() ... 'NoneType'`, не поймано → `routers/wines.py:43`
отдаёт 500 (трасса снята живьём, воспроизведено дважды; тот же класс данных уже ронял
`/analogs` раньше — `schemas.py:151-164`, хотфикс winery/region у того же `chateau-de-talu`).
Фикс: `sensory.get(axis) is not None` + регресс-фикстура с null-осью. Живьём basis=sensory
не проходит успешно ни на одной реальной записи (единств. кандидат — этот же битый), только
на синтетике юнит-теста — слепая зона покрытия.
## 3. Сценарий: 5 живых фото → pairings/analogs
`POST /v1/scan/photo` rich, тот же API. 7 попыток → 5 уверенных карточек (2 честно
`not_in_catalog` — вариативность CV, не тема волны). Для всех 5: pairings 200 (1.5–2.7 мс,
catalog×3/heuristic×2), `/v1/analogs` 200 (1.9–6.7 мс) по логике `ScanScreen.tsx` (grapes →
имя → retry `grapes[0]` при 404, не понадобился) — wine_id согласован скан→карточка→
pairings/analogs, 0 ошибок.
## 4. Браузер (встроенный)
`apps/web` dev (vite, порт 5174 — 5173 занят чужой сессией, 8080 не трогал), `VITE_API_MODE=
real`. `vite.config.ts` шьёт прокси на :8000 жёстко (не моя зона) — на время пункта живой API
переподнят на :8000, та же конфигурация. Гость → скан → фото (`DataTransfer` в `<input
type=file>`, штатная замена недоступного OS-диалога, без правок приложения) → уверенная
карточка (тот же wine_id, что в п.3) → «К чему подать» (3 тега, catalog) и «Похоже по вкусу»
(стиль `cabernet-napa`, список) рендерятся, **0 ошибок/warn в консоли**. Двойной `GET
/pairings` и aborted `/v1/events` — `React.StrictMode` (dev-only, был и до скана), не регрессия.
## Вердикт: needs-work
Причина — 500 на реальной записи каталога (раздел 2), нарушение критерия «нет 500».
Остальное (frontend, 404/схемы, e2e скан→pairings→analogs, браузер) — чисто. Backend:
точечный null-guard `food_pairing.py:324` + регресс-тест; переприёмка — только
`test_wine_pairings.py` и этот `wine_id`.
Воспроизвести: `pytest -q` в apps/api; `npm test && npm run build` в apps/web; живой API —
`infra/local-check/run-check-server.sh` с `CV_FUSION_TEXT_SOURCE=ocr CV_FUSION_CROPS=2`,
своими копиями `data-d1`/`rag/data` без `.lock`, портом 8775; `curl .../v1/wines/chateau-
de-talu-uroki-frantsuzskogo-krasnostop-krasnostop-anapskiy-krasnoe-suhoe-145/pairings` с
Bearer гостя → 500.
## Вопросы тимлиду
- Фикс в зоне backend — чинить самим по отчёту или ждать бриф? И стоит ли занести в
  ORCHESTRATION.md правило «своя копия без `.lock`» также для `packages/rag/data` (не
  только `data-d1`) при параллельных живых API?
