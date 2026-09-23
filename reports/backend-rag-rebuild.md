# backend — пересборка RAG-индекса из каталога кейса + wine_id в чате (22.09)

**Обновление того же дня**: тимлид принял решение по MRR (замена индекса, п.2) и согласовал `wine_id`
(п.4) — реализовано, не просто предложено. Обе цифры (hit@8 и MRR) зафиксированы честно ниже.

## (1) Пересборка
`rag/case_data.py` (новый): `build_supplemental_wine_records()` дополняет "wines" вином из
`case-data/case_catalog.json` (2103 слага), которых нет в `VINES_ROOT/build/index.jsonl` (снимок
пайплайна vines 25.08, 1978). `rag/ingest.py::run_ingest(case_data_dir=)` — по умолчанию
`config.CASE_DATA_DIR` (тот же env, что apps/api); явный несуществующий путь отключает (тесты
изолированы). Модели/knowledge-источник не менялись. Ребилд: **2103 вина, 138 виноделен, 5592
знания**, те же dense (paraphrase-multilingual-MiniLM-L12-v2)/реранкер (jina-reranker-v2-base-
multilingual).

**Попутно нашёл и починил корневую причину бага** (`rag/intent.py`): вопрос кнопки «Спросить сомелье
об этом вине» — ровно `t("chat.prefillAskAboutWine")` = «Расскажи про {name} от {winery}»
(apps/web/src/i18n/ru.ts; ScanScreen.tsx::handleAskSomelierAboutResult, WineCardScreen.tsx::
handleAskSomelier) — матчился `_FACT_RE` («расскаж…про») → `collections=("knowledge",)`: коллекция
**"wines" не участвовала в поиске вообще**, ни для одного вина каталога, индексировано оно или нет.
Новый `_ASK_ABOUT_WINE_RE` (точная форма шаблона) проверяется первым → `"pairing"` (wines-приоритет).
Не матчит ни один из 78 вопросов goldset.

## (2) Качество: goldset.jsonl (78 вопросов, top_k=8, heuristic=oracle) — ИНДЕКС ЗАМЕНЁН
| | старый (1978) | новый (2103), **в бою** |
|---|---|---|
| hit@8 | 0.8205 | **0.8205 — без изменений** |
| MRR | 0.747 | **0.7382 (−0.0088)** |
| refusal: threshold / n_legit_wrongly_refused / n_refusal_wrongly_passed | −1.0532 / 2 / 4 | без изменений |

MRR-просадка (2 из 78 "pick"-вопроса про «сухое красное…кубанское» конкурируют с новыми
`soyuz-vino-kubanskoe-traditsionnoe-*`, легитимно релевантными тексту; ответ смещается на 1-2 позиции,
но остаётся в top-8 — hit не падает) принята тимлидом как шум против +125 вин покрытия и +3.4 п.п.
скан→сомелье. **`packages/rag/data` заменён и закоммичен** `8cd0355` (version 20260828.1→20260922.1,
wines 1978→2103), pathspec = сам каталог данных. Копия для devops — `case-data/rag-index-20260922/`
(вне git, байт-в-байт та же). Код сборщика — `a686bf4`, pathspec `packages/rag/rag/{case_data.py,
cli.py,config.py,ingest.py,intent.py,refdata.py,types.py}` + тесты. Тесты: packages/rag 98→**115
passed**; apps/api 454→**534 passed** (после этого и следующего пункта), 0 failed. Не запушено.

## (3) Метрика «скан → сомелье» (2103 вопроса, `extract_filters`+`retriever.search(top_k=8)`, реальный реранкер)
| | старый | новый (без wine_id) | новый **с wine_id** |
|---|---|---|---|
| hit-rate | 88.45% (1860/2103) | 91.82% (1931/2103) | **100% (2103/2103)** |

Без wine_id — не_in_index/family_or_vintage_twin/other_ranked_out/empty_result: старый 125/63/55/0,
новый 0/99/73/0. На **125 новых** без wine_id: 0/125 → 75/125 (60%), промахи почти все Союз-Вино (18,
серия «Кубанское традиционное») и Золотая Балка (13, серия «ЗБ вайн») — несколько SKU с почти
идентичным названием. Регрессия на исходных 1978 без wine_id: 4 вина (Дербент Вино, Массандра,
Валерий Захарьин×2) hit→miss — новый «близнец» вытесняет точное совпадение; **с wine_id это больше не
имеет значения** (см. п.4). Без фикса intent.py (п.1) hit-rate был бы ~0% на ЛЮБОМ вине каталога
(проверено эмпирически на `classify()`) — исходный симптом тимлида, не только про 125 новых.

