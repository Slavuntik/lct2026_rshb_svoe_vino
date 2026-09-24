"""Prepare a local-only browser bundle; never uploads shelf photos or calls an LLM."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = 'mobilenet-v3-small-imagenet1k-v1-576-letterbox224'


def preprocess(image: Image.Image, size: int = 224) -> np.ndarray:
    image = ImageOps.exif_transpose(image).convert('RGB')
    ratio = min(size / image.width, size / image.height)
    resized = image.resize((round(image.width * ratio), round(image.height * ratio)), Image.Resampling.BILINEAR)
    canvas = Image.new('RGB', (size, size), 'white')
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    data = np.asarray(canvas, dtype=np.float32) / 255
    data = (data - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
    return data.transpose(2, 0, 1)[None]


def export_models(output: Path, weights: str) -> None:
    import torch
    from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
    from ultralytics import YOLO

    torch.set_num_threads(4)
    detector_path = output / 'bottle-detector.onnx'
    if not detector_path.exists():
        detector = YOLO(weights)
        # COCO bottle is class 39. A custom front-label model must adapt its manifest class ID.
        if detector.names.get(39) != 'bottle':
            raise ValueError('Expected COCO weights with bottle class 39')
        exported = detector.export(format='onnx', imgsz=640, batch=1, dynamic=False, simplify=False, opset=17, device='cpu', nms=False)
        shutil.copyfile(exported, detector_path)
    embedder_path = output / 'bottle-embedding.onnx'
    if not embedder_path.exists():
        model = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1).eval()
        encoder = torch.nn.Sequential(model.features, model.avgpool, torch.nn.Flatten(1)).eval()
        torch.onnx.export(encoder, torch.zeros(1, 3, 224, 224), str(embedder_path), input_names=['image'], output_names=['embedding'], opset_version=17, dynamo=False)


def prepare_catalog(catalog: Path, images: Path, output: Path, limit: int, slugs: Path | None):
    import onnxruntime as ort
    options = ort.SessionOptions(); options.intra_op_num_threads = 4
    session = ort.InferenceSession(str(output / 'bottle-embedding.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
    wanted = set(slugs.read_text().splitlines()) if slugs else None
    rows = [json.loads(line) for line in catalog.read_text().splitlines() if line.strip()]
    valid = [r for r in rows if r.get('image', {}).get('file') and (images / r['image']['file']).is_file() and (wanted is None or r['slug'] in wanted)]
    # Deterministic coverage across wineries rather than silently taking one winery's first 150 SKUs.
    groups: dict[str, list[dict]] = {}
    for row in sorted(valid, key=lambda r: (r.get('winery', ''), r['slug'])):
        groups.setdefault(row.get('winery', ''), []).append(row)
    ordered = []
    while any(groups.values()):
        for group in groups.values():
            if group: ordered.append(group.pop(0))
    selected = ordered[:limit] if limit else ordered
    if wanted and {r['slug'] for r in selected} != wanted:
        raise ValueError('Some requested slugs are missing images or exceed --limit')
    coverage = {'catalogRows': len(rows), 'availableReferences': len(valid), 'selectedReferences': len(selected), 'missingReferenceSlugs': [row['slug'] for row in rows if row['slug'] not in {r['slug'] for r in valid}]}
    (output / 'coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2)+'\n')
    wines = []
    for position, row in enumerate(selected, 1):
        path = (images / row['image']['file']).resolve()
        if not path.is_relative_to(images.resolve()): raise ValueError('Reference path escapes image directory')
        with Image.open(path) as image:
            vector = session.run(None, {session.get_inputs()[0].name: preprocess(image)})[0].reshape(-1)
        vector = vector / max(np.linalg.norm(vector), 1e-12)
        if position % 100 == 0: print(f'Embedded {position}/{len(selected)} references', flush=True)
        wines.append({'id': row['slug'], 'name': row['name'], 'brand': row.get('winery', ''), 'region': row.get('region', ''), 'group': row.get('category', '') or 'Без группы', 'references': [[round(float(x), 6) for x in vector]]})
    if not wines: raise ValueError('No reference images available')
    result = {'version': 1, 'embeddingModel': MODEL_ID, 'dimension': 576, 'wines': wines}
    (output / 'catalog.json').write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return len(wines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog', type=Path, required=True, help='WineScan catalog.jsonl')
    p.add_argument('--images', type=Path, required=True, help='Strapi uploads directory')
    p.add_argument('--output', type=Path, default=ROOT / 'public/models')
    p.add_argument('--weights', default='yolo11n.pt')
    p.add_argument('--limit', type=int, default=0, help='0 = all available references (default); a positive limit is opt-in')
    p.add_argument('--slugs', type=Path, help='One desired SKU slug per line')
    args = p.parse_args()
    if args.limit < 0: p.error('--limit must be nonnegative')
    args.output.mkdir(parents=True, exist_ok=True)
    export_models(args.output, args.weights)
    count = prepare_catalog(args.catalog, args.images, args.output, args.limit, args.slugs)
    files = ['bottle-detector.onnx', 'bottle-embedding.onnx', 'catalog.json']
    manifest = {'version': 1, 'detector': files[0], 'embedder': files[1], 'catalog': files[2], 'embeddingModel': MODEL_ID, 'dimension': 576, 'detectorSize': 640, 'bottleClass': 39, 'hashes': {name: hashlib.sha256((args.output / name).read_bytes()).hexdigest() for name in files}, 'notes': 'Baseline COCO bottle detector and ImageNet embeddings; SKU thresholds are not field-calibrated.', 'detectorLicense': 'Ultralytics AGPL-3.0; see https://www.ultralytics.com/license', 'references': count}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'references': count, 'output': str(args.output), 'modelBytes': sum((args.output/name).stat().st_size for name in files)}))

if __name__ == '__main__': main()
