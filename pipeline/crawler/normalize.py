"""Нормализация каталога: справочники, связи, расчёт derived-полей.

Правило: ничего не досочиняем. Если признак не выводится из данных
портала — оставляем None и снижаем confidence.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from config import CATALOG, REF


def slugify(text: str) -> str:
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "cz", "ч": "ch", "ш": "sh", "щ": "shh",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    text = unicodedata.normalize("NFC", text).lower().strip()
    out = "".join(table.get(ch, ch) for ch in text)
    out = re.sub(r"[^a-z0-9]+", "-", out)
    return out.strip("-")


@lru_cache(maxsize=None)
def load_ref(name: str) -> dict[str, Any]:
    """Справочники читаются на каждое вино, поэтому кешируем разбор YAML.
    Без кеша normalize перечитывал reference_styles.yaml 1978 раз."""
    return yaml.safe_load((REF / name).read_text(encoding="utf-8"))


class GrapeResolver:
    def __init__(self) -> None:
        ref = load_ref("grape_synonyms.yaml")
        self.index: dict[str, dict[str, Any]] = {}
        for bucket in ("autochthonous", "hybrid_and_soviet", "international"):
            for g in ref.get(bucket, []) or []:
                g["bucket"] = bucket
                for key in [g["name"], *g.get("synonyms", [])]:
                    self.index[key.lower().strip()] = g
        self.non_grapes = {s.lower().strip() for s in ref.get("non_grapes", []) or []}

    def resolve(self, raw: str) -> dict[str, Any] | None:
        return self.index.get(raw.lower().strip())

    def is_non_grape(self, raw: str) -> bool:
        return raw.lower().strip() in self.non_grapes

    def unknown(self, names: list[str]) -> list[str]:
        return [n for n in names if not self.resolve(n)]


PAREN_RE = re.compile(r"\s*\([^)]*\)")


def expand_grapes(raw_names: list[str], resolver: GrapeResolver) -> list[str]:
    """Готовит список сортов к разбору: выбрасывает служебные значения портала
    («Красные сорта винограда») и разбивает склейки, где в одну строку записаны
    два сорта («Пино чёрный (Пино нуар) и Мерло»).

    Результат идёт только в derived. source не трогаем — там остаётся ровно то,
    что напечатано на портале.
    """
    out: list[str] = []
    for raw in raw_names:
        name = raw.strip()
        if not name or resolver.is_non_grape(name):
            continue
        if resolver.resolve(name):
            out.append(name)
            continue
        bare = PAREN_RE.sub("", name).strip()
        if resolver.resolve(bare):
            out.append(bare)
            continue
        parts = [PAREN_RE.sub("", p).strip() for p in re.split(r"\s+и\s+", name)]
        if len(parts) > 1 and all(resolver.resolve(p) for p in parts):
            out.extend(parts)
            continue
        out.append(name)
    return out


# --------------------------------------------------------------------------
# сенсорный вектор
# --------------------------------------------------------------------------

SUGAR_TO_SWEETNESS = {
    "экстра брют": 0.02,
    "брют": 0.08,
    "сухое": 0.05,
    "полусухое": 0.30,
    "полусладкое": 0.55,
    "сладкое": 0.85,
}

# Потолок танина и тела по цвету: способ винификации важнее сортового профиля.
TANNIN_CAP = {"белое": 0.10, "розовое": 0.20, "оранжевое": 0.60}
BODY_CAP = {"белое": 0.80, "розовое": 0.65}

SPARKLING_HINTS = ("брют", "экстра брют")
OAK_HINTS = ("баррель", "бочк", "дуб", "выдержк", "barrique")
PETNAT_HINTS = ("пет нат", "пет-нат", "pet nat", "petnat")


def sensory_from_source(
    source: dict[str, Any], resolver: GrapeResolver, grapes: list[str] | None = None
) -> dict[str, Any]:
    """Считает сенсорный вектор. confidence отражает, сколько признаков реально выведено."""
    out: dict[str, Any] = {
        "sweetness": None, "acidity": None, "tannin": None, "body": None,
        "oak": None, "aromatic_intensity": None, "bubbles": None,
    }
    known = 0

    sugar = (source.get("sugar_category") or "").lower()
    if sugar in SUGAR_TO_SWEETNESS:
        out["sweetness"] = SUGAR_TO_SWEETNESS[sugar]
        known += 1

    text = " ".join(
        filter(None, [source.get("name", ""), source.get("description", "")])
    ).lower()

    if any(h in sugar for h in SPARKLING_HINTS) or any(h in text for h in PETNAT_HINTS):
        out["bubbles"] = 0.6
        known += 1
    else:
        out["bubbles"] = 0.0

    if any(h in text for h in OAK_HINTS):
        out["oak"] = 0.6
        known += 1
    else:
        out["oak"] = 0.1

    # усреднение опорных профилей сортов
    acc: dict[str, list[float]] = {}
    for g in (source.get("grapes", []) if grapes is None else grapes):
        info = resolver.resolve(g)
        if info and info.get("profile"):
            for k, v in info["profile"].items():
                acc.setdefault(k, []).append(float(v))
    for k, vals in acc.items():
        if k in out and out[k] is None:
            out[k] = round(sum(vals) / len(vals), 3)
            known += 1

    color = (source.get("color") or "").lower()
    if out["tannin"] is None:
        out["tannin"] = {"красное": 0.55, "оранжевое": 0.5}.get(color, 0.05)
    if out["body"] is None:
        out["body"] = {"красное": 0.65, "оранжевое": 0.6, "розовое": 0.4}.get(color, 0.45)

    # Танин и тело определяются не сортом, а тем, сколько сок провёл на кожице.
    # Розе из Каберне Совиньона по танину не Каберне Совиньон, а розе; то же
    # у «каберне по-белому». Без этого потолка сортовой профиль протаскивал
    # в розовые вина танин 0.7-0.8 и они переставали походить на розовые стили.
    if (cap := TANNIN_CAP.get(color)) is not None:
        out["tannin"] = min(out["tannin"], cap)
    if (cap := BODY_CAP.get(color)) is not None:
        out["body"] = min(out["body"], cap)

    abv = source.get("abv_percent")
    if abv and out["body"] is not None:
        out["body"] = round(min(1.0, out["body"] + (float(abv) - 12.0) * 0.03), 3)

    out["confidence"] = round(min(1.0, known / 6), 2)
    out["method"] = "rules"
    return out


# --------------------------------------------------------------------------
# матчинг эталонных стилей
# --------------------------------------------------------------------------

# Порог отсечки. Откалиброван на каталоге 2026-08-25 (1978 вин): при 0.80
# стиль получают ~2/3 вин, треть остаётся без матча — это и есть рабочий
# режим. Прежние 0.55 лежали НИЖЕ пола метрики (минимум по каталогу был
# 0.64), поэтому матч получали все вина без исключения, и поле не значило
# ничего. Менять порог — только вместе с замером распределения.
STYLE_MATCH_THRESHOLD = 0.80

# Меньше трёх общих измерений — сравнивать нечего: совпадение по телу и
# дубу само по себе ни о чём не говорит.
MIN_SHARED_DIMS = 3

GRAPE_BONUS = 0.10


def match_reference_styles(
    sensory: dict[str, Any],
    source: dict[str, Any],
    top_n: int = 3,
    grapes: list[str] | None = None,
    threshold: float = STYLE_MATCH_THRESHOLD,
) -> list[str]:
    styles = load_ref("reference_styles.yaml")["styles"]
    color = (source.get("color") or "").lower()
    sugar = (source.get("sugar_category") or "").lower()
    stillness = "игристое" if (sensory.get("bubbles") or 0) > 0.3 else "тихое"
    names = {
        g.lower() for g in (source.get("grapes", []) if grapes is None else grapes)
    }

    results: list[tuple[float, str]] = []

    for st in styles:
        # --- жёсткие фильтры: несовпадение категории не лечится близостью вектора ---
        if st.get("color") and color and st["color"].lower() != color:
            continue
        if st.get("stillness") and st["stillness"] != stillness:
            continue
        allowed = [s.lower() for s in (st.get("sugar") or [])]
        if allowed and sugar and sugar not in allowed:
            continue

        target = st.get("sensory") or {}
        dims = [k for k in target if sensory.get(k) is not None]
        if len(dims) < MIN_SHARED_DIMS:
            continue

        diffs = [abs(float(target[k]) - float(sensory[k])) for k in dims]
        # Половина веса — средняя непохожесть, половина — худшее измерение.
        # Иначе один грубо расходящийся признак усредняется и исчезает:
        # сухое вино попадало в сладкий стиль, потому что тело и танин сошлись.
        score = 1.0 - (0.5 * (sum(diffs) / len(diffs)) + 0.5 * max(diffs))

        hints = st.get("match_hints") or {}
        prefer = {g.lower() for g in hints.get("prefer_grapes", [])}
        if prefer and names & prefer:
            score += GRAPE_BONUS

        results.append((min(1.0, score), st["slug"]))

    results.sort(reverse=True)
    return [slug for score, slug in results[:top_n] if score >= threshold]


# --------------------------------------------------------------------------
# основной проход
# --------------------------------------------------------------------------

def normalize_all() -> dict[str, Any]:
    resolver = GrapeResolver()
    taxonomy = load_ref("taxonomy.yaml")
    region_by_name = {r["name"].lower(): r["slug"] for r in taxonomy["regions"]}

    stats = {"wines": 0, "unknown_grapes": set(), "unknown_regions": set(), "low_confidence": 0}
    wine_dir = CATALOG / "wines"

    for path in sorted(wine_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        src = doc["source"]

        if src.get("region_name"):
            slug = region_by_name.get(src["region_name"].lower())
            if slug:
                src["region"] = slug
            else:
                stats["unknown_regions"].add(src["region_name"])

        grapes = expand_grapes(src.get("grapes", []), resolver)
        grape_slugs = []
        for g in grapes:
            info = resolver.resolve(g)
            if info:
                grape_slugs.append(info["slug"])
            else:
                stats["unknown_grapes"].add(g)
        # синонимы схлопываются в один slug: «Сира» и «Шираз» в одной карточке
        # не должны давать сорт дважды
        grape_slugs = list(dict.fromkeys(grape_slugs))

        sensory = sensory_from_source(src, resolver, grapes)
        if sensory["confidence"] < 0.5:
            stats["low_confidence"] += 1

        style_tags = []
        text = (src.get("name") or "").lower()
        if any(h in text for h in PETNAT_HINTS):
            style_tags.append("пет-нат")
        if any(h in (src.get("description") or "").lower() for h in OAK_HINTS):
            style_tags.append("бочковое")

        doc["derived"] = {
            "stillness": (
                "игристое" if sensory["bubbles"] and sensory["bubbles"] > 0.3 else "тихое"
            ),
            "style_tags": style_tags,
            "grape_slugs": grape_slugs,
            "aroma_descriptors": [],
            "sensory": sensory,
            "price_tier": None,
            "reference_style_matches": match_reference_styles(sensory, src, grapes=grapes),
        }

        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        stats["wines"] += 1

    stats["wineries"] = normalize_wineries(region_by_name, stats["unknown_regions"])

    stats["unknown_grapes"] = sorted(stats["unknown_grapes"])
    stats["unknown_regions"] = sorted(stats["unknown_regions"])
    return stats


def normalize_wineries(region_by_name: dict[str, str], unknown: set[str]) -> int:
    """Виноделен касается только привязка региона к slug'у таксономии —
    сенсорики и сортов у хозяйства нет. Без этого прохода поле region
    у всех виноделен оставалось null."""
    wdir = CATALOG / "wineries"
    if not wdir.exists():
        return 0

    n = 0
    for path in sorted(wdir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        src = doc["source"]
        if src.get("region_name"):
            slug = region_by_name.get(src["region_name"].lower())
            if slug:
                src["region"] = slug
            else:
                unknown.add(src["region_name"])
        doc["derived"] = {
            "wine_count": len(src.get("wine_slugs", [])),
        }
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        n += 1
    return n
