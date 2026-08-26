# Отчёт агента B — Backend API

Зона: `apps/api/`, `packages/llm/`. Контракты реализованы по v0.2.2 (`contracts/openapi.yaml`,
`contracts/schema.sql`, `contracts/llm-adapter.md`, `contracts/rag-interface.md`,
`contracts/events.md`). RAG — через мок по `contracts/rag-interface.md` на 6 вымышленных винах,
агента A не ждал (появился параллельно как `packages/rag`; A уже сам догнал v0.2.1 своим
коммитом `44475b9` — интеграция с реальным ретривером в мою задачу не входила и не делалась, см.
«Предложения к контрактам» #4).

**Важно про окружение:** на машине появился полный Xcode с непринятой лицензией — голый `git`
(и системный `python3`) из PATH ломается предупреждением лицензии. Все `git`-команды в этой
сессии (и, вероятно, в следующих, пока лицензия не принята) нужно префиксовать
`DEVELOPER_DIR=/Library/Developer/CommandLineTools`, например:
`DEVELOPER_DIR=/Library/Developer/CommandLineTools git commit -m "..." -- <пути>`.
Тесты (через `./.venv/bin/python`, не системный `python3`) этой проблемой не затронуты.

## Версии контракта, покрытые этим отчётом

- **v0.2** — база: guest-auth (без строки в users), `/events`, `/chat/feedback`, `/analogs`,
  словарь кодов ошибок, `citation.n`.
- **v0.2.1** (по предложениям агента B из первой версии этого отчёта) — `users` с гостевой
  NULL-identity (`is_guest`, `age_confirmed_at`), апгрейд гость→регистрация той же строки,
  `get_by_id`/`list_reference_styles`/`get_retriever` формализованы в `rag-interface.md`,
  `internal_error` — в словаре кодов.
- **v0.2.2** (пробел нашёл агент C) — `GET /taste/candidates`, колода для свайп-дегустации.

Всё ниже описывает финальное состояние после всех трёх волн.

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
cd apps/api     && ./.venv/bin/python -m pytest -q     # 87 passed
```

Оба прогона зелёные, без сети (реальные LLM-драйверы тестируются офлайн через
`httpx.MockTransport`, RAG — мок на фикстурах). `sqlite:///:memory:` + `StaticPool` на процесс
теста — полная изоляция между тестами, без файлов на диске.

## Покрытие DoD (по пунктам брифа)

| Пункт DoD | Статус | Где |
|---|---|---|
| `uvicorn` одной командой, mock-LLM+mock-RAG | Готово | `app/main.py::create_app`, проверено вживую на сокете |
| Все тесты зелёные | Готово | 87 (api) + 20 (llm) = 107, см. команды выше |
| OpenAPI FastAPI совпадает с контрактом по путям/методам | Готово | `tests/test_openapi_contract.py` — парсит `contracts/openapi.yaml` напрямую (не руками), проверяет и покрытие, и точное совпадение множеств (сейчас 17/17 путей 1:1) |
| Регистрация: <18 → 403; ledger; логин/JWT | Готово | `tests/test_auth.py` |
| `/scan/resolve` контрактная форма + `low_confidence` | Готово | `tests/test_scan.py` |
| `/chat`: citation до done; refusal на пустой выдаче; email не в промпте | Готово | `tests/test_chat.py` (детали ниже) |
| data-export возвращает всё; DELETE + релогин → 401 | Готово | `tests/test_profile.py` |
| events: валидное 204 / невалидное 400 | Готово | `tests/test_events.py` |
| Отчёт с командами запуска | Этот файл | — |

Дополнительно (v0.2, тоже покрыто тестами): гостевой вход (`/auth/guest`) и его ограничения,
`consent_ledger` переживает `DELETE /profile` (FK снят), `/analogs` (happy path + 404 с топ-5
подсказкой), `/chat/feedback`, rate limit на auth.

Дополнительно (v0.2.1, `tests/test_guest_upgrade.py` + `tests/test_guest_attribution.py`, 14
тестов): гость — полноценная строка `users` (NULL-identity), `POST /auth/register` с гостевым
Bearer апгрейдит ту же строку и сохраняет историю сканов/чата, FK у `scans`/`chat_messages`/
`events`/`feedback` реально работают на гостя, и отдельно доказано, что `PRAGMA
foreign_keys=ON` в dev-движке не декоративен (прямая попытка вставить строку с
несуществующим `user_id` в `scans`/`chat_messages` ловит `IntegrityError`, а `consent_ledger`
это осознанно не подчиняется).

Дополнительно (v0.2.2, `tests/test_taste.py`, 7 тестов): `GET /taste/candidates` — колода для
свайп-дегустации, гейт `consent_required` для гостя, исключение уже свайпнутых вин (любой
verdict), лимит 1..50.

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

### Гость с NULL-identity: апгрейд и атрибуция (v0.2.1)

`users.is_guest` — единственный источник истины про «кто это»; JWT несёт только `sub`, поэтому
апгрейд строки (гость → регистрация) применяется мгновенно ко всем уже выданным токенам этой
строки, без переиздания. `POST /auth/register` с гостевым `Authorization: Bearer` в заголовке
дозаполняет ТУ ЖЕ строку (`email`/`password_hash`/`birth_date`, `is_guest=false`,
`age_confirmed_at` сохраняет момент ПЕРВОГО подтверждения 18+, не перезаписывается) — id не
меняется, поэтому `scans`/`chat_messages` этого id остаются на месте. Без токена, с мусорным
токеном или с токеном уже полноценного пользователя — обычная новая строка, чужая не трогается
(последние два случая контрактом не описаны буквально, см. допущения ниже).

Раньше (v0.2) гость был просто голым uuid без строки в `users`, и FK у
`scans`/`chat_messages`/`events`/`feedback` приходилось обходить, записывая туда `NULL` вместо
гостевого id (`security.fk_user_id`, теперь удалена) — иначе `PRAGMA foreign_keys=ON` уронил бы
вставку. Теперь эта проблема не нужна: гость атрибутируется как обычный `user_id`.
`tests/test_guest_attribution.py` доказывает это напрямую (реальный `user_id` в каждой из четырёх
таблиц) и отдельно доказывает, что сама PRAGMA не декоративна: вставка `Scan`/`ChatMessage` с
заведомо несуществующим `user_id` через ORM в обход роутеров ловит `IntegrityError` — без этой
проверки расхождение с Postgres всплыло бы не на dev-машине, а на демо.

### GET /taste/candidates (v0.2.2)

`MockRetriever.candidates_for_taste(exclude_ids, limit)` — round-robin по цвету вина поверх 6
фикстур (на таком объёме «разнообразие» почти тривиально, но алгоритм честно масштабируется).
Исключение уже свайпнутых — по ЛЮБОМУ verdict (`like`/`dislike`/`skip`), не только `like`: в
Tinder-стиле колоды повторно показывать уже просвайпанное явно неверно. Гейт тот же
`require_profiling_consent`, что у `/taste/swipes`/`/taste/profile` — гость получает `403
consent_required`, как явно требует описание эндпоинта в контракте.

## Ключевые допущения (не специфицировано контрактом буквально)

- **Кто аутентифицирован на каких путях.** `openapi.yaml` не содержит `securitySchemes`/`security`
  вообще. Решение: `/healthz`, `/auth/*`, `/waitlist`, `/events` — без токена (события ДО
  аутентификации вроде `age_gate_failed` иначе невозможны); `/scan/resolve`, `/scan/ocr`,
  `/wines/{id}`, `/chat`, `/chat/feedback`, `/analogs`, `/consents` — нужен принципал (гость или
  юзер); `/taste/*` — принципал + активный scope `profiling` (гость отсекается всегда, до похода
  в ledger); `/profile/data-export`, `DELETE /profile` — только зарегистрированный. Мотив жёстко
  требовать принципала на scan/chat/wines/analogs: иначе 18+ гейт тривиально обходится прямым
  вызовом API мимо `/auth/guest|register` — см. докстринг `app/security.py`.
- **Гость и таблицы с FK на `users`** — решено в v0.2.1 (было допущением/предложением в первой
  версии этого отчёта): гость теперь полноценная строка `users` (NULL-identity), FK у
  `scans`/`chat_messages`/`events`/`feedback` работают на него без обходов. `consent_ledger`
  по-прежнему намеренно без FK — но уже не из-за гостя, а потому что обязан пережить `DELETE
  /profile` (см. `apps/api/app/models.py::ConsentLedger`).
- **`POST /auth/register` с токеном НЕ гостя** (уже полноценный юзер, или мусорный/просроченный
  токен) — контракт описывает только два случая («с гостевым Bearer» / «без токена»). Решение:
  любой токен, который не резолвится в живую гостевую строку, молча игнорируется — обычная новая
  регистрация, чужая строка не трогается и не возвращается ошибка о «уже вошли». Так эндпоинт
  остаётся полностью публичным (не требует валидного заголовка вообще).
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
только при `PRAGMA foreign_keys=ON` (включено вручную на каждое соединение — **этот пункт был
явно проверен по запросу оркестратора**: `tests/test_guest_attribution.py` доказывает и что
constraint активен в dev, и что без него он был бы молча проигнорирован — см. раздел выше),
составные индексы без `DESC` (портируемости ради, функционально не важно на объёме MVP),
CHECK-констрейнт `users` (`is_guest OR identity NOT NULL`) воспроизведён как есть — SQLite его
поддерживает нативно. В проде (Postgres, `DATABASE_URL=postgresql+psycopg://...`) код тот же —
SQLAlchemy сам работает с настоящими типами; синхронный `create_engine` теперь имеет рабочий
драйвер (`psycopg[binary]`, добавлено по замечанию агента E, см. ниже).

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
  `uv sync --frozen`). Проверено: `uv sync --frozen --extra dev` + тесты зелёные + диалект
  `postgresql+psycopg` резолвится SQLAlchemy без реального сервера под рукой.
