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


def test_v3_changes_the_target_but_keeps_layout():
    v2, v3 = _frame("v2"), _frame("v3")

    assert v2.shape == v3.shape
    difference = np.abs(v2 - v3).mean()
    assert difference > 1.0, f"кадр почти не изменился: {difference:.2f}"
    assert difference < 60.0, f"кадр изменился до неузнаваемости: {difference:.2f}"


def test_v3_is_reproducible():
    assert np.array_equal(_frame("v3", seed=11), _frame("v3", seed=11))
    assert not np.array_equal(_frame("v3", seed=11), _frame("v3", seed=12))
