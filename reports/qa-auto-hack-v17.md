# qa-auto: приёмка hack-v17 (23.09)

Коммиты: `db090e1` (similar_wines/top_styles_named), `f9f043d` (openapi 0.3.6), `e06c132`+`b18c662`
(фронт). Волна маленькая — проверено ровно по заданию, без прогона 100 фото, на стенд не ходил.
**Вердикт: approve.**

## 1. Полные наборы тестов — все зелёные
`apps/api` 539 passed, 12 skipped (= ожидание 539/12) · `apps/web` 112 passed/17 файлов (= 112) ·
`packages/rag` 115 passed (= 115) · `packages/cv` 422 passed · `packages/llm` 32 passed (обе
последние без изменений в волне, числа стабильны).

## 2. Живой API (127.0.0.1:8771, своя копия индексов без `.lock`, `RAG_PROVIDER=real`)
- `GET /v1/wines/{id}`: проверено 14 вин (в т.ч. требуемые 5) — `similar_wines` заполнено 6/6,
  порядок 1:1 с `similar`, имена непустые.
- Слаг без имени: во всём каталоге (2103) он один —
  `chateau-de-talu-uroki-frantsuzskogo-krasnostop-krasnostop-anapskiy-krasnoe-suhoe-145`
  (`source.name=None` подтверждено живьём). Обошёл 14 вероятных соседей (та же винодельня/сорт
  «Красностоп Анапский») — ни у одного он не в top-6 `similar`, живого примера пропуска найти не
  успел (не копал глубже, похоже на вырожденный эмбеддинг пустого текста). Сама логика пропуска —
  зелёный pytest (`test_wines.py`, монкипатч), это НЕ блокер.
- `card` в `/v1/scan/photo`: реальное фото `48.98_...` → `denisov_rubin_klaret_krasnaya_strelka`,
  `card.similar_wines` 6/6, ключи карточки идентичны `GET /wines/{id}`.
- `GET /v1/taste/profile`: пустой профиль (свежий тестовый аккаунт, 0 свайпов) → vector 0.5×7,
  `top_styles`/`top_styles_named` = []. После 1 like → `top_styles_named` =
  [{prosecco-rose,Просекко розе,Италия},{champagne-rose,Шампанское розе,Франция}], слаги/порядок
  совпадают с `top_styles`.

## 3. Сверка с openapi 0.3.6, поле в поле — расхождений нет
`GET /wines/{id}`: ключи ответа 6/6 = контракт (`wine_id,source,derived,source_url,similar,
similar_wines`); `similar_wines[]` 4/4 (`wine_id,name,winery,image_url`). `GET /taste/profile`:
4/4 (`vector,top_styles,top_styles_named,swipes_count`); `top_styles_named[]` 3/3
(`slug,name,country`). `card` в `ScanPhotoRichResponse` — тот же набор ключей, что `GET
/wines/{id}` (контрактное «ровно тело» подтверждено живьём).

## 4. Регресс 10 фото + чат
10/10 `flat_slug` (`/v1/eval/predict`) 1:1 с `case-data/real-photos-labels/served/
stand-hack-v16.jsonl` по тем же файлам, 0 расхождений (ожидаемо — волна не трогает `packages/cv`/
`routers/scan.py`). `POST /v1/chat` с `wine_id=denisov_rubin_klaret_krasnaya_strelka` → citation
№1 — тот же `wine_id`, верный url, цитата про это вино. Побочно: citation [2]-[9] — нерелевантные
статьи (тот же класс, что qa-manual-hack-v16 п.4.3 / frontend-jury-pass-fixes.md п.4, backend, вне
этой волны — не регрессия, на `LLM_PROVIDER=mock`). 0 ошибок 500/исключений в логе сервера.

## Как воспроизвести
Конфиг — как `infra/local-check/run-check-server.sh` (read-only, не менял), порт 8771,
`CV_DATA_DIR`/`RAG_DATA_DIR` — свои копии `packages/cv/data-d1` и `packages/rag/data` без
`.lock`, `RAG_PROVIDER=real`, гостевой/полный JWT через `/auth/guest`, `/auth/register`.

## Уборка
Сервер погашен, порт 8771 свободен. `rm -rf` копий индексов заблокировала песочница — пути для
ручного удаления (данных кейса и секретов там нет, только копии индексов и локальные JWT):
`/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/qa-auto-hack-v17-cv-d1`
(114M), `.../scratchpad/qa-auto-hack-v17-rag-data` (90M), `.../scratchpad/qa-auto-hack-v17.db`,
`*.log`, `*token*.txt`, `run-qa-auto-hack-v17-server.sh` — все в session-scratchpad, не в репо.

## Тимлиду
Не блокирует: не нашёл живого случая слага-без-имени в чужом `similar` (п.2) — если для демо
важен видимый пример, стоит отдельно завести намеренно «дырявую» запись в тестовых данных, живой
каталог даёт его слишком редко.
