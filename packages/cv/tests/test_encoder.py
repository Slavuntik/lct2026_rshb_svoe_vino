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


def _fake_encoder(tmp_path):
    import torch
    enc = SiglipEncoder(model_name="test/fake", device="cpu", cache_dir=tmp_path / 'cache')
    enc._processor = lambda **kwargs: {}
    class Model:
        calls = 0
        def get_image_features(self, **kwargs):
            self.calls += 1
            return torch.tensor([[3., 4.]])
    enc._model = Model()
    return enc


def test_cache_deletion_does_not_break_live_encoder(tmp_path):
    import shutil
    import numpy as np
    enc = _fake_encoder(tmp_path)
    photo = np.zeros((2, 4, 3), dtype=np.uint8)
    expected = enc.encode(photo)
    shutil.rmtree(enc.cache_dir)
    assert enc.encode(photo) == expected
    assert enc._cache_path(photo).is_file()


def test_corrupt_cache_is_recomputed_and_replaced(tmp_path):
    import json
    import numpy as np
    enc = _fake_encoder(tmp_path)
    photo = np.zeros((2, 4, 3), dtype=np.uint8)
    expected = enc.encode(photo)
    for bad in ('{', '{}', '[true]', '["bad"]', '[NaN]', '[]'):
        enc._cache_path(photo).write_text(bad)
        assert enc.encode(photo) == expected
        assert json.loads(enc._cache_path(photo).read_text()) == expected
    assert not list(enc.cache_dir.glob('*.tmp'))


def test_readonly_or_unavailable_cache_never_blocks_inference(tmp_path, monkeypatch):
    import numpy as np
    import cv.encoder as module
    enc = _fake_encoder(tmp_path)
    monkeypatch.setattr(module.os, 'replace', lambda *a: (_ for _ in ()).throw(PermissionError('read only')))
    assert len(enc.encode(np.zeros((2, 4, 3), dtype=np.uint8))) == 2
    assert not list(enc.cache_dir.glob('*.tmp'))
    # Existing regular file in place of the directory also cannot break construction/inference.
    enc.cache_dir.rmdir()
    enc.cache_dir.write_text('not a directory')
    assert len(enc.encode(np.zeros((2, 4, 3), dtype=np.uint8))) == 2


def test_cache_keys_include_shape_dtype_and_schema(tmp_path, monkeypatch):
    import numpy as np
    import cv.encoder as module
    enc = _fake_encoder(tmp_path)
    a = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    original = enc._cache_path(a)
    assert original != enc._cache_path(a.reshape(4, 2, 3))
    assert original != enc._cache_path(a.view(np.int8))
    monkeypatch.setattr(module, 'CACHE_SCHEMA', 'changed-preprocessor')
    assert original != enc._cache_path(a)


def test_cache_hit_does_not_run_model_again(tmp_path):
    import numpy as np
    enc = _fake_encoder(tmp_path)
    photo = np.zeros((2, 4, 3), dtype=np.uint8)
    assert enc.encode(photo) == enc.encode(photo)
    assert enc._model.calls == 1
