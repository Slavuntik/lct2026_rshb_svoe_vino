# Контракт LLM-адаптера (packages/llm) v0.2 — ЗАМОРОЖЕН

v0.2 (22.09, architect): +драйвер `openai` — ратификация по факту (`reports/
backend-llm-openai.md`): реализован, закоммичен, задеплоен на стенд тегом hack-v13
(`reports/devops-hack-v13.md`) ДО этой ратификации — контракт документирует уже боевое
поведение, не предписывает новое (та же логика, что архитектор применил к `/scan/photo` в
`openapi.yaml` 22.09, `reports/architect-submission-audit.md`). Проверено: `packages/llm/
llm/base.py::_PROVIDERS` несёт ровно 4 ключа (`deepseek`, `gigachat`, `anthropic`, `openai`)
плюс `mock` — таблица ниже синхронна с кодом.

Единый интерфейс, четыре прод-драйвера + обязательный `mock`. Выбор — только через env,
никакой логики выбора в коде API.

```python
# packages/llm/llm/base.py
from typing import Iterator, Literal, Protocol, TypedDict

class Msg(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str

class LLM(Protocol):
    def chat(self, messages: list[Msg], *, json_mode: bool = False,
             max_tokens: int = 1024, temperature: float = 0.3) -> str: ...
    def chat_stream(self, messages: list[Msg], *, max_tokens: int = 1024,
                    temperature: float = 0.3) -> Iterator[str]: ...  # чанки текста

def get_llm() -> LLM: ...  # фабрика по env
```

## Драйверы

| env `LLM_PROVIDER` | Реализация | Примечания |
|---|---|---|
| `deepseek` | OpenAI-совместимый HTTP (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) | прод демо |
| `openai` | OpenAI-совместимый HTTP — общая механика `drivers/_openai_compat.py` (та же, что теперь и `deepseek` наследует); модель по умолчанию `qwen3.8-27b` | 22.09: боевой чат хак-стенда (свой GPU-шлюз, LiteLLM `/v1/chat/completions`, Bearer, SSE) вместо GigaChat без ключа. **`LLM_BASE_URL` обязателен, БЕЗ дефолта в коде** (адрес шлюза — секрет) — единственный драйвер без публичного дефолт-URL, в отличие от `deepseek`/`anthropic`; отсутствие — та же ленивая проверка, что отсутствие ключа (конструктор сеть не трогает, `LLMUnavailable` только при вызове) |
| `gigachat` | OAuth-токен по client credentials, свой REST | следующий прод, ключа `GIGACHAT_AUTH_KEY` пока нет |
| `anthropic` | Messages API (`LLM_API_KEY`, `LLM_MODEL`) | dev / judge в eval |
| `mock` | детерминированные ответы из фикстур | тесты и разработка без ключей |

Обязателен драйвер `mock`: собирает ответ из подставленных цитат, чтобы API и клиент
разрабатывались без единого ключа. Таймаут 30с, 2 ретрая с бэкоффом, ошибки провайдера
превращаются в `LLMUnavailable` — API отвечает refusal, не 500.

**Известное ограничение `openai` (22.09, зафиксировано backend, не блокирует ратификацию):**
`json_mode=True` → `response_format: {"type": "json_object"}` в теле запроса (как у
`deepseek`) — поддержка именно ЭТИМ шлюзом/моделью (LiteLLM, `qwen3.8-27b`) вживую не
проверена, только офлайн-моком (`httpx.MockTransport`, нет ключа в сессии, где писался
драйвер). `/v1/chat` эту опцию не использует (стриминг текста, не JSON) — риск не
блокирует боевой чат, актуален только для будущих json_mode-вызовов этого драйвера.

## Гигиена

- В messages НИКОГДА не попадают: email, id пользователя, сырые события, геолокация.
- Вкусовой паспорт передаётся строкой вида `sweetness=0.3 acidity=0.7 ...` без идентичности.
- Логируется: провайдер, модель, latency, токены. НЕ логируется содержимое messages.
