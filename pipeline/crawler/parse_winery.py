"""Парсер карточки винодельни.

Две стратегии:

  1. META — og:title / og:description / og:image. Шаблон title:
     "Винодельня <название> <регион>: история, вина и особенности терруара"
  2. DOM — привязка к текстовым меткам блока характеристик
     ("Площадь виноградников", "Регион", "Местность", "Климат").

JSON-LD на карточке винодельни есть, но только BreadcrumbList — полезных
полей там нет, поэтому третьей стратегии нет.

Карточки бывают пустыми: у мелких хозяйств блок характеристик и текст
отсутствуют. Тогда поля остаются null — это факт источника, не ошибка.
"""

from __future__ import annotations

import re
from typing import Any

from selectolax.parser import HTMLParser

import dom

LABELS = {
    "area": ("Площадь виноградников", "Площадь"),
    "region": ("Регион",),
    "locality": ("Местность", "Рельеф"),
    "climate": ("Климат",),
}

AREA_RE = re.compile(r"([\d][\d\s.,]*)\s*га", re.IGNORECASE)
FOUNDED_RE = re.compile(r"основан[а-я]*\s+в\s+(1[6-9]\d\d|20[0-2]\d)\s*год", re.IGNORECASE)


def from_meta(tree: HTMLParser) -> dict[str, Any]:
    data: dict[str, Any] = {}

    og_title = dom.meta(tree, "og:title")
    if og_title:
        data["name"] = og_title.strip()

    desc = dom.meta(tree, "og:description") or dom.meta(tree, "description")
    if desc:
        data["description"] = desc

    img = dom.meta(tree, "og:image")
    if img:
        data["image_url"] = img

    return data


def from_dom(tree: HTMLParser) -> dict[str, Any]:
    data: dict[str, Any] = {}
    blocks = dom.text_blocks(tree)

    h1 = tree.css_first("h1")
    if h1 and h1.text(strip=True):
        data["name"] = re.sub(r"\s+", " ", h1.text()).strip()

    if (r := dom.value_after(blocks, LABELS["region"], max_len=60)):
        data["region_name"] = r
    if (loc := dom.value_after(blocks, LABELS["locality"])):
        data["locality"] = loc
    if (cl := dom.value_after(blocks, LABELS["climate"])):
        data["climate"] = cl

    if (a := dom.value_after(blocks, LABELS["area"], max_len=40)):
        data["vineyard_area_raw"] = a
        ma = AREA_RE.search(a)
        if ma:
            num = ma.group(1).replace(" ", "").replace("\xa0", "").replace(",", ".")
            try:
                data["vineyard_area_ha"] = float(num)
            except ValueError:
                pass

    # Редакционный текст хозяйства: содержательные абзацы, без навигации и подписей.
    paragraphs = [
        p
        for p in dict.fromkeys(
            re.sub(r"\s+", " ", n.text()).strip() for n in tree.css("p")
        )
        if len(p) > 140
    ]
    if paragraphs:
        data["about"] = "\n\n".join(paragraphs)
        mf = FOUNDED_RE.search(data["about"])
        if mf:
            data["founded_year"] = int(mf.group(1))

    return data


def parse_winery(html: str, slug: str) -> dict[str, Any]:
    tree = HTMLParser(html)
    merged, refs = dom.merge_layers([("meta", from_meta(tree)), ("dom", from_dom(tree))])

    source = {
        "name": merged.get("name"),
        "region": None,
        "region_name": merged.get("region_name"),
        "locality": merged.get("locality"),
        "climate": merged.get("climate"),
        "vineyard_area_ha": merged.get("vineyard_area_ha"),
        "vineyard_area_raw": merged.get("vineyard_area_raw"),
        "founded_year": merged.get("founded_year"),
        "description": merged.get("description"),
        "about": merged.get("about"),
        "image_url": merged.get("image_url"),
        "wine_slugs": dom.links(html, "wines"),
        "nearby_winery_slugs": dom.links(html, "wineries", exclude=slug),
    }

    return {"slug": slug, "source": source, "_field_strategies": refs}
