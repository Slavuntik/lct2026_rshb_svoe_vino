"""Смоук-тесты CLI: структура парсера (без реального энкодера/сети — быстро)."""
from __future__ import annotations

from cv.cli import build_parser


def test_all_four_subcommands_registered():
    parser = build_parser()
    sub_actions = [a for a in parser._subparsers._group_actions if a.dest == "command"][0]
    assert set(sub_actions.choices) == {"build-index", "search", "bench", "selfcheck"}


def test_build_index_requires_refs_and_version():
    parser = build_parser()
    args = parser.parse_args(["build-index", "--refs", "some/dir", "--version", "v1"])
    assert args.refs == "some/dir"
    assert args.version == "v1"
    assert args.func.__name__ == "cmd_build_index"


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
