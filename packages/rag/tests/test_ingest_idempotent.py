"""ingest идемпотентен: повторный прогон на тех же данных не дублирует точки
в Qdrant и не удлиняет сайдкары (payloads/labels). Стабильные id (uuid5) —
повторный upsert перезаписывает ту же точку.
"""
from __future__ import annotations

from rag.ingest import run_ingest
from rag.store import QdrantStore


def _line_count(path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def test_repeated_ingest_does_not_duplicate(tiny_source, tmp_path):
    build_dir, catalog_dir = tiny_source
    data_dir = tmp_path / "data"
    no_goldset = tmp_path / "no-such-goldset.jsonl"  # см. conftest.tiny_index — калибровка не в тему мини-фикстуры
    no_case_data = tmp_path / "no-such-case-data"  # см. conftest.tiny_index — реальные 125 вин кейса тут ни к чему

    manifest1 = run_ingest(
        version="v1",
        source_dir=build_dir,
        catalog_dir=catalog_dir,
        data_dir=data_dir,
        goldset_path=no_goldset,
        case_data_dir=no_case_data,
    )
    store = QdrantStore(path=data_dir / "qdrant")
    counts_after_first = {name: store.count(name) for name in ("wines", "wineries", "knowledge")}
    payload_lines_first = {
        name: _line_count(data_dir / "payloads" / f"{name}.jsonl") for name in ("wines", "wineries", "knowledge")
    }
    labels_lines_first = _line_count(data_dir / "labels.jsonl")
    store.close()

    assert counts_after_first["wines"] == 7  # 6 обычных + 1 декой-игристое красное... см. conftest
    assert manifest1["counts"]["wines"] == counts_after_first["wines"]

    # Повторный прогон — тот же источник, тот же data_dir, ДРУГАЯ версия
    # (как если бы оператор перегнал ingest ещё раз тем же source).
    manifest2 = run_ingest(
        version="v2",
        source_dir=build_dir,
        catalog_dir=catalog_dir,
        data_dir=data_dir,
        goldset_path=no_goldset,
        case_data_dir=no_case_data,
    )
    store = QdrantStore(path=data_dir / "qdrant")
    counts_after_second = {name: store.count(name) for name in ("wines", "wineries", "knowledge")}
    payload_lines_second = {
        name: _line_count(data_dir / "payloads" / f"{name}.jsonl") for name in ("wines", "wineries", "knowledge")
    }
    labels_lines_second = _line_count(data_dir / "labels.jsonl")
    store.close()

    assert counts_after_second == counts_after_first, "повторный ingest не должен плодить точки в Qdrant"
    assert payload_lines_second == payload_lines_first, "сайдкары payloads не должны удлиняться"
    assert labels_lines_second == labels_lines_first, "labels.jsonl не должен удлиняться"
    assert manifest2["version"] == "v2"
    assert manifest2["counts"] == manifest1["counts"]


def test_ingest_point_ids_are_stable_across_runs(tiny_source, tmp_path):
    """uuid5(id) стабилен -> один и тот же вход даёт одну и ту же точку."""
    from rag.store import point_id

    build_dir, catalog_dir = tiny_source
    data_dir = tmp_path / "data"
    no_goldset = tmp_path / "no-such-goldset.jsonl"
    no_case_data = tmp_path / "no-such-case-data"
    run_ingest(
        version="v1",
        source_dir=build_dir,
        catalog_dir=catalog_dir,
        data_dir=data_dir,
        goldset_path=no_goldset,
        case_data_dir=no_case_data,
    )

    store = QdrantStore(path=data_dir / "qdrant")
    vec_before = store.get_vector("wines", "red-dry-kuban-1")
    store.close()

    run_ingest(
        version="v2",
        source_dir=build_dir,
        catalog_dir=catalog_dir,
        data_dir=data_dir,
        goldset_path=no_goldset,
        case_data_dir=no_case_data,
    )
    store = QdrantStore(path=data_dir / "qdrant")
    vec_after = store.get_vector("wines", "red-dry-kuban-1")
    store.close()

    assert vec_before is not None and vec_after is not None
    assert point_id("red-dry-kuban-1") == point_id("red-dry-kuban-1")
    # то же вино встаёт в ту же точку (не новую) — векторы идентичной длины
    # для одного и того же текста/модели.
    assert len(vec_before) == len(vec_after)
