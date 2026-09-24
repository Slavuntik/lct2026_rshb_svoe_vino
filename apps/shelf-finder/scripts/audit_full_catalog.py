"""Offline diagnostic using the existing WineScan gallery; NOT the browser model.

Outputs candidates, never treats uncalibrated cosine scores as confirmed identities.
Requires the standalone WineScan ML environment and its prebuilt artifacts.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
from winescan.search.multi import MultiIndexSearcher


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--photos', type=Path, required=True)
    p.add_argument('--detections', type=Path, required=True)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', default='cuda')
    args = p.parse_args()
    rows = [r for r in json.loads(args.detections.read_text())['rows'] if not r['dense']]
    cards = {r['slug']: r for r in map(json.loads, args.catalog.read_text().splitlines())}
    searcher = MultiIndexSearcher(['siglip2-so400m-patch14-384__yaw-30_-15_0_15_30', 'siglip2-so400m-patch14-384__label__yaw-30_-15_0_15_30'], [.5, .5], device=args.device)
    result = {'engine': 'WineScan SigLIP2 so400m full+label multiview; offline diagnostic only', 'catalogSize': len(searcher.wine_slugs), 'confirmedIdentities': None, 'rows': []}
    for row in rows:
        with Image.open(args.photos/row['file']) as original: image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1920,1920))
        detections = [b for b in row['boxes'] if not b['tooSmall']]
        vectors = searcher.embed_views([image]*len(detections), [tuple(b['box']) for b in detections], batch_size=16)
        scores = searcher.wine_scores(vectors)
        proposals = []
        for box, values in zip(detections, scores):
            indices = np.argsort(values)[::-1][:3]
            candidates = []
            for i in indices:
                slug = searcher.wine_slugs[i]; card = cards[slug]
                candidates.append({'id': slug, 'name': card['name'], 'brand': card.get('winery',''), 'reference': card['image']['file'], 'score': float(values[i])})
            proposals.append({'box': box['box'], 'candidates': candidates, 'confirmed': False})
        result['rows'].append({'file':row['file'], 'width':row['width'], 'height':row['height'], 'proposals':proposals})
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        top=max((b['candidates'][0]['score'] for b in proposals),default=0)
        print(row['file'],len(proposals),'crops, best cosine',round(top,3),flush=True)

if __name__ == '__main__': main()
