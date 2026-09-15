"""Генерация фикстур web/mocks/ для режима NUXT_PUBLIC_MOCK=1.

Берёт реальный каталог (artifacts/catalog/catalog.jsonl), сохранённые ответы /v1/scan
(artifacts/eval/participant_public/scan_*.json) и прогоняет детерминированный код аналогов и
«Цифрового сомелье» из src/winescan/product — только CPU, без запуска сервиса и моделей.

Запуск из корня репозитория:  .venv/bin/python web/scripts/generate_mocks.py
"""

import json
from pathlib import Path

from winescan.product.analogs import AnalogFinder
from winescan.product.sommelier import DISHES, Sommelier, SommelierRequest, questions

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "mocks"
SCAN_FOUND = ROOT / "artifacts/eval/participant_public/scan_02eef911.json"
SCAN_NOT_FOUND = ROOT / "artifacts/eval/participant_public/scan_019c68d0.json"
PER_KEY = 9  # три страницы «Другие варианты»
CATEGORIES = ("", "Белое", "Красное", "Розовое", "Оранжевое")
BODIES = ("", "light", "full")


def dump(name: str, value, pretty: bool = False) -> None:
    options = {"indent": 1} if pretty else {"separators": (",", ":")}
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, **options) + "\n", encoding="utf8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cards = {}
    with open(ROOT / "artifacts/catalog/catalog.jsonl", encoding="utf8") as f:
        for line in f:
            card = json.loads(line)
            cards[card["slug"]] = card

    scan_found = json.loads(SCAN_FOUND.read_text(encoding="utf8"))
    scan_not_found = json.loads(SCAN_NOT_FOUND.read_text(encoding="utf8"))
    dump("scan_found.json", scan_found, pretty=True)
    dump("scan_not_found.json", scan_not_found, pretty=True)
    dump("questions.json", {"questions": questions()}, pretty=True)

    # подборки сомелье: ключ «блюдо|цвет|стиль»; сладость мок фильтрует сам по карточкам
    sommelier = Sommelier(cards)
    suggest = {}
    for dish in DISHES:
        for category in CATEGORIES:
            for body in BODIES:
                chosen, exclude = [], []
                while len(chosen) < PER_KEY:
                    request = SommelierRequest(
                        dish=dish, category=category or None, body=body or None, exclude_slugs=tuple(exclude)
                    )
                    batch = sommelier.suggest(request, limit=3)
                    if not batch:
                        break
                    chosen += [s.as_dict() for s in batch]
                    exclude += [s.slug for s in batch]
                suggest[f"{dish}|{category}|{body}"] = chosen
    dump("suggest.json", suggest)

    # аналоги: для найденного вина, кандидатов top-5 обоих сканов и первых подборок сомелье
    finder = AnalogFinder(cards)
    base = [scan_found["slug"]] + [c["slug"] for c in scan_found["top5"]] + [c["slug"] for c in scan_not_found["top5"]]
    base += sorted({s["slug"] for key, items in suggest.items() if key.endswith("||") for s in items[:3]})
    base = list(dict.fromkeys(base))
    analogs = {slug: [a.as_dict() for a in finder.find(slug, limit=6)] for slug in base}
    dump("analogs.json", analogs)

    referenced = set(base)
    referenced |= {s["slug"] for items in suggest.values() for s in items}
    referenced |= {a["slug"] for items in analogs.values() for a in items}
    dump("wines.json", {slug: cards[slug] for slug in sorted(referenced)})
    print(f"wines={len(referenced)} analog_bases={len(analogs)} suggest_keys={len(suggest)} -> {OUT}")


if __name__ == "__main__":
    main()
