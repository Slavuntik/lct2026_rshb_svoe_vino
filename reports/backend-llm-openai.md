# backend — драйвер `openai` для сомелье-чата через свой GPU-шлюз (22.09)

Задача тимлида: `/v1/chat` на стенде отвечает заглушкой (`LLM_PROVIDER=mock`), ключа GigaChat
нет. Решение Вячеслава: пока — свой Qwen3.8-27b на GPU-сервере команды через OpenAI-совместимый
шлюз (LiteLLM, `/v1/chat/completions`, Bearer, SSE).

## Что сделано (`packages/llm/`)

- `llm/drivers/_openai_compat.py` (новый): общая HTTP-механика Chat Completions (запрос/ответ,
  SSE-парсинг, ретраи через `_http.py`) — вынесена из `drivers/deepseek.py`, который уже был
  "почти готовым" OpenAI-совместимым драйвером (по заданию — обобщил, не задублировал).
- `llm/drivers/deepseek.py`: теперь `DeepSeekLLM(OpenAICompatLLM)`, только свои дефолты
  (`DEFAULT_BASE_URL`, `DEFAULT_MODEL`). Поведение не изменилось — старые тесты зелёные без правок.
- `llm/drivers/openai.py` (новый): `OpenAILLM(OpenAICompatLLM)`, `provider="openai"`. env:
  `LLM_BASE_URL` (обязателен, БЕЗ дефолта в коде — адрес шлюза секретный), `LLM_API_KEY`,
  `LLM_MODEL` (default `qwen3.8-27b`). Отсутствие `LLM_BASE_URL` — та же ленивая проверка, что
  отсутствие ключа: конструктор сеть не трогает, `LLMUnavailable` только при реальном вызове.
- `llm/base.py`: `LLM_PROVIDER=openai` в фабрике `get_llm()` и `_PROVIDERS`.
- Стриминг — тот же протокол, что у deepseek/gigachat (SSE `data:` → `choices[0].delta.content`);
  `apps/api/app/chat/service.py` его уже потребляет по чанкам — Protocol LLM не менялся, apps/api
  трогать не пришлось.
- Ключ/адрес нигде не логируются (как у соседних драйверов — логирования в пакете нет вовсе).

## Тесты (офлайн, `httpx.MockTransport`)

- `tests/test_offline_real_drivers.py`: +7 тестов `OpenAILLM` (happy path, отсутствие ключа,
  отсутствие `LLM_BASE_URL` — случай, уникальный для openai, 5xx-ретраи, 4xx без ретрая,
  chat_stream, from_env).
- `tests/test_factory.py`: +1 тест выбора провайдера; `test_unknown_provider_raises` переключён
  на `LLM_PROVIDER=not-a-real-provider` (был `openai` — теперь легальный провайдер).
- `cd packages/llm && uv run pytest -q` → **32 passed** (было 24).
- `cd apps/api && uv run pytest -q` → **365 passed, 11 skipped** (без изменений — регрессий нет).

## infra/ams3 (прямой пункт тимлида, вне обычной зоны backend — env обычно идёт через devops)

- `somelye.env.example`: закомментированный блок `LLM_PROVIDER=openai` + `LLM_BASE_URL`/
  `LLM_API_KEY`/`LLM_MODEL`, альтернатива блоку GigaChat.
- `README.md`: раздел «Подключение LLM через шлюз» — что это, чем отличается от
  `VISION_LLM_URL`/`VISION_LLM_KEY` (может быть тот же физический шлюз, но другие переменные и
  назначение — чат vs чтение этикетки), установка вручную по SSH (`pbpaste | ssh ... read -r` —
  тот же приём, что уже задокументирован для GigaChat).

## Риски / предложения к контрактам

- `contracts/llm-adapter.md` v0.1 (ЗАМОРОЖЕН) описывает "три драйвера" + mock; этот добавляет
  четвёртый (`openai`). Контракт сам не правил (не моя зона). Предложение architect: строка
  `openai` в таблице драйверов, с пометкой "обязательный LLM_BASE_URL без дефолта" (в отличие от
  deepseek/anthropic — у тех есть публичный дефолт).
- `json_mode=True` → `response_format:{"type":"json_object"}` в теле запроса (как у deepseek) —
  поддержка шлюзом LiteLLM/моделью вживую не проверена (только офлайн-мок, нет реального ключа).
- Секрета GitHub под `LLM_BASE_URL`/`LLM_API_KEY` нет (в отличие от `VISION_LLM_URL/KEY`) —
  включение на стенде сейчас только вручную по SSH. Если нужно через CI — отдельная задача devops.
- Не проверено вживую на реальном шлюзе (нет ключа/адреса в этой сессии) — только офлайн-тесты по
  описанию тимлида (LiteLLM, `/v1/chat/completions`, Bearer, SSE).
