"""Фолбэк POST /v1/scan/resolve на пустых matches (agents/B7-foreign-analogs.md):
"«Urban Risling» -> 0 совпадений в каталоге, но узнаваемый сорт/стиль есть —
отдать российские аналоги по стилю". Источники — ТОЛЬКО ЧТЕНИЕ
`pipeline/ref/grape_synonyms.yaml` (мост «сорт <-> мировая классика») и
`reference_styles.yaml` (эталонные импортные стили; slug почти всегда
латиницей, например "chianti-classico" — это и ловит однословные аппелласьоны
вроде "Chianti", у которых самого сорта в тексте запроса нет вовсе).

Словарный проход, НЕ NLP (по заданию брифа): нормализация регистра и ё/е,
точное совпадение по слову/фразе, и лёгкая опечаточная устойчивость (edit
distance <= 1, только для слов длиной >= 5 — короткие слова фаззи не матчим,
слишком шумно) поверх rapidfuzz, который уже используется в app/rag/mock.py.
Найденный токен уходит В ТОЧНОСТИ В resolve_style() — тот же резолвер стиля,
что и у /v1/analogs (routers/analogs.py) — сама фаззи-логика сопоставления с
эталонными стилями здесь НЕ дублируется, см. app/analog_lookup.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from rapidfuzz.distance import Levenshtein

from .analog_lookup import wines_for_style
from .config import Settings
from .rag.interface import Retriever
from .schemas import AnalogsWineItem

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+")
_MIN_STYLE_WORD_LEN = 4  # короткие слова слага стиля не индексируем — слишком шумно
_MIN_FUZZY_TOKEN_LEN = 5  # опечатки ловим только на достаточно длинных словах

# Слова слага, которые сами по себе НЕ идентифицируют конкретный стиль —
# общая лексика (в т.ч. просто "вино" по-английски/итальянски), стороны
# света и части составных геослагов ("bordeaux-left-bank"). Найдено разбором
# полного индекса вручную (см. reports/b7-foreign-analogs.md) — без стоп-листа
# "wine"/"port"/"left"/"light" ловили бы ПОЧТИ ЛЮБОЙ англоязычный текст про
# вино на случайный конкретный стиль. Сознательный компромисс словарного
# прохода (бриф: "не изобретай NLP") — список не претендует на полноту,
# только снимает самые вопиющие ложные срабатывания, найденные вручную.
_STYLE_WORD_STOPLIST: frozenset[str] = frozenset({
    "wine", "vino", "blend", "sparkling", "dessert", "port", "skin", "contact",
    "light", "white", "bank", "left", "right", "south", "north", "world", "new",
})

# phrase/word (нормализовано) -> (label для analog_reason, query в resolve_style)
_Index = dict[str, tuple[str, str]]


def _normalize(word: str) -> str:
    return word.lower().replace("ё", "е")


def _tokenize(text: str) -> list[str]:
    return [_normalize(w) for w in _WORD_RE.findall(text)]


def _index_candidate(unigrams: _Index, phrases: _Index, candidate: str, value: tuple[str, str]) -> None:
    words = [_normalize(w) for w in _WORD_RE.findall(candidate)]
    if not words:
        return
    if len(words) == 1:
        unigrams.setdefault(words[0], value)
    else:
        phrases.setdefault(" ".join(words), value)


def _build_grape_index(path: Path) -> tuple[_Index, _Index]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unigrams: _Index = {}
    phrases: _Index = {}
    for key, section in data.items():
        if key == "non_grapes" or not isinstance(section, list):
            continue  # non_grapes — служебные строки портала, не сорта
        for grape in section:
            if not isinstance(grape, dict) or "name" not in grape:
                continue
            name = grape["name"]
            value = (name, name)  # label == resolve_query: имя сорта само по себе — валидный запрос стиля
            for candidate in {grape.get("slug", ""), name, *grape.get("synonyms", [])}:
                if candidate:
                    _index_candidate(unigrams, phrases, candidate, value)
    return unigrams, phrases


def _build_style_index(path: Path) -> tuple[_Index, _Index]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unigrams: _Index = {}
    phrases: _Index = {}
    for style in data.get("styles", []):
        if not isinstance(style, dict) or "slug" not in style or "name" not in style:
            continue
        grapes = style.get("grapes") or []
        # Аппелласьон/стиль без указанного сорта (напр. "Вальполичелла Рипассо") —
        # называть в analog_reason нечего, кроме самого стиля; resolve_style в
        # обоих случаях зовём по названию стиля (см. докстринг модуля).
        label = grapes[0] if grapes else style["name"]
        value = (label, style["name"])

        # Полная ФРАЗА и по slug, и по name — точное совпадение всей
        # последовательности слов подряд не шумит (например "гевюрцтраминер
        # эльзас" не появится в тексте случайно).
        for candidate in (style["slug"], style["name"]):
            words = [_normalize(w) for w in _WORD_RE.findall(candidate)]
            if len(words) > 1:
                phrases.setdefault(" ".join(words), value)

        # ОДНОСЛОВНЫЙ индекс — ТОЛЬКО по slug (латиница), не по name: name —
        # свободная кириллическая проза ("Вино Нобиле ди Монтепульчано",
        # "Белый бленд Роны"), и отдельные слова оттуда ("вино", "белый") —
        # обычные словарные слова языка, а не идентификаторы стиля: ловили
        # мусорный текст на слове "вино" (regression: тот же ввод, что и
        # test_scan_resolve_low_confidence_on_garbage_text). slug почти
        # всегда несёт то же самое однословным аппелласьоном на латинице
        # ("chianti-classico", "barolo", "sancerre") — этого достаточно для
        # словарного прохода без NLP.
        for word in [_normalize(w) for w in _WORD_RE.findall(style["slug"])]:
            if len(word) >= _MIN_STYLE_WORD_LEN and word not in _STYLE_WORD_STOPLIST:
                unigrams.setdefault(word, value)
    return unigrams, phrases


def _search(tokens: list[str], unigrams: _Index, phrases: _Index) -> tuple[str, str] | None:
    for i in range(len(tokens) - 1):
        phrase = f"{tokens[i]} {tokens[i + 1]}"
        if phrase in phrases:
            return phrases[phrase]
    for tok in tokens:
        if tok in unigrams:
            return unigrams[tok]
    # Лёгкая опечаточная устойчивость (бриф: "risling/riesling") — только
    # после того, как точное совпадение не нашлось нигде.
    for tok in tokens:
        if len(tok) < _MIN_FUZZY_TOKEN_LEN:
            continue
        for key, value in unigrams.items():
            if abs(len(key) - len(tok)) <= 1 and Levenshtein.distance(tok, key) <= 1:
                return value
    return None


@dataclass(frozen=True)
class ForeignLabelMatch:
    label: str  # для текста analog_reason, напр. "Рислинг" / "Санджовезе"
    resolve_query: str  # что передать в retriever.resolve_style(...)


class ForeignRefLookup:
    def __init__(self, ref_dir: Path) -> None:
        self._grape_unigrams, self._grape_phrases = _build_grape_index(ref_dir / "grape_synonyms.yaml")
        self._style_unigrams, self._style_phrases = _build_style_index(ref_dir / "reference_styles.yaml")

    def find(self, text: str) -> ForeignLabelMatch | None:
        tokens = _tokenize(text)
        if not tokens:
            return None
        found = _search(tokens, self._grape_unigrams, self._grape_phrases)
        if found is None:
            found = _search(tokens, self._style_unigrams, self._style_phrases)
        if found is None:
            return None
        label, resolve_query = found
        return ForeignLabelMatch(label=label, resolve_query=resolve_query)


@lru_cache(maxsize=8)
def _cached_lookup(ref_dir: str) -> ForeignRefLookup | None:
    # Как read_eval_report (app/cv/eval_report.py): отсутствующий/битый файл
    # — не повод ронять /scan/resolve, фолбэк просто молча выключается.
    # lru_cache на самом ref_dir — справочники читаются с диска ровно один
    # раз за жизнь процесса (146+118 записей, незачем парсить на каждый запрос).
    try:
        return ForeignRefLookup(Path(ref_dir))
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return None


def resolve_foreign_analogs(
    retriever: Retriever, text: str, settings: Settings,
) -> tuple[list[AnalogsWineItem], str | None]:
    """Вызывается ТОЛЬКО когда matches у /scan/resolve пуст (routers/scan.py).
    None-путь везде (справочник не нашёлся, токен не нашёлся, resolve_style
    не распознал) — просто пустой результат, старое поведение без изменений."""
    ref_dir = str(Path(settings.scan_foreign_ref_dir).resolve())
    lookup = _cached_lookup(ref_dir)
    match = lookup.find(text) if lookup is not None else None
    if match is None:
        return [], None
    style = retriever.resolve_style(match.resolve_query)
    if style is None:
        return [], None
    wines = wines_for_style(retriever, style["slug"], top_k=12)
    reason = f"«{text}» вне каталога российских вин — аналоги по стилю: {match.label.lower()}"
    return wines, reason
