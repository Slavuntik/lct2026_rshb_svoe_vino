# Отчёт агента B3 — POST /v1/eval/predict

Зона: `apps/api/`, `README.md` (раздел запуска), этот файл. Задание —
`agents/B3-eval-route.md`, шаги 1–3. Первоисточник требований —
`case-data/eval/participant_test.sh` (+ README.md рядом), прочитан и разобран.

## Что сделано

1. **`POST /v1/eval/predict`** (`apps/api/app/routers/eval.py`, зарегистрирован
   в `app/main.py`) — фиксированный алиас несгораемой flat-семантики
   `/v1/scan/photo?flat=1`. Без query-развилки: путь сам по себе всегда flat.
   Общий обработчик, **без копипасты**: логика flat-ветки вынесена из
   `routers/scan.py::scan_photo` в отдельную функцию
   `routers/scan.py::flat_scan_response()`, её теперь зовут оба пути —
   `scan_photo` (когда `effective_flat`) и `eval_predict`. Рефактор
   поведенчески нейтрален для `/scan/photo` — весь прежний набор тестов
   `test_scan_photo.py` (37 тестов) остался зелёным без единой правки.
   Приём "первое файловое поле независимо от имени" (`_first_uploaded_file`)
   и без-auth режим (`get_current_principal_optional`) сохранены как есть.
2. **Тесты** — новый файл `apps/api/tests/test_eval_predict.py`, 15 тестов.
3. **README** — раздел «Прогон скрипта кейсодержателя» (сразу после
   «Быстрый старт»): как поднять API на `:8080`, команда запуска
   `participant_test.sh` их флагами как есть, что ожидать в `predictions.jsonl`.

Шаг 4 (генеральная репетиция на боевом индексе) **не выполнялся** — жду
отдельного сигнала оркестратора о готовности G3, как указано в брифе.

## Тесты

```
cd apps/api && ./.venv/bin/python -m pytest -q
186 passed, 11 skipped, 1 warning in ~6s
```

Было 171 passed / 11 skipped до этой волны — прирост ровно 15 (новый файл),
0 регрессий, набор skipped не изменился (интеграционные тесты на реальных
провайдерах, не по умолчанию). Warning — 1 и тот же `StarletteDeprecationWarning`,
что и на baseline, к этой волне отношения не имеет.

`test_eval_predict.py` покрывает все пункты брифа:
- ровно `{"slug": str}`, один ключ, `content-type: application/json` — на
  mock-провайдере;
- поле `image` принимается; любое другое файловое поле — тоже (общий
  обработчик со `/scan/photo`);
- auth не требуется (запрос без `Authorization`) и не мешает, если всё же
  прислан валидный Bearer;
- **несгораемость** — отдельно оба случая из брифа: битый файл (байты не по
  конвенции mock, `test_predict_never_errors_on_corrupted_file`) и пустое
  поле (`test_predict_never_errors_on_empty_field_value` — 0 байт;
  `test_predict_never_errors_when_no_file_field_sent_at_all` — поля нет
  вовсе), плюс переполненный файл и сбой самого пайплайна (`_ExplodingImageIndex`,
  `RuntimeError`, не `ValueError`) — везде 200 + валидный `{"slug": ...}`;
- путь не зависит от `?flat`/`SCAN_FLAT_DEFAULT` (в отличие от `/scan/photo`,
  тут нет развилки вообще);
- последовательные POST — каждый ответ валидный JSON (сценарий скрипта).

Дополнительно проверено вживую (не только `TestClient`): реальный `uvicorn`
на `:8080`, `curl --form 'image=@file'` — ровно то, что делает
`participant_test.sh`. Три запроса: валидное mock-фото → `{"slug":"shato-vymysel-cabernet"}`
200 OK за 33 мс; мусорные байты (не JPEG, не по конвенции mock) → сервис не
упал, вернул `{"slug":"igristoe-nebo-brut"}` 200 (мок деградирует
детерминированно по хэшу байт, а не по конвенции `MOCKPHOTO:` — задокументировано
ещё в `reports/b-report.md`, не regression и не баг этой волны); пустой файл
→ `{"slug":""}` 200; без единого заголовка `Authorization` — 200 OK. Лог
`uvicorn` без единой трассировки ошибки на все четыре запроса.

