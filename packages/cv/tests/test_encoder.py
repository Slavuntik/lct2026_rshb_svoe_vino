"""Быстрые тесты обвязки энкодера, не требующие скачивания весов модели."""
from __future__ import annotations

from cv.encoder import SiglipEncoder, pick_device


def test_pick_device_respects_explicit_choice():
    assert pick_device("cpu") == "cpu"
    assert pick_device("mps") == "mps"


def test_pick_device_auto_returns_known_backend():
    assert pick_device(None) in {"mps", "cuda", "cpu"}


def test_encoder_is_lazy_does_not_load_model_on_construction():
    enc = SiglipEncoder(model_name="not/a-real-model-id", device="cpu")
    assert enc._model is None
    assert enc._processor is None
