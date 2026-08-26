"""MockRetriever — реализация Retriever (contracts/rag-interface.md) поверх
фикстур из 6 вымышленных вин. Никакой векторки: rapidfuzz + жёсткие фильтры,
детерминированно, без сети и без тяжёлых моделей — специально для разработки
и тестов API/чата без агента A (agents/B-api.md: "не жди его").
"""
from __future__ import annotations

from rapidfuzz import fuzz, process

from .fixtures import (
    INDEX_VERSION,
    KNOWLEDGE_CHUNKS,
    REFERENCE_STYLES,
    REFERENCE_STYLES_BY_SLUG,
    WINES,
    WINES_BY_SLUG,
)
from .interface import Candidate, Filters

_STYLE_MATCH_THRESHOLD = 60.0
_LABEL_MATCH_MIN_SCORE = 0.15  # ниже этого даже не возвращаем — совсем мусор

# search() без порога возвращал бы top_k «наименее плохих» кандидатов даже на
# полную бессмыслицу (token_set_ratio редко даёт ровный 0). Порог подобран
# эмпирически: реальные русскоязычные вопросы о вине ложатся в диапазон
# ~0.15-0.27 даже на не самый релевантный чанк (короткие тексты, общие
# предлоги — сама rapidfuzz это охотно "видит"), а бессвязная латиница/цифры
# — ниже 0.05. Порог 0.08 разделяет эти случаи и делает "пустая выдача
# ретривера => refusal" (contracts/openapi.yaml v0.2) достижимой не только
# юнит-тестом на стабе, но и через реальный HTTP-путь /chat.
_SEARCH_MIN_SCORE = 0.08


def _wine_text_for_prompt(wine: dict) -> str:
    pairings = ", ".join(wine.get("food_pairings", []))
    return (
        f"{wine['name']} ({wine['winery_name']}, {wine['region_name']}). "
        f"{wine['description']} Сочетается с: {pairings}."
    )


def _wine_matches_filters(wine: dict, filters: Filters | None) -> bool:
    if filters is None:
        return True
    if filters.color and wine["color"] != filters.color:
        return False
    if filters.sugar and wine["sugar_category"] != filters.sugar:
        return False
    if filters.region and wine["region"] != filters.region:
        return False
    if filters.stillness and wine["derived"]["stillness"] != filters.stillness:
        return False
    if filters.grapes:
        wine_grape_slugs = set(wine["derived"].get("grape_slugs", []))
        if not wine_grape_slugs.intersection(filters.grapes):
            return False
    return True


def _wine_to_candidate(wine: dict, score: float) -> Candidate:
    meta = {
        "name": wine["name"],
        "winery_name": wine["winery_name"],
        "region": wine["region"],
        "region_name": wine["region_name"],
        "color": wine["color"],
        "sugar_category": wine["sugar_category"],
        "grapes": wine["grapes"],
        "stillness": wine["derived"]["stillness"],
        "reference_style_matches": wine["derived"]["reference_style_matches"],
        "image_url": wine.get("image_url"),
    }
    return Candidate(
        id=wine["slug"], kind="wine", score=score,
        text=_wine_text_for_prompt(wine), url=wine["source_url"], meta=meta,
    )


def _chunk_to_candidate(chunk: dict, score: float) -> Candidate:
    return Candidate(
        id=chunk["id"], kind="chunk", score=score, text=chunk["text"],
        url=chunk["url"], meta={},
    )


def _wine_to_full_candidate(wine: dict) -> Candidate:
    """Для get_by_id(): meta несёт source+derived целиком (как GET /wines/{id}
    хочет их отдать), в отличие от _wine_to_candidate() выше, где meta —
    лёгкая выжимка для цитат чата/списков совпадений."""
    source = {k: v for k, v in wine.items() if k not in ("slug", "derived", "source_url")}
    return Candidate(
        id=wine["slug"], kind="wine", score=1.0, text=_wine_text_for_prompt(wine),
        url=wine["source_url"], meta={"source": source, "derived": wine["derived"]},
    )