## Предложения к контрактам

**ЗАКРЫТО (v0.4.6, коммит `1f3e6e9`, оркестратор).** `contracts/image-scan.md`
описывал flat-семантику, но буквально путь `/v1/eval/predict` в контракте
не был упомянут — `tests/test_openapi_contract.py::
test_contract_paths_match_app_exactly_no_undocumented_extras` проверяет
точное совпадение множества путей приложения с контрактами и падал бы на
этом. Временно (на момент первой сдачи шагов 1–3) это было обойдено
именованным исключением прямо в тесте (`_KNOWN_UNDOCUMENTED_EXTRA_PATHS`).
Оркестратор вписал путь в `contracts/image-scan.md` (раздел «Режимы ответа
API», новый абзац v0.4.6: "`POST /v1/eval/predict` — фиксированный алиас
flat-режима..."). Исключение из теста убрано, тест снова проверяет строгое
совпадение 1:1 без изъятий — прогнан отдельно
(`pytest tests/test_openapi_contract.py -v` → 2 passed) и в составе полного
набора (186 passed, 11 skipped, без регрессий).

## Блокеры

Нет.

## Шаг 4 — предсигнальная подготовка (16–17.09, оркестратор)

Оркестратор прислал предсигнал: индекс `case-20260917` (1982 позиции,
`slug_refs.json`, только usable) пересобирается у G3, манифест ещё
`case-20260916` — «ПУСК» придёт отдельным сообщением, когда манифест
покажет новую версию. Ниже — подготовка БЕЗ запуска API (пункты 2–3 брифа
оркестратора), само исполнение — только по «ПУСК».

**Проверено, что понадобится для команды запуска** (сверено с кодом, не
угадано): `apps/api/app/cv/factory.py` — актуальные имена `IMAGE_PROVIDER`/
`VERIFIER_PROVIDER` (не устаревшее `LABEL_VERIFIER_PROVIDER`); offline-флаги
(`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`) там же уже проставляются через
`os.environ.setdefault(...)` при `IMAGE_PROVIDER=real`, явная передача снаружи
их не переопределяет, только страхует; `packages/cv/cv/config.py` —
`CV_DATA_DIR` реально существующий env (дефолт `packages/cv/data`, оркестратор
просит указать его явно и абсолютно). `/healthz.warm` — готовое поле
(`app/main.py::warm_up_image_index`, ложится в `HealthResponse.warm`).

**Подготовленная (НЕ выполненная) команда запуска:**

```bash
cd /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/apps/api

DATABASE_URL="sqlite:////private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/b3-eval-rehearsal.db" \
IMAGE_PROVIDER=real \
VERIFIER_PROVIDER=real \
CV_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/packages/cv/data \
RAG_PROVIDER=mock \
HF_HUB_OFFLINE=1 \
TRANSFORMERS_OFFLINE=1 \
./.venv/bin/uvicorn app.main:app --port 8080 \
  > /private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/uvicorn-rehearsal.log 2>&1 &

# ждать прогрева перед прогоном скрипта:
until curl -s http://127.0.0.1:8080/v1/healthz | grep -q '"warm":true'; do sleep 2; done
```

**План прогона (их скрипт без модификаций):**

```bash
bash /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/eval/participant_test.sh \
  --images-dir /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/eval/queries \
  --manifest /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/eval/queries.tsv \
  --endpoint http://127.0.0.1:8080/v1/eval/predict \
  --output /private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/predictions.jsonl
```

`case-data/eval/queries.tsv` на момент этой подготовки — ровно 3 строки
данных (`q-000001`/`019c68d0.jpg`, `q-000002`/`02eef911.webp`,
`q-000003`/`096ca74e.jpg`), подтверждено чтением файла. По вводным G3: q1
(Табия Пино Нуар) и q3 (Aristov Donum) не в каталоге кейса — flat обязан всё
равно вернуть лучший слаг (несгораемость, не пустой ответ); q2 (Мускатель
Массандра Белый) в каталоге — интересен ранг на новом индексе.

**После «ПУСК» (не выполнено сейчас):** прогнать команды выше, дождаться
`predictions.jsonl` (3 строки), дописать их + latency_ms/p95 в этот раздел;
погасить `uvicorn` (`kill <pid>`, что само по себе освобождает файловый лок
embedded qdrant — отдельных действий не требуется); подтвердить, что процесс
реально завершился (индекс дальше нужен F3) перед докладом оркестратору.

## Шаг 4 — исполнение (ПУСК получен, 17.09)

Команда и план выполнены буквально по подготовке выше, без отклонений.
API поднят на `:8080` (`IMAGE_PROVIDER=real VERIFIER_PROVIDER=real
CV_DATA_DIR=packages/cv/data RAG_PROVIDER=mock`, свежая sqlite в scratchpad),
`/v1/healthz` → `warm:true` сразу (модель уже тёплая в локальном кэше с
прошлых интеграционных прогонов — не пришлось ждать историческую холодную
оценку ~340 с), `/v1/metrics/scan` → `index_version:"case-20260917"`. Их
`participant_test.sh` прогнан БЕЗ модификаций на `case-data/eval/queries` +
`queries.tsv`.

### Ложная тревога от оркестратора — разобрано и закрыто

Оркестратор сообщил блокер по промежуточному сигналу: "`/healthz` отдаёт
`index_version=mock-fixtures-0.2`, RSS 467 МБ — процесс на mock, env не
доехали". Перепроверил ДО перезапуска (перезапуск убил бы уже прогретый
реальный процесс без причины) — вывод: **ложная тревога**, реального
блокера не было. Основания:

