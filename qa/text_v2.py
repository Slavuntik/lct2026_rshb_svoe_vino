"""Прототип текстового скоринга v2 для реальных фото (офлайн, qa/ — не прод-код).

Отличия от cv.text_rerank (v1), найденные на размеченных реальных фото:
  1. Гомоглифы: OCR (eslav-модель) пишет кириллицу латинскими двойниками — CAMAPA=САМАРА,
     KPACHAA=КРАСНАЯ, ДЕHИCOB=ДЕНИСОВ (смешанный), PO3E=РОЗЕ. Токен получает вариант с
     заменой двойников на кириллицу, дальше общая транслитерация в латиницу. agents/
     H1-cpu-path.md: то же самое и для ГРЕЧЕСКИХ заглавных двойников пяти кириллических
     букв без латинского аналога — Λ→Л, Γ→Г, Π→П, Δ→Д, Φ→Ф (OΛEΓ→ОЛЕГ).
  2. Потокенное нечёткое совпадение (обрезки CKАЛИСТ→скалистый, ОРМУЛА→формула) вместо
     одного token_set_ratio на всю строку.
  3. Без «безопасного гейта» по медиане IDF: на реальных фото он отсекал названия виноделен.

СИНХРОНИЗИРУЙ homoglyph_variant()/_UP/_LOW/_DIG/_GREEK/_CYR/_LAT/_GRK с
packages/cv/cv/text_fusion.py (боевой перенос этого прототипа) — agents/H1-cpu-path.md
задача 2: правка гомоглифов вносится в ОБА файла одинаково.
"""
from __future__ import annotations

import math
import re

from rapidfuzz import fuzz

from cv import text_rerank as tr

_UP = dict(zip("ABCEHKMOPTXY", "АВСЕНКМОРТХУ"))
_LOW = dict(zip("aceopxyu", "асеорхуи"))
_DIG = {"3": "З", "0": "О", "6": "б"}
# Греческие заглавные двойники пяти кириллических букв без латинского аналога (см.
# докстринг модуля, п.1) — синхронно с packages/cv/cv/text_fusion.py::_GREEK.
_GREEK = dict(zip("ΛΓΠΔΦ", "ЛГПДФ"))
_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)
_GRK = re.compile("[" + "".join(_GREEK) + "]")
_YEAR = re.compile(r"^(19[5-9]\d|20[0-3]\d)$")


def homoglyph_variant(tok: str) -> str | None:
    """Кириллический вариант токена или None, если замена неприменима."""
    has_cyr, has_lat, has_grk = bool(_CYR.search(tok)), bool(_LAT.search(tok)), bool(_GRK.search(tok))
    if has_lat and has_cyr:  # смешанный — латиница внутри кириллического слова
        return "".join(_UP.get(ch, _LOW.get(ch, ch)) for ch in tok)
    if (has_lat or has_grk) and not has_cyr:
        letters = [ch for ch in tok if ch.isalpha()]
        if letters and all(ch in _UP or ch in _GREEK for ch in letters):  # только «двойниковые» заглавные
            return "".join(_UP.get(ch, _GREEK.get(ch, _DIG.get(ch, ch))) for ch in tok)
        if letters and all(ch in _UP or ch in _LOW for ch in letters) and any(ch in _DIG for ch in tok):
            return "".join(_UP.get(ch, _LOW.get(ch, _DIG.get(ch, ch))) for ch in tok)
    if not has_lat and has_cyr and any(ch in _DIG for ch in tok):  # PO3E-подобные с цифрой внутри
        return "".join(_DIG.get(ch, ch) for ch in tok)
    return None


_EN_SUGAR = [
    (re.compile(r"\b(semi[\s-]?dry|demi[\s-]?sec|halbtrocken)\b", re.I), " polusuhoe "),
    (re.compile(r"\b(semi[\s-]?sweet|moelleux|halbs[uü]ss|lieblich)\b", re.I), " polusladkoe "),
    (re.compile(r"\bextra[\s-]?brut\b", re.I), " ekstra bryut "),
    (re.compile(r"\b(dry|sec|trocken|secco)\b", re.I), " suhoe "),
    (re.compile(r"\b(sweet|dolce|doux)\b", re.I), " sladkoe "),
    (re.compile(r"\bbrut\b", re.I), " bryut "),
]


