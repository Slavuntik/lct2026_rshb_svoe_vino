"""Conservative OCR evidence: no recognition from OCR alone, no missing-word veto."""

import math
import re
from collections import Counter
import numpy as np
from PIL import Image

ALIASES = {
    "cabernet": "каберне",
    "sauvignon": "совиньон",
    "chardonnay": "шардоне",
    "pinot": "пино",
    "noir": "нуар",
    "merlot": "мерло",
    "muscat": "мускат",
    "brut": "брют",
    "reserve": "резерв",
    "reserva": "резерв",
    "riesling": "рислинг",
    "glera": "глера",
    "bastardo": "бастардо",
    "portwein": "портвейн",
    "port": "портвейн",
}
GRAPES = {
    "каберне",
    "совиньон",
    "шардоне",
    "пино",
    "мерло",
    "мускат",
    "рислинг",
    "глера",
    "бастардо",
    "саперави",
    "кокур",
}


def tokens(text):
    return {
        ALIASES.get(t, t)
        for t in re.findall(r"[a-zа-я]+", text.lower().replace("ё", "е"))
        if len(t) >= 4
    }


class TextCheck:
    def __init__(self, catalog, load_model=True):
        self.words = {
            k: tokens(" ".join([v["name"], v.get("brand", ""), *v.get("grapes", [])]))
            for k, v in catalog.items()
        }
        counts = Counter(w for ws in self.words.values() for w in ws)
        self.weights = {
            w: math.log(1 + len(catalog) / (1 + n)) for w, n in counts.items()
        }
        self.engine = None
        if load_model:
            from rapidocr import RapidOCR, LangRec, ModelType, OCRVersion

            self.engine = RapidOCR(
                params={
                    "Global.use_cls": False,
                    "Det.limit_side_len": 640,
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.MOBILE,
                    "Rec.lang_type": LangRec.ESLAV,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.model_type": ModelType.MOBILE,
                    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                }
            )
            self.engine(np.ones((160, 80, 3), dtype=np.uint8) * 255)

    def read(self, image, box):
        x0, y0, x1, y1 = box
        crop = image.crop(
            (
                int(x0 * image.width),
                int((y0 + (y1 - y0) * 0.25) * image.height),
                int(x1 * image.width),
                int(y1 * image.height),
            )
        )
        scale = min(4, 768 / max(crop.height, 1))
        crop = crop.resize(
            (max(16, round(crop.width * scale)), max(16, round(crop.height * scale))),
            Image.Resampling.BICUBIC,
        )
        result = self.engine(np.asarray(crop)[:, :, ::-1].copy())
        return " ".join(
            t for t, s in zip(result.txts or [], result.scores or []) if s >= 0.85
        )

    def conflicts(self, text, slug):
        observed = tokens(text)
        expected = self.words.get(slug, set())
        # Only contradictory named grapes; missing words never establish a conflict.
        a, b = observed & GRAPES, expected & GRAPES
        return bool(a and b and a.isdisjoint(b))

    def choose(self, text, ids):
        observed = tokens(text)
        scores = []
        for slug in ids:
            common = observed & self.words.get(slug, set())
            specific = {w for w in common if self.weights.get(w, 0) >= 2}
            score = sum(self.weights.get(w, 0) for w in specific)
            scores.append((score, slug, len(specific)))
        scores.sort(reverse=True)
        if not scores or scores[0][2] < 2 or scores[0][0] < 5:
            return None
        if len(scores) > 1 and scores[0][0] - scores[1][0] < 2:
            return None
        return None if self.conflicts(text, scores[0][1]) else scores[0][1]
