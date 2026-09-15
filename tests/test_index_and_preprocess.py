import numpy as np
import pytest
from PIL import Image

from winescan.search.index import VectorIndex
from winescan.vision.preprocess import crop_box, fit_on_square, label_region, query_view, reference_view


def _unit(*values):
    vector = np.array(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def test_search_aggregates_views_of_one_wine_by_max(tmp_path):
    index = VectorIndex(
        slugs=["a", "a", "b"],
        vectors=np.stack([_unit(1, 0, 0), _unit(0, 1, 0), _unit(0.8, 0.6, 0)]),
    )
    slugs, scores = index.search(np.stack([_unit(0, 1, 0)]), k=2)

    assert slugs == [["a", "b"]]
    assert scores[0, 0] == pytest.approx(1.0) and scores[0, 1] == pytest.approx(0.6)

    index.save(tmp_path)
    assert VectorIndex.load(tmp_path).search(np.stack([_unit(0, 1, 0)]), k=1)[0] == [["a"]]


def test_index_rejects_non_contiguous_wine_rows():
    with pytest.raises(ValueError):
        VectorIndex(slugs=["a", "b", "a"], vectors=np.eye(3, dtype=np.float32))


def test_fit_on_square_centers_with_margin():
    square = fit_on_square(Image.new("RGB", (100, 300), (0, 0, 0)), margin=0.5)

    assert square.size == (600, 600)
    assert square.getpixel((5, 5)) == (255, 255, 255) and square.getpixel((300, 300)) == (0, 0, 0)


def test_label_region_crops_only_tall_packages():
    bottle = Image.new("RGB", (100, 400))
    box = Image.new("RGB", (300, 300))

    assert label_region(bottle).size == (100, 228)  # 0,40–0,97 высоты
    assert label_region(box).size == (300, 300)


def test_reference_view_is_square_and_query_view_crops_box():
    bottle = Image.new("RGBA", (100, 400), (0, 0, 0, 0))
    bottle.paste((120, 0, 0, 255), (30, 20, 70, 380))

    assert reference_view(bottle).size[0] == reference_view(bottle).size[1]
    photo = Image.new("RGB", (400, 300), (10, 10, 10))
    assert crop_box(photo, (100, 50, 200, 250), margin=0).size == (100, 200)
    # рамка 100×200 + 3% поля с каждой стороны = 106×212, квадрат с полями 4%: round(212 × 1,08) = 229
    assert query_view(photo, (100, 50, 200, 250)).size == (229, 229)
