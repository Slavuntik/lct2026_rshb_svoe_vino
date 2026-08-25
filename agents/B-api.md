# Агент B · Backend API

**Зона:** `apps/api/`, `packages/llm/`, `reports/b-report.md`
**Контракты:** `contracts/openapi.yaml` (реализовать в точности), `contracts/schema.sql`,
`contracts/llm-adapter.md`, `contracts/events.md`. RAG — через интерфейс из
`contracts/rag-interface.md` с моком (пакет A подключится позже, не жди его).

## Задача

FastAPI-приложение по контракту + LLM-адаптер с четырьмя драйверами (deepseek, gigachat,
anthropic, mock) + consent-ledger + промпт-сборка чата с цитатами.

## Порядок

1. `uv venv` в `apps/api`. Зависимости: fastapi, uvicorn, sqlalchemy (или asyncpg+чистый SQL),
   pydantic v2, argon2-cffi, pyjwt, httpx, sse-starlette, pytest, pytest-asyncio.
   Postgres в дев-среде НЕТ — SQLite-совместимый слой или запуск схемы в pg при наличии;
   схема contracts/schema.sql — источник истины, различия SQLite задокументируй.
2. Эндпоинты по openapi.yaml, включая коды ошибок и формат error. Гейт 18+ на регистрации.
   Consent-ledger append-only; /consents POST c grant=false и scope=base ставит deleted_at.
3. `packages/llm`: Protocol + 4 драйвера по контракту. mock-драйвер собирает ответ из
   переданных цитат детерминированно. Реальные драйверы — код готов, ключи через env,
   интеграционные вызовы за флагом (в тестах не зовём сеть).
4. Чат: retrieval через RAG-интерфейс (мок с фикстурами из 6 вин) → промпт «отвечай только
   по выдержкам, каждое утверждение с [n]» → SSE token/citation/done; пустая выдача → refusal.
   Маскирование: в messages не попадает ничего из users/consents — проверь тестом.
5. События: POST-хук пишет в events по словарю contracts/events.md; неизвестное имя — 400.
6. /waitlist без auth; rate limit на auth и waitlist (slowapi или своя простая корзина).

## Тесты (pytest, обязательны)

- регистрация: до 18 → 403; согласия пишутся в ledger; логин/JWT.
- /scan/resolve с моком RAG: контрактная форма ответа, low_confidence.
- /chat: SSE-поток содержит citation до done; refusal при пустой выдаче;
  тест «в промпт не утёк email» (прогнать сборку промпта на юзере с данными).
- data-export возвращает всё; DELETE /profile + повторный логин → 401.
- events: валидное имя 204, невалидное 400.

## DoD

`uvicorn` поднимается одной командой с mock-LLM и mock-RAG; все тесты зелёные;
openapi-схема FastAPI совпадает с контрактом по путям/методам (тест-сверка);
отчёт с командами запуска.

## Не делать

Не трогать веб/инфру; не ставить Docker; не звать внешние API в тестах.
