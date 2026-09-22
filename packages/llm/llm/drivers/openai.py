"""openai — универсальный OpenAI-совместимый HTTP-драйвер (свой GPU-шлюз).

Решение Вячеслава (22.09, задача тимлида, reports/backend-llm-openai.md):
пока нет ключа GigaChat, сомелье-чат (/v1/chat) подключается к внутреннему
GPU-серверу команды через OpenAI-совместимый шлюз (LiteLLM,
`/v1/chat/completions`, Bearer-токен, модель qwen3.8-27b, SSE-стрим
поддержан шлюзом). Это НЕ то же самое, что VISION_LLM_URL/VISION_LLM_KEY
(app/config.py) — те про чтение этикетки на скане (packages/cv), эти — про
текстовый чат (packages/llm).

env: LLM_BASE_URL (обязателен, например https://<gpu-host>/v1 — БЕЗ дефолта
в коде: адрес шлюза секретный, см. contracts/llm-adapter.md "Гигиена" и
backend.md "Правила для всех"), LLM_API_KEY (обязателен при реальном
вызове, не логируется), LLM_MODEL (default qwen3.8-27b). Таймаут и ретраи —
как у соседних HTTP-драйверов (_http.py: 30с, 2 ретрая с бэкоффом).

Сборка запроса/разбор ответа и SSE-стрима — общий код с drivers/deepseek.py
(оба говорят протоколом OpenAI Chat Completions), см. _openai_compat.py.
Любая другая OpenAI-совместимая модель за этим же шлюзом (или другим) — тоже
этот драйвер, просто с другими LLM_BASE_URL/LLM_MODEL.
"""
from __future__ import annotations

import os

from ._openai_compat import OpenAICompatLLM

DEFAULT_MODEL = "qwen3.8-27b"


class OpenAILLM(OpenAICompatLLM):
    provider = "openai"

    @classmethod
    def from_env(cls) -> "OpenAILLM":
        return cls(
            base_url=os.environ.get("LLM_BASE_URL"),
            api_key=os.environ.get("LLM_API_KEY"),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        )
