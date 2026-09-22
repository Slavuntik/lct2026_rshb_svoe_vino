"""DeepSeek — OpenAI-совместимый HTTP-драйвер (прод демо).

env: LLM_BASE_URL (default https://api.deepseek.com), LLM_API_KEY (обязателен
при реальном вызове), LLM_MODEL (default deepseek-chat).

Сборка запроса/разбор ответа и SSE-стрима — общий код с drivers/openai.py
(оба говорят протоколом OpenAI Chat Completions), см. _openai_compat.py.
Этот модуль задаёт только дефолты DeepSeek.
"""
from __future__ import annotations

import os

from ._openai_compat import OpenAICompatLLM

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekLLM(OpenAICompatLLM):
    provider = "deepseek"

    @classmethod
    def from_env(cls) -> "DeepSeekLLM":
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get("LLM_API_KEY"),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        )
