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

## Дополнение (22.09, needs-work qa-auto → фикс)

qa-auto (`reports/qa-auto-post-scan.md`): `chateau-de-talu-uroki-frantsuzskogo-
krasnostop-krasnostop-anapskiy-krasnoe-suhoe-145` → 500. Причина: `derived.sensory`
непуст, но `sweetness`/`acidity`/`aromatic_intensity` — ключи со значением `null`
(битая запись живого каталога, не отсутствующие ключи); `food_pairing.py:324` проверял
`axis in sensory`, не `is not None` → `float(None)` → `TypeError` без перехвата → 500.

**Фикс:** `_is_usable_sensory()` — `basis="sensory"` теперь только когда ВСЕ 7 осей
числом; неполный вектор молчаливым пропуском оси НЕ чинится (`applicable` в формуле
скоринга растёт от `when`, дырка в данных иначе неотличима от "явно не прошло порог" —
тихо занижала бы score), вместо этого каскад честно падает на heuristic/unavailable, как
будто `derived.sensory` не было вовсе. `build_sensory_wine_vector` — тот же
`isinstance`-фильтр вторым слоем защиты. Заодно `build_heuristic_wine_vector`:
`color` оборачивается в `str(...)` (та же живая запись несёт `color: null`, не только
пустую строку) — не наблюдалось падений, но тот же класс риска.

**Тесты (+2 в `test_wine_pairings.py`, итого 12):** запись с частичным null (3 из 7
осей) → `basis="heuristic"`, не 500; запись с ПОЛНОСТЬЮ null sensory + пустой `color`
→ `basis="unavailable"`, не 500. `pytest -q` — **348 passed, 11 skipped**, регрессий нет
(WIP-тест ml-engineer из отчёта qa-auto к этому моменту сам стал зелёным).

**Прогон по всем 4081 записям** (`RAG_PROVIDER=real` на своей копии
`packages/rag/data` без `.lock` — та же ловушка qdrant embedded, что поймал qa-auto —
+ `CASE_DATA_DIR` оригинал, только JSON, лока нет; через `TestClient`, скрипт
одноразовый, не в pytest): **0 ошибок 500 на 4081 вин** (1978 наш каталог + 2103
каталог кейса), p50≈2.6 мс/вызов. Basis: наш каталог — catalog=1977, unavailable=1
(сам дефектный `chateau-de-talu-...`: `color=null` тоже); каталог кейса — catalog=1977
(слаг совпал с нашим RAG), heuristic=125 (собственно кейсовые слаги), unavailable=1.
**basis=sensory не встретился ни разу на реальных данных** — не регресс: 1977 из 1978
вин нашего каталога несут непустой `food_pairings` (уровень 1 побеждает раньше, чем
дело доходит до sensory); из оставшихся 1014/1978 вин с непустым `derived.sensory`
несут ≥1 null-ось (ровно то, что нашёл qa-auto, только в масштабе). Путь sensory
по-прежнему покрыт юнит-тестом (`test_pairings_sensory_basis_scores_via_rules_engine`)
через фикстуру — слепая зона на реальных данных ожидаема, не блокер.

Воспроизвести сводный прогон: скрипт не в репозитории (разовая диагностика, тяжёлые
ML-зависимости) — команды и код приведены выше; при повторе скопировать
`packages/rag/data` в scratch-директорию БЕЗ `qdrant/.lock` перед стартом.
