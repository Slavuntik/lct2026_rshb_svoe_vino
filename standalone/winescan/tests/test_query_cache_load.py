import json

import numpy as np
import pytest

from winescan.eval.offline import QueryCache


def _cache(tmp_path, indexes: list[str]):
    """Минимальный кэш запросов на диске: meta, один запрос, нулевые векторы."""
    directory = tmp_path / "cache" / "synth_test"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(
        json.dumps({"split": "synth_test", "indexes": indexes, "index_weights": [0.5] * len(indexes)}),
        encoding="utf-8",
    )
    (directory / "queries.jsonl").write_text(
        json.dumps({"query_id": "q1", "expected_slug": "a", "image_path": "q1.jpg", "size": [100, 300],
                    "in_phash_group": False, "shares_image": False, "slots": [{"kind": "gt", "box": [0, 0, 10, 10]}]}) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    np.save(directory / "vectors.npy", np.zeros((1, 1, len(indexes), 4), dtype=np.float16))


def test_load_rejects_gallery_of_other_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("WINESCAN_ARTIFACTS_DIR", str(tmp_path))
    _cache(tmp_path, ["full", "label"])

    # ошибка должна быть понятной и до загрузки индексов: папок индексов здесь нет вовсе
    with pytest.raises(ValueError, match="подменять можно только галерею той же формы"):
        QueryCache.load("synth_test", indexes=["full"])