def query_tokens(ocr_text: str) -> set[str]:
    out: set[str] = set()
    text = ocr_text or ""
    for rx, rep in _EN_SUGAR:
        text = rx.sub(rep, text)  # замена: «semi-dry» не должен дать ещё и «dry»→suhoe
    for raw in re.split(r"[\s·,;:/|()\"«»]+", text):
        raw = raw.strip(".-'’`")
        if not raw:
            continue
        variants = [raw]
        hv = homoglyph_variant(raw)
        if hv:
            variants.append(hv)
        for v in variants:
            for t in tr.tokenize(v):
                if t.isdigit() and not _YEAR.match(t):
                    continue
                if len(t) >= 3 or _YEAR.match(t):
                    out.add(t)
    return out


def token_sim(q: str, c: str) -> float:
    if q == c:
        return 1.0
    if q.isdigit() or c.isdigit() or min(len(q), len(c)) < 4:
        return 0.0
    r = fuzz.ratio(q, c) / 100.0
    if r >= 0.8:
        return r
    if len(q) >= 5 and len(c) >= 5 and (c.startswith(q) or q.startswith(c)):
        return 0.85
    return 0.0


_SLUG_SUGAR = [("ekstra-bryut", "ekstra bryut"), ("polusuhoe", "polusuhoe"), ("polusladkoe", "polusladkoe"),
               ("bryut", "bryut"), ("desertn", "desertnoe")]
_PHOTO_SUGAR = [
    (re.compile(r"(экстра\s?брют|extra\s?brut)", re.I), "ekstra bryut"),
    (re.compile(r"(п\.\s?сух|п\s сух|полусух|semi[\s-]?dry|semidry)", re.I), "polusuhoe"),
    (re.compile(r"(п\.\s?сл|полусл|semi[\s-]?sweet)", re.I), "polusladkoe"),
    (re.compile(r"(брют|brut)", re.I), "bryut"),
    (re.compile(r"(сух|\bdry\b)", re.I), "suhoe"),
    (re.compile(r"(сладк|\bсл\.)", re.I), "sladkoe"),
]


def sugar_of(slug: str, photo_names: list[str], name: str) -> str:
    """Канонический маркер сахара позиции: из слага, иначе из имени фото/названия."""
    for key, val in _SLUG_SUGAR:
        if key in slug:
            return val
    if re.search(r"(^|-)suhoe", slug):
        return "suhoe"
    if re.search(r"(^|-)sladkoe", slug):
        return "sladkoe"
    for src in photo_names + [name]:
        for rx, val in _PHOTO_SUGAR:
            if rx.search(src or ""):
                return val
    return ""


class TextIndexV2:
    def __init__(self, catalog: dict[str, "tr.CatalogText"], fields=("name", "winery", "grape"),
                 extra: dict[str, dict[str, str]] | None = None):
        """extra: slug -> {поле: текст} для полей вне CatalogText (category, sugar)."""
        self.slugs = list(catalog)
        self.doc_tokens: list[set[str]] = []
        df: dict[str, int] = {}
        for s in self.slugs:
            e = catalog[s]
            toks = set()
            for f in fields:
                val = getattr(e, f, None) if hasattr(e, f) else (extra or {}).get(s, {}).get(f, "")
                toks |= {t for t in tr.tokenize(val or "") if len(t) >= 3 or _YEAR.match(t)}
            self.doc_tokens.append(toks)
            for t in toks:
                df[t] = df.get(t, 0) + 1
        n = len(self.slugs)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self.vocab = list(self.idf)

    def scores(self, ocr_text: str) -> tuple[list[float], list[float]]:
        """(recall по кандидату, абсолютная IDF-масса совпадений) для каждого слага."""
        q = query_tokens(ocr_text)
        if not q:
            return [0.0] * len(self.slugs), [0.0] * len(self.slugs)
        # лучший матч каждого слова словаря с запросом — один раз на словарь
        best: dict[str, float] = {}
        for c in self.vocab:
            m = max(token_sim(t, c) for t in q)
            if m > 0:
                best[c] = m
        rec, mass = [], []
        for toks in self.doc_tokens:
            tot = sum(self.idf[t] for t in toks) or 1.0
            got = sum(self.idf[t] * best[t] for t in toks if t in best)
            rec.append(got / tot)
            mass.append(got)
        return rec, mass
