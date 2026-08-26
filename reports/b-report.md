# Отчёт агента B — Backend API

Зона: `apps/api/`, `packages/llm/`. Контракты реализованы по v0.2 (`contracts/openapi.yaml`,
`contracts/schema.sql`, `contracts/llm-adapter.md`, `contracts/rag-interface.md`,
`contracts/events.md`). RAG — через мок по `contracts/rag-interface.md` на 6 вымышленных винах,
агента A не ждал (появился параллельно как `packages/rag`, коммит `5337982` — интеграция с
реальным ретривером в мою задачу не входила и не делалась, см. «Предложения к контрактам» #4).

## Как поднять

Одноразовая установка (Python 3.12 через uv, зависимости из `uv.lock`):

```bash
cd packages/llm && ~/.local/bin/uv venv --python 3.12 && ~/.local/bin/uv pip install -e ".[dev]"
cd apps/api    && ~/.local/bin/uv venv --python 3.12 && ~/.local/bin/uv sync --frozen --extra dev
```

### Сервер — mock-LLM + mock-RAG (DoD: одна команда, без единого ключа)

```bash
cd apps/api
./.venv/bin/uvicorn app.main:app --reload
# GET  http://127.0.0.1:8000/v1/healthz      -> {"status":"ok","index_version":"mock-fixtures-0.2"}
# GET  http://127.0.0.1:8000/docs            -> Swagger UI
# GET  http://127.0.0.1:8000/openapi.json
```

Ничего дополнительно не требуется: `LLM_PROVIDER` и `RAG_PROVIDER` по умолчанию `mock`,
`DATABASE_URL` по умолчанию `sqlite:///./svoy_somelye.db` (создаётся сама). Проверено не
только через `TestClient`, но и вживую — `uvicorn` на реальном сокете, `curl` на `/v1/healthz`,
`/openapi.json` и `POST /v1/auth/guest` отвечали корректно.

Переключение на реальные провайдеры — только через env, без правок кода:

```bash
LLM_PROVIDER=anthropic LLM_API_KEY=... LLM_MODEL=claude-3-5-haiku-latest ./.venv/bin/uvicorn app.main:app
LLM_PROVIDER=deepseek  LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=deepseek-chat ./.venv/bin/uvicorn app.main:app
LLM_PROVIDER=gigachat  GIGACHAT_AUTH_KEY=... ./.venv/bin/uvicorn app.main:app
RAG_PROVIDER=real ./.venv/bin/uvicorn app.main:app   # см. блокер/предложение #4 — пока деградирует с понятной ошибкой
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db ./.venv/bin/uvicorn app.main:app
```

### Тесты

```bash
cd packages/llm && ./.venv/bin/python -m pytest -q     # 20 passed
cd apps/api     && ./.venv/bin/python -m pytest -q     # 66 passed
```

Оба прогона зелёные, без сети (реальные LLM-драйверы тестируются офлайн через
`httpx.MockTransport`, RAG — мок на фикстурах). `sqlite:///:memory:` + `StaticPool` на процесс
теста — полная изоляция между тестами, без файлов на диске.

## Покрытие DoD (по пунктам брифа)

| Пункт DoD | Статус | Где |
|---|---|---|
| `uvicorn` одной командой, mock-LLM+mock-RAG | Готово | `app/main.py::create_app`, проверено вживую на сокете |
| Все тесты зелёные | Готово | 66 (api) + 20 (llm) = 86, см. команды выше |
| OpenAPI FastAPI совпадает с контрактом по путям/методам | Готово | `tests/test_openapi_contract.py` — парсит `contracts/openapi.yaml` напрямую (не руками), проверяет и покрытие, и точное совпадение множеств (сейчас 17/17 путей 1:1) |
| Регистрация: <18 → 403; ledger; логин/JWT | Готово | `tests/test_auth.py` |
| `/scan/resolve` контрактная форма + `low_confidence` | Готово | `tests/test_scan.py` |
| `/chat`: citation до done; refusal на пустой выдаче; email не в промпте | Готово | `tests/test_chat.py` (детали ниже) |
| data-export возвращает всё; DELETE + релогин → 401 | Готово | `tests/test_profile.py` |
| events: валидное 204 / невалидное 400 | Готово | `tests/test_events.py` |
| Отчёт с командами запуска | Этот файл | — |

Дополнительно (v0.2, появилось после ревью 01, тоже покрыто тестами): гостевой вход
(`/auth/guest`) и его ограничения, `consent_ledger` переживает `DELETE /profile` (FK снят),
`/analogs` (happy path + 404 с топ-5 подсказкой), `/chat/feedback`, rate limit на auth.

### Как именно проверен «email не утекает в промпт»

`tests/test_chat.py::test_email_never_reaches_llm_prompt` — регистрирует пользователя с
характерным email, даёт ему вкусовой профиль (лайк вина), подменяет `app.state.llm` на
`RecordingLLM` (обёртка вокруг настоящего mock-драйвера, запоминающая все `messages`), дёргает
`/chat` и проверяет, что ни email, ни `user_id`, ни сам домен `@example.com` не встречаются ни в
одном сообщении, реально ушедшем в LLM. Плюс структурная гарантия: `build_messages()` в
`app/chat/prompt.py` физически не принимает email/id как параметры (тест
`test_build_messages_signature_has_no_identity_parameters` проверяет сигнатуру) — утечка
потребовала бы сначала осознанно изменить сигнатуру функции, а не просто дописать одну строку.

### Как проверен refusal на пустой выдаче

Два теста: юнит `test_run_chat_refuses_on_empty_retrieval` — `run_chat()` на стабе
`Retriever.search() -> []` напрямую; end-to-end `test_chat_endpoint_refuses_end_to_end_on_gibberish`
— настоящий HTTP-запрос с бессмысленным текстом даёт `{"type":"refusal","reason":...}` без единой
`citation`. Потребовалось добавить порог `_SEARCH_MIN_SCORE=0.08` в `MockRetriever.search()`
(иначе fuzzy-скоринг всегда возвращал «наименее плохие» top-k даже на полную бессмыслицу) —
это реальное улучшение мока, не костыль теста, задокументировано в `app/rag/mock.py`.

## Ключевые допущения (не специфицировано контрактом буквально)

- **Кто аутентифицирован на каких путях.** `openapi.yaml` не содержит `securitySchemes`/`security`
  вообще. Решение: `/healthz`, `/auth/*`, `/waitlist`, `/events` — без токена (события ДО
  аутентификации вроде `age_gate_failed` иначе невозможны); `/scan/resolve`, `/scan/ocr`,
  `/wines/{id}`, `/chat`, `/chat/feedback`, `/analogs`, `/consents` — нужен принципал (гость или
  юзер); `/taste/*` — принципал + активный scope `profiling` (гость отсекается всегда, до похода
  в ledger); `/profile/data-export`, `DELETE /profile` — только зарегистрированный. Мотив жёстко
  требовать принципала на scan/chat/wines/analogs: иначе 18+ гейт тривиально обходится прямым
  вызовом API мимо `/auth/guest|register` — см. докстринг `app/security.py`.
- **Гость и таблицы с FK на `users`.** `consent_ledger` в v0.2 намеренно без FK (переживает
  удаление), но `scans`/`chat_messages`/`events`/`feedback` — по-прежнему с FK (CASCADE/SET NULL)
  на `users(id)`, а у гостя строки в `users` нет и не будет. При `PRAGMA foreign_keys=ON`
  (включено для SQLite, см. `app/db.py`) вставка гостевого uuid в эти колонки уронила бы
  `IntegrityError`. Решение: `security.fk_user_id(principal)` пишет `NULL` вместо гостевого id в
  эти конкретные колонки — гость может scan/chat/оставлять feedback, но эти строки не
  привязываются к его анонимному id на уровне БД (только на уровне валидности самого JWT в
  течение его жизни). См. предложение к контракту #3 ниже, если продукту важно связывать
  гостевую активность.
- **Ответ `/consents` GET** — контракт не даёт схему («200: ok»). Отдаю список последних записей
  по каждому scope: `[{scope, consent_version, granted, at}]`.
- **`/profile/data-export`** — контракт не даёт схему («полный дамп»). Отдаю
  `{user, consents, swipes, taste_profile, scans, chat_messages, events, feedback}`;
  `password_hash` из `user` сознательно исключён (это секрет аутентификации, не персональные
  данные в смысле права на выгрузку).
- **`answer_id` в `/chat/feedback`** — контракт типизирует как `string`; это стрингифицированный
  `chat_messages.id` (int). Нечисловой/несуществующий `answer_id` → `404 not_found`, а не `422`.
- **Вектор вкусового паспорта** — ни один контракт не описывает алгоритм. Эвристика уровня MVP:
  среднее сенсорных векторов лайкнутых вин, топ-3 самых частых `reference_style_matches`; без
  лайков — нейтральный вектор `0.5`. `dislike`/`skip` не двигают вектор, но входят в
  `swipes_count`.
- **Регистрация** дополнительно требует `"base" in consent_scopes` (иначе `400 validation_error`)
  и уникальность email (иначе `400 validation_error`, кода `conflict` в словаре нет). Оба —
  легальные допущения по духу consent-ledger дизайна, не по букве контракта.
- **`/scan/ocr` → всегда `501 not_implemented`** — по явному сокращению оркестратора (веб-скан
  фото отложен). Форма запроса (`multipart`, `image`+`explicit_consent`) объявлена по контракту
  для формы OpenAPI-схемы, тело не разбирается.
- **Сервер не пишет `events` сам за другие эндпоинты** — единственная точка записи в таблицу
  `events` это `POST /events`, вызываемый клиентом. Домены (`scans`, `chat_messages`, `feedback`,
  `swipes`) пишутся эндпоинтами напрямую, это разные таблицы с разным назначением. Если
  продукту важна аналитическая полнота при ненадёжном клиенте — стоит явно решить, каким
  событиям (`chat_answer_done`, `scan_resolved`) быть server-authoritative; см. предложение ниже.
- **SSE буферизуется целиком** до отправки клиенту (токены шлются последовательно, но после
  того как весь ответ уже получен от LLM), а не проксируется чанк-в-чанк живьём — так проще
  гарантировать «token* → citation* → done» и защитный refusal-фоллбэк на ответ без цитат.
  Компромисс по задержке до первого байта, не по корректности; см. `app/chat/service.py`.

## Расхождения SQLite vs `contracts/schema.sql` (Postgres)

Подробно в докстринге `apps/api/app/models.py`; коротко: `uuid`→`String(36)` (id генерирует
приложение), `bigserial`→`Integer autoincrement`, `jsonb`→`JSON` (сериализуется как TEXT),
`timestamptz`→naive `DateTime` (везде UTC без tzinfo), `ON DELETE CASCADE/SET NULL` работает
только при `PRAGMA foreign_keys=ON` (включено вручную на каждое соединение), составные индексы
без `DESC` (портируемости ради, функционально не важно на объёме MVP). В проде (Postgres,
`DATABASE_URL=postgresql+psycopg://...`) код тот же — SQLAlchemy сам работает с настоящими
типами; синхронный `create_engine` теперь имеет рабочий драйвер (`psycopg[binary]`, добавлено
по замечанию агента E, см. ниже).

## Блокеры

Активных блокеров, мешающих DoD, нет — всё зелёное. Один процессный инцидент (закрыт):

- **Гонка `git add`/`git commit` на общем индексе.** Мой `apps/api/app/*` и `apps/api/pyproject.toml`
  оказались закоммичены под чужим сообщением (`b85223b`, агент E) — E сделал `git add`
  ровно в своих путях, но следом голый `git commit` (без pathspec) забрал весь индекс, где к
  тому моменту уже лежал мой `git add` (я коммичу в несколько шагов, `apps/api/tests/` в этот
  момент ещё не был застейджен). Содержимое проверено побайтово (`git diff HEAD -- apps/api/app
  apps/api/pyproject.toml` пуст) — ничего не потеряно и не расходится с рабочим деревом, чисто
  вопрос атрибуции коммита. Агент E сам это заметил и зафиксировал (`reports/e-report.md`),
  оркестратор ввёл правило коммитить только с явным pathspec (`git commit -m "..." --
  <пути>`) — с этого момента так и делаю. Стоит на будущее: явный pathspec на `commit` сужает
  окно гонки, но не убирает её полностью на этапе `add` (тоже отмечено агентом E) — если волна
  агентов будет и дальше идти параллельно в одном рабочем дереве, возможно, стоит
  сериализовать `add+commit` между агентами на уровне оркестратора.
- **По замечаниям ревью инфры (агент E) — закрыто в этой сессии:** добавлен
  `psycopg[binary]>=3.1` в `apps/api/pyproject.toml` (прод `DATABASE_URL=postgresql+psycopg://...`
  иначе не поднялся бы) и закоммичен `apps/api/uv.lock` (нужен `infra/Dockerfile.api` для
  `uv sync --frozen`). Проверено: `uv sync --frozen --extra dev` + все 66 тестов зелёные +
  диалект `postgresql+psycopg` резолвится SQLAlchemy без реального сервера под рукой.

## Предложения к контрактам

1. **`Retriever` не даёт точечный доступ к карточке вина по id.** Нужен для `GET /wines/{wine_id}`
   (там просто lookup по known slug, не семантический поиск) — `search`/`similar` для этого не
   подходят. `MockRetriever.get_by_id(wine_id) -> dict | None` — расширение сверх контракта;
   `app/routers/wines.py` берёт его через `getattr(..., "get_by_id", None)` и честно отдаёт 404,
   если у ретривера такого метода нет. Предлагаю добавить в `contracts/rag-interface.md`.
2. **Нет способа перечислить эталонные стили** (нужно для подсказки топ-5 в `404 not_found` на
   `/analogs`, когда `resolve_style` не распознал запрос). `MockRetriever.list_reference_styles()`
   — тоже расширение сверх контракта, тот же паттерн деградации через `getattr`.
3. **`consent_ledger` без FK на `users` (v0.2) — а `scans`/`chat_messages`/`events`/`feedback`
   всё ещё с FK.** Это осознанно ломает привязку гостевой активности к стабильному id на уровне
   БД (см. «Ключевые допущения» выше) — для MVP-демо это, возможно, даже плюс (меньше
   персистентного анонимного трекинга), но если продукту важно смёржить историю гостя в
   аккаунт после полноценной регистрации, тем же таблицам понадобится то же решение, что уже
   применили к `consent_ledger`.
4. **`packages/rag` не имеет декларированного способа получить готовый `Retriever`** (в отличие
   от `packages/llm.get_llm()`). `app/rag/factory.py` при `RAG_PROVIDER=real` пробует
   `rag.get_retriever()`, затем `rag.Retriever()` без аргументов, иначе — понятная ошибка вместо
   краша. Агент A выпустил `packages/rag` уже после того, как я закончил (коммит `5337982`) —
   реальная интеграция и проверка фабрики против настоящего пакета не входила в мою задачу
   («не жди его», работать через мок) и не делалась; вероятно, стоит отдельным пунктом
   следующей волны свести `RAG_PROVIDER=real` с фактической формой `packages/rag`.
5. **В словаре кодов ошибок (`openapi.yaml` v0.2, шапка) нет кода для непредвиденных 500.**
   Использую `internal_error` как единственное осознанное отступление от списка (иначе на любом
   необработанном исключении сломался бы сам JSON-конверт ошибки, что хуже) — см.
   `app/errors.py`. Предлагаю формально добавить `internal_error` в словарь.
6. **`/chat` refusal.reason и `/analogs` 404 message — не по словарю кодов, а человеческий
   текст.** Читаю формат как «этот словарь про `error.code` в JSON-конверте, `reason`/`message`
   внутри SSE и хинтов — свободный честный текст», но это неявная трактовка — стоит явно
   прописать в контракте, если это важно для клиента (например, чтобы UI мог ветвиться по коду,
   а не парсить текст).

## Что не делал (по брифу — не моя зона)

Не трогал `apps/web`, `apps/shell`, `infra/`, `packages/rag/`, контракты. Не поднимал Docker и
не звал внешние LLM/сеть ни в одном тесте (реальные драйверы deepseek/gigachat/anthropic
покрыты только офлайн-тестами через `httpx.MockTransport`).
