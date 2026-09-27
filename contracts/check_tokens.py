#!/usr/bin/env python3
"""Сверка копий дизайн-токенов с contracts/tokens.css v0.2.

Зачем. Общей сборки у потребителей нет: apps/web и apps/shelf-finder — отдельные проекты
Vite, страницы public/legal/ не проходят сборку вовсе, кросс-импорт `../../web/...` ломает
build одного приложения при отсутствии другого. Механизм синхронизации — копия + ЭТА
проверка (contracts/tokens.css, раздел «Потребители и сверка»), а не «сверим глазами».

Проверяются три правила:
  A. Ни один потребитель не объявляет токен контракта с ДРУГИМ значением и не изобретает
     собственных `--токенов` в блоке темы.
  B. Полные копии светлого блока объявляют ВЕСЬ нормативный набор имён.
  C. Блок, переопределяющий палитру (правило R2 контракта), объявляет все 15 литеральных
     цветов и `color-scheme` — частичное переопределение молча смешивает две темы.

Запуск (без зависимостей, ~0.05 с)::

    python3 contracts/check_tokens.py           # 0 — синхронно, 1 — список расхождений
    python3 contracts/check_tokens.py --root .  # другой корень репозитория

Правка начинается в contracts/tokens.css, потом расходится копиями. Скрипт ничего не
чинит и ничего не пишет — только печатает отчёт.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "contracts/tokens.css"

LIGHT = ":root"
DARK_MEDIA = ':root:not([data-theme="light"])'
DARK_ATTR = ':root[data-theme="dark"]'

# Псевдонимы: var()-ссылки на другие токены. В блок-переопределение темы НЕ входят —
# следуют за своей целью автоматически (см. правило R2 контракта).
ALIASES = ("--danger", "--danger-bg")

FULL_COPY = "full-copy"        # весь нормативный набор имён светлого блока
PALETTE_OVERRIDE = "palette"   # все литеральные цвета + color-scheme
VALUES_ONLY = "values"         # только правило A

# (путь, селектор, режим, тема-эталон)
CONSUMERS = (
    ("apps/web/src/styles/tokens.css", LIGHT, FULL_COPY, LIGHT),
    ("apps/web/src/styles/tokens.css", DARK_MEDIA, PALETTE_OVERRIDE, DARK_MEDIA),
    ("apps/web/src/styles/tokens.css", DARK_ATTR, PALETTE_OVERRIDE, DARK_ATTR),
    ("apps/web/public/legal/tokens.css", LIGHT, FULL_COPY, LIGHT),
    # у legal.css поверх стоит безусловный пин светлой темы, тёмный блок здесь инертен —
    # полноты не требуем, но значения обязаны совпадать
    ("apps/web/public/legal/tokens.css", DARK_MEDIA, VALUES_ONLY, DARK_MEDIA),
    ("apps/web/public/legal/tokens.css", DARK_ATTR, VALUES_ONLY, DARK_ATTR),
    ("apps/web/public/legal/legal.css", DARK_MEDIA, PALETTE_OVERRIDE, LIGHT),
    ("apps/web/src/themes/portal.css", ':root[data-theme="portal"]', PALETTE_OVERRIDE, LIGHT),
    ("apps/shelf-finder/src/tokens.css", LIGHT, FULL_COPY, LIGHT),
)

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
DECL_RE = re.compile(r"(--[\w-]+|color-scheme)\s*:\s*([^;}]+)")


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub(" ", text)


def find_block(text: str, selector: str) -> dict[str, str] | None:
    """Объявления блока с данным селектором. None — блока в файле нет."""
    needle = selector + " {"
    start = text.find(needle)
    if start < 0:
        needle = selector + "{"
        start = text.find(needle)
        if start < 0:
            return None
    open_brace = start + len(needle) - 1
    depth, i = 0, open_brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[open_brace + 1 : i]
    return {name: " ".join(value.split()) for name, value in DECL_RE.findall(body)}


def load(path: Path, selector: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    return find_block(strip_comments(path.read_text(encoding="utf-8")), selector)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT), help="корень репозитория")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    contract_path = root / CONTRACT
    if not contract_path.exists():
        print(f"НЕТ КОНТРАКТА: {contract_path}")
        return 1

    reference: dict[str, dict[str, str]] = {}
    for selector in (LIGHT, DARK_MEDIA, DARK_ATTR):
        block = load(contract_path, selector)
        if block is None:
            print(f"НЕТ БЛОКА {selector} в {CONTRACT}")
            return 1
        reference[selector] = block

    light = reference[LIGHT]
    colors = tuple(
        name
        for name, value in light.items()
        if name.startswith("--") and value.startswith("#") and name not in ALIASES
    )
    required_override = colors + ("color-scheme",)

    problems: list[str] = []
    checked = 0
    for rel, selector, mode, theme in CONSUMERS:
        path = root / rel
        block = load(path, selector)
        if block is None:
            if mode == VALUES_ONLY:
                continue  # необязательный блок (правило R3) — его отсутствие норма
            problems.append(f"{rel}: нет блока {selector}")
            continue
        checked += 1
        expected = reference[theme]
        where = f"{rel} [{selector}]"

        for name, value in block.items():
            if name not in expected:
                problems.append(f"{where}: {name} — нет в контракте (правило R5)")
            elif value != expected[name]:
                problems.append(
                    f"{where}: {name} = {value}, контракт = {expected[name]} (правило A)"
                )

        if mode == FULL_COPY:
            missing = [n for n in expected if n not in block]
            if missing:
                problems.append(f"{where}: не хватает {', '.join(missing)} (правило B)")
        elif mode == PALETTE_OVERRIDE:
            missing = [n for n in required_override if n not in block]
            if missing:
                problems.append(
                    f"{where}: переопределение палитры неполное, не хватает "
                    f"{', '.join(missing)} (правило R2/C)"
                )

    print(f"contracts/tokens.css: {len(light)} объявлений в :root, {len(colors)} литеральных цветов")
    print(f"проверено блоков: {checked} в {len({c[0] for c in CONSUMERS})} файлах")
    if problems:
        print(f"\nРАСХОЖДЕНИЙ: {len(problems)}")
        for problem in problems:
            print(f"  - {problem}")
        print("\nПравка начинается в contracts/tokens.css, копии подтягиваются под неё.")
        return 1
    print("расхождений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
