"""Синхронизация пакета `packages/winescan` с исходным репозиторием winescan.

Зачем. Пакет приехал в монорепо из отдельного репозитория (LCT 2026), где продолжается
работа: измерения, пресеты синтетики, правки поиска. Его полная копия с историей
теперь лежит в standalone/winescan; это источник по умолчанию. Разовое копирование разошлось бы за
день, поэтому перенос выполняется этим скриптом, а не руками, и каждый перенос записывает,
из какого коммита источника собран пакет (`packages/winescan/SYNC.md`).

Правила:

* источник истины для кода пакета — standalone/winescan (или явный --source); правки, сделанные прямо в монорепо,
  скрипт покажет как расхождение и не затрёт молча (для переноса нужен явный ``--apply``);
* всё, что относится к интеграции (адаптер под контракт `ImageIndex`, эта папка `tools/`),
  живёт только в монорепо и в сверке не участвует — список в `INTEGRATION_ONLY`;
* скрипт ничего не коммитит: он только переносит файлы и печатает отчёт.

Запуск::

    python tools/sync_winescan.py --check                     # что разошлось
    python tools/sync_winescan.py --apply                     # перенести из источника
    python tools/sync_winescan.py --check --source /путь/репо # другой путь к источнику
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "winescan"
DEFAULT_SOURCE = REPO_ROOT / "standalone" / "winescan"

# (путь в репозитории-источнике, путь в монорепо)
TRACKED = (
    ("src/winescan", "winescan"),
    ("tests", "tests"),
    ("configs", "configs"),
)

# файлы монорепо, которых в источнике нет и быть не должно: интеграционный слой и метаданные
INTEGRATION_ONLY = (
    "winescan/integration",
    "tests/test_vinchik_index.py",
    "pyproject.toml",
    "README.md",
    "SYNC.md",
)

IGNORED_NAMES = ("__pycache__", ".pytest_cache", ".DS_Store")


@dataclass
class Diff:
    only_in_source: list[str]
    only_in_package: list[str]
    changed: list[str]

    @property
    def empty(self) -> bool:
        return not (self.only_in_source or self.only_in_package or self.changed)

    def report(self) -> str:
        if self.empty:
            return "пакет совпадает с источником"
        lines = []
        for title, items in (("только в источнике", self.only_in_source),
                             ("только в монорепо", self.only_in_package),
                             ("различаются", self.changed)):  # fmt: skip
            if items:
                lines.append(f"{title} ({len(items)}):")
                lines += [f"    {name}" for name in sorted(items)[:40]]
                if len(items) > 40:
                    lines.append(f"    ... и ещё {len(items) - 40}")
        return "\n".join(lines)


def _files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    found = set()
    for path in root.rglob("*"):
        if path.is_file() and not any(part in IGNORED_NAMES for part in path.parts):
            found.add(str(path.relative_to(root)))
    return found


def _is_integration_only(relative: str) -> bool:
    return any(relative == name or relative.startswith(f"{name}/") for name in INTEGRATION_ONLY)


def compare(source: Path) -> Diff:
    only_source, only_package, changed = [], [], []
    for source_rel, package_rel in TRACKED:
        source_dir, package_dir = source / source_rel, PACKAGE_ROOT / package_rel
        source_files, package_files = _files(source_dir), _files(package_dir)
        for name in sorted(source_files - package_files):
            only_source.append(f"{package_rel}/{name}")
        for name in sorted(package_files - source_files):
            relative = f"{package_rel}/{name}"
            if not _is_integration_only(relative):
                only_package.append(relative)
        for name in sorted(source_files & package_files):
            if not filecmp.cmp(source_dir / name, package_dir / name, shallow=False):
                changed.append(f"{package_rel}/{name}")
    return Diff(only_source, only_package, changed)


def source_commit(source: Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(source), "log", "-1", "--format=%H %ad %s", "--date=short", "--", "."],
                                capture_output=True, text=True, check=True)  # fmt: skip
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "неизвестен (источник не является git-репозиторием)"


def apply(source: Path) -> None:
    """Переносит отслеживаемые каталоги, сохраняя интеграционный слой монорепо."""
    for source_rel, package_rel in TRACKED:
        source_dir, package_dir = source / source_rel, PACKAGE_ROOT / package_rel
        if not source_dir.exists():
            raise SystemExit(f"нет каталога источника: {source_dir}")
        keep = {name for name in _files(package_dir) if _is_integration_only(f"{package_rel}/{name}")}
        saved = {name: (package_dir / name).read_bytes() for name in keep}
        shutil.rmtree(package_dir, ignore_errors=True)
        shutil.copytree(source_dir, package_dir, ignore=shutil.ignore_patterns(*IGNORED_NAMES))
        for name, blob in saved.items():
            target = package_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
    (PACKAGE_ROOT / "SYNC.md").write_text(
        "# Синхронизация пакета winescan\n\n"
        "Файл пишется `tools/sync_winescan.py --apply`; правки руками бессмысленны.\n\n"
        f"- источник: `{source}`\n"
        f"- коммит источника: `{source_commit(source)}`\n"
        f"- отслеживаются: {', '.join(f'`{s}` -> `{p}`' for s, p in TRACKED)}\n"
        "- интеграционный слой монорепо (в сверке не участвует): "
        f"{', '.join(f'`{name}`' for name in INTEGRATION_ONLY)}\n\n"
        "Проверить расхождения: `python tools/sync_winescan.py --check`.\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="корень репозитория winescan")
    parser.add_argument("--check", action="store_true", help="только показать расхождения")
    parser.add_argument("--apply", action="store_true", help="перенести из источника в пакет")
    args = parser.parse_args()
    if args.check == args.apply:
        raise SystemExit("нужен ровно один режим: --check или --apply")

    source = args.source.expanduser()
    if not source.exists():
        raise SystemExit(f"источник не найден: {source}")

    if args.check:
        difference = compare(source)
        print(f"источник: {source}\nкоммит:   {source_commit(source)}\n")
        print(difference.report())
        raise SystemExit(0 if difference.empty else 1)

    apply(source)
    print(f"перенесено из {source}\nкоммит источника: {source_commit(source)}")
    remaining = compare(source)
    print(remaining.report())


if __name__ == "__main__":
    main()
