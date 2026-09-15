"""Общие примитивы разбора HTML для всех парсеров каталога.

Здесь лежит то, что раньше дублировалось в parse_wine и что при этом
легко сделать неправильно — порядок обхода дерева и привязка значения
к текстовой метке.
"""

from __future__ import annotations

import json
import re
from typing import Any

from selectolax.parser import HTMLParser

# Теги, в которых на портале лежит осмысленный текст.
TEXT_TAGS = {"div", "span", "p", "li", "td", "dt", "dd", "h1", "h2", "h3", "h4"}


def text_blocks(tree: HTMLParser) -> list[str]:
    """Текстовые блоки страницы строго в порядке документа.

    Осторожно: tree.css("div, p") в selectolax возвращает совпадения
    СГРУППИРОВАННЫМИ ПО СЕЛЕКТОРУ, а не в порядке документа — для
    '<div>A</div><p>B</p><div>C</div>' получается [A, C, B]. Привязка
    «метка -> следующий блок» на таком порядке работает только когда метка
    и значение лежат в одинаковых тегах, и молча ломается во всех
    остальных случаях. Поэтому обходим дерево сами.

    Пробелы схлопываем: <p>Крепость<br>вина</p> иначе даёт «Крепостьвина»
    и перестаёт совпадать с меткой.
    """
    out: list[str] = []
    for node in tree.root.traverse(include_text=False):
        if node.tag not in TEXT_TAGS:
            continue
        t = re.sub(r"\s+", " ", node.text()).strip()
        if t:
            out.append(t)
    return out


def value_after(
    blocks: list[str], labels: tuple[str, ...], max_len: int = 400, look: int = 4
) -> str | None:
    """Значение, идущее за текстовой меткой.

    Метка часто дублируется (обёртка и внутренний span с тем же текстом),
    поэтому повторы самой метки пропускаем.
    """
    for i, b in enumerate(blocks):
        if b in labels or b.rstrip(":") in labels:
            for nxt in blocks[i + 1 : i + 1 + look]:
                if nxt and nxt not in labels and nxt.rstrip(":") not in labels:
                    return nxt if len(nxt) <= max_len else None
    return None


def meta(tree: HTMLParser, name: str) -> str | None:
    for sel in (f'meta[property="{name}"]', f'meta[name="{name}"]'):
        node = tree.css_first(sel)
        if node and node.attributes.get("content"):
            return node.attributes["content"].strip()
    return None


def jsonld(html: str, types: str | tuple[str, ...]) -> dict[str, Any] | None:
    """Первый блок application/ld+json, чей @type входит в types."""
    wanted = (types,) if isinstance(types, str) else types
    for raw in re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S
    ):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("@type") in wanted:
                return obj
    return None


def links(html: str, entity: str, exclude: str | None = None) -> list[str]:
    """Slug'и ссылок на сущность, в порядке появления, без повторов."""
    found = re.findall(rf"/{entity}/([a-z0-9_\-]+)", html)
    return [s for s in dict.fromkeys(found) if s != exclude and s not in {"map", "page"}]


def merge_layers(layers: list[tuple[str, dict[str, Any]]]) -> tuple[dict, list[dict]]:
    """Сливает результаты стратегий: побеждает более ранняя, более надёжная.

    Возвращает (данные, field_refs) — по каждому полю видно, какая стратегия
    его дала, и легко чинить при смене вёрстки.
    """
    merged: dict[str, Any] = {}
    refs: list[dict[str, str]] = []
    for strategy, payload in layers:
        for k, v in payload.items():
            if k not in merged and v not in (None, "", [], {}):
                merged[k] = v
                refs.append({"field": k, "strategy": strategy})
    return merged, refs
