#!/usr/bin/env python3
"""Сверка дизайн-токенов с contracts/tokens.css v0.2.1.

Зачем именно это. Копий токенов в репозитории больше нет: `apps/shelf-finder/src/tokens.css`
— это `@import` реализации, `/legal/tokens.css` генерируется из неё плагином Vite. Импорты
сняли расхождение между потребителями, поэтому сверка «пяти копий» (v0.2) стала бессмысленной
и была бы худшим видом проверки — зелёной всегда. Осталось ровно три вещи, которые импорт НЕ
решает, и скрипт стережёт только их:

  1. КОНТРАКТ ↔ РЕАЛИЗАЦИЯ. `contracts/tokens.css` — отдельный файл, и он расходится с кодом
     молча: именно так v0.1 разъехался на 17 значений из 27 и месяц никто не заметил.
  2. КОПИЯ НЕ ЗАВЕЛАСЬ СНОВА. Любой CSS в `apps/**`, объявляющий ≥8 имён контракта мимо
     реализации, — вернувшаяся копия. За один день 27.09 её воссоздавали дважды, так что
     правило «импорт, а не копия» без сторожа не держится.
  3. СХЕМА И ПАЛИТРА НЕ РАЗЪЕЗЖАЮТСЯ (правило R2). Кто объявляет `color-scheme: light`, тот
     обязан быть исключён из тёмного блока. Ровно этот дефект тимлид снял с боевого стенда
     27.09: `--warn-bg #2e2820`, `--ok-bg #222a26`, `color-scheme: dark` на светлой странице.

Запуск (без зависимостей, ~0.05 с)::

    python3 contracts/check_tokens.py           # 0 — порядок, 1 — список расхождений
    python3 contracts/check_tokens.py --root .  # другой корень репозитория

Скрипт ничего не чинит и не пишет — только печатает отчёт.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "contracts/tokens.css"
IMPLEMENTATION = "apps/web/src/styles/tokens.css"

LIGHT = ":root"
DARK_MEDIA = ':root:not([data-theme="light"]):not([data-theme="portal"])'
DARK_ATTR = ':root[data-theme="dark"]'

# Правило 2: сколько имён контракта в одном файле уже означает «копия», а не точечное
# переопределение. themes/portal.css законно объявляет 4 (+color-scheme).
COPY_THRESHOLD = 8

# Правило 3: (файл с пином схемы, имя темы, где искать подтверждение).
# Тема `light` подтверждается атрибутом в index.html обоих приложений.
SCHEME_PINS = (
    ("apps/web/src/themes/portal.css", "portal"),
    ("apps/web/index.html", "light"),
    ("apps/shelf-finder/index.html", "light"),
)

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
DECL_RE = re.compile(r"(--[\w-]+|color-scheme)\s*:\s*([^;}]+)")
NOT_THEME_RE = re.compile(r':not\(\[data-theme="([^"]+)"\]\)')
HTML_THEME_RE = re.compile(r'<html[^>]*\bdata-theme="([^"]+)"')


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub(" ", text)


def find_block(text: str, selector: str) -> dict[str, str] | None:
    """Объявления блока с данным селектором. None — блока в файле нет."""
    for needle in (selector + " {", selector + "{"):
        start = text.find(needle)
        if start >= 0:
            break
    else:
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
    return {n: " ".join(v.split()) for n, v in DECL_RE.findall(text[open_brace + 1 : i])}


def load(path: Path, selector: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    return find_block(strip_comments(path.read_text(encoding="utf-8")), selector)


def rule_contract_vs_implementation(root: Path, problems: list[str]) -> dict[str, str]:
    """Правило 1. Возвращает нормативный светлый набор (нужен правилу 2)."""
    contract, impl = root / CONTRACT, root / IMPLEMENTATION
    light: dict[str, str] = {}
    for selector in (LIGHT, DARK_MEDIA, DARK_ATTR):
        expected = load(contract, selector)
        actual = load(impl, selector)
        if expected is None:
            problems.append(f"{CONTRACT}: нет блока {selector}")
            continue
        if selector == LIGHT:
            light = expected
        if actual is None:
            problems.append(
                f"{IMPLEMENTATION}: нет блока {selector} — он есть в контракте (правило 1)"
            )
            continue
        for name, value in expected.items():
            if name not in actual:
                problems.append(f"{IMPLEMENTATION} [{selector}]: нет {name} (правило 1)")
            elif actual[name] != value:
                problems.append(
                    f"{IMPLEMENTATION} [{selector}]: {name} = {actual[name]}, "
                    f"контракт = {value} (правило 1)"
                )
        for name in actual:
            if name not in expected:
                problems.append(
                    f"{IMPLEMENTATION} [{selector}]: {name} — нет в контракте; "
                    f"ратифицировать или убрать (правила 1 и R5)"
                )
    return light


def rule_no_new_copies(root: Path, light: dict[str, str], problems: list[str]) -> int:
    """Правило 2. Возвращает число просмотренных файлов."""
    names = set(light)
    seen = 0
    for path in sorted((root / "apps").rglob("*.css")):
        rel = path.relative_to(root).as_posix()
        if rel == IMPLEMENTATION or "node_modules" in rel or "/dist/" in rel:
            continue
        if "/android/" in rel or "/ios/" in rel:  # собранные артефакты Capacitor
            continue
        seen += 1
        declared = {n for n, _ in DECL_RE.findall(strip_comments(path.read_text(encoding="utf-8")))}
        hits = declared & names
        if len(hits) >= COPY_THRESHOLD:
            problems.append(
                f"{rel}: объявляет {len(hits)} имён контракта — это копия. Подключись "
                f'импортом `@import "…/{IMPLEMENTATION.split("/")[-1]}"` (правило 2)'
            )
    return seen


def rule_scheme_matches_palette(root: Path, problems: list[str]) -> None:
    """Правило 3 (R2): кто пинит color-scheme: light — исключён из тёмного блока."""
    impl_text = strip_comments((root / IMPLEMENTATION).read_text(encoding="utf-8"))
    dark_selector = ""
    for line in impl_text.splitlines():
        if ":root" in line and ":not(" in line and "{" in line:
            dark_selector = line
            break
    excluded = set(NOT_THEME_RE.findall(dark_selector))
    if not excluded:
        problems.append(
            f"{IMPLEMENTATION}: тёмный блок никого не исключает — любой потребитель с "
            f"`color-scheme: light` получит тёмную палитру (правило 3/R2)"
        )
        return
    for rel, theme in SCHEME_PINS:
        path = root / rel
        if not path.exists():
            problems.append(f"{rel}: файла нет, а правило 3 на него ссылается")
            continue
        text = path.read_text(encoding="utf-8")
        if rel.endswith(".html"):
            found = HTML_THEME_RE.search(text)
            if not found or found.group(1) != theme:
                problems.append(
                    f'{rel}: <html> без data-theme="{theme}" — на тёмной ОС приложение '
                    f"получит тёмную палитру при светлой схеме (правило 3/R2)"
                )
                continue
        elif "color-scheme" not in strip_comments(text):
            continue  # схему не пинит — правило не про него
        if theme not in excluded:
            problems.append(
                f'{rel}: тема "{theme}" пинит светлую схему, но НЕ исключена из тёмного '
                f"блока {IMPLEMENTATION} (правило 3/R2)"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT), help="корень репозитория")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    if not (root / CONTRACT).exists() or not (root / IMPLEMENTATION).exists():
        print(f"НЕТ ФАЙЛА: {CONTRACT} или {IMPLEMENTATION}")
        return 1

    problems: list[str] = []
    light = rule_contract_vs_implementation(root, problems)
    scanned = rule_no_new_copies(root, light, problems)
    rule_scheme_matches_palette(root, problems)

    print(f"контракт: {len(light)} объявлений в :root; реализация — {IMPLEMENTATION}")
    print(f"просмотрено CSS в apps/ на предмет вернувшихся копий: {scanned}")
    if problems:
        print(f"\nРАСХОЖДЕНИЙ: {len(problems)}")
        for problem in problems:
            print(f"  - {problem}")
        print("\nПравка начинается в contracts/tokens.css, следом — реализация.")
        return 1
    print("расхождений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
