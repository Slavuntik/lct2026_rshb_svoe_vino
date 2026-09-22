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

Санити топ-6 по всем 9 тегам — **все 6 из 6 catalog**. Ожидания тимлида подтверждены: BBQ 6/6
красное; Блюда из рыбы 6/6 белое; Выпечка и десерты 5 сладких+1 полусладкое. Блюда из птицы
изначально были 6/6 красное (артефакт калибровки `portal_tag_defaults` — вынес как наблюдение,
не моя зона); ml-lead поправила (коммит `b6872e5`, `protein_heavy` 0.6→0.55) — сейчас 5 бел+1
роз, подтверждено её независимым живым прогоном. Это изменило 4 пиннед-теста в
`test_wine_pairings.py` (моя зона, направление вино→блюдо) — обновил числа по её таблице
(`reports/ml-lead-dish-pairing-poultry-fix.md`), сам проверяемый механизм (hard-block/тай-брейк/
деградация) не ослаблен, только числа.

## Прогрев кэша при старте

12 с холодного кэша не должны доставаться первому пользователю (демо) — `warm_up_catalog_cache()`
(`app/dish_pairing.py`) зовётся из `app/main.py` в фоновом потоке (`create_app()` возвращается
сразу, не ждёт), лог `dish-pairing catalog warm-up: warm=%s, %.3f s`. Сбой не роняет старт (try/
except внутри), кэш тогда соберётся лениво на первом запросе. Под pytest поток НЕ запускается
(`PYTEST_CURRENT_TEST` guard) — `case_catalog.py` читает `CASE_DATA_DIR` "живьём" не синхронно с
`create_app()`, а pytest зовёт его сотни раз за прогон с быстро сменяющимся `CASE_DATA_DIR`:
без guard'а отставший поток одного теста дочитывал до env уже следующего и заражал process-wide
`_load_catalog()` (поймано эмпирически — 4 теста валились нестабильно). `create_app()` как была,
так и осталась быстрой (мс) — прогрев не блокирует её ни в проде, ни в тестах.

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

70 новых (`test_dish_recognition.py` 29, `test_dish_pairing.py` 12 — включая регресс-тест
"лучшее по тегу вино вне выборки 30 всё равно в выдаче" и 2 на прогрев, `test_pairing_router.py` 20,
`test_pairing_contract_schema.py` 7, `test_main.py` 2 — фоновый поток реально запускается вне
pytest, реально пропускается под pytest) + `conftest.py` (автосброс кэша каталога, дефолтный
несуществующий `CASE_DATA_DIR`) + правки `test_docs.py`/`test_openapi_contract.py` (регистрация
роутера) + 4 пиннед-теста `test_wine_pairings.py` обновлены под калибровку ml-lead (см. выше).
`cd apps/api && .venv/bin/pytest -q` — **524 passed, 12 skipped** (было 454/12), без сети —
тесты подменяют `_iter_catalog_cards`/`warm_up_catalog_cache`, не читают реальный `case_catalog.json`.

## Риски / предложения

- `DISH_ZERO_SHOT_MARGIN=0.03` не калиброван (n=1 набл. margin~0.03 на грани) — synthetic starting point.
- Копия `case-data/real-photos-labels/rag-dish-pairing-copy` (~90М, без `.lock`) осталась на диске
  для замера выше — permission denied на `rm -rf` в этой среде, безопасно удалить вручную.
- `contracts/post-scan.md` §4.3 нужно поправить на «весь каталог» (см. выше) — за architect.

Коммит: pathspec `apps/api/app/{dish_recognition,dish_pairing}.py apps/api/app/cv/vision_llm.py
apps/api/app/food_pairing.py apps/api/app/config.py apps/api/app/main.py apps/api/app/schemas.py
apps/api/app/routers/pairing.py apps/api/tests/{test_dish_recognition,test_dish_pairing,
test_pairing_router,test_pairing_contract_schema,test_main,test_wine_pairings,test_docs,
test_openapi_contract,conftest}.py reports/backend-dish-photo.md`, не пушил.
