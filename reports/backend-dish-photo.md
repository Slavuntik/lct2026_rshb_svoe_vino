# backend — «Что подать» по фото блюда (22.09)

Контракт: `contracts/post-scan.md` v1.1 §4/§5, `openapi.yaml` 0.3.4 (ратифицировано architect
во время реализации). Первая версия (`candidates_for_taste`/`search`, пул 30 вин) получила
needs-work от тимлида — исправлено на полный каталог, см. ниже.

## Сделано

`app/cv/vision_llm.py`: `ask_json_or_raise`/`prepare_full_frame_image` (весь кадр, без
`CENTER_CROP`) — `read_label` не тронут. `app/dish_recognition.py`: промпт (9 тегов
подставляются), `_ask_models` (шлюз+локальная параллельно, приоритет шлюзу, БЕЗ склейки,
переиспользует `_FUSION_MODEL_POOL`/`_MODEL_BREAKERS` из `cv/service.py` буквально), `resolve_category`
(точное→синонимы→fuzzy), `zero_shot_classify` (reflection в `image_index.encoder._model/_processor`,
packages/cv не тронут). `app/food_pairing.py`: `+portal_tags`, `+score_wine_for_dish`. `app/dish_pairing.py`:
`select_wines_for_dish` — пул теперь **весь каталог** (`case_catalog.all_slugs()`, 2103 слагов на
снимке 22.09, карточка+вектор через `build_wine_card()`), кэш `@lru_cache` по объекту `retriever`
("once per process", тесты подменяют `_iter_catalog_cards()` целиком — реальный файл/сеть не
трогают). Ярус catalog — ВСЕ вина с тегом в `food_pairings` (включение по тегу, не по score),
сортировка по score движка правил; ярус rules — остаток, score>0. ≤2/винодельня, ≤6.
`routers/pairing.py`+`schemas.py`(`DishInfo/PairingWineItem/DishPairingResponse/DishManualRequest`)+`main.py`.

## Замер (тимлид, п.5/п.6) — реальный каталог, `RAG_PROVIDER=real` (своя копия `packages/rag/data`,
без `.lock`), 2103 вина

Холодный кэш (`build_wine_card` x2103): **12.0 с** (один раз на процесс). Тёплый кэш, `select_wines_for_dish`
по всем 9 тегам: **20-69 мс** (avg ~50 мс) — в целевом «десятки миллисекунд».

Санити топ-6 по всем 9 тегам — **все 6 из 6 catalog** (реальных тег-совпадений хватает с запасом,
ярус rules в топ-6 не потребовался). Ожидания тимлида подтверждены: BBQ 6/6 красное; Блюда из
рыбы 6/6 белое; Выпечка и десерты 5 сладких+1 полусладкое (5 белых+1 красное). Остальные тоже
разумны (Устрицы/Брускетты — белое+брют/сухое; Азиатская кухня — белое, полусладкое/полусухое).
Блюда из птицы — 6/6 **красное** (не белое) — не баг подбора: `portal_tag_defaults["Блюда из
птицы"]` (`protein_heavy=0.6, weight=0.5`) в движке `food_pairing_rules.yaml` triggers
`protein_needs_tannin` (require `wine.tannin>=0.55`) — содержимое `pipeline/ref/` не моя зона
(ml-lead), выношу как наблюдение, не правил.

## Расхождения с контрактом §4.2 (единственное оставшееся)

Fuzzy-порог `resolve_category()`: контракт — 60, код — 80. При 60 «марсианская кухня»/
«итальянская кухня» ложно резолвятся в «Азиатская кухня» (общее слово «кухня» переоценивается
`token_set_ratio`, проверено на rapidfuzz). При 80 опечатки («Азиятская кухня» 93%) по-прежнему
ловятся. Пул кандидатов (был расхождением) — закрыт правкой ниже, **прошу architect поправить
§4.3 с «пул top-30 Retriever.search()» на «весь каталог `case_catalog.all_slugs()`+`build_wine_card`»**.

## Негативный тест на 100 живых фото бутылок (case-data/real-photos)

Боевой шлюз (Qwen3.8-27b): **100/100 = 100.0%** status=bottle. Zero-shot (реальный SigLIP2
base-384, офлайн, локальный HF-кэш): **92/100 = 92.0%** bottle, 8/100 честный unsure (margin),
0 ложных "food". Позитивных фото блюд нет — **прошу 15-20 фото блюд у Вячеслава** для recall.

## Тесты

66 новых (`test_dish_recognition.py` 29, `test_dish_pairing.py` 10 — включая регресс-тест
"лучшее по тегу вино вне выборки 30 всё равно в выдаче", `test_pairing_router.py` 20,
`test_pairing_contract_schema.py` 7) + `conftest.py` (автосброс кэша каталога, тот же приём, что
у `_MODEL_BREAKERS`) + правки `test_docs.py`/`test_openapi_contract.py` (регистрация роутера).
`cd apps/api && .venv/bin/pytest -q` — **520 passed, 12 skipped** (было 454/12), без сети —
тесты подменяют `_iter_catalog_cards`, не читают реальный `case_catalog.json`.

## Риски / предложения

- `DISH_ZERO_SHOT_MARGIN=0.03` не калиброван (n=1 набл. margin~0.03 на грани) — synthetic starting point.
- Копия `case-data/real-photos-labels/rag-dish-pairing-copy` (~90М, без `.lock`) осталась на диске
  для замера выше — permission denied на `rm -rf` в этой среде, безопасно удалить вручную.
- `contracts/post-scan.md` §4.3 нужно поправить на «весь каталог» (см. выше) — за architect.

Коммит: pathspec `apps/api/app/{dish_recognition,dish_pairing}.py apps/api/app/cv/vision_llm.py
apps/api/app/food_pairing.py apps/api/app/config.py apps/api/app/main.py apps/api/app/schemas.py
apps/api/app/routers/pairing.py apps/api/tests/test_dish_recognition.py
apps/api/tests/test_dish_pairing.py apps/api/tests/test_pairing_router.py
apps/api/tests/test_pairing_contract_schema.py apps/api/tests/test_docs.py
apps/api/tests/test_openapi_contract.py apps/api/tests/conftest.py reports/backend-dish-photo.md`,
не пушил.