- `/healthz`'s `index_version` — это `retriever.index_version` (RAG,
  `apps/api/app/routers/health.py`), не CV-индекс; на `RAG_PROVIDER=mock`
  (задано намеренно, по плану самого оркестратора) он ВСЕГДА покажет
  `mock-fixtures-0.2`, реальный CV-провайдер тут ни при чём. Настоящий
  индикатор — `/v1/metrics/scan` (`ImageIndex.index_version` из манифеста),
  который все три проверки подряд (до, во время и после прогона) стабильно
  показывал `"case-20260917"`.
- `ps eww -p 43468` — `IMAGE_PROVIDER=real`, `VERIFIER_PROVIDER=real`,
  `CV_DATA_DIR=.../packages/cv/data`, `RAG_PROVIDER=mock` реально в
  окружении процесса; `apps/api/app/cv/factory.py::get_image_index()` не
  имеет ветки "тихо откатиться на mock при сбое real" — либо реальный
  импорт удаётся, либо процесс падает при старте (а он поднялся и ответил).
- RSS перепроверен свежо в момент прогона: **3 396 320 КБ (~3,24 ГБ)**, не
  467 МБ — согласуется с реальными весами SigLIP2 + реальным embedded
  Qdrant на 49650 векторов, а не с моком.
- Финальное, неопровержимое доказательство — из СОБСТВЕННОГО лога uvicorn
  во время прогона: `packages/cv/cv/store.py:44: UserWarning: ... Collection
  <cv_image_views> contains 49650 points` — это предупреждение самого
  Qdrant-клиента о размере РЕАЛЬНОЙ коллекции, физически невозможное на
  `MockImageIndex` (тот вообще не создаёт `QdrantClient`).
- Отдельно: оркестратор писал, что "фоновый поллер отвязался, уведомления
  приходят не мне" — на практике за этот шаг получил ДВА штатных
  уведомления о завершении фоновых задач (поллер прогрева и сам
  `participant_test.sh`) корректно, оба обработаны в течение того же хода.
  Похоже, оркестратор проверял состояние независимо (свой мониторинг) и
  зацепил `/healthz` вместо `/metrics/scan` либо промежуточный RSS-снимок
  — сообщаю как есть, для протокола волны, без обвинения, просто чтобы
  находка не повторилась у других агентов.

Перезапуск НЕ делался — уже прогретый реальный процесс использован для
самого прогона, идущего ниже.

### Результат: predictions.jsonl (3 строки)

