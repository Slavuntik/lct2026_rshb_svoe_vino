"""«Аналог импортного»: reference_style -> вина с этим стилем в derived.

Чисто метаданный запрос — без эмбеддингов и без похода в Qdrant: у каждого
вина уже лежит предвычисленный список style-совпадений
(filters.reference_style_matches, см. ingest.py), матчер stillness/color/sugar
уже применён апстримом в vines. Ранжируем по сенсорной дистанции до эталона.
Это не топится отсутствием скачанных моделей (см. риск 3 ревью 01).
"""
from __future__ import annotations

from collections import Counter

from rag import refdata
from rag.filtering import passes_filters
from rag.meta import public_meta
from rag.resolve import resolve_style
from rag.types import Candidate, Filters

_SENSORY_DIMS = ("sweetness", "acidity", "tannin", "body", "oak", "aromatic_intensity", "bubbles")


def sensory_distance(a: dict | None, b: dict | None) -> float:
    a = a or {}
    b = b or {}
    diffs = []
    for dim in _SENSORY_DIMS:
        va, vb = a.get(dim), b.get(dim)
        if va is None or vb is None:
            continue
        diffs.append((va - vb) ** 2)
    if not diffs:
        return 1.0  # нечего сравнивать — нейтральная дистанция, не отбрасываем
    return sum(diffs) ** 0.5


class StyleMatcher:
    def __init__(self, styles: list[dict] | None = None):
        self.styles = styles if styles is not None else refdata.load_reference_styles()
        self.by_slug = {s["slug"]: s for s in self.styles}

    def resolve(self, query: str) -> dict | None:
        return resolve_style(query, self.styles)

    def analog_for_style(
        self,
        style_slug: str,
        wine_payloads: list[dict],
        *,
        filters: Filters | None = None,
        top_k: int = 12,
    ) -> list[Candidate]:
        style = self.by_slug.get(style_slug)
        if style is None:
            return []

        allowed_sugar = set(style.get("sugar") or [])
        scored: list[tuple[float, dict]] = []
        for payload in wine_payloads:
            f = payload.get("filters", {}) or {}
            style_matches = f.get("reference_style_matches") or []
            if style_slug not in style_matches:
                continue
            # Защитная перепроверка жёстких фильтров стиля (цвет/игристость/сахар)
            # поверх апстримного матчера — так DoD-тест «только игристые» держится
            # даже если апстримные данные когда-нибудь разъедутся.
            if style.get("color") and f.get("color") != style["color"]:
                continue
            if style.get("stillness") and f.get("stillness") != style["stillness"]:
                continue
            if allowed_sugar and f.get("sugar") not in allowed_sugar:
                continue
            if not passes_filters(payload, filters):
                continue
            dist = sensory_distance(style.get("sensory"), payload.get("sensory"))
            scored.append((dist, payload))

        scored.sort(key=lambda item: item[0])
        results: list[Candidate] = []
        for dist, payload in scored[:top_k]:
            score = round(1.0 / (1.0 + dist), 4)
            results.append(
                Candidate(
                    id=payload["id"],
                    kind="wine",
                    score=score,
                    text=payload.get("text", ""),
                    url=payload.get("url", ""),
                    meta=public_meta(payload),
                )
            )
        return results

    def list_popular(self, wine_payloads: list[dict], top_n: int = 5) -> list[dict]:
        """Топ-N стилей по частоте в filters.reference_style_matches каталога —
        подсказка для 404 /analogs (v0.2.1, предложение агента B)."""
        counts: Counter[str] = Counter()
        for payload in wine_payloads:
            for slug in (payload.get("filters", {}) or {}).get("reference_style_matches") or []:
                counts[slug] += 1

        out = []
        for slug, _n in counts.most_common(top_n):
            style = self.by_slug.get(slug)
            if style is None:
                continue  # защитно: slug в данных, которого нет в текущем справочнике
            out.append({"slug": style["slug"], "name": style["name"], "country": style.get("country")})
        return out
