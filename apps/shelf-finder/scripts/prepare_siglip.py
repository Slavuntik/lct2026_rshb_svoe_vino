"""Prepare SigLIP2 Base for browser inference; gallery is computed offline on catalog references only.

Requires the standalone WineScan environment/PYTHONPATH. No shelf images or review labels
are used here. FP16 gallery features and q4 query features share the same pretrained model;
quantization drift must be measured in the browser audit.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from pathlib import Path
import numpy as np
from PIL import Image
from huggingface_hub import hf_hub_download
from winescan.vision.embedder import ImageEmbedder
from winescan.vision.preprocess import reference_view

MODEL_ID = 'siglip2-base-patch16-224-full-label-q4-v1'
REPO = 'onnx-community/siglip2-base-patch16-224-ONNX'
REVISION = 'ba1f3b0843f24bc5417d38e19c37b287d719b2f4'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['catalog', 'images', 'baseline', 'output']:
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--device', default='cuda')
    p.add_argument('--reference-cache', type=Path, help='Optional WineScan catalog cutouts (<slug>.png), never shelf crops')
    args = p.parse_args()
    if args.output.resolve() == args.baseline.resolve():
        raise ValueError('Use a separate output directory to retain the baseline bundle')
    args.output.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in args.catalog.read_text().splitlines() if line.strip()]
    embedder = ImageEmbedder('google/siglip2-base-patch16-224', device=args.device)
    vectors = []
    for view in ['full', 'label']:
        chunks = []
        for start in range(0, len(rows), 32):
            images = []
            for row in rows[start:start+32]:
                source = args.reference_cache or args.images
                path = (source / (row['slug']+'.png' if args.reference_cache else row['image']['file'])).resolve()
                if not path.is_relative_to(source.resolve()): raise ValueError('Reference escapes image directory')
                with Image.open(path) as image:
                    image.thumbnail((800, 800))
                    images.append(reference_view(image, view).resize((224, 224), Image.Resampling.BILINEAR))
            chunks.append(embedder.embed(images, batch_size=32))
            print(view, min(start+32,len(rows)), '/', len(rows), flush=True)
        vectors.append(np.concatenate(chunks))
    combined = np.concatenate(vectors, axis=1) / np.sqrt(2)
    wines = [{'id':r['slug'], 'name':r['name'], 'brand':r.get('winery',''), 'region':r.get('region',''), 'group':r.get('category','') or 'Без группы', 'references':[[round(float(x),6) for x in v]]} for r,v in zip(rows,combined)]
    (args.output/'catalog.json').write_text(json.dumps({'version':1,'embeddingModel':MODEL_ID,'dimension':1536,'wines':wines},ensure_ascii=False,separators=(',',':')))
    onnx = hf_hub_download(REPO,'onnx/vision_model_q4.onnx',revision=REVISION)
    shutil.copyfile(onnx,args.output/'siglip2-base-q4.onnx')
    baseline = json.loads((args.baseline/'manifest.json').read_text())
    shutil.copyfile(args.baseline/baseline['detector'],args.output/'bottle-detector.onnx')
    files = ['catalog.json','bottle-detector.onnx','siglip2-base-q4.onnx']
    manifest = {**baseline,'embedder':files[2],'embeddingModel':MODEL_ID,'dimension':1536,'preprocessing':'siglip-full-label','embeddingOutput':'pooler_output','references':len(wines),'threshold':.80,'margin':.03,'hashes':{n:hashlib.sha256((args.output/n).read_bytes()).hexdigest() for n in files},'modelSource':{'repo':REPO,'revision':REVISION},'notes':'Experimental SigLIP2 full/label retrieval. Thresholds are not calibrated on independent SKU ground truth; names are automatic hypotheses.'}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')

if __name__ == '__main__': main()
