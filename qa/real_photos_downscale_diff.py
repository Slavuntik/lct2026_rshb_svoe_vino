"""A/B двух прогонов `qa/real_photos_serve.py --flat-only` по `flat_slug` и по времени.

Метрика — как у жюри: top-1 по точному слагу на 62 фото каталога
(`part*.csv`, `true_slug != NONE`). Отдельно — 38 фото NONE: там правильный ответ —
любой, кроме уверенной карточки, поэтому считается только СМЕНА ответа.

  packages/cv/.venv/bin/python qa/real_photos_downscale_diff.py A.jsonl B.jsonl [--labels-a A --labels-b B]
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

LAB = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels")


def labels() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for p in sorted(LAB.glob("part[12].csv")):
        with p.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows[r["photo"]] = r
    return rows


def load(path: str) -> dict[str, dict]:
    return {json.loads(l)["photo"]: json.loads(l)
            for l in Path(path).read_text().splitlines() if l.strip()}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--names", nargs="*", default=[])
    a = ap.parse_args()
    names = a.names or [Path(p).stem for p in a.runs]
    runs = [load(p) for p in a.runs]
    lab = labels()
    in_cat = [p for p, r in lab.items() if r["true_slug"] != "NONE"]
    none = [p for p, r in lab.items() if r["true_slug"] == "NONE"]
    print(f"размеченных фото {len(lab)}: каталог {len(in_cat)}, NONE {len(none)}")

    print(f"\n{'прогон':16s} {'top-1 (62)':>12s} {'ошибок':>7s} {'p50':>7s} {'p95':>7s} {'max':>7s} {'сумма, с':>9s}")
    for name, rows in zip(names, runs):
        hits = sum(rows.get(p, {}).get("flat_slug") == lab[p]["true_slug"] for p in in_cat)
        ms = [r["flat_ms"] for r in rows.values() if "flat_ms" in r]
        err = sum(1 for r in rows.values() if r.get("error"))
        print(f"{name:16s} {hits:3d}/{len(in_cat)} = {hits / len(in_cat):5.1%} {err:7d} "
              f"{pct(ms, 0.5):7.0f} {pct(ms, 0.95):7.0f} {max(ms):7.0f} {sum(ms) / 1000:9.1f}")

    if len(runs) < 2:
        return
    base, *others = runs
    bname, *onames = names
    for name, rows in zip(onames, others):
        print(f"\n== пофотографийная разница {bname} -> {name} ==")
        for group, title in ((in_cat, "каталог (62, метрика жюри)"), (none, "NONE (38, вне каталога)")):
            diffs = [p for p in group if base.get(p, {}).get("flat_slug") != rows.get(p, {}).get("flat_slug")]
            print(f"  {title}: расхождений {len(diffs)}")
            for p in sorted(diffs):
                truth = lab[p]["true_slug"]
                x, y = base.get(p, {}).get("flat_slug"), rows.get(p, {}).get("flat_slug")
                verdict = ("улучшение" if y == truth else "РЕГРЕСС" if x == truth else "оба неверны")
                if truth == "NONE":
                    verdict = f"{'пусто' if not x else 'карточка'} -> {'пусто' if not y else 'карточка'}"
                print(f"    {p}")
                print(f"      истина: {truth}")
                print(f"      {bname}: {x or '(пусто)'}")
                print(f"      {name}: {y or '(пусто)'}   [{verdict}]")
        # промахи каждого варианта на 62
        for nm, rr in ((bname, base), (name, rows)):
            miss = [p for p in in_cat if rr.get(p, {}).get("flat_slug") != lab[p]["true_slug"]]
            print(f"  промахи {nm} на 62: {len(miss)} -> {', '.join(sorted(m.split('_')[0] for m in miss))}")


if __name__ == "__main__":
    main()
