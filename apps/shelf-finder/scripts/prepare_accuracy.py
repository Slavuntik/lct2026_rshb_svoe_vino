"""Build optional stronger matcher and label reference views; organizer images only."""

import argparse, hashlib, json, sys, subprocess
from pathlib import Path
import numpy as np
from PIL import Image
import torch

p = argparse.ArgumentParser(description=__doc__)
for n in ["models", "source", "weights", "references", "catalog", "csv", "output"]:
    p.add_argument("--" + n, type=Path, required=True)
a = p.parse_args()
revision = subprocess.check_output(
    ["git", "-C", str(a.source), "rev-parse", "HEAD"], text=True
).strip()
if revision != "d12b4ba1632f558234e3f084e1f3d8bdf9147890":
    raise ValueError("Use the documented LightGlue ONNX revision")
sys.path[:0] = [
    str(a.source.resolve()),
    str(Path(__file__).resolve().parents[1] / "server/src"),
]
from lightglue_dynamo.models.lightglue import LightGlue
from shelf_api.engine import ShelfEngine

torch.set_num_threads(4)
a.output.mkdir(parents=True, exist_ok=True)
engine = ShelfEngine(a.models)
matcher = LightGlue(a.weights, input_dim=128, n_layers=5).eval().cuda()
with torch.inference_mode():
    k = torch.rand(2, 512, 2, device="cuda") * 2 - 1
    d = torch.nn.functional.normalize(torch.rand(2, 512, 128, device="cuda"), dim=-1)
    k[1] = k[0] + 0.01
    d[1] = d[0]
    traced = torch.jit.trace(matcher, (k, d), check_trace=False)
    traced.save(str(a.output / "aliked-matcher.pt"))
    for pairs in [1, 2, 4, 8]:
        k = torch.rand(2 * pairs, 512, 2, device="cuda") * 2 - 1
        d = torch.nn.functional.normalize(
            torch.rand(2 * pairs, 512, 128, device="cuda"), dim=-1
        )
        k[1::2] = k[::2] + 0.01
        d[1::2] = d[::2]
        actual = traced(k, d)
        expected = matcher(k, d)
        for x, y in zip(actual, expected):
            torch.testing.assert_close(x, y, rtol=1e-4, atol=1e-5)
        for i in range(pairs):
            torch.testing.assert_close(
                actual[0][actual[0][:, 0] == i][:, 1:],
                matcher(k[i * 2 : i * 2 + 2], d[i * 2 : i * 2 + 2])[0][:, 1:],
            )
    refs = {}
    vectors = []
    for i, slug in enumerate(engine.ids):
        with Image.open(a.references / (slug + ".png")) as im:
            f = engine.extract(
                im.convert("RGB"), [0, 0.32, 1, 0.98], engine.retriever, 0.2
            )
        file = hashlib.sha256(slug.encode()).hexdigest()[:24] + ".npz"
        np.savez_compressed(
            a.output / file,
            points=f.points,
            descriptors=f.descriptors.cpu().numpy(),
            size=f.size,
        )
        refs[slug] = file
        vectors.append(
            torch.stack([engine.vlad(f, False), engine.vlad(f, True)]).cpu().numpy()
        )
        if (i + 1) % 250 == 0:
            print("Label references", i + 1, flush=True)
    np.save(a.output / "label-vectors.npy", np.stack(vectors))
import csv, collections

source = list(csv.DictReader(a.csv.open()))
by_slug = collections.defaultdict(set)
for r in source:
    by_slug[r["Slug"].strip()].add(r["Название фото"].strip())
cards = {r["slug"]: r for r in map(json.loads, a.catalog.read_text().splitlines())}
groups = collections.defaultdict(list)
for slug in engine.ids:
    groups[cards[slug]["image"]["file"]].append(slug)
metadata = {
    "referenceFiles": refs,
    "sharedImages": [v for v in groups.values() if len(v) > 1],
    "catalogNames": {
        s: {
            "name": cards[s]["name"],
            "brand": cards[s].get("winery", ""),
            "grapes": cards[s].get("grapes", []),
        }
        for s in engine.ids
    },
    "coverage": {
        "sku": len(engine.ids),
        "csvRows": len(source),
        "uniquePhotoNames": sum(map(len, by_slug.values())),
        "skuWithMultipleSourceNames": sum(len(v) > 1 for v in by_slug.values()),
        "uniqueSelectedFiles": len(groups),
    },
    "matcherBatchChecks": [1, 2, 4, 8],
}
(a.output / "references.json").write_text(json.dumps(metadata, ensure_ascii=False))
manifest = {
    "version": 1,
    "sourceRevision": revision,
    "matcherWeightsSha256": hashlib.sha256(a.weights.read_bytes()).hexdigest(),
    "baseManifestSha256": hashlib.sha256(
        (a.models / "server-manifest.json").read_bytes()
    ).hexdigest(),
    "hashes": {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest()
        for f in a.output.iterdir()
        if f.is_file() and f.name != "manifest.json"
    },
}
(a.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
print(metadata["coverage"], flush=True)
