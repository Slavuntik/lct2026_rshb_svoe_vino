"""Copy an existing organizer-only semantic gallery into a standalone, checksummed bundle."""

import argparse, hashlib, json, shutil
from pathlib import Path
from huggingface_hub import snapshot_download

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--index", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
model = "google/siglip2-so400m-patch14-384"
snapshot = Path(
    snapshot_download(
        model,
        revision="e8e487298228002f3d8a82e0cd5c8ea9c567f57f",
        local_files_only=True,
    )
)
(a.output / "encoder").mkdir(exist_ok=True)
for f in snapshot.iterdir():
    if f.is_file() and f.suffix in [".json", ".safetensors", ".txt", ".model"]:
        shutil.copy2(f, a.output / "encoder" / f.name)
for view, suffix in [("full", ""), ("label", "__label")]:
    source = a.index / ("siglip2-so400m-patch14-384" + suffix + "__yaw-30_-15_0_15_30")
    shutil.copytree(source, a.output / view, dirs_exist_ok=True)
manifest = {
    "model": model,
    "sourceRevision": snapshot.name,
    "hashes": {
        f.relative_to(a.output).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in a.output.rglob("*")
        if f.is_file() and f.name != "manifest.json"
    },
}
(a.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
print("Prepared semantic gallery", snapshot.name)
