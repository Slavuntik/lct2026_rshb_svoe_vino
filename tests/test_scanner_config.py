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
    assert config.box_candidates == 1 and config.box_rule == {"prior": 1.0, "top1": 0.0, "margin": 0.0}
    assert config.fusion_path is None and config.use_vlm is False


def test_env_new_pipeline_options(monkeypatch):
    monkeypatch.setenv("WINESCAN_BOX_CANDIDATES", "3")
    monkeypatch.setenv("WINESCAN_BOX_RULE", '{"prior": 0.02, "top1": 1.0, "margin": 3.0}')
    monkeypatch.setenv("WINESCAN_FUSION", "configs/fusion_v1.json")
    monkeypatch.setenv("WINESCAN_USE_VLM", "1")
    monkeypatch.setenv("WINESCAN_VLM_MARGIN", "0.5")

    config = ScannerConfig.from_env()

    assert config.box_candidates == 3 and config.box_rule["margin"] == 3.0
    assert config.fusion_path == "configs/fusion_v1.json" and config.use_vlm and config.vlm_margin == 0.5


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
