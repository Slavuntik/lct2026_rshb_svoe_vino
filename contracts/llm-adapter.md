# Контракт LLM-адаптера (packages/llm) v0.1 — ЗАМОРОЖЕН

Единый интерфейс, три драйвера. Выбор — только через env, никакой логики выбора в коде API.

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
| `gigachat` | OAuth-токен по client credentials, свой REST | следующий прод |
| `anthropic` | Messages API (`LLM_API_KEY`, `LLM_MODEL`) | dev / judge в eval |
| `mock` | детерминированные ответы из фикстур | тесты и разработка без ключей |

Обязателен драйвер `mock`: собирает ответ из подставленных цитат, чтобы API и клиент
разрабатывались без единого ключа. Таймаут 30с, 2 ретрая с бэкоффом, ошибки провайдера
превращаются в `LLMUnavailable` — API отвечает refusal, не 500.

## Гигиена

- В messages НИКОГДА не попадают: email, id пользователя, сырые события, геолокация.
- Вкусовой паспорт передаётся строкой вида `sweetness=0.3 acidity=0.7 ...` без идентичности.
- Логируется: провайдер, модель, latency, токены. НЕ логируется содержимое messages.
