# backend — пересборка RAG-индекса из каталога кейса + корневая причина «сомелье не знает» (22.09)

## (1) Пересборка
`rag/case_data.py` (новый): `build_supplemental_wine_records()` дополняет "wines" вином из
`case-data/case_catalog.json` (2103 слага), которых нет в `VINES_ROOT/build/index.jsonl` (снимок
пайплайна vines 25.08, 1978). `rag/ingest.py::run_ingest(case_data_dir=)` — по умолчанию
`config.CASE_DATA_DIR` (тот же env, что apps/api); явный несуществующий путь отключает (тесты
изолированы). Модели/knowledge-источник не менялись. Ребилд: **2103 вина, 138 виноделен, 5592
знания**, те же dense (paraphrase-multilingual-MiniLM-L12-v2)/реранкер (jina-reranker-v2-base-
multilingual). Результат: `case-data/rag-index-20260922/` (вне git), состав файлов = `packages/rag/data`.

**Попутно нашёл и починил корневую причину бага** (`rag/intent.py`): вопрос кнопки «Спросить сомелье
об этом вине» — ровно `t("chat.prefillAskAboutWine")` = «Расскажи про {name} от {winery}»
(apps/web/src/i18n/ru.ts; ScanScreen.tsx::handleAskSomelierAboutResult, WineCardScreen.tsx::
handleAskSomelier) — матчился `_FACT_RE` («расскаж…про») → `collections=("knowledge",)`: коллекция
**"wines" не участвовала в поиске вообще**, ни для одного вина каталога, индексировано оно или нет.
Новый `_ASK_ABOUT_WINE_RE` (точная форма шаблона) проверяется первым → `"pairing"` (wines-приоритет).
Не матчит ни один из 78 вопросов goldset.

## (2) Качество: goldset.jsonl (78 вопросов, top_k=8, heuristic=oracle)
| | старый (1978) | новый (2103) |
|---|---|---|
| hit@8 | 0.8205 | **0.8205 — без изменений** |
| MRR | 0.747 | **0.7382 (−0.0088)** |
| refusal: threshold / n_legit_wrongly_refused / n_refusal_wrongly_passed | −1.0532 / 2 / 4 | без изменений |

MRR-регресс объяснён: 2 "pick"-вопроса («сухое красное…кубанское») конкурируют с новыми
`soyuz-vino-kubanskoe-traditsionnoe-*` (легитимно релевантны) — ответ смещается на 1-2 позиции, но
**остаётся в top-8**. Раз MRR формально хуже — **`packages/rag/data` НЕ заменён** (условие тимлида «не
хуже»), версия осталась 20260828.1. Код (не данные) закоммичен `a686bf4`, pathspec
`packages/rag/rag/{case_data.py,cli.py,config.py,ingest.py,intent.py,refdata.py,types.py}` + тесты +
`apps/api/scripts/scan_to_chat_eval.py`. Тесты: packages/rag 98→**115 passed**; apps/api **454 passed**
(не менялся), 0 failed. Не запушено.

## (3) Метрика «скан → сомелье» (2103 вопроса, `extract_filters`+`retriever.search(top_k=8)`, реальный реранкер)
| | старый | новый |
|---|---|---|
| hit-rate | **88.45%** (1860/2103) | **91.82%** (1931/2103) |
| not_in_index / family_or_vintage_twin / other_ranked_out / empty_result | 125 / 63 / 55 / 0 | 0 / 99 / 73 / 0 |

На **125 новых**: 0/125 (структурно) → **75/125 (60%)**. 50 промахов — 32 twin + 18 ranked_out, почти
все Союз-Вино (18, серия «Кубанское традиционное»/«Зелёная Долина», варианты объёма/сахара) и Золотая
Балка (13, серия «ЗБ вайн»); часть twin — дубль слага ОДНОГО вина между case-data и vines (напр.
`zb-vajn-moskato-polusladkoe-rozovoe` ≈ `zolotaya-balka-zb-moscato-semi-sweet-rose-...-9`), не ошибка
ранжирования. Регрессия на исходных 1978: **4 вина** (Дербент Вино, Массандра, Валерий Захарьин×2)
hit→miss — новый «близнец» вытесняет точное совпадение из top-8; newly-fixed вне 125 — 0. `empty_result`
не встретился нигде — «пустая выдача» не подтвердилась. Без фикса intent.py hit-rate был бы ~0% на
ЛЮБОМ вине каталога (`classify()` на реальных «Расскажи про X от Y» отдавал "fact"/"travel" — проверено
эмпирически) — это и есть исходный симптом тимлида, не только 125 новых.

## (4) Предложение к контракту (architect, не реализовано)
91.82% < 98% — опциональный `ChatRequest.wine_id: str | None`: фронт передаёт слаг отсканированного/
открытого вина (то же значение, что уже несёт `card.wine_id`); backend при непустом `wine_id` кладёт
`retriever.get_by_id(wine_id)` первым кандидатом (без дедупликации текста). Снимает twin/ranked_out как
класс промаха для «вопрос про ИМЕННО ЭТО вино». Не закрывает случай `get_by_id() is None` (сейчас 0 таких).

## (5) Devops: подмена индекса на стенде (не выполнено, роль не моя)
`packages/rag/data` не менялся — решение по MRR-компромиссу за тимлидом/architect. Если решат заменить:
(А) закоммитить `packages/rag/data` (слепок готов, не влит) → `git pull` на Mac → `infra/ams3/
sync-data.sh <host>` (уже льёт `packages/rag/data/`→`/opt/somelye/data/rag/`, менять нечего) →
`ssh somelye@<host> 'sudo systemctl restart somelye-api'`. (Б) safer blue-green: rsync готовую
`case-data/rag-index-20260922/` в **новую** `/opt/somelye/data/rag-20260922/`, `RAG_DATA_DIR` в
`somelye.env`, рестарт; старая `rag/` остаётся для мгновенного отката. Проверка: `curl .../v1/healthz`
→ `rag_index_version`=="20260922.1", `warm:true`; дымовой `/v1/chat` про одно из 125 новых → citation с
ожидаемым `wine_id`. Модели не менялись — `.fastembed_cache/` пересылать не нужно.

## Как воспроизвести
`cd packages/rag && .venv/bin/python -m rag.cli ingest --data <куда> --version <v>` (по умолчанию тянет
case-data). Eval: `rag.cli eval --data <индекс> --out <файл>`. Скан→чат: `cd apps/api &&
.venv/bin/python scripts/scan_to_chat_eval.py --data-dir <индекс> --label <x> --out <файл.jsonl>`
(resume-friendly, ~2103 запроса, ~25-45 мин на CPU этой машины с реранкером).

## Риски
`ARCHITECTURE.md`/`README.md`/`docs/product/*` всё ещё пишут «1978 вин» — вне моей зоны, product/pm
обновит после решения по индексу. Обе фоновые задачи (ingest, 2×scan-to-chat) — CPU-heavy на общей
машине, где параллельно работали qa-auto/ml-eng.
