"""Аналоги вина из других виноделен — без LLM, по полям каталога.

Сценарий из ТЗ: «подбор аналогов из других виноделен». Обязательны другая винодельня, тот же
цвет и та же игристость (тихое / игристое); скор складывается из совпадения сладости, сортов,
региона и близости описаний (TF-IDF). Объяснение строится шаблоном из совпавших полей — детерминированно и в
нейтральном информационном тоне (38-ФЗ: без призывов и оценок «лучше/купите»).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from winescan.search.text_match import STOP_TOKENS, tokens

SWEETNESS_ORDER = ["brut_nature", "extra_brut", "brut", "dry", "semi_dry", "semi_sweet", "sweet"]
SWEETNESS_RU = {
    "brut_nature": "брют натюр", "extra_brut": "экстра брют", "brut": "брют", "dry": "сухое",
    "semi_dry": "полусухое", "semi_sweet": "полусладкое", "sweet": "сладкое",
}  # fmt: skip


@dataclass(frozen=True)
class Analog:
    slug: str
    name: str
    winery: str
    score: float
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"slug": self.slug, "name": self.name, "winery": self.winery, "score": round(self.score, 3),
                "reasons": self.reasons}  # fmt: skip


class AnalogFinder:
    def __init__(self, cards: dict[str, dict]):
        self.cards = cards
        documents = {slug: Counter(t for t in tokens(card.get("description", "")) if t not in STOP_TOKENS)
                     for slug, card in cards.items()}  # fmt: skip
        document_frequency = Counter(t for counts in documents.values() for t in counts)
        total = len(documents)
        self._vectors: dict[str, dict[str, float]] = {}
        for slug, counts in documents.items():
            vector = {t: (1 + math.log(c)) * math.log((1 + total) / (1 + document_frequency[t])) for t, c in counts.items()}
            norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
            self._vectors[slug] = {t: v / norm for t, v in vector.items()}

    def _description_similarity(self, a: str, b: str) -> float:
        va, vb = self._vectors.get(a, {}), self._vectors.get(b, {})
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(v * vb.get(t, 0.0) for t, v in va.items())

    def find(self, slug: str, limit: int = 6) -> list[Analog]:
        base = self.cards[slug]
        base_attrs = base.get("attributes", {})
        base_grapes = {g.lower() for g in base.get("grapes", [])}
        results = []
        for other in self.cards.values():
            if other["slug"] == slug or other["winery"] == base["winery"] or other["category"] != base["category"]:
                continue
            attrs = other.get("attributes", {})
            # тихое и игристое — разные категории для покупателя: штраф −2 в тесте проигрывал
            # совпадению сорта и описания, и игристый брют выходил аналогом тихого сухого
            if bool(base_attrs.get("sparkling")) != bool(attrs.get("sparkling")):
                continue
            score, reasons = 1.0, [f"тот же цвет: {base['category'].lower()}"]

            grapes = {g.lower() for g in other.get("grapes", [])}
            if base_grapes and grapes:
                overlap = len(base_grapes & grapes) / len(base_grapes | grapes)
                if overlap:
                    score += 3.0 * overlap
                    shared = ", ".join(g for g in other["grapes"] if g.lower() in base_grapes)
                    reasons.append(f"сорт: {shared}")

            if base_attrs.get("sweetness") and attrs.get("sweetness"):
                distance = abs(SWEETNESS_ORDER.index(base_attrs["sweetness"]) - SWEETNESS_ORDER.index(attrs["sweetness"]))
                if distance == 0:
                    score += 2.0
                    reasons.append(f"сладость: {SWEETNESS_RU[attrs['sweetness']]}")
                elif distance == 1:
                    score += 0.7
            if attrs.get("sparkling"):
                reasons.append("игристое")

            if other.get("region") and other.get("region") == base.get("region"):
                score += 1.0
                reasons.append(f"регион: {other['region']}")

            similarity = self._description_similarity(slug, other["slug"])
            score += 2.0 * similarity
            if similarity >= 0.25:
                reasons.append("похожее описание вкуса")

            results.append(Analog(other["slug"], other["name"], other["winery"], score, reasons))
        results.sort(key=lambda analog: analog.score, reverse=True)
        return results[:limit]
