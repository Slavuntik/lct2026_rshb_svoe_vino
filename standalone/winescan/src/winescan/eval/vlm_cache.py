"""Кэш полей этикетки, прочитанных VLM, для офлайн-слияния и оценки.

Запуск: ``python -m winescan.eval.vlm_cache --cache synth_v2 [--limit N]``

Для каждого запроса кэша берётся рамка (правило из box-selection.json или первая по весу),
кроп читается Qwen3-VL и поля пишутся в artifacts/cache/<cache>/vlm_fields.jsonl.
Файл дописывается: прерванный прогон продолжится с того же места.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from winescan.config import get_paths
from winescan.eval.offline import QueryCache, choose_slot
from winescan.eval.run import _load_query, load_split
from winescan.logging_setup import setup_logging
from winescan.vision.preprocess import crop_box
from winescan.vision.vlm import DEFAULT_VLM, LabelFieldReader

VLM_FILE = "vlm_fields.jsonl"


def load_fields(cache_name: str) -> dict[str, dict]:
    """query_id -> поля (как в search.fields.LabelFields) из кэша VLM, если он есть."""
    path = get_paths().artifacts_dir / "cache" / cache_name / VLM_FILE
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {row["query_id"]: row["fields"] for row in map(json.loads, fh)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Кэш полей этикетки (VLM)")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_VLM)
    parser.add_argument("--device", default=None)
    args = parser.parse_args(argv)
    setup_logging()

    paths = get_paths()
    cache_dir = paths.artifacts_dir / "cache" / args.cache
    cache = QueryCache.load(args.cache)
    rule_path = cache_dir / "box-selection.json"
    rule = json.loads(rule_path.read_text())["best_rule"] if rule_path.exists() else {"prior": 1.0, "top1": 0.0, "margin": 0.0}
    split = json.loads((cache_dir / "meta.json").read_text())["split"]
    _, images_dir = load_split(split)
    done = set(load_fields(args.cache))
    reader = LabelFieldReader(args.model, device=args.device)

    records = cache.records[: args.limit] if args.limit else cache.records
    with (cache_dir / VLM_FILE).open("a", encoding="utf-8") as fh:
        for query_index, record in enumerate(records):
            if record["query_id"] in done:
                continue
            slot = choose_slot(cache, query_index, rule)
            box = record["slots"][slot]["box"]
            image, _ = _load_query(images_dir / record["image_path"])
            fields = reader.read(crop_box(image, box) if box else image)
            fh.write(json.dumps({"query_id": record["query_id"], "fields": asdict(fields), "raw": reader.last_raw,
                                 "ms": round(reader.last_ms, 1)}, ensure_ascii=False) + "\n")  # fmt: skip
            fh.flush()
            if query_index % 50 == 0:
                print(f"{query_index + 1} / {len(records)}: {reader.last_ms:.0f} мс", flush=True)


if __name__ == "__main__":
    main()
