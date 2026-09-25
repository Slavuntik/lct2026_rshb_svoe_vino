"""No model downloads or GPU: verify the detector/feature resolution boundary."""

from types import SimpleNamespace
import pytest
from PIL import Image

pytest.importorskip("torch")
pytest.importorskip("onnxruntime")
pytest.importorskip("cv2")
from shelf_api import engine as module


def test_label_features_use_original_pixels_while_detection_is_bounded(monkeypatch):
    sizes = {}

    def detect(image, session):
        sizes["detection"] = image.size
        return [{"box": [0.1, 0.1, 0.9, 0.9], "score": 0.9}]

    def extract(image, *args):
        sizes["features"] = image.size
        return SimpleNamespace(points=[])

    monkeypatch.setattr(module, "detect", detect)
    engine = module.ShelfEngine.__new__(module.ShelfEngine)
    engine.sync = lambda: None
    engine.detector = engine.extractor = None
    engine.extract = extract
    engine.wines = {}
    engine.pipeline_version = engine.catalog_version = "test"
    image = Image.new("RGB", (4000, 2000))
    result = engine.scan(image)
    assert sizes == {"detection": (1920, 960), "features": (4000, 2000)}
    assert image.size == (4000, 2000)
    assert result["detectedCount"] == 1
    assert result["matches"] == []
