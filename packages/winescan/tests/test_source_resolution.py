import threading
from types import SimpleNamespace

import numpy as np
from PIL import Image

from winescan.service.pipeline import Scanner, ScannerConfig
from winescan.search.rerank import Candidate


def test_detection_uses_small_image_but_features_use_original():
    scanner = Scanner.__new__(Scanner)
    scanner.config = ScannerConfig(fusion_path=None, use_ocr=False)
    scanner.fusion = None
    scanner.cards = {"wine": {"name": "Wine"}}
    scanner._lock = threading.Lock()
    seen = {}
    def boxes(image, timings, user_box):
        seen["detector"] = image.size
        return [((400, 100, 800, 700), 1.0)]
    scanner._boxes = boxes
    def embed(images, boxes):
        seen["source"] = images[0].size
        seen["box"] = boxes[0]
        return None
    scanner.searcher = SimpleNamespace(
        embed_views=embed, wine_scores=lambda _: np.array([[0.95]]), wine_slugs=["wine"],
    )
    def legacy(package, *args):
        seen["package"] = package.size
        return [Candidate("wine", 0.95, 0.95)], {}, None
    scanner._legacy = legacy
    result = scanner.scan(Image.new("RGB", (4000, 2000)))
    assert seen["detector"] == (1600, 800)
    assert seen["source"] == (4000, 2000)
    assert seen["box"] == (1000, 250, 2000, 1750)
    assert seen["package"] == (1060, 1590)
    assert result.box == [400, 100, 800, 700]