class MockRetriever:
    index_version = INDEX_VERSION

    # --- contracts/rag-interface.md ------------------------------------
    def search(
        self,
        query: str,
        *,
        filters: Filters | None = None,
        collections: tuple[str, ...] = ("wines", "knowledge"),
        top_k: int = 8,
    ) -> list[Candidate]:
        scored: list[Candidate] = []

        if "wines" in collections:
            for wine in WINES:
                if not _wine_matches_filters(wine, filters):
                    continue
                target = f"{wine['name']} {wine['description']} {' '.join(wine['food_pairings'])}"
                score = fuzz.token_set_ratio(query, target) / 100.0
                scored.append(_wine_to_candidate(wine, score))

        if "knowledge" in collections:
            for chunk in KNOWLEDGE_CHUNKS:
                score = fuzz.token_set_ratio(query, chunk["text"]) / 100.0
                scored.append(_chunk_to_candidate(chunk, score))

        scored = [c for c in scored if c.score >= _SEARCH_MIN_SCORE]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]

    def resolve_label(self, text: str, hints: dict | None = None) -> list[Candidate]:
        hints = hints or {}
        results: list[Candidate] = []
        for wine in WINES:
            target = f"{wine['name']} {wine['winery_name']}"
            score = fuzz.token_set_ratio(text, target) / 100.0
            if hints.get("color") and hints["color"] == wine["color"]:
                score = min(1.0, score + 0.05)
            if hints.get("winery") and fuzz.ratio(hints["winery"], wine["winery_name"]) > 80:
                score = min(1.0, score + 0.05)
            if score >= _LABEL_MATCH_MIN_SCORE:
                results.append(_wine_to_candidate(wine, score))
        results.sort(key=lambda c: c.score, reverse=True)
        return results

    def similar(self, wine_id: str, top_k: int = 6) -> list[Candidate]:
        wine = WINES_BY_SLUG.get(wine_id)
        if wine is None:
            return []
        ordered_slugs = list(wine.get("similar_wine_slugs", []))
        for other in WINES:
            if other["slug"] == wine_id or other["slug"] in ordered_slugs:
                continue
            if other["color"] == wine["color"]:
                ordered_slugs.append(other["slug"])
        result = []
        for slug in ordered_slugs[:top_k]:
            other = WINES_BY_SLUG.get(slug)
            if other:
                result.append(_wine_to_candidate(other, 1.0))
        return result

    def analog_for_style(
        self, style_slug: str, *, filters: Filters | None = None, top_k: int = 12
    ) -> list[Candidate]:
        matches = [
            wine for wine in WINES
            if style_slug in wine["derived"]["reference_style_matches"]
            and _wine_matches_filters(wine, filters)
        ]
        return [_wine_to_candidate(w, 1.0) for w in matches[:top_k]]

    def resolve_style(self, query: str) -> dict | None:
        choices: dict[str, str] = {}
        for style in REFERENCE_STYLES:
            haystack = " / ".join([style["slug"], style["name"], *style["synonyms"]])
            choices[style["slug"]] = haystack
        best = process.extractOne(query, choices, scorer=fuzz.token_set_ratio)
        if best is None:
            return None
        _matched_text, score, slug = best
        if score < _STYLE_MATCH_THRESHOLD:
            return None
        style = REFERENCE_STYLES_BY_SLUG[slug]
        return {"slug": style["slug"], "name": style["name"], "country": style["country"]}

    # --- v0.2.1 (по предложению агента B, теперь часть контракта) ------
    def get_by_id(self, id: str) -> Candidate | None:
        wine = WINES_BY_SLUG.get(id)
        if wine is not None:
            return _wine_to_full_candidate(wine)
        for chunk in KNOWLEDGE_CHUNKS:
            if chunk["id"] == id:
                return _chunk_to_candidate(chunk, 1.0)
        return None  # winery:<slug> — фикстур на отдельные винодельни нет

    def list_reference_styles(self, top_n: int = 5) -> list[dict]:
        styles = [
            {"slug": s["slug"], "name": s["name"], "country": s["country"]}
            for s in REFERENCE_STYLES
        ]
        return styles[:top_n]

    # --- v0.2.2 (пробел нашёл агент C, GET /taste/candidates) -----------
    # НЕ часть contracts/rag-interface.md — там правки не было, только
    # openapi.yaml. Как get_by_id/list_reference_styles до v0.2.1, это
    # MockRetriever-расширение; предложение к контракту в reports/b-report.md
    # (кандидат на следующую версию, если понадобится настоящему packages/rag).
    def candidates_for_taste(self, *, exclude_ids: set[str] | None = None, limit: int = 20) -> list[Candidate]:
        """Колода для свайп-дегустации: вымышленные вина за вычетом уже
        просмотренных (любой verdict — см. routers/taste.py), с простым
        детерминированным round-robin по цвету для разнообразия ("вина для
        экрана «паспорт вкуса»: разнообразие по цвету/региону/стилю" —
        contracts/openapi.yaml v0.2.2). На 6 фикстурах разнообразие почти
        тривиально, но алгоритм честно масштабируется на больший каталог.
        """
        exclude_ids = exclude_ids or set()
        by_color: dict[str, list[dict]] = {}
        for wine in WINES:
            if wine["slug"] in exclude_ids:
                continue
            by_color.setdefault(wine["color"], []).append(wine)

        ordered: list[dict] = []
        while any(by_color.values()):
            for bucket in by_color.values():
                if bucket:
                    ordered.append(bucket.pop(0))

        return [_wine_to_candidate(w, 1.0) for w in ordered[:limit]]
