"""Смоук-тесты CLI: структура парсера (без реального энкодера/сети — быстро)."""
from __future__ import annotations

import json

from cv.cli import build_parser, discover_refs_from_slug_refs_json, load_near_dup_groups


def test_all_subcommands_registered():
    """G3 (дополнение оркестратора 16.09.2026): + `audit-refs` — отдельный дешёвый проход
    детектора этикетки по эталонам, без энкодера/Qdrant (cv/audit.py)."""
    parser = build_parser()
    sub_actions = [a for a in parser._subparsers._group_actions if a.dest == "command"][0]
    assert set(sub_actions.choices) == {"build-index", "search", "bench", "selfcheck", "audit-refs"}


def test_build_index_requires_refs_and_version():
    parser = build_parser()
    args = parser.parse_args(["build-index", "--refs", "some/dir", "--version", "v1"])
    assert args.refs == "some/dir"
    assert args.version == "v1"
    assert args.func.__name__ == "cmd_build_index"


def test_build_index_refs_json_and_uploads_dir_args():
    """G3 (agents/G3-real-index.md п.1): `cv build-index --refs-json ... --uploads-dir
    ...` — альтернатива `--refs <dir|csv>` для боевого датасета кейса."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "build-index",
            "--refs-json",
            "case-data/slug_refs.json",
            "--uploads-dir",
            "case-data/uploads",
            "--version",
            "case-20260916",
        ]
    )
    assert args.refs_json == "case-data/slug_refs.json"
    assert args.uploads_dir == "case-data/uploads"
    assert args.refs is None  # --refs не задан и больше не required (было required=True)


def test_selfcheck_refs_json_and_sample_n_args():
    parser = build_parser()
    args = parser.parse_args(
        [
            "selfcheck",
            "--refs-json",
            "case-data/slug_refs.json",
            "--uploads-dir",
            "case-data/uploads",
            "--sample-n",
            "300",
            "--sample-seed",
            "7",
        ]
    )
    assert args.refs_json == "case-data/slug_refs.json"
    assert args.uploads_dir == "case-data/uploads"
    assert args.sample_n == 300
    assert args.sample_seed == 7


def test_bench_refs_json_and_sample_seed_args():
    """G3 п.3: `cv bench --refs-json ... --uploads-dir ... --n 300` — search p50/p95
    на боевом масштабе, случайная выборка по --sample-seed (не первые N алфавитно)."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "bench",
            "--refs-json",
            "case-data/slug_refs.json",
            "--uploads-dir",
            "case-data/uploads",
            "--n",
            "300",
            "--sample-seed",
            "3",
        ]
    )
    assert args.refs_json == "case-data/slug_refs.json"
    assert args.n == 300
    assert args.sample_seed == 3


def test_audit_refs_subcommand_registered_with_defaults():
    parser = build_parser()
    args = parser.parse_args(["audit-refs"])
    assert args.func.__name__ == "cmd_audit_refs"
    assert args.refs_json is None

    args2 = parser.parse_args(["audit-refs", "--refs-json", "x.json", "--uploads-dir", "up", "--out", "o.json"])
    assert (args2.refs_json, args2.uploads_dir, args2.out) == ("x.json", "up", "o.json")


def test_search_defaults():
    parser = build_parser()
    args = parser.parse_args(["search", "photo.jpg"])
    assert args.photo == "photo.jpg"
    assert args.top_k == 5
    assert args.no_normalize is False


def test_search_no_normalize_flag():
    parser = build_parser()
    args = parser.parse_args(["search", "photo.jpg", "--no-normalize"])
    assert args.no_normalize is True


def test_selfcheck_default_min_rate_matches_dod():
    parser = build_parser()
    args = parser.parse_args(["selfcheck"])
    assert args.min_rate == 0.9  # DoD: self-match >= 90%


# --- discover_refs_from_slug_refs_json (G3, agents/G3-real-index.md п.1) -----------------


def _write_uploads(tmp_path, names: dict[str, bytes]):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for name, data in names.items():
        (uploads / name).write_bytes(data)
    return uploads


def test_discover_refs_from_slug_refs_json_primary_and_extra_real(tmp_path):
    uploads = _write_uploads(tmp_path, {"a.webp": b"fake-a", "b.webp": b"fake-b", "c.webp": b"fake-c"})
    refs_json = tmp_path / "slug_refs.json"
    refs_json.write_text(
        json.dumps(
            {
                "mapping": {
                    "slug-one": {"name": "One", "winery": "W", "photos_csv": ["a.webp"], "files": ["a.webp"]},
                    # >1 файл (near-dup серия каталога) -> первый эталон, остальные доп-ракурсы
                    "slug-multi": {"name": "Multi", "winery": "W", "photos_csv": [], "files": ["b.webp", "c.webp"]},
                    # files=[] (F3 ещё не сопоставил фото, misses в том же JSON) -> пропускается
                    "slug-miss": {"name": "Miss", "winery": "W", "photos_csv": [], "files": []},
                    # файл есть в JSON, но не на диске -> пропускается (не падает)
                    "slug-ghost": {"name": "Ghost", "winery": "W", "photos_csv": [], "files": ["nope.webp"]},
                }
            }
        ),
        encoding="utf-8",
    )

    out = discover_refs_from_slug_refs_json(refs_json, uploads)

    assert set(out.keys()) == {"slug-one", "slug-multi"}
    assert out["slug-one"] == [uploads / "a.webp"]
    assert out["slug-multi"] == [uploads / "b.webp", uploads / "c.webp"]  # порядок = порядок в JSON


