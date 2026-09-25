"""Offline model-only ablations. Annotations are deliberately not accepted as input."""

import argparse, json, time
from pathlib import Path
from PIL import Image, ImageOps
from shelf_api.accuracy import AccuracyEngine

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--photos", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--models", type=Path, default=Path("artifacts/server-models"))
p.add_argument(
    "--accuracy-models", type=Path, default=Path("artifacts/accuracy/models")
)
p.add_argument("--semantic", type=Path)
p.add_argument("--semantic-scope", choices=["all", "selected"], default="selected")
p.add_argument("--files", nargs="*")
p.add_argument("--limit", type=int, default=12)
p.add_argument("--budget", type=float, default=60)
p.add_argument("--no-labels", action="store_true")
p.add_argument("--ocr", action="store_true")
p.add_argument("--no-semantic-veto", action="store_true")
a = p.parse_args()
engine = AccuracyEngine(
    a.models,
    a.accuracy_models,
    rescue_limit=a.limit,
    budget_seconds=a.budget,
    use_labels=not a.no_labels,
    use_ocr=a.ocr,
    semantic_dir=a.semantic,
    semantic_scope=a.semantic_scope,
    semantic_veto=not a.no_semantic_veto,
)
result = {"manualReview": False, "mode": "offline native accuracy", "rows": []}
a.output.parent.mkdir(parents=True, exist_ok=True)
for file in a.files or sorted(p.name for p in a.photos.glob("*.jpg")):
    with Image.open(a.photos / file) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    r = engine.scan(image, diagnostics=True)
    r["file"] = file
    r["image"] = {"width": image.width, "height": image.height}
    result["rows"].append(r)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        file,
        len(r["matches"]),
        round(r["timingsMs"]["processing"] / 1000, 2),
        flush=True,
    )
