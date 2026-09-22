"""Подбор вин к распознанному/выбранному блюду — `POST /v1/pairing/dish-photo`,
`POST /v1/pairing/dish` (contracts/post-scan.md v1.1 §4.3, ратифицировано
architect 22.09 — сверено построчно С ЭТИМ КОДОМ). ОДНО задокументированное
расхождение с буквальным текстом §4.3 — пул кандидатов (детали и почему —
ниже, у `_POOL_SIZE`); фикс бага, найденного ПОСЛЕ снимка кода, по которому
писался контракт. Второе расхождение (fuzzy-порог 80 vs 60 §4.2) — в
`app/dish_recognition.py`. Оба — с цифрами в reports/backend-dish-photo.md,
"Расхождения с контрактом".

Два яруса, "сначала каталог... затем движок" (contracts/post-scan.md §4.3):
  1. `basis="catalog"` — вина, у которых `category` буквально есть в
     `source.food_pairings` портала (то же поле, что уровень 1
     `app/food_pairing.py::compute_pairings` читает для ДРУГОГО направления).
  2. `basis="rules"` — движок `pipeline/ref/food_pairing_rules.yaml` в
     направлении блюдо → вино (`app/food_pairing.py::score_wine_for_dish`):
     sensory-вектор вина, если он надёжен (`_is_usable_sensory` — тот же
     гейт "все 7 осей числом", что и у GET /wines/{id}/pairings), иначе
     heuristic по цвету/ключевым словам — обе функции построения вектора
     переиспользованы 1:1, не дублируются здесь.

Диверсификация — не больше `_MAX_PER_WINERY` вин одной винодельни, всего
`_MAX_TOTAL` (числа — буквально из брифа тимлида, НЕ из
`food_pairing_rules.yaml::output_contract.top_n` — тот специфицирован для
ДРУГОГО направления, вино → блюдо, contracts/post-scan.md v1.0 §1).

Пул кандидатов — `Retriever.candidates_for_taste([], limit=_POOL_SIZE)`, НЕ
`search()`. Первая попытка была через `search(category, ...)` — ОТКАЗАЛАСЬ
эмпирически: `MockRetriever.search("BBQ", ...)` отдаёт ПУСТОЙ список
(fuzzy-скор английской аббревиатуры против русских текстов вин ниже
внутреннего порога релевантности `_SEARCH_MIN_SCORE`, app/rag/mock.py) —
движок правил тогда не видит ни одного вина для скоринга, хотя семантически
кандидаты есть (rules-ярус не зависит от текста вовсе, только от sensory/
heuristic вектора). `candidates_for_taste` — диверсифицированная выборка БЕЗ
текстового запроса (contracts/rag-interface.md v0.3: "разнообразие по
цвету/региону/стилю"), не подвержена этой проблеме. `Retriever` не даёт
метода "все вина"/"вина с тегом X" буквально — contracts/rag-interface.md
заморожен (правит только architect), а `packages/rag` вне зоны backend в эту
волну ("там параллельно другой backend пересобирает индекс", бриф тимлида) —
так что каталожный ярус тоже видит diversity-сэмпл размера `_POOL_SIZE`, не
буквально ВСЕ вина с этим тегом. Известное ограничение v1 (может пропустить
тег-совпадение вне сэмпла на большом каталоге) — см. reports/
backend-dish-photo.md, "Предложения к контрактам"."""
from __future__ import annotations

from .config import Settings
from .food_pairing import (
    _is_usable_sensory,
    _load_rules_data,
    _rules_path,
    build_heuristic_wine_vector,
    build_sensory_wine_vector,
    score_wine_for_dish,
)
from .rag.interface import Candidate, Retriever

# _is_usable_sensory/_load_rules_data/_rules_path — приватные по соглашению
# (leading underscore), но этот модуль в той же зоне (backend) и том же
# файле владения (apps/api/app/), что food_pairing.py — переиспользуем как
# есть, вторая реализация загрузки YAML/гейта надёжности sensory была бы
# чистым дублированием уже протестированной логики.

_POOL_SIZE = 30  # limit candidates_for_taste() — кандидатный пул для ОБОИХ ярусов
_MAX_PER_WINERY = 2  # бриф тимлида: "не больше 2 вин одной винодельни"
_MAX_TOTAL = 6  # бриф тимлида: "всего 6"

