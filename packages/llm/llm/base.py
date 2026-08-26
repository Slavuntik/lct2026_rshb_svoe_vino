"""Единый интерфейс LLM-адаптера.

Контракт: contracts/llm-adapter.md — ЗАМОРОЖЕН, воспроизведён здесь дословно
(Protocol LLM, Msg, get_llm()). Выбор драйвера — только через env LLM_PROVIDER,
никакой ветвящейся логики в коде API.
"""
from __future__ import annotations

import os
from typing import Iterator, Literal, Protocol, TypedDict


class Msg(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLM(Protocol):
    def chat(
        self,
        messages: list[Msg],
        *,
        json_mode: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str: ...

    def chat_stream(
        self,
        messages: list[Msg],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Iterator[str]: ...  # чанки текста


class LLMUnavailable(Exception):
    """Провайдер недоступен (таймаут/ошибка сети/ошибка API после ретраев).

    API обязан ловить это исключение и отвечать честным refusal, а не 500.
    """


# Провайдеры, требующие сеть. Их конструкторы не обязаны трогать сеть сами по
# себе (ленивая инициализация HTTP-клиента), поэтому импорт модулей безопасен
# даже без ключей — фактический вызов упадёт в LLMUnavailable при отсутствии
# LLM_API_KEY.
_PROVIDERS = ("deepseek", "gigachat", "anthropic", "mock")


def get_llm() -> LLM:
    """Фабрика по env. LLM_PROVIDER не задан => mock (безопасный дефолт для dev)."""
    provider = os.environ.get("LLM_PROVIDER", "mock").strip().lower()

    if provider == "mock":
        from .drivers.mock import MockLLM

        return MockLLM()
    if provider == "deepseek":
        from .drivers.deepseek import DeepSeekLLM

        return DeepSeekLLM.from_env()
    if provider == "gigachat":
        from .drivers.gigachat import GigaChatLLM

        return GigaChatLLM.from_env()
    if provider == "anthropic":
        from .drivers.anthropic import AnthropicLLM

        return AnthropicLLM.from_env()

    raise ValueError(
        f"Неизвестный LLM_PROVIDER={provider!r}, ожидается один из {_PROVIDERS}"
    )
