"""Гастропары «к чему подать это вино» — `GET /v1/wines/{wine_id}/pairings`
(contracts/post-scan.md v1.0, contracts/openapi.yaml 0.3.3, 22.09 — "Задача
backend" из reports/architect-post-scan.md).

Резолюция `wine_id` НЕ дублируется здесь — вызывающая сторона
(`app/routers/wines.py`) уже получила карточку через `app/rag/cards.py::
build_wine_card`. Этот модуль берёт готовую карточку (`source`/`derived`) и
считает три уровня данных по убыванию точности (contracts/post-scan.md §1,
"первый пригодный — используется"):

  1. `basis="catalog"`   — `source.food_pairings` как есть (портал),
                           `score=None`, `triggered_rules=[]`.
  2. `basis="sensory"`   — `derived.sensory` (наш каталог) прогоняется через
                           движок `pipeline/ref/food_pairing_rules.yaml`.
  3. `basis="heuristic"` — ни того, ни другого (обычный случай для каталога
                           кейса при сканировании — у него нет ни
                           food_pairings, ни сенсорики structurally):
                           вектор строится из `source.color` + ключевых слов
                           сахара/игристости в `name`+`description` (таблица
                           дефолтов — contracts/post-scan.md §1, посчитана
                           архитектором по `pipeline/ref/reference_styles.yaml`).
  4. `basis="unavailable"` — даже `color` пуст (или не входит в 4 известных
                           значения) → честное "нет данных", не выдумываем.

Никакого LLM: `explain` всегда дословно из `rules[].explain` в
food_pairing_rules.yaml — `never_include` (contract запрещает заявления о
пользе алкоголя) соблюдается автоматически, т.к. текст не генерируется.

Мини-DSL правил (`when`/`require`/`penalize`/`hard_blocks[].condition`) читает
один общий `eval_condition(dict, dish, wine)` — `hard_blocks[].condition`
единственный намеренно смешивает namespace `dish.*`/`wine.*` в одном словаре
(contracts/post-scan.md §1, "Правила скоринга"); остальные секции держат один
namespace, но парсер общий — сам ключ несёт свой namespace ("dish.fat" /
"wine.acidity"), поэтому два разных обработчика не нужны.

Место хранения `pipeline/ref/food_pairing_rules.yaml` не назначено ничьей
зоной в `TEAM.md` явно (архитектор зафиксировал пробел, `contracts/
post-scan.md`, "Ограничения v1") — решение backend: читаем файл как есть
(`pipeline/ref/` — только чтение, `Settings.pairing_rules_path`), не заводим
вторую копию таблиц в `apps/api`. Зафиксировано в `reports/backend-pairings.md`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from functools import lru_cache
from pathlib import Path

import yaml

from .config import Settings

# --------------------------------------------------------------------------
# Мини-DSL: единственный общий вычислитель условий (when/require/penalize/
# hard_blocks[].condition) — операторы, реально встречающиеся в
# pipeline/ref/food_pairing_rules.yaml (contracts/post-scan.md §1, "Правила
# скоринга" перечисляет их исчерпывающе):
#   ">=X" / "<=X"            — числовой порог
#   "~=dish.Y ± Z"           — допуск вокруг ДРУГОГО поля
#   ">=dish.Y" / "<=dish.Y"  — сверка с ДРУГИМ полем (не константой)
#   "matches wine.region"    — строковое совпадение (структурно недостижимо:
#                              у тега блюда нет cuisine_region — не баг)
# --------------------------------------------------------------------------
_NUM_CMP_RE = re.compile(r"^(>=|<=)\s*(-?\d+(?:\.\d+)?)$")
_FIELD_CMP_RE = re.compile(r"^(>=|<=)\s*(dish|wine)\.(\w+)$")
_APPROX_RE = re.compile(r"^~=\s*(dish|wine)\.(\w+)\s*±\s*(-?\d+(?:\.\d+)?)$")
_MATCHES_RE = re.compile(r"^matches\s+(dish|wine)\.(\w+)$")


def _resolve(namespace: str, field: str, dish: dict, wine: dict) -> float | str | None:
    if namespace == "dish":
        # contracts/post-scan.md §1: "Отсутствующая в теге ось dish.* = 0.0"
        # (portal_tag_defaults — намеренно неполные словари на тег).
        return dish.get(field, 0.0)
    if namespace == "wine":
        # wine.* строится ЦЕЛИКОМ в build_sensory_wine_vector/
        # build_heuristic_wine_vector — None здесь только для полей вне этого
        # вектора (напр. "region" у недостижимого regional_affinity).
        return wine.get(field)
    raise ValueError(f"food_pairing_rules.yaml: неизвестный namespace {namespace!r}")


def eval_condition(condition: dict, dish: dict, wine: dict) -> bool:
    """Общий вычислитель для `when`/`require`/`penalize`/
    `hard_blocks[].condition` — словарь из 1+ ключей, несколько ключей = И
    (contracts/post-scan.md §1: "несколько ключей = И, все должны выполниться").
    Ключ вида "always" (булево, только `when: {always: true}` в файле) —
    особый случай, не namespaced."""
    for key, op in condition.items():
        if key == "always":
            if not op:
                return False
            continue

        namespace, _, field_name = key.partition(".")
        lhs = _resolve(namespace, field_name, dish, wine)

        if not isinstance(op, str):
            raise ValueError(f"food_pairing_rules.yaml: неожиданный операнд {op!r} для {key!r}")

        m = _NUM_CMP_RE.match(op)
        if m:
            cmp, num_s = m.groups()
            if lhs is None:
                return False
            ok = lhs >= float(num_s) if cmp == ">=" else lhs <= float(num_s)
            if not ok:
                return False
            continue

        m = _FIELD_CMP_RE.match(op)
        if m:
            cmp, rns, rfield = m.groups()
            rhs = _resolve(rns, rfield, dish, wine)
            if lhs is None or rhs is None:
                return False
            ok = lhs >= rhs if cmp == ">=" else lhs <= rhs
            if not ok:
                return False
            continue

        m = _APPROX_RE.match(op)
        if m:
            rns, rfield, tol_s = m.groups()
            rhs = _resolve(rns, rfield, dish, wine)
            if lhs is None or rhs is None:
                return False
            if abs(lhs - rhs) > float(tol_s):
                return False
            continue

        m = _MATCHES_RE.match(op)
        if m:
            rns, rfield = m.groups()
            rhs = _resolve(rns, rfield, dish, wine)
            # "Структурно недостижимо" (contracts/post-scan.md §1) — dish.*
            # никогда не несёт cuisine_region (нет такого dish_feature), lhs
            # почти всегда 0.0 (дефолт) против строки wine.region: сравнение
            # через `==` безопасно для разнотипных значений (не бросает,
            # просто False), в отличие от `>=`/`<=`.
            if lhs is None or rhs is None or lhs != rhs:
                return False
            continue

        raise ValueError(f"food_pairing_rules.yaml: нераспознанный оператор {op!r} для {key!r}")
    return True


# --------------------------------------------------------------------------
# Скоринг (contracts/post-scan.md §1, "Правила скоринга")
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ScoredPairing:
    tag: str
    score: float
    triggered_rules: list[dict] = dataclass_field(default_factory=list)


def score_pairings(wine: dict, rules_data: dict) -> list[ScoredPairing]:
    """9 тегов `portal_tag_defaults` — кандидаты. Хард-блок исключает тег
    целиком до скоринга. Для оставшихся: `applicable = Σ weight` правил, чей
    `when` истинен; `require` выполнен → `+weight`, `penalize` выполнен →
    `−weight` (независимые вклады — `umami_needs_maturity` может дать оба
    сразу), ни require, ни penalize (бонус, напр. `regional_affinity`) →
    `+weight` безусловно. `score = clamp(raw/applicable, 0, 1)`, тег входит в
    результат только при `score > 0`. Топ-`output_contract.top_n` по score
    убыв., равенство — по алфавиту тега (детерминированность)."""
    dish_defaults: dict[str, dict] = rules_data.get("portal_tag_defaults") or {}
    rules: list[dict] = rules_data.get("rules") or []
    hard_blocks: list[dict] = rules_data.get("hard_blocks") or []
    top_n = top_n_of(rules_data)

    results: list[ScoredPairing] = []
    for tag, dish in dish_defaults.items():
        if any(eval_condition(hb["condition"], dish, wine) for hb in hard_blocks):
            continue  # хард-блок — тег исключён из кандидатов целиком

        applicable = 0.0
        raw = 0.0
        triggered: list[dict] = []
        for rule in rules:
            when = rule.get("when", {})
            if not eval_condition(when, dish, wine):
                continue
            weight = float(rule["weight"])
            applicable += weight

            has_require = "require" in rule
            has_penalize = "penalize" in rule
            positive = False
            if has_require and eval_condition(rule["require"], dish, wine):
                raw += weight
                positive = True
            if has_penalize and eval_condition(rule["penalize"], dish, wine):
                raw -= weight
            if not has_require and not has_penalize:
                raw += weight  # бонус-правило (regional_affinity) — при истинном when
                positive = True
            if positive:
                # "почему подобрали" — только положительный вклад (require
                # выполнен либо бонус). Правило вроде spice_avoids_tannin
                # (только penalize) в triggered_rules не попадает — ему
                # нечего объяснять В ПОЛЬЗУ тега, только против (решение
                # backend по недосказанности контракта в этой точке — см.
                # reports/backend-pairings.md, "Предложения к контрактам").
                triggered.append({"id": rule["id"], "explain": rule["explain"]})

        score = max(0.0, min(1.0, raw / applicable)) if applicable > 0 else 0.0
        if score > 0:
            results.append(ScoredPairing(tag=tag, score=score, triggered_rules=triggered))

    results.sort(key=lambda r: (-r.score, r.tag))
    return results[:top_n]


def top_n_of(rules_data: dict) -> int:
    return int((rules_data.get("output_contract") or {}).get("top_n", 3))


# --------------------------------------------------------------------------
# Уровень 3 — эвристика по цвету + ключевым словам (contracts/post-scan.md §1,
# таблица дефолтов посчитана архитектором 22.09 по pipeline/ref/
# reference_styles.yaml — среднее по 146 стилям, сгруппировано по
# (color, stillness); †/‡ — оговорки контракта про недостающие наблюдения).
# --------------------------------------------------------------------------
_SENSORY_AXES = ("sweetness", "acidity", "tannin", "body", "oak", "aromatic_intensity", "bubbles")

_COLOR_TABLE: dict[str, dict[str, dict[str, float]]] = {
    "белое": {
        "тихое": {"sweetness": 0.20, "acidity": 0.69, "tannin": 0.05, "body": 0.58,
                  "oak": 0.17, "aromatic_intensity": 0.69, "bubbles": 0.00},
        "игристое": {"sweetness": 0.29, "acidity": 0.71, "tannin": 0.05, "body": 0.49,
                     "oak": 0.08, "aromatic_intensity": 0.68, "bubbles": 0.73},
    },
    "красное": {
        "тихое": {"sweetness": 0.15, "acidity": 0.63, "tannin": 0.62, "body": 0.72,
                  "oak": 0.38, "aromatic_intensity": 0.75, "bubbles": 0.00},
        "игристое": {"sweetness": 0.27, "acidity": 0.60, "tannin": 0.38, "body": 0.55,
                     "oak": 0.15, "aromatic_intensity": 0.75, "bubbles": 0.67},
    },
    "розовое": {
        "тихое": {"sweetness": 0.13, "acidity": 0.62, "tannin": 0.17, "body": 0.51,
                  "oak": 0.06, "aromatic_intensity": 0.60, "bubbles": 0.00},
        "игристое": {"sweetness": 0.20, "acidity": 0.72, "tannin": 0.17, "body": 0.49,
                     "oak": 0.08, "aromatic_intensity": 0.65, "bubbles": 0.78},
    },
    "оранжевое": {
        "тихое": {"sweetness": 0.05, "acidity": 0.69, "tannin": 0.42, "body": 0.64,
                  "oak": 0.18, "aromatic_intensity": 0.68, "bubbles": 0.00},
        # ‡ 0 наблюдений оранжевых игристых среди 146 стилей (контракт) — то
        # же тихое, кроме bubbles (грубая отметка "есть пузырьки").
        "игристое": {"sweetness": 0.05, "acidity": 0.69, "tannin": 0.42, "body": 0.64,
                     "oak": 0.18, "aromatic_intensity": 0.68, "bubbles": 0.60},
    },
}

_DEFAULT_ABV = 12.5  # contracts/post-scan.md §1: синтетическая стартовая точка, не калибровано

# Сахар — первое совпадение по порядку побеждает ("полусладк" ДО "сладк":
# "полусладкое" содержит подстроку "сладк", иначе матчилось бы не то).
_SWEETNESS_KEYWORDS: list[tuple[str, float]] = [
    ("полусладк", 0.55),
    ("сладк", 0.85),
    ("десертн", 0.85),
    ("ликёрн", 0.85),
    ("ликерн", 0.85),
    ("полусух", 0.35),
    ("брют", 0.05),
    ("brut", 0.05),
    ("сух", 0.10),
    ("dry", 0.10),
]

_SPARKLING_KEYWORDS = (
    "игрист", "шампан", "champagne", "просекко", "prosecco", "асти", "asti",
    "креман", "cremant", "пет-нат", "pet-nat", "petnat", "cava", "кава",
)


def _sweetness_from_keywords(text: str) -> float | None:
    for keyword, value in _SWEETNESS_KEYWORDS:
        if keyword in text:
            return value
    return None


def _is_sparkling(text: str) -> bool:
    return any(keyword in text for keyword in _SPARKLING_KEYWORDS)


def _resolve_abv(source: dict) -> float:
    abv = source.get("abv_percent")
    return float(abv) if isinstance(abv, (int, float)) else _DEFAULT_ABV


def build_heuristic_wine_vector(source: dict) -> dict[str, float] | None:
    """`None` → вызывающая сторона честно отдаёт `basis="unavailable"`
    (color пуст либо вне 4 известных значений контракта)."""
    color_raw = (source.get("color") or "").strip()
    if not color_raw:
        return None

    color_table = _COLOR_TABLE.get(color_raw.lower())
    if color_table is None:
        return None

    text = f"{source.get('name') or ''} {source.get('description') or ''}".lower()
    stillness = "игристое" if _is_sparkling(text) else "тихое"
    vector = dict(color_table[stillness])

    sweetness_override = _sweetness_from_keywords(text)
    if sweetness_override is not None:
        vector["sweetness"] = sweetness_override

    vector["abv_percent"] = _resolve_abv(source)
    return vector


def build_sensory_wine_vector(source: dict, sensory: dict) -> dict[str, float]:
    """Уровень 2 — `derived.sensory` "без каких-либо преобразований: имена
    осей совпадают буквально" (contracts/post-scan.md §1). `wine.abv_percent`
    — единственное исключение, не ось `derived.sensory` (`source.abv_percent`
    отдельно, дефолт `_DEFAULT_ABV`)."""
    vector = {axis: float(sensory[axis]) for axis in _SENSORY_AXES if axis in sensory}
    vector["abv_percent"] = _resolve_abv(source)
    return vector


# --------------------------------------------------------------------------
# Загрузка pipeline/ref/food_pairing_rules.yaml — ТОЛЬКО ЧТЕНИЕ, кэш по пути
# (тот же принцип, что app/rag/case_catalog.py::_load_catalog): файл
# коммитится в репозиторий (в отличие от case-data) — отсутствие/битый
# формат честно считаем багом деплоя, не поводом молча деградировать.
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_rules_data(path_str: str) -> dict:
    data = yaml.safe_load(Path(path_str).read_text(encoding="utf-8"))
    return data or {}


def _rules_path(settings: Settings) -> str:
    return str(Path(settings.pairing_rules_path).resolve())


# --------------------------------------------------------------------------
# Оркестрация трёх уровней — вызывается роутером с готовой карточкой
# (build_wine_card()'s "source"/"derived", БЕЗ wine_id/source_url/similar).
# --------------------------------------------------------------------------
_NO_RULE_FIRED_MESSAGE = (
    "Не нашлось явных гастропар для этого вина — ни одно правило не сработало."
)
_UNAVAILABLE_MESSAGE = (
    "Гастропары для этого вина пока недоступны — недостаточно данных о вкусовом профиле."
)


def compute_pairings(card: dict, settings: Settings) -> dict:
    """Возвращает `{"basis", "pairings", "message"}` — всё, кроме `wine_id`
    (его подставляет роутер, он уже есть в карточке отдельным полем)."""
    source = card.get("source") or {}
    derived = card.get("derived") or {}
    rules_data = _load_rules_data(_rules_path(settings))

    food_pairings = source.get("food_pairings") or []
    if food_pairings:
        top_n = top_n_of(rules_data)
        pairings = [
            {"tag": tag, "score": None, "triggered_rules": []}
            for tag in food_pairings[:top_n]
        ]
        return {"basis": "catalog", "pairings": pairings, "message": None}

    sensory = derived.get("sensory") or {}
    if sensory:
        wine_vector = build_sensory_wine_vector(source, sensory)
        return _scored_result("sensory", wine_vector, rules_data)

    heuristic_vector = build_heuristic_wine_vector(source)
    if heuristic_vector is not None:
        return _scored_result("heuristic", heuristic_vector, rules_data)

    return {"basis": "unavailable", "pairings": [], "message": _UNAVAILABLE_MESSAGE}


def _scored_result(basis: str, wine_vector: dict, rules_data: dict) -> dict:
    scored = score_pairings(wine_vector, rules_data)
    if not scored:
        # contracts/post-scan.md §1, правило 5: вектор БЫЛ, просто ни одно
        # правило не выстрелило — basis остаётся тем, что было, не unavailable.
        return {"basis": basis, "pairings": [], "message": _NO_RULE_FIRED_MESSAGE}
    pairings = [
        {"tag": s.tag, "score": round(s.score, 4), "triggered_rules": s.triggered_rules}
        for s in scored
    ]
    return {"basis": basis, "pairings": pairings, "message": None}