def test_discover_refs_from_slug_refs_json_supports_chosen_candidates_schema(tmp_path):
    """F3 (qa/case_census.py) переехал с `files: list[str]` на `chosen: str` +
    `candidates: list[str]` + `usable: bool` (16.09.2026, прямо в процессе этой волны,
    см. cv/cli.py::discover_refs_from_slug_refs_json). `chosen` идёт первым эталоном
    (даже если стоит не первым в `candidates`), остальные candidates — доп-ракурсы."""
    uploads = _write_uploads(tmp_path, {"a.webp": b"a", "b.webp": b"b", "c.webp": b"c"})
    refs_json = tmp_path / "slug_refs.json"
    refs_json.write_text(
        json.dumps(
            {
                "mapping": {
                    # chosen НЕ первый в candidates -> всё равно должен встать первым
                    "slug-multi": {"chosen": "b.webp", "candidates": ["a.webp", "b.webp", "c.webp"], "usable": True},
                    "slug-single": {"chosen": "a.webp", "candidates": ["a.webp"], "usable": True},
                    "slug-noise": {"chosen": "c.webp", "candidates": ["c.webp"], "usable": False},
                }
            }
        ),
        encoding="utf-8",
    )

    out = discover_refs_from_slug_refs_json(refs_json, uploads)
    assert set(out.keys()) == {"slug-multi", "slug-single"}
    assert out["slug-multi"] == [uploads / "b.webp", uploads / "a.webp", uploads / "c.webp"]
    assert out["slug-single"] == [uploads / "a.webp"]


def test_discover_refs_from_slug_refs_json_honors_usable_false(tmp_path):
    """Дополнение оркестратора (16.09.2026, п.2): F3 проставит `usable: false` в
    slug_refs.json на шумные эталоны (лайфстайл/виноградник/интерьер) — пересборка
    БЕЗ них должна быть той же командой с обновлённым JSON, без изменений в CLI."""
    uploads = _write_uploads(tmp_path, {"a.webp": b"fake-a", "b.webp": b"fake-b"})
    refs_json = tmp_path / "slug_refs.json"
    refs_json.write_text(
        json.dumps(
            {
                "mapping": {
                    "slug-good": {"name": "Good", "winery": "W", "photos_csv": [], "files": ["a.webp"], "usable": True},
                    "slug-noise": {"name": "Noise", "winery": "W", "photos_csv": [], "files": ["b.webp"], "usable": False},
                    # поле usable отсутствует -> трактуется как обычная (True) позиция,
                    # текущий датасет кейса его ещё не несёт вовсе
                    "slug-no-field": {"name": "NoField", "winery": "W", "photos_csv": [], "files": ["a.webp"]},
                }
            }
        ),
        encoding="utf-8",
    )

    out = discover_refs_from_slug_refs_json(refs_json, uploads)
    assert set(out.keys()) == {"slug-good", "slug-no-field"}


def test_load_near_dup_groups_reads_families_as_frozensets(tmp_path):
    """`cmd_selfcheck` должен использовать РЕАЛЬНЫЕ семьи каталога, не дев-фикстурную
    cv.selfcheck.NEAR_DUP_GROUPS (та покрывает только одну известную пару)."""
    refs_json = tmp_path / "slug_refs.json"
    refs_json.write_text(
        json.dumps(
            {
                "mapping": {},
                "families": {
                    "aligote-barrel": ["aligote-barrel-2024", "aligote-barrel-2025"],
                    "david": ["david", "david-2021", "david-2022"],
                },
            }
        ),
        encoding="utf-8",
    )
    groups = load_near_dup_groups(refs_json)
    assert frozenset({"aligote-barrel-2024", "aligote-barrel-2025"}) in groups
    assert frozenset({"david", "david-2021", "david-2022"}) in groups
    assert len(groups) == 2


def test_load_near_dup_groups_reads_standalone_f3_families_json(tmp_path):
    """F3 (qa/case_census.py) переехал со встроенного slug_refs.json["families"]
    (плоский {family: [slug,...]}) на отдельный families.json
    ({family_id: {"slugs": [...], "chosen_files": {...}, "ocr": {...}}}, 16.09.2026,
    прямо в процессе этой волны) — новая форма значений (dict с ключом "slugs"), без
    обёртки "families" на верхнем уровне."""
    families_json = tmp_path / "families.json"
    families_json.write_text(
        json.dumps(
            {
                "aligote-barrel": {
                    "slugs": ["aligote-barrel-2024", "aligote-barrel-2025"],
                    "chosen_files": {"aligote-barrel-2024": "x.webp", "aligote-barrel-2025": "x.webp"},
                    "differentiator": "год",
                },
                "empty-family": {"slugs": []},
            }
        ),
        encoding="utf-8",
    )
    groups = load_near_dup_groups(families_json)
    assert groups == [frozenset({"aligote-barrel-2024", "aligote-barrel-2025"})]  # пустая семья пропущена


def test_load_near_dup_groups_missing_file_returns_empty_list(tmp_path):
    groups = load_near_dup_groups(tmp_path / "does-not-exist.json")
    assert groups == []


def test_discover_refs_from_slug_refs_json_supports_flat_mapping(tmp_path):
    """Без обёртки {"mapping": {...}} — на случай другого формата экспорта."""
    uploads = _write_uploads(tmp_path, {"x.webp": b"fake-x"})
    refs_json = tmp_path / "flat.json"
    refs_json.write_text(
        json.dumps({"flat-slug": {"name": "Flat", "winery": "W", "photos_csv": [], "files": ["x.webp"]}}),
        encoding="utf-8",
    )

    out = discover_refs_from_slug_refs_json(refs_json, uploads)
    assert out == {"flat-slug": [uploads / "x.webp"]}
