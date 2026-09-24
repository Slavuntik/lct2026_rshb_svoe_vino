"""Dense YOLO11n bottle proposals in normalized upright-image coordinates."""

import numpy as np
from PIL import Image


def iou(a, b):
    area = lambda x: max(0, x[2] - x[0]) * max(0, x[3] - x[1])
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    return intersection / max(area(a) + area(b) - intersection, 1e-12)


def detect(image, session):
    width, height = image.size
    tiles = [
        (0, 0, width, height),
        (0, 0, int(width * 0.6), int(height * 0.6)),
        (int(width * 0.4), 0, width, int(height * 0.6)),
        (0, int(height * 0.4), int(width * 0.6), height),
        (int(width * 0.4), int(height * 0.4), width, height),
    ]
    proposals = []
    for x0, y0, x1, y1 in tiles:
        tile = image.crop((x0, y0, x1, y1))
        scale = 640 / max(tile.size)
        resized = tile.resize(
            (round(tile.width * scale), round(tile.height * scale)),
            Image.Resampling.BILINEAR,
        )
        canvas = Image.new("RGB", (640, 640), (114, 114, 114))
        dx, dy = (640 - resized.width) // 2, (640 - resized.height) // 2
        canvas.paste(resized, (dx, dy))
        data = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255
        prediction = session.run(None, {session.get_inputs()[0].name: data})[0]
        if prediction.shape != (1, 84, 8400):
            raise ValueError("Expected YOLO11 COCO output [1,84,8400]")
        values = prediction[0]
        for i in np.flatnonzero(values[43] >= 0.25):
            cx, cy, w, h = values[:4, i]
            box = [
                max(x0, (cx - w / 2 - dx) / scale + x0) / width,
                max(y0, (cy - h / 2 - dy) / scale + y0) / height,
                min(x1, (cx + w / 2 - dx) / scale + x0) / width,
                min(y1, (cy + h / 2 - dy) / scale + y0) / height,
            ]
            if box[2] > box[0] and box[3] > box[1]:
                proposals.append(
                    {"box": list(map(float, box)), "score": float(values[43, i])}
                )
    kept = []
    for proposal in sorted(proposals, key=lambda x: -x["score"]):
        if all(iou(proposal["box"], p["box"]) < 0.45 for p in kept):
            kept.append(proposal)
    return kept[:80]
