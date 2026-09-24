"""Эвристический разбор атрибутов вина из названия и slug.

В CSV нет отдельных полей «год», «сладость», «игристое», а именно они отличают
near-duplicates (одна этикетка, разный год/сладость). Эти поля понадобятся для
переранжирования кандидатов по тексту этикетки (OCR). Название приоритетнее slug:
slug бывает сгенерирован автоматически и содержит «хвосты» вроде крепости.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from winescan.catalog.strapi_names import transliterate

_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
_VOLUME = re.compile(r"(?<![\d.,])(0[.,]\d{1,3}|1[.,]5|3[.,]0)(?:\s*л)?(?![\d.,])")
_TOKEN = re.compile(r"[a-z0-9]+")

SPARKLING_TOKENS = frozenset(
    {"bryut", "brut", "igristoe", "shampanskoe", "spumante", "petnat",
     "frizzante", "prosecco", "kremant", "cremant", "kava", "cava", "sekt", "shipuchee",
     "millesimato", "blanc", "blan"}
)  # fmt: skip
# «blanc/blan» сами по себе не признак игристого: учитываются только в паре «blan de ...»
_SPARKLING_ALONE = SPARKLING_TOKENS - {"blanc", "blan"}


@dataclass(frozen=True)
class WineAttributes:
    year: int | None
    sweetness: str | None
    sparkling: bool
    volume_l: float | None


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(transliterate(text).lower())


def _sweetness(tokens: list[str]) -> str | None:
    joined = " ".join(tokens)
    if re.search(r"\b(ekstra|extra) (bryut|brut)\b", joined):
        return "extra_brut"
    if re.search(r"\b(bryut|brut) (natyur|nature)\b", joined):
        return "brut_nature"
    rules = (
        ("brut", ("bryut", "brut")),
        ("semi_dry", ("polusuh", "semisec")),
        ("semi_sweet", ("poluslad",)),
        ("sweet", ("sladk", "sweet", "dolce")),
        ("dry", ("suh", "dry", "secco")),
    )
    for label, prefixes in rules:
        if any(token.startswith(prefix) for token in tokens for prefix in prefixes):
            return label
    return None


def _sparkling(tokens: list[str]) -> bool:
    if any(token in _SPARKLING_ALONE for token in tokens):
        return True
    return any(
        tokens[i] in {"blanc", "blan"} and tokens[i + 1] == "de" for i in range(len(tokens) - 1)
    )


def parse_attributes(name: str, slug: str) -> WineAttributes:
    year_match = _YEAR.search(name) or _YEAR.search(slug.replace("-", " "))
    volume_match = _VOLUME.search(name)
    name_tokens = _tokens(name)
    slug_tokens = slug.lower().split("-")
    return WineAttributes(
        year=int(year_match.group(1)) if year_match else None,
        sweetness=_sweetness(name_tokens) or _sweetness(slug_tokens),
        sparkling=_sparkling(name_tokens) or _sparkling(slug_tokens),
        volume_l=float(volume_match.group(1).replace(",", ".")) if volume_match else None,
    )


def attributes_dict(name: str, slug: str) -> dict:
    return asdict(parse_attributes(name, slug))
