"""GPU shelf comparison: SigLIP2 full/label gallery + SIFT geometry, without inferred ground truth."""
from __future__ import annotations
import argparse
import base64
import hashlib
import html
import io
import json
import time
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps
from winescan.search.multi import MultiIndexSearcher
from winescan.search.local_match import extract, match


def thumbnail(image, size=(150, 210)):
    image = image.copy(); image.thumbnail(size)
    stream = io.BytesIO(); image.save(stream, format='JPEG', quality=80)
    return 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('photos', 'detections', 'catalog', 'images', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(4)
    indexes = ['siglip2-so400m-patch14-384__yaw-30_-15_0_15_30', 'siglip2-so400m-patch14-384__label__yaw-30_-15_0_15_30']
    searcher = MultiIndexSearcher(indexes, [.5, .5], device='cuda')
    cards = {r['slug']: r for r in map(json.loads, args.catalog.read_text().splitlines())}
    if set(cards) != set(searcher.wine_slugs): raise ValueError('Catalog and index SKU sets differ')
    inputs = json.loads(args.detections.read_text())
    rows = [r for r in inputs['rows'] if r['dense']]
    references = {}
    def reference(slug):
        if slug not in references:
            path = (args.images / cards[slug]['image']['file']).resolve()
            if not path.is_relative_to(args.images.resolve()): raise ValueError('Invalid reference path')
            with Image.open(path) as raw: im = ImageOps.exif_transpose(raw).convert('RGB')
            references[slug] = (extract(im), thumbnail(im))
        return references[slug]
    result = {'engine': 'SigLIP2 so400m full+label multiview + SIFT/MAGSAC top10 evidence', 'indexes': indexes, 'catalogSize':len(cards), 'groundTruth':None, 'detector':'Existing YOLO11n dense proposals, retained for paired browser comparison', 'recognitionInput':'Crops from original resolution; detection coordinates scaled from audit image', 'rows':[]}
    summaries = []
    for row in rows:
        started = time.perf_counter()
        path = args.photos / row['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']: raise ValueError('Photo changed since detection')
        with Image.open(path) as raw: image = ImageOps.exif_transpose(raw).convert('RGB')
        sx, sy = image.width/row['width'], image.height/row['height']
        boxes = [[b['box'][0]*sx,b['box'][1]*sy,b['box'][2]*sx,b['box'][3]*sy] for b in row['boxes']]
        vectors = searcher.embed_views([image]*len(boxes), boxes, batch_size=8)
        scores = searcher.wine_scores(vectors)
        proposals, blocks = [], []
        for number, (b, box, values) in enumerate(zip(row['boxes'], boxes, scores), 1):
            crop = image.crop(tuple(round(x) for x in box)); features = extract(crop)
            candidates = []
            for i in np.argsort(values)[::-1][:10]:
                slug = searcher.wine_slugs[i]; local, _ = reference(slug)
                evidence = match(features, local)
                candidates.append({'id':slug, 'name':cards[slug]['name'], 'brand':cards[slug].get('winery',''), 'score':float(values[i]), 'inliers':evidence.inliers, 'goodMatches':evidence.good_matches, 'reference':cards[slug]['image']['file']})
            # Geometry is reported independently; uncalibrated inliers do not establish identity.
            geometric = max(candidates, key=lambda c: (c['inliers'], c['score']))
            browser = b.get('candidates', [])
            proposals.append({'number':number, 'box':b['box'], 'originalBox':box, 'candidates':candidates, 'geometryBest':geometric['id'], 'maxInliers':geometric['inliers'], 'browserCandidates':browser, 'browserAcceptedId':b.get('id'), 'browserTooSmall':b['tooSmall'], 'sameTop1': bool(browser and browser[0]['id']==candidates[0]['id']), 'reviewedId':None})
            def card(c):
                _, src = reference(c['id'])
                return f'<figure><img src="{src}"><figcaption>{html.escape(c["brand"]+" / "+c["name"])}<br>cos={c["score"]:.3f}; SIFT={c["inliers"]}<br><small>{html.escape(c["id"])}</small></figcaption></figure>'
            browser_name = cards[browser[0]['id']]['name'] if browser else 'не сравнивался: мелкая область'
            shown = list(candidates[:3])
            if geometric['id'] not in {c['id'] for c in shown} and geometric['inliers'] >= 8: shown.append(geometric)
            blocks.append(f'<article id="b{number}"><h2>#{number} · MobileNet top-1 (Python/ONNX): {html.escape(browser_name)}</h2><div class="cards"><figure><img src="{thumbnail(crop)}"><figcaption>Вырезка витрины</figcaption></figure>{"".join(card(c) for c in shown)}</div></article>')
        outlines = ''.join(f'<rect x="{b[0]}" y="{b[1]}" width="{b[2]-b[0]}" height="{b[3]-b[1]}" fill="none" stroke="#ffd045" stroke-width="2"/><text x="{b[0]}" y="{b[1]+16}" fill="white" stroke="black" paint-order="stroke" stroke-width="2" font-size="16">{i}</text>' for i,b in enumerate([x['box'] for x in row['boxes']],1))
        source = thumbnail(image,(1920,1920))
        report = f'<html lang="ru"><meta charset="utf-8"><title>{row["file"]}</title><style>body{{font:16px Arial;margin:24px}}svg{{max-height:900px;width:100%}}.cards{{display:flex;flex-wrap:wrap}}figure{{width:190px;margin:10px}}img{{height:210px;max-width:180px;object-fit:contain}}small{{overflow-wrap:anywhere}}article{{border-top:1px solid #aaa}}h2{{font-size:18px}}</style><h1>{row["file"]}</h1><p>Все 2103 SKU. Кандидаты мощной модели, НЕ подтверждённая разметка. Cosine не является вероятностью, SIFT — число геометрически согласованных точек. Нужна проверка читаемой этикетки; возможны неизвестные товары.</p><svg viewBox="0 0 {row["width"]} {row["height"]}"><image href="{source}" width="{row["width"]}" height="{row["height"]}"/>{outlines}</svg>{"".join(blocks)}</html>'
        target = path.stem+'.html'; (args.output/target).write_text(report)
        summary = {'file':row['file'], 'sha256':row['sha256'], 'width':row['width'], 'height':row['height'], 'proposals':proposals, 'elapsedSeconds':round(time.perf_counter()-started,2)}
        result['rows'].append(summary)
        (args.output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        stats = {'file':row['file'], 'boxes':len(proposals), 'geometry8':sum(p['maxInliers']>=8 for p in proposals), 'sameTop1':sum(p['sameTop1'] for p in proposals), 'browserCompared':sum(bool(p['browserCandidates']) for p in proposals)}
        summaries.append(stats); print(json.dumps(stats),flush=True)
    (args.output/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
    links = ''.join(f'<li><a href="{Path(r["file"]).stem}.html">{r["file"]}</a> — {r["boxes"]} областей, {r["geometry8"]} с ≥8 точками SIFT; top-1 совпал у {r["sameTop1"]}/{r["browserCompared"]}</li>' for r in summaries)
    (args.output/'index.html').write_text('<meta charset="utf-8"><h1>Витрины: сравнение моделей</h1><p>SigLIP2 so400m + SIFT против MobileNet. Совпадение моделей и геометрия не являются точностью. Все названия требуют проверки человеком.</p><ul>'+links+'</ul>')

if __name__ == '__main__': main()
