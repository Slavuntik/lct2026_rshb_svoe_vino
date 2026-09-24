"""Measure the standalone detector/matcher on local shelf photos. No ground truth is inferred."""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from pathlib import Path
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps
from prepare import ROOT, preprocess


def iou(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    area = lambda x: max(0, x[2]-x[0])*max(0, x[3]-x[1])
    return intersection / max(area(a)+area(b)-intersection, 1e-12)


def detect(image, session, dense=False):
    width, height = image.size
    tiles = [(0, 0, width, height)]
    if dense:
        tiles += [(0, 0, int(width*.6), int(height*.6)), (int(width*.4), 0, width, int(height*.6)), (0, int(height*.4), int(width*.6), height), (int(width*.4), int(height*.4), width, height)]
    proposals = []
    for x0, y0, x1, y1 in tiles:
        tile = image.crop((x0, y0, x1, y1))
        ratio = min(640 / tile.width, 640 / tile.height)
        resized = tile.resize((round(tile.width*ratio), round(tile.height*ratio)), Image.Resampling.BILINEAR)
        canvas = Image.new('RGB', (640, 640), (114, 114, 114))
        dx, dy = (640-resized.width)//2, (640-resized.height)//2
        canvas.paste(resized, (dx, dy))
        data = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255
        predictions = session.run(None, {session.get_inputs()[0].name: data})[0][0]
        for i in np.flatnonzero(predictions[43] >= .25):
            cx, cy, w, h = predictions[:4, i]
            box = [max(x0, (cx-w/2-dx)/ratio+x0), max(y0, (cy-h/2-dy)/ratio+y0), min(x1, (cx+w/2-dx)/ratio+x0), min(y1, (cy+h/2-dy)/ratio+y0)]
            if box[2] > box[0] and box[3] > box[1]: proposals.append({'box': [float(v) for v in box], 'score': float(predictions[43, i])})
    kept = []
    for item in sorted(proposals, key=lambda x: -x['score']):
        if all(iou(item['box'], previous['box']) < .45 for previous in kept): kept.append(item)
    return kept[:80]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--photos', type=Path, required=True)
    p.add_argument('--models', type=Path, default=ROOT/'public/models')
    p.add_argument('--output', type=Path, default=ROOT/'artifacts/audit')
    args = p.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    options = ort.SessionOptions(); options.intra_op_num_threads = 4
    detector = ort.InferenceSession(str(args.models/'bottle-detector.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
    embedder = ort.InferenceSession(str(args.models/'bottle-embedding.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
    catalog = json.loads((args.models/'catalog.json').read_text())
    references = np.asarray([w['references'][0] for w in catalog['wines']], dtype=np.float32)
    rows = []
    for path in sorted(args.photos.iterdir()):
        if path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'): continue
        with Image.open(path) as original: image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1920, 1920))
        for dense in (False, True):
            started = time.perf_counter(); boxes = detect(image, detector, dense); detection_ms = (time.perf_counter()-started)*1000
            for box in boxes:
                x0,y0,x1,y1 = box['box']; box['tooSmall'] = x1-x0 < 40 or y1-y0 < 80
                box['id'] = None
                if box['tooSmall']: continue
                data = preprocess(image.crop((round(x0),round(y0),round(x1),round(y1))))
                vector = embedder.run(None, {embedder.get_inputs()[0].name: data})[0].reshape(-1)
                vector /= max(np.linalg.norm(vector), 1e-12)
                scores = references @ vector; indices = np.argsort(scores)[::-1][:3]
                box['candidates'] = [{'id': catalog['wines'][i]['id'], 'score': float(scores[i])} for i in indices]
                if scores[indices[0]] >= .9 and scores[indices[0]]-scores[indices[1]] >= .05: box['id'] = catalog['wines'][indices[0]]['id']
            row = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'width': image.width, 'height': image.height, 'dense': dense, 'detections': len(boxes), 'accepted': sum(b['id'] is not None for b in boxes), 'tooSmall': sum(b['tooSmall'] for b in boxes), 'detectionMs': round(detection_ms), 'elapsedMs': round((time.perf_counter()-started)*1000), 'boxes': boxes}
            rows.append(row); print(f"{path.name} dense={dense}: {len(boxes)} boxes, {row['accepted']} accepted, {row['elapsedMs']}ms", flush=True)
    (args.output/'results.json').write_text(json.dumps({'catalogSize': len(catalog['wines']), 'groundTruth': None, 'rows': rows}, ensure_ascii=False, indent=2))
    lines = ['# Проверка фотографий витрин', '', 'CPU, ONNX Runtime Python. Это проверка обработки и числа предложенных рамок, **не** оценка точности: фотографии не размечены. Числа не являются FPS iPhone или браузера.', '', '| Фото | Обычный / плотный режим: рамок | Принято SKU | Время, мс |', '|---|---:|---:|---:|']
    for a,b in zip(rows[::2], rows[1::2]): lines.append(f"| {a['file']} | {a['detections']} / {b['detections']} | {a['accepted']} / {b['accepted']} | {a['elapsedMs']} / {b['elapsedMs']} |")
    lines += ['', 'Для recall, top-1/top-3 и ложных подсветок нужны проверенные рамки/SKU и неизвестные товары. Разделять данные следует по съёмочным сессиям, а не соседним фотографиям.']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n')

if __name__ == '__main__': main()
