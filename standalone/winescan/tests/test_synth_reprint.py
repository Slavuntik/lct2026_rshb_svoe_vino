"""Пресет v3: другой тираж печати.

Синтетика по построению слишком добра к сопоставителям — в кадре лежат пиксели самого эталона.
Пресет v3 портит целевую упаковку так, как её меняет другая партия печати и пересъёмка: сдвиг
цвета и тона, растр, цикл «размытие — резкость», пересжатие. Вёрстка и надписи остаются.
"""

import random

import numpy as np
from PIL import Image

from winescan.validation.synth import SYNTH_PRESETS, SynthConfig, render_sample


def _bottle(seed: int = 0) -> Image.Image:
    """Прямоугольная «бутылка» с цветной этикеткой на прозрачном фоне."""
    rng = np.random.default_rng(seed)
    array = np.zeros((400, 120, 4), dtype=np.uint8)
    array[:, 20:100] = (40, 90, 60, 255)
    array[150:260, 25:95, :3] = rng.integers(60, 240, size=(110, 70, 3), dtype=np.uint8)
    return Image.fromarray(array, "RGBA")


def _frame(preset: str, seed: int = 7) -> np.ndarray:
    image, _ = render_sample(_bottle(), [], None, random.Random(seed), SYNTH_PRESETS[preset])
    return np.asarray(image.convert("RGB"), dtype=np.int16)


def test_new_settings_are_off_by_default():
    config = SynthConfig()

    assert config.reprint_hue == 0 and config.reprint_saturation == 0 and config.reprint_gamma == 0
    assert config.halftone_prob == 0 and config.resharpen_prob == 0 and config.recompress_rounds == 0
    # v1 и v2 не должны измениться: старые выборки воспроизводятся тем же seed
    assert SYNTH_PRESETS["v1"] == SynthConfig()
    assert SYNTH_PRESETS["v2"].reprint_hue == 0 and SYNTH_PRESETS["v2"].recompress_rounds == 0


def test_v2_and_v3_differ_only_in_the_target():
    """При одном посеве v2 и v3 обязаны совпадать всюду, кроме целевой упаковки.

    Пресет нужен, чтобы мерить цену «другого тиража». Если порча печати расходует числа общего
    генератора, сдвигается вся дальнейшая сцена — положение, фон, засветка, — и разница двух
    выборок перестаёт быть разницей тиража. Тест сторожит именно это свойство.
    """
    bottle = _bottle()
    v2, box_v2 = render_sample(bottle, [], None, random.Random(5), SYNTH_PRESETS["v2"])
    v3, box_v3 = render_sample(bottle, [], None, random.Random(5), SYNTH_PRESETS["v3"])
    assert box_v2 == box_v3, f"рамка разъехалась: {box_v2} против {box_v3}"

    a = np.asarray(v2.convert("RGB"), dtype=np.int16)
    b = np.asarray(v3.convert("RGB"), dtype=np.int16)
    x0, y0, x1, y1 = box_v2
    pad = 24  # смаз и засветка размазывают разницу на несколько пикселей за рамку
    outside = np.abs(a - b).copy()
    outside[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = 0

    assert outside.mean() < 0.5, f"сцена за пределами упаковки разъехалась: {outside.mean():.2f}"
    # на посеве 5 отличие внутри рамки 8,9 из 255; по проверенным посевам минимум 2,7
    inside = np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean()
    assert inside > 3.0, f"целевая упаковка почти не изменилась: {inside:.2f}"


def test_v3_is_reproducible():
    assert np.array_equal(_frame("v3", seed=11), _frame("v3", seed=11))
    assert not np.array_equal(_frame("v3", seed=11), _frame("v3", seed=12))
