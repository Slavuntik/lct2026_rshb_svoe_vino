"""Вторая модель поворота бутылки: независимость закона проекции и парность выборок.

Мультиракурсная галерея и повороты запросов в синтетике делались одной функцией, поэтому
измеренный выигрыш галереи мог быть артефактом общего кода. Перспективная модель нужна, чтобы
эту оговорку проверить, — значит она обязана отличаться от ортографической там, где это важно,
и сходиться к ней там, где физика этого требует.
"""

import random

import numpy as np
from PIL import Image

from winescan.validation.cylinder_alt import rotate_cylinder_perspective
from winescan.validation.synth import SYNTH_PRESETS, SynthConfig, render_sample
from winescan.vision.cylinder import rotate_cylinder


def _bottle(seed: int = 0) -> Image.Image:
    """Прямоугольная «бутылка» с цветной этикеткой на прозрачном фоне."""
    rng = np.random.default_rng(seed)
    array = np.zeros((400, 120, 4), dtype=np.uint8)
    array[:, 20:100] = (40, 90, 60, 255)
    array[150:260, 25:95, :3] = rng.integers(60, 240, size=(110, 70, 3), dtype=np.uint8)
    return Image.fromarray(array, "RGBA")


def _rgb(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.int16)


def test_models_disagree_on_the_same_rotation():
    bottle = _bottle()
    ortho = _rgb(rotate_cylinder(bottle, yaw_deg=25))
    perspective = _rgb(rotate_cylinder_perspective(bottle, yaw_deg=25, camera_distance=4.0))

    assert ortho.shape == perspective.shape
    difference = np.abs(ortho - perspective).mean()
    assert difference > 1.0, f"модели дали почти одно и то же: {difference:.2f}"


def test_distant_camera_approaches_orthographic():
    """Проверка вывода формул: при удалении камеры перспектива обязана сходиться к ортографии."""
    bottle = _bottle()
    ortho = _rgb(rotate_cylinder(bottle, yaw_deg=20))
    near = np.abs(_rgb(rotate_cylinder_perspective(bottle, yaw_deg=20, camera_distance=2.0)) - ortho).mean()
    far = np.abs(_rgb(rotate_cylinder_perspective(bottle, yaw_deg=20, camera_distance=400.0)) - ortho).mean()

    assert far < near, f"дальняя камера не ближе к ортографии: {far:.2f} против {near:.2f}"
    assert far < 3.0, f"при камере в 400 радиусов расхождение осталось большим: {far:.2f}"


def test_presets_keep_the_old_model_by_default():
    assert SynthConfig().warp == "cylinder"
    assert [SYNTH_PRESETS[name].warp for name in ("v1", "v2", "v3")] == ["cylinder"] * 3
    assert SYNTH_PRESETS["v2p"].warp == "perspective"
    # v2p должен отличаться от v2 только моделью искривления
    assert SYNTH_PRESETS["v2p"] == type(SYNTH_PRESETS["v2"])(**{**SYNTH_PRESETS["v2"].__dict__, "warp": "perspective"})


def test_v2_and_v2p_stay_paired():
    """При одном посеве сцена обязана совпасть: различаться должна только сама упаковка."""
    v2, box_v2 = render_sample(_bottle(), [], None, random.Random(5), SYNTH_PRESETS["v2"])
    v2p, box_v2p = render_sample(_bottle(), [], None, random.Random(5), SYNTH_PRESETS["v2p"])
    assert v2.size == v2p.size

    # рамка целевой упаковки может чуть сдвинуться: у близкой камеры видно меньше поверхности
    width = v2.size[0]
    assert max(abs(a - b) for a, b in zip(box_v2, box_v2p)) < 0.1 * width, f"{box_v2} против {box_v2p}"

    a, b = _rgb(v2), _rgb(v2p)
    x0 = min(box_v2[0], box_v2p[0])
    y0 = min(box_v2[1], box_v2p[1])
    x1 = max(box_v2[2], box_v2p[2])
    y1 = max(box_v2[3], box_v2p[3])
    pad = 24  # смаз и засветка размазывают разницу на несколько пикселей за рамку
    outside = np.abs(a - b).copy()
    outside[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = 0

    assert outside.mean() < 0.5, f"сцена за пределами упаковки разъехалась: {outside.mean():.2f}"
    inside = np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean()
    assert inside > 1.0, f"упаковка почти не изменилась: {inside:.2f}"