_CATALOG_REASON_TEMPLATE = "Портал рекомендует это вино к категории «{category}»."
_FALLBACK_RULES_REASON = "Хорошо сочетается по вкусовому профилю блюда."


def _wine_vector(source: dict, derived: dict) -> dict | None:
    """Тот же каскад sensory→heuristic, что уровни 2/3 `compute_pairings()`
    (без уровня 1 — тег уже проверен отдельно каталожным ярусом ДО вызова
    этой функции, см. `select_wines_for_dish`). `None` — ни надёжной
    sensory, ни пригодного `color` (вызывающий код просто пропускает вино,
    не роняя подбор, как `compute_pairings` роняет в basis=unavailable)."""
    sensory = derived.get("sensory") or {}
    if _is_usable_sensory(sensory):
        return build_sensory_wine_vector(source, sensory)
    return build_heuristic_wine_vector(source)


def _winery_key(candidate: Candidate) -> str:
    source = candidate.meta.get("source") or {}
    return source.get("winery") or source.get("winery_name") or candidate.id


def _wine_item(candidate: Candidate, *, reason: str, basis: str) -> dict:
    source = candidate.meta.get("source") or {}
    return {
        "wine_id": candidate.id,
        "name": source.get("name") or candidate.id,
        "winery": source.get("winery_name") or source.get("winery") or None,
        "color": source.get("color") or None,
        "sugar": source.get("sugar_category") or None,
        "image_url": source.get("image_url"),
        "reason": reason,
        "basis": basis,
    }


def select_wines_for_dish(retriever: Retriever, category: str, settings: Settings) -> list[dict]:
    """До `_MAX_TOTAL` вин, не больше `_MAX_PER_WINERY` одной винодельни;
    каталожный ярус первым, движок правил — вторым, добирает остаток (см.
    докстринг модуля). Детерминировано при фиксированном состоянии
    `retriever`/`food_pairing_rules.yaml`: сортировки ниже — единственный
    источник порядка, никакой случайности (`candidates_for_taste` у
    настоящего packages/rag сама детерминирована day-seed'ом — тот же вызов
    в течение одного дня даёт тот же пул, см. её докстринг)."""
    pool = retriever.candidates_for_taste([], limit=_POOL_SIZE)
    wine_candidates = [c for c in pool if c.kind == "wine"]

    rules_data = _load_rules_data(_rules_path(settings))
    dish_vector = (rules_data.get("portal_tag_defaults") or {}).get(category) or {}
    reason_catalog = _CATALOG_REASON_TEMPLATE.format(category=category)

    catalog_tier: list[Candidate] = []
    rules_tier: list[tuple[float, Candidate, str]] = []
    seen_ids: set[str] = set()

    for candidate in wine_candidates:
        if candidate.id in seen_ids:
            continue
        seen_ids.add(candidate.id)

        source = candidate.meta.get("source") or {}
        if category in (source.get("food_pairings") or []):
            catalog_tier.append(candidate)
            continue

        derived = candidate.meta.get("derived") or {}
        wine_vector = _wine_vector(source, derived)
        if wine_vector is None:
            continue
        scored = score_wine_for_dish(dish_vector, wine_vector, rules_data)
        if scored is None:
            continue
        score, explain = scored
        rules_tier.append((score, candidate, explain or _FALLBACK_RULES_REASON))

    catalog_tier.sort(key=lambda c: c.id)  # детерминизм: алфавит wine_id
    rules_tier.sort(key=lambda triple: (-triple[0], triple[1].id))

    selected: list[dict] = []
    winery_counts: dict[str, int] = {}

    def _try_add(candidate: Candidate, reason: str, basis: str) -> None:
        if len(selected) >= _MAX_TOTAL:
            return
        key = _winery_key(candidate)
        if winery_counts.get(key, 0) >= _MAX_PER_WINERY:
            return
        winery_counts[key] = winery_counts.get(key, 0) + 1
        selected.append(_wine_item(candidate, reason=reason, basis=basis))

    for candidate in catalog_tier:
        _try_add(candidate, reason_catalog, "catalog")
    for _score, candidate, explain in rules_tier:
        _try_add(candidate, explain, "rules")

    return selected
