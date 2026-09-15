"""Парсер статьи «Историй в бокале».

Три стратегии:

  1. JSON-LD — на карточке статьи лежит полноценный schema.org/Article
     с headline, description, articleBody, datePublished, author, image.
     Это самый точный путь: текст приходит уже собранным, без вёрстки.
  2. META — og:title / og:description / og:image, если JSON-LD не отдался.
  3. DOM — h1, рубрика и подпись к фото; текст собираем из абзацев.

Правовой режим (README, п.5): текст статьи кэшируется как основа для поиска
и цитирования со ссылкой на первоисточник, а не как собственный контент.
"""

from __future__ import annotations

import re
from typing import Any

from selectolax.parser import HTMLParser

import dom

PHOTO_CREDIT_RE = re.compile(r"^Фото:\s*(.+)$")

# Подвальные блоки: после них начинаются чужие статьи, а не разделы текущей.
TAIL_MARKERS = {
    "Может быть интересно",
    "Читайте также",
    "Ещё по теме",
    "Похожие статьи",
    "Другие статьи",
}


def from_jsonld(html: str) -> dict[str, Any]:
    # Новости размечены как NewsArticle, лонгриды — как Article. Набор полей
    # одинаковый, различается только @type.
    art = dom.jsonld(html, ("Article", "NewsArticle", "BlogPosting"))
    if not art:
        return {}

    data: dict[str, Any] = {"schema_type": art.get("@type")}
    if art.get("headline"):
        data["title"] = art["headline"].strip()
    if art.get("description"):
        data["lead"] = art["description"].strip()
    if art.get("articleBody"):
        data["body"] = art["articleBody"].strip()
    if art.get("datePublished"):
        data["published_at"] = art["datePublished"]
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", art["datePublished"])
        if m:
            data["published_date"] = m.group(0)
            data["published_year"] = int(m.group(1))
    if art.get("dateModified"):
        data["modified_at"] = art["dateModified"]

    author = art.get("author")
    if isinstance(author, dict) and author.get("name"):
        data["author"] = author["name"].strip()
    elif isinstance(author, str):
        data["author"] = author.strip()

    image = art.get("image")
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict) and image.get("url"):
        data["image_url"] = image["url"]
    elif isinstance(image, str):
        data["image_url"] = image

    return data


def from_meta(tree: HTMLParser) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if (t := dom.meta(tree, "og:title")):
        data["title"] = t
    if (d := dom.meta(tree, "og:description") or dom.meta(tree, "description")):
        data["lead"] = d
    if (i := dom.meta(tree, "og:image")):
        data["image_url"] = i
    return data


def from_dom(tree: HTMLParser, author: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    blocks = dom.text_blocks(tree)

    h1 = tree.css_first("h1")
    if h1 and h1.text(strip=True):
        data["title"] = re.sub(r"\s+", " ", h1.text()).strip()

    # Шапка статьи идёт подряд: «18 ноября» · «Вино» · «Маргарита Собещанская».
    # Автора знаем из JSON-LD, поэтому рубрику берём как блок перед ним.
    if author:
        needle = author.strip()
        for i, b in enumerate(blocks):
            if b == needle and i:
                prev = blocks[i - 1]
                if prev and len(prev) < 40 and prev != needle:
                    data["rubric"] = prev
                break

    for b in blocks:
        mc = PHOTO_CREDIT_RE.match(b)
        if mc and len(b) < 120:
            data["photo_credit"] = mc.group(1).strip()
            break

    # Заголовки разделов — полезны для нарезки на чанки. После блока «Может быть
    # интересно» идут заголовки чужих статей из подвала: обрезаем, иначе в чанки
    # текущей статьи затекает соседний контент.
    headings: list[str] = []
    for n in tree.css("h2, h3"):
        h = re.sub(r"\s+", " ", n.text()).strip()
        if not h:
            continue
        if h in TAIL_MARKERS:
            break
        headings.append(h)
    if headings:
        data["headings"] = list(dict.fromkeys(headings))

    paragraphs = [
        p
        for p in dict.fromkeys(
            re.sub(r"\s+", " ", n.text()).strip() for n in tree.css("p")
        )
        if len(p) > 120
    ]
    if paragraphs:
        data["body"] = "\n\n".join(paragraphs)

    return data


def parse_article(html: str, slug: str) -> dict[str, Any]:
    tree = HTMLParser(html)

    ld = from_jsonld(html)
    merged, refs = dom.merge_layers(
        [
            ("jsonld", ld),
            ("meta", from_meta(tree)),
            ("dom", from_dom(tree, ld.get("author"))),
        ]
    )

    body = merged.get("body")
    source = {
        "title": merged.get("title"),
        "schema_type": merged.get("schema_type"),
        "lead": merged.get("lead"),
        "body": body,
        "body_chars": len(body) if body else None,
        "headings": merged.get("headings", []),
        "rubric": merged.get("rubric"),
        "author": merged.get("author"),
        "photo_credit": merged.get("photo_credit"),
        "published_at": merged.get("published_at"),
        "published_date": merged.get("published_date"),
        "published_year": merged.get("published_year"),
        "modified_at": merged.get("modified_at"),
        "image_url": merged.get("image_url"),
        "wine_slugs": dom.links(html, "wines"),
        "winery_slugs": dom.links(html, "wineries"),
    }

    return {"slug": slug, "source": source, "_field_strategies": refs}