- **Полный Xcode с непринятой лицензией сломал голый `git`/`python3` из PATH** (обнаружил
  оркестратор) — обход `DEVELOPER_DIR=/Library/Developer/CommandLineTools` перед каждой
  git-командой, см. шапку отчёта. На тесты не влияет (venv-python не завязан на системный).
- v0.2.1/v0.2.2 доработки прошли без новых блокеров — все 21 правленый/новый файл прогнаны через
  полный прогон тестов (87 зелёных) и живой `uvicorn` перед коммитом.

## Предложения к контрактам

Пункты 1/2/5 из первой версии этого отчёта приняты оркестратором в v0.2.1 (коммит `112ac96`) —
отмечены ниже как ПРИНЯТО. Новые пункты — 7 (v0.2.2) и обновлённый статус 3/4.

1. **ПРИНЯТО (v0.2.1).** `Retriever.get_by_id(id) -> Candidate | None` теперь в
   `contracts/rag-interface.md`. `MockRetriever.get_by_id` переписан под точную сигнатуру —
   возвращает `Candidate` с `meta={"source":..., "derived":...}` для вина (не сырой `dict`, как
   было в черновой реализации до принятия), резолвит и `article:<slug>#n` id тоже. Вызывающий код
   (`routers/wines.py`, `routers/taste.py`) больше не оборачивает вызов в `getattr(..., None)` —
   это теперь гарантированный метод контракта, а не необязательное расширение.
