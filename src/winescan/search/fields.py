"""Слой 3: поля этикетки, прочитанные мультимодальной моделью, и их сверка с карточкой вина.

Модель возвращает JSON с полями; ни одному полю не верим «на слово»: каждое сверяется с
карточками кандидатов из визуального top-K, противоречие (другой год, цвет, сладость) снижает
скор. Поиск по всему каталогу по этим полям не делается — только перестановка кандидатов.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from winescan.search.text_match import LabelText, _coverage, tokens

COLOR_TO_CATEGORY = {"белое": "Белое", "красное": "Красное", "розовое": "Розовое", "оранжевое": "Оранжевое"}
SWEETNESS_TO_CODE = {
    "брют натюр": "brut_nature", "экстра брют": "extra_brut", "брют": "brut", "сухое": "dry",
    "полусухое": "semi_dry", "полусладкое": "semi_sweet", "сладкое": "sweet",
}  # fmt: skip


@dataclass(frozen=True)
class LabelFields:
    winery: str = ""
    name: str = ""
    grapes: tuple[str, ...] = ()
    year: int | None = None
    color: str | None = None  # категория каталога: Белое / Красное / Розовое / Оранжевое
    sweetness: str | None = None  # код: dry, semi_dry, …
    sparkling: bool | None = None
    text: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.winery or self.name or self.grapes or self.year or self.color or self.sweetness or self.text)

    def all_text(self) -> str:
        return " ".join([self.winery, self.name, " ".join(self.grapes), self.text])


def parse_fields(raw: str) -> LabelFields:
    """Разбор ответа модели: берём первый JSON-объект, неизвестные значения игнорируем."""
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return LabelFields(text=raw.strip()[:300])
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return LabelFields(text=raw.strip()[:300])

    def text_value(key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) and value.strip().lower() not in ("null", "none", "") else ""

    year = data.get("year")
    if isinstance(year, str) and year.isdigit():
        year = int(year)
    grapes = data.get("grapes") or []
    if isinstance(grapes, str):
        grapes = [g.strip() for g in grapes.split(",")]
    sparkling = data.get("sparkling")
    return LabelFields(
        winery=text_value("winery"),
        name=text_value("name"),
        grapes=tuple(g for g in grapes if isinstance(g, str) and g.strip()),
        year=year if isinstance(year, int) and 1950 <= year <= 2049 else None,
        color=COLOR_TO_CATEGORY.get(text_value("color").lower()),
        sweetness=SWEETNESS_TO_CODE.get(text_value("sweetness").lower()),
        sparkling=sparkling if isinstance(sparkling, bool) else None,
        text=text_value("text"),
    )


def field_score(card: dict, fields: LabelFields) -> float:
    """Согласие полей этикетки с карточкой: примерно от −1,5 до +3; 0 — модель ничего не прочитала."""
    if fields.is_empty:
        return 0.0
    label = LabelText.from_ocr(fields.all_text())
    score = 1.0 * _coverage(tokens(card["name"]), label)
    score += 0.7 * _coverage(tokens(card["winery"]), label)
    score += 0.5 * _coverage(tokens(" ".join(card.get("grapes", []))), label)
    attributes = card.get("attributes", {})
    if fields.year and attributes.get("year"):
        score += 0.5 if fields.year == attributes["year"] else -0.5
    if fields.color and card.get("category"):
        score += 0.3 if fields.color == card["category"] else -0.6
    if fields.sweetness and attributes.get("sweetness"):
        score += 0.3 if fields.sweetness == attributes["sweetness"] else -0.4
    if fields.sparkling is not None and attributes.get("sparkling") is not None:
        score += 0.0 if fields.sparkling == bool(attributes["sparkling"]) else -0.3
    return score