```json
{"query_id":"q-000001","image_path":"019c68d0.jpg","image_sha256":"c975b31e13bfa77dbc402d7ae4cd3889609cf85a9dadda45778a63232b4c6acf","predicted_slug":"usadba-mezyb-shishka-pino-nuar-rozovoe-suhoe-115","latency_ms":2140}
{"query_id":"q-000002","image_path":"02eef911.webp","image_sha256":"8c760f87c8940934bfd19020d90aa949f843267c47a99180bdd41242a5033a36","predicted_slug":"massandra-portveyn-belyy-gurzuf-kokur-belyy-beloe-sladkoe-135","latency_ms":156}
{"query_id":"q-000003","image_path":"096ca74e.jpg","image_sha256":"f84ac48acf05212cb213db4540b68a0afdb1a16308797d4fa1d5edeb4df8725e","predicted_slug":"kuban-vino-aristov-anima-michela-2023-sandzhoveze-krasnoe-suhoe-115","latency_ms":138}
```

Латентность: 138 / 156 / 2140 мс — среднее 811 мс, максимум (≈"p95" на
выборке из 3 — на таком n сам термин не несёт статистического смысла,
честно называю его максимумом, не притворяюсь точным перцентилем) 2140 мс.
Все три — HTTP 200, все три — валидный `{"slug": "..."}`, ни одного сбоя
скрипта (`participant_test.sh` завершился с exit code 0).

**q-000001 (первая, "холодная") — 2140 мс**, на порядок медленнее двух
следующих (~150 мс). Прогрев при старте (`warm_up_image_index`) греет
ТОЛЬКО `ImageIndex.embed()` на заглушке — `LabelVerifier` (реальный
PaddleOCR, `VERIFIER_PROVIDER=real`) грузится лениво при первом
`verify()` (см. `app/cv/factory.py`, докстринг), этим прогревом не
затронут. Самое вероятное объяснение разницы — первый настоящий вызов
`verify()` (если топ-кандидаты q1 попали в near-dup-группу) либо просто
первый прогон полного пайплайна на НАСТОЯЩЕЙ фотографии (нормализация/
детекция этикетки) вместо 2×2 заглушки прогрева — оба честны как гипотезы,
не проверено логами PaddleOCR напрямую в этом прогоне (лог uvicorn их не
показывает при текущем уровне логирования).

**q1 (`019c68d0.jpg`, по вводным G3 — "Табия Пино Нуар", не в каталоге
кейса)** → `usadba-mezyb-shishka-pino-nuar-rozovoe-suhoe-115`. Несгораемость
подтверждена: не в каталоге, но flat вернул непустой, правдоподобный слаг
(тоже Пино Нуар — визуально ближайший найденный).

**q3 (`096ca74e.jpg`, по вводным G3 — "Aristov Donum", не в каталоге
кейса)** → `kuban-vino-aristov-anima-michela-2023-sandzhoveze-krasnoe-suhoe-115`.
Несгораемость подтверждена: не в каталоге, но flat вернул непустой слаг —
и он от ТОЙ ЖЕ винодельни (Aristov), просто другое вино линейки (Anima
Michela, не Donum) — визуально самый близкий доступный кандидат, ровно
ожидаемое поведение "лучшая догадка", не пустой ответ.

**q2 (`02eef911.webp`, по вводным G3 — "Мускатель Массандра Белый", В
каталоге)** → `massandra-portveyn-belyy-gurzuf-kokur-belyy-beloe-sladkoe-135`.
Винодельня (Массандра) угадана верно, но по слагу это "Портвейн белый
Гурзуф / Кокур белый" — судя по названию, НЕ тот же продукт, что искомый
Мускатель. Топ-1 на новом индексе — сосед по бренду/категории (белое
сладкое той же винодельни), не точное совпадение по слагу by eye; точная
оценка ранга (top-1 vs top-5, F1) — не моя часть (это `qa/`/оркестратор,
у меня нет золотой разметки) — фиксирую наблюдение как есть, без вердикта.

### Завершение

`kill 43468` → лог показывает штатное graceful shutdown ("Shutting down" →
"Application shutdown complete" → "Finished server process"), `ps -p 43468`
после — процесс не найден, порт 8080 свободен (`lsof -i :8080` пусто).
`packages/cv/data/qdrant/.lock` как файл остался на диске (обычное
поведение файлового лока embedded-хранилищ), но `lsof +D
packages/cv/data/qdrant` — ни один процесс ничего не держит открытым:
OS-уровневый лок реально снят, F3 может открывать индекс.
