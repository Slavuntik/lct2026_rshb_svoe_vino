# backend — гастропары `GET /v1/wines/{wine_id}/pairings` (22.09)

Контракт: `contracts/post-scan.md` v1.0 §1, `contracts/openapi.yaml` 0.3.3. Бриф —
`reports/architect-post-scan.md`, "Задача backend". Никакого LLM — `explain` всегда
дословно из `rules[].explain` в `pipeline/ref/food_pairing_rules.yaml` (только чтение).

## Что сделано

- Резолюция `wine_id` переиспользует `app/rag/cards.py::build_wine_card` буквально
  (вторая копия наш-каталог/каталог-кейса не писалась) — 404 `not_found`, как у
  `GET /wines/{id}`.
- `apps/api/app/food_pairing.py` (новый модуль): общий `eval_condition(dict, dish,
  wine)` для `when`/`require`/`penalize`/`hard_blocks[].condition` (операторы `>=X`,
  `<=X`, `~=dish.Y±Z`, `>=dish.Y`, `matches wine.region`); скоринг по 9 тегам
  `portal_tag_defaults` с хард-блоками и alphabetical tie-break; таблица дефолтов
  уровня 3 + ключевые слова сахара/игристости — константы здесь (см. "Решения");
  каскад catalog→sensory→heuristic→unavailable.
- `schemas.py`: `TriggeredRule`/`WinePairingItem`/`WinePairingsResponse` — 1:1 с
  `openapi.yaml`. `routers/wines.py`: `GET /{wine_id}/pairings`, тот же Bearer-гейт,
  что `GET /{wine_id}`. `config.py`: `pairing_rules_path` (env `PAIRING_RULES_PATH`,
  дефолт как у `scan_foreign_ref_dir` — резолвится сам на ams3, env.example не трогал).

## Решения (зона backend)

1. Владелец `pipeline/ref/` — пока писался код, `TEAM.md` обновился (`8c24738`, pm):
   зона ml-lead, read-only для остальных. Уже соответствует сделанному (только чтение).
2. Таблица/ключевые слова уровня 3 — константа в `apps/api/app/food_pairing.py`
   (вариант Б брифа), не новая секция yaml.
3. `triggered_rules` — контракт не расписывает это программно; включены только
   правила с положительным вкладом (`require` выполнен либо бонус) — `spice_avoids_
   tannin` (только `penalize`) не попадает, объяснять нечего В ПОЛЬЗУ тега.
4. Нераспознанный `color` (вне 4 значений контракта) → `unavailable`, как и пустой.

## Тесты

`apps/api/tests/test_wine_pairings.py` — 10 тестов: все уровни + 404 + auth +
детерминизм + hard-block (брют → `dry_wine_with_dessert` роняет «Выпечка и десерты»)
+ алфавитный tie-break (3-way тай на игристом векторе, сверено прогоном настоящего
`food_pairing_rules.yaml`, не руками).

Воспроизвести: `cd apps/api && .venv/bin/pytest -q tests/test_wine_pairings.py` (10
passed); `.venv/bin/pytest -q` — 341 passed, 11 skipped (регрессий нет). Один КРАСНЫЙ
тест вне прогона — `tests/test_scan_photo_fusion_ml1_winery_alias.py`, чужой
незакоммиченный WIP ml-engineer (`app/cv/`), вне зоны backend.

## Риски / предложения к контрактам

- Таблица дефолтов уровня 3 / `abv_percent=12.5` — синтетика архитектора (уже
  оговорено в контракте), не трогалась.
- `config.py` в момент коммита нёс параллельный незакоммиченный кусок ml-engineer
  (`cv_fusion_merge_model_text`) — pathspec-коммит не умеет выбирать куски внутри
  одного файла, унёс и его (чужой текст не менялся и не терялся).