2. **ПРИНЯТО (v0.2.1).** `Retriever.list_reference_styles(top_n=5) -> list[dict]` — тоже в
   контракте; переименовал параметр мока с `limit` на `top_n` под точную сигнатуру,
   `routers/analogs.py` вызывает напрямую.
3. **Частично закрыто (v0.2.1).** Гостевая атрибуция на `scans`/`chat_messages`/`events`/
   `feedback` решена — но не добавлением FK-гигиены к этим таблицам (как я предлагал), а тем, что
   гость сам стал полноценной строкой `users`. `consent_ledger` осознанно остаётся без FK — уже
   не из-за гостя, а ради выживания `DELETE /profile` (см. выше). Предложение можно закрывать.
4. **Остаётся открытым.** `packages/rag` теперь тоже реализует `get_retriever()` (коммит A
   `44475b9`, после моего `112ac96`) — `app/rag/factory.py` готов её вызывать при
   `RAG_PROVIDER=real`, но фактическая интеграция/smoke-тест против настоящего пакета всё ещё не
   входила в мою задачу («не жди его») и не делалась. Стоит явным пунктом следующей волны.
5. **ПРИНЯТО (v0.2.1).** `internal_error` — в словаре кодов ошибок `openapi.yaml`. `app/errors.py`
   больше не отступление от контракта, использует легальный код.
6. **Остаётся открытым.** `/chat` refusal.reason и `/analogs` 404 message — по-прежнему
   свободный человеческий текст, не код из словаря. Трактовка (словарь — только для
   `error.code` JSON-конверта) не менялась контрактом явно.
7. **Новое (v0.2.2).** `GET /taste/candidates` тоже не отражена в `contracts/rag-interface.md` —
   правка задела только `openapi.yaml`. `MockRetriever.candidates_for_taste(exclude_ids, limit)`
   — расширение сверх контракта тем же паттерном деградации (`getattr(..., None)` → пустая
   колода вместо 500), что был у `get_by_id`/`list_reference_styles` до их принятия в v0.2.1.
   Если колода останется частью продукта после MVP, имеет смысл формализовать метод (или его
   аналог) в `rag-interface.md` — настоящему `packages/rag` понадобится реализовать
   «разнообразие по цвету/региону/стилю» поверх настоящего каталога, не только поверх 6 фикстур.

## Что не делал (по брифу — не моя зона)

Не трогал `apps/web`, `apps/shell`, `infra/`, `packages/rag/`, контракты. Не поднимал Docker и
не звал внешние LLM/сеть ни в одном тесте (реальные драйверы deepseek/gigachat/anthropic
покрыты только офлайн-тестами через `httpx.MockTransport`).
