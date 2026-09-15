"""Слой 3: сходство текста этикетки (OCR) с карточкой вина.

Нужен для near-duplicates: вина одной серии отличаются на изображении только словами
(сорт, год, сладость). Этикетки бывают на кириллице и латинице (ARISTOV / Аристов,
Chardonnay / Шардоне), поэтому токены сравниваются по «скелету» — транслит, согласные,
упрощённые сочетания. Движок OCR этот модуль не знает: на вход — просто строка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from winescan.catalog.attributes import parse_attributes
from winescan.catalog.strapi_names import transliterate

_TOKEN = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")

# слова, которые есть почти на каждой этикетке и не помогают отличить вино
STOP_TOKENS = frozenset(
    {"vino", "wine", "vin", "beloe", "belyj", "belyy", "krasnoe", "rozovoe", "roze", "rose", "oranzhevoe",
     "suhoe", "polusuhoe", "polusladkoe", "sladkoe", "bryut", "brut", "ekstra", "extra", "igristoe",
     "dry", "sweet", "red", "white", "rosso", "bianco", "the", "and", "de", "di", "la", "le", "i", "v",
     "rossii", "rossiya", "russia", "zashhishhennogo", "geograficheskogo", "ukazaniya", "naimenovaniya",
     "proishozhdeniya", "vinodelnya", "winery", "estate", "god", "urozhaya", "l", "ml", "ob", "alk"}
)  # fmt: skip


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(transliterate(text).lower().replace("ё", "e")) if len(t) >= 2]


def skeleton(token: str) -> str:
    """Согласный «скелет»: chardonnay -> shrdn, shardone -> shrdn."""
    token = token.replace("ch", "sh").replace("sch", "sh").replace("ph", "f").replace("th", "t")
    token = token.translate(str.maketrans({"c": "k", "q": "k", "w": "v", "x": "ks", "y": "i", "j": "i", "z": "s"}))
    token = re.sub(r"[aeiou]", "", token)
    return re.sub(r"(.)\1+", r"\1", token)


def token_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a.isdigit() or b.isdigit():
        return 0.0
    direct = SequenceMatcher(None, a, b).ratio()
    sa, sb = skeleton(a), skeleton(b)
    if min(len(sa), len(sb)) >= 3:
        shape = SequenceMatcher(None, sa, sb).ratio()
    else:
        # короткие скелеты сравниваем только на точное равенство: noir/nuar -> «nr»,
        # но rozovyj («rsv») не должен совпасть с rossii («rs»)
        shape = 0.85 if sa == sb and len(sa) >= 2 else 0.0
    return max(direct, shape)


@dataclass(frozen=True)
class LabelText:
    tokens: tuple[str, ...]
    year: int | None
    sweetness: str | None

    @classmethod
    def from_ocr(cls, text: str) -> LabelText:
        found = [t for t in tokens(text) if t not in STOP_TOKENS]
        years = _YEAR.findall(text)
        return cls(tuple(found), int(years[0]) if years else None, parse_attributes(text, "").sweetness)


def _coverage(field_tokens: list[str], label: LabelText, threshold: float = 0.8) -> float:
    """Доля значимых слов поля, найденных на этикетке (с нечётким совпадением)."""
    significant = [t for t in dict.fromkeys(field_tokens) if t not in STOP_TOKENS and not t.isdigit()]
    if not significant or not label.tokens:
        return 0.0
    found = sum(max(token_similarity(t, o) for o in label.tokens) >= threshold for t in significant)
    return found / len(significant)


def text_score(card: dict, label: LabelText) -> float:
    """Сходство этикетки с карточкой, примерно в [-0,5; 1,8]; 0 — текст ничего не говорит."""
    if not label.tokens:
        return 0.0
    score = 1.0 * _coverage(tokens(card["name"]), label)
    score += 0.5 * _coverage(tokens(card["winery"]), label)
    score += 0.3 * _coverage(tokens(" ".join(card.get("grapes", []))), label)
    attributes = card.get("attributes", {})
    if label.year and attributes.get("year"):
        score += 0.3 if label.year == attributes["year"] else -0.3
    if label.sweetness and attributes.get("sweetness"):
        score += 0.2 if label.sweetness == attributes["sweetness"] else -0.2
    return score
