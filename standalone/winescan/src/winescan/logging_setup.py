"""Единая настройка логов для CLI: наши сообщения — INFO, болтливые библиотеки — WARNING."""

from __future__ import annotations

import logging

NOISY_LOGGERS = ("httpx", "httpcore", "huggingface_hub", "urllib3", "PIL")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
