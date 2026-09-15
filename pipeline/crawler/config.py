"""Конфигурация краулера vino-svoe.ru."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RAW = ROOT / "raw"
CATALOG = ROOT / "catalog"
REF = ROOT / "ref"
BUILD = ROOT / "build"

BASE = "https://vino-svoe.ru"

PARSER_VERSION = "0.4.0"

ENTITIES = {
    "wines": {
        "list": BASE + "/wines?page={page}",
        "detail": BASE + "/wines/{slug}",
        "link_re": r"/wines/([a-z0-9_\-]+)",
        "pages": 126,
    },
    "wineries": {
        "list": BASE + "/wineries?page={page}",
        "detail": BASE + "/wineries/{slug}",
        "link_re": r"/wineries/([a-z0-9_\-]+)",
        "pages": None,  # определяется на discover
    },
    "articles": {
        "list": BASE + "/articles?page={page}",
        "detail": BASE + "/articles/{slug}",
        "link_re": r"/articles/([a-z0-9_\-]+)",
        "pages": None,
    },
    "events": {
        "list": BASE + "/events?page={page}",
        "detail": BASE + "/events/{slug}",
        "link_re": r"/events/([a-z0-9_\-]+)",
        "pages": None,
    },
}

# Служебные slug'и, которые ловятся регуляркой, но сущностями не являются
SLUG_BLACKLIST = {"map", "page", "filter", "search"}

HEADERS = {
    "User-Agent": (
        "svoe-vino-lct-research/0.3 (hackathon dataset builder; contact: team@example.org)"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Вежливость. Не снижать без согласования с заказчиком.
DELAY_SEC = 0.7
CONCURRENCY = 4
TIMEOUT_SEC = 25
RETRIES = 3
