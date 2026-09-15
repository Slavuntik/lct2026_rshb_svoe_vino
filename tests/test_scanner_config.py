from winescan.service.pipeline import ScannerConfig


def test_defaults_match_documented_service_settings(monkeypatch):
    for name in ("WINESCAN_INDEXES", "WINESCAN_INDEX_WEIGHTS", "WINESCAN_LOCAL_WEIGHT", "WINESCAN_USE_OCR",
                 "WINESCAN_TEXT_WEIGHT", "WINESCAN_MIN_VISUAL_SCORE", "WINESCAN_MIN_MARGIN", "WINESCAN_DEVICE"):
        monkeypatch.delenv(name, raising=False)  # fmt: skip

    config = ScannerConfig.from_env()

    assert config.indexes == ("siglip2-so400m-patch14-384", "siglip2-so400m-patch14-384__label")
    assert config.index_weights == (0.5, 0.5)
    assert (config.local_weight, config.use_ocr, config.text_weight, config.min_visual_score) == (0.15, True, 0.02, 0.74)
    assert config.min_margin is None and config.device is None


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("WINESCAN_INDEXES", "a, b")
    monkeypatch.setenv("WINESCAN_INDEX_WEIGHTS", "0.3,0.7")
    monkeypatch.setenv("WINESCAN_USE_OCR", "0")
    monkeypatch.setenv("WINESCAN_LOCAL_WEIGHT", "0")
    monkeypatch.setenv("WINESCAN_MIN_VISUAL_SCORE", "")
    monkeypatch.setenv("WINESCAN_USE_DETECTOR", "0")

    config = ScannerConfig.from_env()

    assert config.indexes == ("a", "b") and config.index_weights == (0.3, 0.7)
    assert config.use_ocr is False and config.local_weight == 0.0 and config.use_detector is False
    assert config.min_visual_score == 0.74  # пустое значение = значение по умолчанию
