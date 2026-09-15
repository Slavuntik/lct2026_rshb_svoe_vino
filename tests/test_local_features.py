import numpy as np
from PIL import Image, ImageDraw

from winescan.search.local_features import LocalFeatureStore
from winescan.search.local_match import Features, extract, inliers


def _label(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.fromarray(rng.integers(0, 255, (60, 40, 3), dtype=np.uint8)).resize((400, 600), Image.NEAREST)
    ImageDraw.Draw(image).rectangle([40, 200, 360, 420], fill="white")
    return image


def test_store_roundtrip_keeps_matching_quality(tmp_path):
    original = extract(_label(1))
    empty = Features(np.zeros((0, 2), np.float32), None)

    LocalFeatureStore.save(tmp_path, [("a", original), ("empty", empty)])
    store = LocalFeatureStore(tmp_path)

    restored = store.get("a")
    assert "a" in store and "missing" not in store
    assert restored.descriptors.dtype == np.float32 and len(restored.descriptors) == len(original.descriptors)
    # квантование в uint8 почти не меняет число согласованных совпадений
    assert abs(inliers(original, restored) - inliers(original, original)) <= 0.05 * inliers(original, original)
    assert store.get("empty").descriptors is None