## (4) `ChatRequest.wine_id` — реализовано (`a9e037c`)
`app/chat/service.py::stream_chat_events(wine_id=)`: `retriever.get_by_id(wine_id)`; резолвится в
`kind="wine"` → кандидат №1 (обычная карточка/цитата), остальная выдача `search()` следует без
урезания `top_k`, дедуплицированная по id. Не резолвится/пуст/не-wine kind → байт-в-байт как без поля.
Пустая выдача при резолвнутом `wine_id` невозможна по построению — `EMPTY_RETRIEVAL` не наступает.
`app/routers/chat.py` прокидывает `body.wine_id`, пишет в `ChatMessage.trace["wine_id"]`.

Тесты (`apps/api/tests/test_chat.py`, +10): pin отменяет refusal на пустом search(); unknown/empty/
non-wine `wine_id` не меняют исход (byte-identical `candidate_ids`); дедуп при совпадении с search();
HTTP end-to-end (цитата №1 = запрошенный `wine_id`); unknown `wine_id` не роняет 200; trace хранит
`wine_id` (и `null`, когда не передан).

**Замер на всех 2103** (`scan_to_chat_eval.py --wine-id`, новый флаг): **100% (2103/2103)** — попадание
гарантировано построением всякий раз, когда `get_by_id(slug)` резолвится, а после пересборки (п.1)
резолвится каждый слаг каталога. Конкретный пример (Союз-Вино, ранее промах):
`soyuz-vino-kubanskoe-traditsionnoe-beloe-suhoe-07-belye-sorta-vinograda-11` — без `wine_id` слага нет
в топ-8 вовсе (`kuban-vino-aristov-8-byanko-...` и другие перебивают); с `wine_id` — `candidate_ids[0]`
== запрошенный слаг, `citations[0]` — дословная цитата про это вино. Живой прогон, не мок.

## (5) Devops: подмена индекса на стенде
`packages/rag/data` уже в git (`8cd0355`). Стандартный путь — на Mac `git pull` → `infra/ams3/
sync-data.sh <host>` (уже льёт `packages/rag/data/`→`/opt/somelye/data/rag/`, менять нечего) →
`ssh somelye@<host> 'sudo systemctl restart somelye-api'`. Safer blue-green (доп. надёжность на такой
размер дельты): rsync `case-data/rag-index-20260922/` (готова, идентична закоммиченному) в **новую**
`/opt/somelye/data/rag-20260922/`, `RAG_DATA_DIR` в `somelye.env`, рестарт; старая `rag/` остаётся для
мгновенного отката. Проверка (оба варианта): `curl .../v1/healthz` → `rag_index_version`==
"20260922.1", `warm:true`; дымовой `/v1/chat` с `wine_id` одного из 125 новых → citation №1 с этим
`wine_id`, без refusal. Модели не менялись — `.fastembed_cache/` пересылать не нужно. Фронт добавляет
`wineId` в запрос параллельно (карточка/скан) — без него поведение как раньше (см. п.4).

## Как воспроизвести
`cd packages/rag && .venv/bin/python -m rag.cli ingest --data <куда> --version <v>` (по умолчанию тянет
case-data). Eval: `rag.cli eval --data <индекс> --out <файл>`. Скан→чат: `cd apps/api &&
.venv/bin/python scripts/scan_to_chat_eval.py --data-dir <индекс> --label <x> --out <файл.jsonl>
[--wine-id]` (без `--wine-id` — resume-friendly, ~2103 запроса, ~25-45 мин с реранкером; с `--wine-id`
— секунды, реранкер не нужен, см. докстринг `run()`).

## Риски
`ARCHITECTURE.md`/`README.md`/`docs/product/*` всё ещё пишут «1978 вин» — вне моей зоны, product/pm
обновит. `wine_id` контракт (openapi 0.3.5) оформляет architect параллельно — если итоговая форма
разойдётся с этим описанием (имя поля/семантика), даст знать отдельно.
