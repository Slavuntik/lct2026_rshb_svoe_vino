# ml-engineer: ML-1 — слияние OCR+модель + алиас винодельни в гейте (22.09)

Бриф `agents/ML-1-ocr-vlm-merge-winery-alias.md`, основание `reports/ml-lead-plan.md`.
Обе задачи реализованы, покрыты тестами, проверены на живом API: Mac, своя копия
индекса D1 (`case-data/real-photos-labels/qdrant-d1-copy` без `.lock`, порт 8774) —
второй процесс на общем `packages/cv/data-d1` тихо отдаёт пустые ответы (ловушка qa-auto).

## Задача 1 — слияние OCR+модель (vlm/vlm_local/vlm_both)

`service.py::_fusion_text_and_vectors()`: при ответе модели(ей) и новом
`CV_FUSION_MERGE_MODEL_TEXT=1` (дефолт `false`) OCR добавляется к тексту модели
через пробел, не только фолбэк; `source`/`ocr_text` от флага не зависят.
`config.py::cv_fusion_merge_model_text` уже в HEAD (backend закоммитил файл
целиком в e32ae1d вместе с моей правкой — `git diff HEAD` пуст, не коммичу повторно).

Живой API (D1 base-384, `CV_FUSION_W=0.3 CROPS=2`, шлюз GPU, свежий baseline перед
своим прогоном), 62 живых фото, режим `vlm`: **95.2% (59/62) → 96.8% (60/62)**.
+1 фото (96.55, «Новый Свет… Российское шампанское выдержанное»); 0 ошибок HTTP на
100 фото, p95 3.8–4.6 с. vlm_local/vlm_both — гейт общий, покрыт юнитами.

## Задача 2 — алиас винодельни (Голубицкое)

`case-data/winery_aliases.json` (вне git, по образцу `families.json`) — алиас из
брифа. `cv/text_fusion.py::load_winery_index()` (+ `load_winery_alias_groups`,
`default_winery_aliases_path`) строит `winery_index` с ОБЪЕДИНЁННЫМИ токенами
winery для слагов одной группы; файл отсутствует/пуст → побитово прежний
`load_catalog_index(csv, fields=("winery",))`. Подключено в `_fusion_winery_index()`
(кэш по catalog_csv+aliases_path). Основной индекс `FUSION_FIELDS` не тронут.

Живой API, `CV_FUSION_TEXT_SOURCE=ocr CV_OCR_ENGINE=rapid` (код-дефолты, `CROPS=2` —
конфигурация hack-v9), 62 фото: **93.5% (58/62) → 95.2% (59/62)**. Ровно +1 фото —
93.97 `golubitskoe-estate-chardonnay` (было `pomeste-golubitskoe-shardone-rezerv-...`).
Diff по 100 фото: изменилась РОВНО эта позиция, 0 регрессий. «До» 93.5% бит-в-бит
совпало с `reports/qa-auto-hack-v9-detector.md` — копия индекса корректна.

## Тесты
`packages/cv`: 371 passed (было 354). `apps/api`: 346 passed, 11 skipped (было
322/11, дважды подряд). Новое: `test_scan_photo_fusion_ml1_merge_text.py`,
`test_scan_photo_fusion_ml1_winery_alias.py`; расширены `test_text_fusion.py`
(recall на синтетике Голубицкого) и `test_scan_photo_fusion.py`
(`_enable_fusion(winery_aliases=...)`). Найден и исправлен баг изоляции:
`cv.config.CASE_DATA_DIR` резолвится один раз при импорте и не видит
`monkeypatch.setenv("CASE_DATA_DIR", ...)` — без явного `CV_WINERY_ALIASES_JSON`
тесты тихо падали на боевой `case-data/winery_aliases.json` (вскрылось только в
полном прогоне apps/api, не в изоляции — строки теста совпали с ним по содержанию).

## Как воспроизвести
`rsync -a --exclude='.lock' packages/cv/data-d1/qdrant/ case-data/real-photos-labels/qdrant-d1-copy/`.
Сервер — по образцу `infra/local-check/run-check-server.sh`, но
`CV_QDRANT_PATH=<копия>` (не `CV_DATA_DIR`), `CV_FUSION_CROPS=2`, порт 8774.
`qa/real_photos_serve.py --api http://127.0.0.1:8774 --flat-only --out X.jsonl` →
из `packages/cv`: `.venv/bin/python ../../qa/real_photos_eval.py --served X.jsonl`.

## Риски / предложения
Оба флага готовы к бою, но НЕ включены в env стенда/local-check — решение «в бой»
идёт через qa-auto → devops (BOARD.md), не эту роль; алиас-файл сам обнаружится по
`CASE_DATA_DIR` — devops достаточно скопировать `winery_aliases.json` на стенд, env
не трогать. Нет предложений к `contracts/image-scan.md`. Алиас «nikolaev» не
добавлял (см. бриф) — на усмотрение pm/product.
