"""Парсер карточки вина.

Три стратегии, применяются по очереди, результаты сливаются
(побеждает более ранняя, более надёжная):

  1. RSC — Next.js App Router складывает пропсы в self.__next_f.push([...]).
     Если удаётся достать JSON — берём поля напрямую, это самый точный путь.
  2. META — метатеги. Шаблон title карточки очень плотный:
     "Вино <цвет> <категория> <название> <винодельня> из винограда сорта
      <сорт> региона <регион>"
     og:description содержит полное описание.
  3. DOM — привязка к текстовым меткам ("Регион", "Крепость вина",
     "Температура подачи", "Сочетание с блюдами"). Устойчиво к смене
     классов, ломается только при смене подписей.

Стратегия, которой досталось поле, пишется в field_refs — видно, откуда
что взялось, и легко чинить при изменении вёрстки.
"""

from __future__ import annotations

import json
import re
from typing import Any

from selectolax.parser import HTMLParser

import dom

COLORS = ["белое", "красное", "розовое", "оранжевое"]
SUGAR = ["экстра брют", "брют", "полусухое", "полусладкое", "сухое", "сладкое"]

TITLE_RE = re.compile(
    r"Вино\s+(?P<color>белое|красное|розовое|оранжевое)\s+"
    r"(?P<sugar>экстра брют|брют|полусухое|полусладкое|сухое|сладкое)\s+"
    r"(?P<rest>.+?)\s+\|",
    re.IGNORECASE,
)
GRAPE_IN_TITLE_RE = re.compile(r"из винограда сорта\s+(?P<grapes>.+?)\s+региона\s+(?P<region>.+?)\s*\|")
ABV_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*%")
TEMP_RE = re.compile(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*°?\s*C", re.IGNORECASE)
VINTAGE_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
RATING_RE = re.compile(r"Народный рейтинг\s+([0-9](?:[.,][0-9]{1,2})?)")
COLOR_IN_GLASS_RE = re.compile(r"цвет:\s*([^\"\n,]+)")


# --------------------------------------------------------------------------
# 1. RSC
# --------------------------------------------------------------------------

def _extract_rsc_payloads(html: str) -> list[Any]:
    """Достаёт JSON-куски из self.__next_f.push([1,"..."])."""
    out: list[Any] = []
    chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)', html)
    if not chunks:
        return out
    joined = "".join(chunks)
    try:
        joined = json.loads(f'"{joined}"')  # разэкранирование
    except json.JSONDecodeError:
        joined = joined.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")

    # Вытаскиваем сбалансированные объекты, содержащие маркерные ключи
    for marker in ('"wine"', '"attributes"', '"grapeSorts"', '"gastronomy"'):
        for start in (m.start() for m in re.finditer(re.escape(marker), joined)):
            brace = joined.rfind("{", 0, start)
            if brace == -1:
                continue
            depth, i = 0, brace
            while i < len(joined):
                if joined[i] == "{":
                    depth += 1
                elif joined[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            try:
                out.append(json.loads(joined[brace : i + 1]))
            except json.JSONDecodeError:
                continue
    return out


def from_rsc(html: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for obj in _extract_rsc_payloads(html):
        if not isinstance(obj, dict):
            continue
        attrs = obj.get("attributes", obj)
        if not isinstance(attrs, dict):
            continue
        mapping = {
            "name": ("name", "title"),
            "description": ("description", "text", "content"),
            "abv_percent": ("alcohol", "strength", "abv"),
            "public_rating": ("rating", "publicRating"),
            "vintage": ("year", "vintage"),
        }
        for target, keys in mapping.items():
            for k in keys:
                if k in attrs and attrs[k] not in (None, ""):
                    data.setdefault(target, attrs[k])
        for k in ("grapeSorts", "grapes", "sorts"):
            if isinstance(attrs.get(k), list):
                names = [
                    g.get("name") if isinstance(g, dict) else g
                    for g in attrs[k]
                ]
                data.setdefault("grapes", [n for n in names if n])
        for k in ("gastronomy", "foodPairings", "dishes"):
            if isinstance(attrs.get(k), list):
                names = [
                    g.get("name") if isinstance(g, dict) else g
                    for g in attrs[k]
                ]
                data.setdefault("food_pairings", [n for n in names if n])
    return data


# --------------------------------------------------------------------------
# 2. META
# --------------------------------------------------------------------------

def _meta(tree: HTMLParser, name: str) -> str | None:
    return dom.meta(tree, name)


def from_meta(tree: HTMLParser) -> dict[str, Any]:
    data: dict[str, Any] = {}
    title = tree.css_first("title")
    title_text = title.text() if title else ""

    m = TITLE_RE.search(title_text)
    if m:
        data["color"] = m.group("color").lower()
        data["sugar_category"] = m.group("sugar").lower()

    g = GRAPE_IN_TITLE_RE.search(title_text)
    if g:
        data["grapes_from_title"] = [s.strip() for s in g.group("grapes").split(",") if s.strip()]
        data["region_name"] = g.group("region").strip()

    desc = _meta(tree, "og:description") or _meta(tree, "description")
    if desc:
        data["description"] = desc

    img = _meta(tree, "og:image")
    if img:
        data["image_url"] = img

    og_title = _meta(tree, "og:title")
    if og_title:
        data["name"] = og_title.strip()

    v = VINTAGE_RE.search(og_title or "")
    if v:
        data["vintage"] = int(v.group(1))

    return data


# --------------------------------------------------------------------------
# 3. DOM по текстовым меткам
# --------------------------------------------------------------------------

LABELS = {
    "region": ("Регион",),
    "grapes": ("Сорта винограда", "Сорт винограда"),
    "category_color": ("Категория и цвет",),
    "serving_temp": ("Температура подачи", "Температура"),
    "abv": ("Крепость вина", "Крепость"),
    "food": ("Сочетание с блюдами", "Гастросочетания"),
}


def _text_blocks(tree: HTMLParser) -> list[str]:
    # Порядок документа и схлопывание пробелов — см. dom.text_blocks.
    return dom.text_blocks(tree)


def from_dom(tree: HTMLParser) -> dict[str, Any]:
    data: dict[str, Any] = {}
    blocks = _text_blocks(tree)
    norm = [b.replace("\xa0", " ").strip() for b in blocks]

    def value_after(labels: tuple[str, ...]) -> str | None:
        for i, b in enumerate(norm):
            for lab in labels:
                if b == lab or b.rstrip(":") == lab:
                    for nxt in norm[i + 1 : i + 4]:
                        if nxt and nxt not in labels and len(nxt) < 200:
                            return nxt
        return None

    if (r := value_after(LABELS["region"])):
        data["region_name"] = r
    if (g := value_after(LABELS["grapes"])):
        data["grapes"] = [s.strip() for s in g.split(",") if s.strip()]
    if (t := value_after(LABELS["serving_temp"])):
        mt = TEMP_RE.search(t)
        if mt:
            data["serving_temp_c"] = [int(mt.group(1)), int(mt.group(2))]
    if (a := value_after(LABELS["abv"])):
        ma = ABV_RE.search(a)
        if ma:
            data["abv_percent"] = float(ma.group(1).replace(",", "."))

    page = "\n".join(norm)

    mr = RATING_RE.search(page)
    if mr:
        data["public_rating"] = float(mr.group(1).replace(",", "."))

    # цвет в бокале — из alt изображения бутылки
    for img in tree.css("img"):
        alt = img.attributes.get("alt") or ""
        mc = COLOR_IN_GLASS_RE.search(alt)
        if mc:
            data["color_in_glass"] = mc.group(1).strip().rstrip(".")
            break

    # винодельня — первая ссылка на /wineries/<slug> в теле
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        m = re.search(r"/wineries/([a-z0-9_\-]+)$", href)
        if m and a.text(strip=True):
            data["winery"] = m.group(1)
            data["winery_name"] = a.text(strip=True)
            break

    # похожие вина
    similar = []
    for a in tree.css("a"):
        m = re.search(r"/wines/([a-z0-9_\-]+)$", a.attributes.get("href") or "")
        if m:
            similar.append(m.group(1))
    if similar:
        data["similar_wine_slugs"] = list(dict.fromkeys(similar))

    # гастросочетания — alt у иконок блюд
    food = []
    for img in tree.css("img"):
        if (img.attributes.get("alt") or "").strip() == "Блюда":
            parent = img.parent
            if parent is not None:
                txt = parent.text(strip=True)
                if txt and len(txt) < 60:
                    food.append(txt)
    if food:
        data["food_pairings"] = list(dict.fromkeys(food))

    return data


# --------------------------------------------------------------------------
# сборка
# --------------------------------------------------------------------------

def parse_wine(html: str, slug: str) -> dict[str, Any]:
    tree = HTMLParser(html)

    layers = [("rsc", from_rsc(html)), ("meta", from_meta(tree)), ("dom", from_dom(tree))]

    merged: dict[str, Any] = {}
    refs: list[dict[str, str]] = []
    for strategy, payload in layers:
        for k, v in payload.items():
            if k not in merged and v not in (None, "", [], {}):
                merged[k] = v
                refs.append({"field": k, "strategy": strategy})

    if "grapes" not in merged and "grapes_from_title" in merged:
        merged["grapes"] = merged["grapes_from_title"]
    merged.pop("grapes_from_title", None)

    if merged.get("name"):
        merged["name"] = re.sub(r"\s*\|.*$", "", merged["name"]).strip()

    source = {
        "name": merged.get("name"),
        "winery": merged.get("winery"),
        "winery_name": merged.get("winery_name"),
        "region": None,
        "region_name": merged.get("region_name"),
        "grapes": merged.get("grapes", []),
        "color": merged.get("color"),
        "sugar_category": merged.get("sugar_category"),
        "color_in_glass": merged.get("color_in_glass"),
        "vintage": merged.get("vintage"),
        "abv_percent": merged.get("abv_percent"),
        "serving_temp_c": merged.get("serving_temp_c"),
        "food_pairings": merged.get("food_pairings", []),
        "description": merged.get("description"),
        "public_rating": merged.get("public_rating"),
        "public_rating_count": merged.get("public_rating_count"),
        "roskachestvo_rating": merged.get("roskachestvo_rating"),
        "image_url": merged.get("image_url"),
        "similar_wine_slugs": [s for s in merged.get("similar_wine_slugs", []) if s != slug],
    }

    return {"slug": slug, "source": source, "_field_strategies": refs}
