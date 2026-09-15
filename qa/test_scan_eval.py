"""pytest для qa/scan_eval.py — eval-раннер сканера кейса ЛЦТ (агент F, задача оркестратора
"новый этап: кейс ЛЦТ").

Группы тестов:
  * метрики (`compute_metrics`, `_percentile`, `_macro_f1`) — на крошечной фикстуре с ЗАРАНЕЕ
    посчитанным вручную ответом (см. докстринг `test_compute_metrics_matches_hand_computed_
    fixture` — там же дан полный расчёт), чтобы формулы были проверены, а не просто "не упали";
  * загрузчик (`load_eval_set`) — терпимость к CSV (алиасы заголовков, auto-discovery
    labels.csv) и к разбору имени файла без CSV, плюс явные предупреждения на несовпадениях;
  * сплит (`assign_split`/`filter_by_split`) — детерминизм, устойчивость к росту каталога
    (стабильный хэш, не shuffle), примерное соответствие доле holdout;
  * предикторы — `MockPredictor` (в процессе) и `FlatApiPredictor`/`RichApiPredictor` против
    настоящего слушающего `mock_scan_server` (свой процесс/поток, реальный TCP, не заглушка
    в памяти) — включая деградацию топ-5 до топ-1, когда `similar` пуст;
  * multipart: round-trip кодировщика `scan_eval._build_multipart` и парсера
    `mock_scan_server.parse_multipart` — свой формат должен читаться своим же парсером;
  * end-to-end: `run_eval` + `build_report`/`render_report_md`/`write_report` на фикстуре
    `qa/tests/fixtures/scan_mini/`;
  * CLI (`scan_eval.run`) — в процессе (быстро) и один прогон через настоящий subprocess,
    как его вызовет Makefile/мок-скрипт;
  * `qa/mock_case_script.sh` — рехёрсал через реальный `mock_scan_server` + реальный `curl`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import mock_scan_server as mss
import scan_eval as se

QA_DIR = Path(__file__).resolve().parent
SCAN_MINI_DIR = QA_DIR / "tests" / "fixtures" / "scan_mini"


# ----------------------------------------------------------------------------------
# _percentile
# ----------------------------------------------------------------------------------


def test_percentile_empty_returns_zero():
    assert se._percentile([], 50) == 0.0


def test_percentile_single_value():
    assert se._percentile([42.0], 50) == 42.0
    assert se._percentile([42.0], 95) == 42.0


def test_percentile_hand_computed():
    # sorted: [80, 100, 120, 150, 3000], N=5
    values = [100.0, 150.0, 120.0, 3000.0, 80.0]
    # p50: k=(5-1)*0.5=2.0 -> ранг ровно 2 -> s[2]=120
    assert se._percentile(values, 50) == pytest.approx(120.0)
    # p95: k=(5-1)*0.95=3.8 -> f=3,c=4 -> 150*0.2 + 3000*0.8 = 30+2400=2430
    assert se._percentile(values, 95) == pytest.approx(2430.0)


# ----------------------------------------------------------------------------------
# compute_metrics — крошечная фикстура с вручную посчитанным ответом
# ----------------------------------------------------------------------------------


def _fixture_records() -> list[se.RunRecord]:
    return [
        se.RunRecord(photo_id="q1", true_slug="A", top1_slug="A", top5_slugs=["A", "B", "C", "D", "E"], latency_ms=100.0),
        se.RunRecord(photo_id="q2", true_slug="B", top1_slug="A", top5_slugs=["A", "B", "C", "D", "E"], latency_ms=150.0),
        se.RunRecord(photo_id="q3", true_slug="C", top1_slug="A", top5_slugs=["A", "C", "D", "E", "F"], latency_ms=120.0),
        se.RunRecord(photo_id="q4", true_slug="D", top1_slug="X", top5_slugs=["X", "Y", "Z", "W", "V"], latency_ms=3000.0),
        se.RunRecord(photo_id="q5", true_slug="A", top1_slug="A", top5_slugs=["A", "B", "C", "D", "E"], latency_ms=80.0),
    ]


def test_compute_metrics_matches_hand_computed_fixture():
    """5 запросов, классы {A,B,C,D} (только реально встретившиеся как true_slug — см.
    докстринг `_macro_f1`), топ1-предсказания [A,A,A,X,A], топ5 см. `_fixture_records`.

    match-rate (top1): верно q1,q5 -> 2/5 = 0.4
    match-rate (top5): true в топ5 у q1,q2,q3,q5 (D у q4 отсутствует) -> 4/5 = 0.8

    F1 top1 (по классам {A,B,C,D}):
      A: TP=2 (q1,q5), FP=2 (q2,q3 предсказаны A, но true!=A), FN=0 -> P=0.5,R=1.0,F1=2/3
      B: TP=0, FP=0 (никто не предсказал B топ1), FN=1(q2) -> F1=0
      C: TP=0, FP=0, FN=1(q3) -> F1=0
      D: TP=0, FP=0, FN=1(q4) -> F1=0
      macro = (2/3)/4 = 1/6 ≈ 0.16667

    F1 top5 (по классам {A,B,C,D}, "предсказан" = класс встретился где-то в топ5 запроса —
    ВНИМАНИЕ, у класса C это НЕ симметрично классу B, хоть оба и встречаются в топ5 три раза
    "у чужих" — C дополнительно встречается в топ5 СВОЕГО ЖЕ q3 (top5(q3)=[A,C,D,E,F] содержит
    C), поэтому pred_pos(C)=4, а не 3, как у B, где top5(q3) класс B не содержит вовсе; эта
    асимметрия и есть главная причина писать тест на числах, а не полагаться на симметрию "по
    аналогии" — первая версия этого докстринга полагалась на неё и была неверна, см. git-историю):
      A: TP=2(q1,q5), A входит в топ5 у q1,q2,q3,q5(4 раза) -> FP=4-2=2, FN=0 -> P=0.5,R=1,F1=2/3
      B: TP=1(q2), B входит в топ5 у q1,q2,q5(3 раза, НЕ у q3 — top5(q3) без B) -> FP=3-1=2,
         FN=0 -> P=1/3,R=1,F1=2*(1/3)/(1/3+1)=1/2
      C: TP=1(q3), C входит в топ5 у q1,q2,q3,q5(4 раза, включая СВОЙ q3) -> FP=4-1=3, FN=0
         -> P=1/4,R=1,F1=2*(1/4)/(1/4+1)=2/5
      D: TP=0 (D не в топ5 q4), D в топ5 у q1,q2,q3,q5(4 раза) -> FP=4, FN=1(q4) -> F1=0
      macro = (2/3 + 1/2 + 2/5 + 0)/4 = (40/60+30/60+24/60)/4 = (94/60)/4 = 94/240 = 47/120 ≈ 0.39167

    p50/p95 задержек [100,150,120,3000,80] — см. test_percentile_hand_computed (120 / 2430).
    """
    metrics = se.compute_metrics(_fixture_records())
    assert metrics["n_total"] == 5
    assert metrics["n"] == 5
    assert metrics["n_errors"] == 0
    assert metrics["error_rate"] == 0.0
    assert metrics["match_rate"] == pytest.approx(0.4)
    assert metrics["match_rate_top5"] == pytest.approx(0.8)
    assert metrics["f1_top1"] == pytest.approx(1 / 6)
    assert metrics["f1_top5"] == pytest.approx(47 / 120)
    assert metrics["p50_ms"] == pytest.approx(120.0)
    assert metrics["p95_ms"] == pytest.approx(2430.0)


def test_compute_metrics_empty_records_returns_zeros_not_crash():
    metrics = se.compute_metrics([])
    assert metrics["n"] == 0
    assert metrics["n_total"] == 0
    assert metrics["match_rate"] == 0.0
    assert metrics["f1_top1"] == 0.0
    assert metrics["p50_ms"] == 0.0


def test_compute_metrics_excludes_errors_from_match_and_latency_but_counts_them():
    records = _fixture_records() + [
        se.RunRecord(photo_id="q6", true_slug="Z", top1_slug=None, top5_slugs=[], latency_ms=0.0, error="timeout"),
    ]
    metrics = se.compute_metrics(records)
    assert metrics["n_total"] == 6
    assert metrics["n"] == 5  # ошибочная запись не участвует в знаменателе
    assert metrics["n_errors"] == 1
    assert metrics["error_rate"] == pytest.approx(1 / 6)
    # остальные метрики — те же, что и без ошибочной записи (она не портит расчёт)
    assert metrics["match_rate"] == pytest.approx(0.4)
    assert metrics["p95_ms"] == pytest.approx(2430.0)


def test_top_confusions_ranks_most_frequent_miss_first():
    records = _fixture_records()  # 3 промаха top1: (B->A), (C->A), (D->X)
    confusions = se.top_confusions(records, limit=10)
    # B->A и C->A встречаются по разу каждая, но обе предсказаны как "A" — считаем по паре (true,pred)
    assert ("D", "X", 1) in confusions
    assert ("B", "A", 1) in confusions
    assert ("C", "A", 1) in confusions
    assert len(confusions) == 3


# ----------------------------------------------------------------------------------
# infer_slug_from_filename / load_eval_set
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("simple-slug.jpg", "simple-slug"),
        ("winery-name-with-many-hyphens-105.jpeg", "winery-name-with-many-hyphens-105"),
        ("slug__variant-a.png", "slug"),
        ("slug__001.webp", "slug"),
    ],
)
def test_infer_slug_from_filename(filename, expected):
    assert se.infer_slug_from_filename(filename) == expected


def test_load_eval_set_raises_on_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        se.load_eval_set(tmp_path / "does-not-exist")


def test_load_eval_set_filename_inference_without_csv(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    (photos_dir / "some-slug.jpg").write_bytes(b"x")
    (photos_dir / "other-slug__extra.png").write_bytes(b"y")
    (photos_dir / "not-an-image.txt").write_bytes(b"z")  # должен быть проигнорирован

    items, warnings = se.load_eval_set(photos_dir)

    by_id = {it.photo_id: it.true_slug for it in items}
    assert by_id == {"some-slug.jpg": "some-slug", "other-slug__extra.png": "other-slug"}
    assert warnings == []


def test_load_eval_set_csv_with_aliased_headers(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    (photos_dir / "a.jpg").write_bytes(b"x")
    (photos_dir / "b.jpg").write_bytes(b"y")
    csv_path = tmp_path / "my_labels.csv"
    csv_path.write_text("file,label\na.jpg,slug-a\nb.jpg,slug-b\n", encoding="utf-8")

    items, warnings = se.load_eval_set(photos_dir, csv_path)

    assert {it.photo_id: it.true_slug for it in items} == {"a.jpg": "slug-a", "b.jpg": "slug-b"}
    assert warnings == []


def test_load_eval_set_auto_discovers_labels_csv_in_dir(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    (photos_dir / "a.jpg").write_bytes(b"x")
    (photos_dir / "labels.csv").write_text("filename,slug\na.jpg,slug-a\n", encoding="utf-8")

    items, warnings = se.load_eval_set(photos_dir)  # без явного labels_csv

    assert len(items) == 1
    assert items[0].true_slug == "slug-a"
    assert warnings == []


def test_load_eval_set_warns_on_csv_row_missing_on_disk(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    (photos_dir / "a.jpg").write_bytes(b"x")
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text("filename,slug\na.jpg,slug-a\nghost.jpg,slug-ghost\n", encoding="utf-8")

    items, warnings = se.load_eval_set(photos_dir, csv_path)

    assert len(items) == 1
    assert any("ghost.jpg" in w for w in warnings)


def test_load_eval_set_warns_on_disk_file_not_in_csv(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    (photos_dir / "a.jpg").write_bytes(b"x")
    (photos_dir / "unlabeled.jpg").write_bytes(b"y")
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text("filename,slug\na.jpg,slug-a\n", encoding="utf-8")

    items, warnings = se.load_eval_set(photos_dir, csv_path)

    assert len(items) == 1
    assert any("unlabeled.jpg" in w for w in warnings)


def test_load_eval_set_raises_on_unresolvable_csv_headers(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    (photos_dir / "a.jpg").write_bytes(b"x")
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text("foo,bar\na.jpg,slug-a\n", encoding="utf-8")

    with pytest.raises(ValueError):
        se.load_eval_set(photos_dir, csv_path)


def test_load_eval_set_on_real_scan_mini_fixture():
    items, warnings = se.load_eval_set(SCAN_MINI_DIR)
    by_id = {it.photo_id: it.true_slug for it in items}
    assert by_id == {
        "demo-winery-flagship-red-2024.jpg": "demo-winery-flagship-red-2024",
        "demo-winery-flagship-red-2025.jpg": "demo-winery-flagship-red-2025",
        "demo-winery-chardonnay-white.jpg": "demo-winery-chardonnay-white",
        "other-winery-rose-brut.jpg": "other-winery-rose-brut",
    }
    assert warnings == []


# ----------------------------------------------------------------------------------
# Сплит dev/holdout
# ----------------------------------------------------------------------------------


def test_assign_split_deterministic_same_seed_same_result():
    ids = [f"photo-{i}.jpg" for i in range(200)]
    first = [se.assign_split(pid, seed=42, holdout_frac=0.2) for pid in ids]
    second = [se.assign_split(pid, seed=42, holdout_frac=0.2) for pid in ids]
    assert first == second


def test_assign_split_roughly_matches_holdout_frac():
    ids = [f"photo-{i}.jpg" for i in range(5000)]
    holdout_count = sum(1 for pid in ids if se.assign_split(pid, seed=42, holdout_frac=0.2) == "holdout")
    frac = holdout_count / len(ids)
    assert 0.17 <= frac <= 0.23  # допуск на хэш-шум при n=5000, seed фиксирован


def test_assign_split_stable_when_dataset_grows():
    """Стабильный хэш, не shuffle+slice: каталог растёт (~50 позиций/день, case.md) —
    уже размеченные фото НЕ должны менять сплит при добавлении новых."""
    base_ids = [f"photo-{i}.jpg" for i in range(300)]
    base_assignment = {pid: se.assign_split(pid, seed=7, holdout_frac=0.25) for pid in base_ids}
    grown_ids = base_ids + [f"photo-new-{i}.jpg" for i in range(50)]
    grown_assignment = {pid: se.assign_split(pid, seed=7, holdout_frac=0.25) for pid in grown_ids}
    for pid in base_ids:
        assert grown_assignment[pid] == base_assignment[pid]


def test_assign_split_rejects_invalid_holdout_frac():
    with pytest.raises(ValueError):
        se.assign_split("x", seed=1, holdout_frac=1.5)


def test_filter_by_split_all_returns_everything():
    items = [se.EvalItem(photo_id=f"p{i}", path=Path(f"p{i}.jpg"), true_slug=f"s{i}") for i in range(10)]
    assert se.filter_by_split(items, "all", seed=1, holdout_frac=0.2) == items


def test_filter_by_split_dev_and_holdout_partition_without_overlap():
    items = [se.EvalItem(photo_id=f"p{i}", path=Path(f"p{i}.jpg"), true_slug=f"s{i}") for i in range(200)]
    dev = se.filter_by_split(items, "dev", seed=99, holdout_frac=0.3)
    holdout = se.filter_by_split(items, "holdout", seed=99, holdout_frac=0.3)
    assert {it.photo_id for it in dev}.isdisjoint({it.photo_id for it in holdout})
    assert len(dev) + len(holdout) == len(items)


def test_filter_by_split_rejects_unknown_split():
    items = [se.EvalItem(photo_id="p", path=Path("p.jpg"), true_slug="s")]
    with pytest.raises(ValueError):
        se.filter_by_split(items, "bogus", seed=1, holdout_frac=0.2)


# ----------------------------------------------------------------------------------
# MockPredictor
# ----------------------------------------------------------------------------------


def test_mock_predictor_default_perfect_predicts_true_slug(tmp_path):
    items = [se.EvalItem(photo_id="a.jpg", path=tmp_path / "a.jpg", true_slug="slug-a")]
    predictor = se.MockPredictor()
    predictor.bind_true_slugs(items)
    pred = predictor.predict(b"bytes", "a.jpg")
    assert pred.top1_slug == "slug-a"
    assert pred.top5_slugs == ["slug-a"]
    assert pred.error is None


def test_mock_predictor_override_wins_over_default(tmp_path):
    items = [se.EvalItem(photo_id="a.jpg", path=tmp_path / "a.jpg", true_slug="slug-a")]
    predictor = se.MockPredictor(overrides={"a.jpg": "wrong-slug"})
    predictor.bind_true_slugs(items)
    pred = predictor.predict(b"bytes", "a.jpg")
    assert pred.top1_slug == "wrong-slug"


def test_mock_predictor_imperfect_without_binding_returns_unknown():
    predictor = se.MockPredictor(default_perfect=False)
    pred = predictor.predict(b"bytes", "unseen.jpg")
    assert pred.top1_slug == "__mock_unknown__"


# ----------------------------------------------------------------------------------
# multipart round-trip
# ----------------------------------------------------------------------------------


def test_multipart_encode_decode_roundtrip():
    content = b"\x00\x01binarycontent\xff\r\n---looks-like-a-boundary\r\n"
    body, boundary = se._build_multipart("image", "photo.jpg", content)
    content_type = f"multipart/form-data; boundary={boundary}"
    filename, decoded = mss.parse_multipart(content_type, body)
    assert filename == "photo.jpg"
    assert decoded == content


def test_multipart_parse_rejects_missing_boundary():
    with pytest.raises(ValueError):
        mss.parse_multipart("multipart/form-data", b"garbage")


# ----------------------------------------------------------------------------------
# FlatApiPredictor / RichApiPredictor против настоящего mock_scan_server
# ----------------------------------------------------------------------------------


@pytest.fixture
def mock_server_factory():
    servers: list[ThreadingHTTPServer] = []

    def _make(slug_map: dict | None = None, min_latency_ms: float = 1.0, max_latency_ms: float = 3.0) -> str:
        server = mss.serve(0, slug_map or {}, min_latency_ms, max_latency_ms, seed=1)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return f"http://localhost:{port}"

    yield _make

    for s in servers:
        s.shutdown()
        s.server_close()


def test_flat_api_predictor_against_mock_server(mock_server_factory):
    base_url = mock_server_factory(slug_map={"a.jpg": ["slug-a"]})
    predictor = se.FlatApiPredictor(base_url, timeout=5.0)
    pred = predictor.predict(b"bytes", "a.jpg")
    assert pred.error is None
    assert pred.top1_slug == "slug-a"
    assert pred.top5_slugs == ["slug-a"]
    assert pred.latency_ms >= 0


def test_flat_api_predictor_unmapped_filename_falls_back_to_inference(mock_server_factory):
    base_url = mock_server_factory(slug_map={})
    predictor = se.FlatApiPredictor(base_url, timeout=5.0)
    pred = predictor.predict(b"bytes", "some-real-slug.jpg")
    assert pred.top1_slug == "some-real-slug"


def test_rich_api_predictor_assembles_top5_from_similar(mock_server_factory):
    base_url = mock_server_factory(slug_map={"a.jpg": ["slug-a", "slug-b", "slug-c"]})
    predictor = se.RichApiPredictor(base_url, timeout=5.0)
    pred = predictor.predict(b"bytes", "a.jpg")
    assert pred.top1_slug == "slug-a"
    assert pred.top5_slugs == ["slug-a", "slug-b", "slug-c"]
    assert pred.degraded_top5 is False
    assert pred.top1_score == pytest.approx(0.9)


def test_rich_api_predictor_degrades_when_similar_empty(mock_server_factory):
    base_url = mock_server_factory(slug_map={"a.jpg": "slug-a"})
    predictor = se.RichApiPredictor(base_url, timeout=5.0)
    pred = predictor.predict(b"bytes", "a.jpg")
    assert pred.top5_slugs == ["slug-a"]
    assert pred.degraded_top5 is True


def test_flat_api_predictor_empty_slug_is_a_valid_miss_not_an_error(mock_server_factory):
    """Регресс: найдено живой проверкой против настоящего apps/api (B, contracts/
    image-scan.md) — {"slug": ""} валиден по контракту ("flat ВСЕГДА отдаёт лучший
    доступный slug"; при нуле кандидатов лучший доступный пуст) и НЕ должен считаться
    ошибкой транспорта — иначе честные "не нашли" тихо выпадают из знаменателя match-rate."""
    base_url = mock_server_factory(slug_map={"nothing.jpg": []})
    predictor = se.FlatApiPredictor(base_url, timeout=5.0)
    pred = predictor.predict(b"bytes", "nothing.jpg")
    assert pred.error is None
    assert pred.top1_slug is None
    assert pred.top5_slugs == []


def test_rich_api_predictor_not_in_catalog_is_a_valid_miss_not_an_error(mock_server_factory):
    base_url = mock_server_factory(slug_map={"nothing.jpg": []})
    predictor = se.RichApiPredictor(base_url, timeout=5.0)
    pred = predictor.predict(b"bytes", "nothing.jpg")
    assert pred.error is None
    assert pred.top1_slug is None
    assert pred.top5_slugs == []


def test_flat_api_predictor_reports_error_on_unreachable_server():
    predictor = se.FlatApiPredictor("http://localhost:1", timeout=1.0)  # порт 1: соединение точно не примут
    pred = predictor.predict(b"bytes", "a.jpg")
    assert pred.error is not None
    assert pred.top1_slug is None


# ----------------------------------------------------------------------------------
# run_eval + отчёт — end-to-end на scan_mini
# ----------------------------------------------------------------------------------


def test_run_eval_end_to_end_mock_predictor_on_scan_mini():
    items, warnings = se.load_eval_set(SCAN_MINI_DIR)
    assert warnings == []
    predictor = se.MockPredictor()
    predictor.bind_true_slugs(items)

    records = se.run_eval(items, predictor)
    metrics = se.compute_metrics(records)

    assert len(records) == 4
    assert metrics["match_rate"] == pytest.approx(1.0)  # идеальный оракул на своём же true_slug


def test_run_eval_end_to_end_against_mock_server_with_injected_confusion(mock_server_factory):
    """То же, что рехёрсал mock_case_script.sh делает через curl, но через Python-клиент —
    с mock_map.json фикстуры (намеренная near-dup ошибка на 1 из 4 фото)."""
    items, warnings = se.load_eval_set(SCAN_MINI_DIR)
    assert warnings == []
    slug_map = json.loads((SCAN_MINI_DIR / "mock_map.json").read_text(encoding="utf-8"))
    base_url = mock_server_factory(slug_map=slug_map)

    records = se.run_eval(items, se.FlatApiPredictor(base_url, timeout=5.0))
    metrics = se.compute_metrics(records)

    assert metrics["n"] == 4
    assert metrics["match_rate"] == pytest.approx(0.75)  # 3 из 4, единственный промах — near-dup
    misses = [r for r in records if not r.hit_top1]
    assert len(misses) == 1
    assert misses[0].true_slug == "demo-winery-flagship-red-2025"
    assert misses[0].top1_slug == "demo-winery-flagship-red-2024"


def test_build_report_and_render_md_contains_key_sections():
    items, _ = se.load_eval_set(SCAN_MINI_DIR)
    predictor = se.MockPredictor()
    predictor.bind_true_slugs(items)
    records = se.run_eval(items, predictor)
    report = se.build_report(
        mode="mock", api_url=None, split="all", seed=se.DEFAULT_SEED, holdout_frac=se.DEFAULT_HOLDOUT_FRAC,
        photos_dir=SCAN_MINI_DIR, items=items, records=records, load_warnings=[], run_warnings=[],
    )
    assert report["metrics"]["match_rate"] == pytest.approx(1.0)
    md = se.render_report_md(report)
    assert "match-rate (top-1)" in md
    assert "F1 top-1" in md
    assert "p95" in md


def test_eval_report_snapshot_matches_cv_eval_report_schema():
    """Схема, которую `apps/api/app/cv/eval_report.py::read_eval_report` ожидает по
    CV_EVAL_REPORT_PATH — ровно {index_version, f1_top1, f1_top5, match_rate, eval_set,
    measured_at}, никаких лишних ключей (та же функция читает файл как JSON-словарь и
    достаёт эти поля по имени)."""
    items, _ = se.load_eval_set(SCAN_MINI_DIR)
    predictor = se.MockPredictor()
    predictor.bind_true_slugs(items)
    records = se.run_eval(items, predictor)
    report = se.build_report(
        mode="mock", api_url=None, split="all", seed=se.DEFAULT_SEED, holdout_frac=se.DEFAULT_HOLDOUT_FRAC,
        photos_dir=SCAN_MINI_DIR, items=items, records=records, load_warnings=[], run_warnings=[],
        index_version="test-index-1",
    )
    snapshot = se.eval_report_snapshot(report)
    assert set(snapshot) == {"index_version", "f1_top1", "f1_top5", "match_rate", "eval_set", "measured_at"}
    assert snapshot["index_version"] == "test-index-1"
    assert snapshot["match_rate"] == pytest.approx(1.0)


def test_write_eval_report_snapshot_writes_valid_json(tmp_path):
    items, _ = se.load_eval_set(SCAN_MINI_DIR)
    predictor = se.MockPredictor()
    predictor.bind_true_slugs(items)
    records = se.run_eval(items, predictor)
    report = se.build_report(
        mode="mock", api_url=None, split="all", seed=se.DEFAULT_SEED, holdout_frac=se.DEFAULT_HOLDOUT_FRAC,
        photos_dir=SCAN_MINI_DIR, items=items, records=records, load_warnings=[], run_warnings=[],
    )
    path = se.write_eval_report_snapshot(report, tmp_path / "eval" / "report.json")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["match_rate"] == pytest.approx(1.0)


def test_fetch_index_version_against_mock_server(mock_server_factory):
    base_url = mock_server_factory()
    assert se._fetch_index_version(base_url, timeout=5.0) == "mock"


def test_fetch_index_version_returns_none_when_unreachable():
    assert se._fetch_index_version("http://localhost:1", timeout=1.0) is None


def test_cli_run_write_eval_report_flag(tmp_path):
    eval_report_path = tmp_path / "eval-report.json"
    exit_code = se.run(
        [
            "--photos-dir", str(SCAN_MINI_DIR),
            "--mode", "mock",
            "--out-dir", str(tmp_path / "out"),
            "--write-eval-report", str(eval_report_path),
        ]
    )
    assert exit_code == 0
    snapshot = json.loads(eval_report_path.read_text(encoding="utf-8"))
    assert snapshot["match_rate"] == pytest.approx(1.0)
    assert snapshot["f1_top1"] == pytest.approx(1.0)


def test_write_report_creates_json_and_md_files(tmp_path):
    items, _ = se.load_eval_set(SCAN_MINI_DIR)
    predictor = se.MockPredictor()
    predictor.bind_true_slugs(items)
    records = se.run_eval(items, predictor)
    report = se.build_report(
        mode="mock", api_url=None, split="all", seed=se.DEFAULT_SEED, holdout_frac=se.DEFAULT_HOLDOUT_FRAC,
        photos_dir=SCAN_MINI_DIR, items=items, records=records, load_warnings=[], run_warnings=[],
    )
    json_path, md_path = se.write_report(report, tmp_path / "out")
    assert json_path.exists() and md_path.exists()
    reloaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert reloaded["metrics"]["n"] == 4


# ----------------------------------------------------------------------------------
# CLI (scan_eval.run) — в процессе + один настоящий subprocess
# ----------------------------------------------------------------------------------


def test_cli_run_mock_mode_writes_reports_and_exit_zero(tmp_path):
    exit_code = se.run(
        [
            "--photos-dir", str(SCAN_MINI_DIR),
            "--mode", "mock",
            "--split", "all",
            "--out-dir", str(tmp_path / "out"),
        ]
    )
    assert exit_code == 0
    assert (tmp_path / "out" / "report.json").exists()
    assert (tmp_path / "out" / "report.md").exists()


def test_cli_run_missing_dir_exit_2(tmp_path):
    exit_code = se.run(
        ["--photos-dir", str(tmp_path / "nope"), "--mode", "mock", "--out-dir", str(tmp_path / "out")]
    )
    assert exit_code == 2


def test_cli_run_empty_split_result_exit_2(tmp_path):
    # holdout_frac=0.0 -> assign_split никогда не вернёт "holdout" -> пустой сет после фильтра
    exit_code = se.run(
        [
            "--photos-dir", str(SCAN_MINI_DIR),
            "--mode", "mock",
            "--split", "holdout",
            "--holdout-frac", "0.0",
            "--out-dir", str(tmp_path / "out"),
        ]
    )
    assert exit_code == 2


def test_cli_run_fail_under_match_rate_gate(tmp_path):
    slug_map_path = tmp_path / "map.json"
    slug_map_path.write_text(
        json.dumps({"demo-winery-flagship-red-2025.jpg": "wrong-on-purpose"}), encoding="utf-8"
    )
    exit_code = se.run(
        [
            "--photos-dir", str(SCAN_MINI_DIR),
            "--mode", "mock",
            "--mock-map", str(slug_map_path),
            "--out-dir", str(tmp_path / "out"),
            "--fail-under-match-rate", "0.9",  # 3/4=0.75 < 0.9 -> порог нарушен
        ]
    )
    assert exit_code == 1


def test_cli_subprocess_matches_expected_invocation(tmp_path):
    result = subprocess.run(
        [
            sys.executable, str(QA_DIR / "scan_eval.py"),
            "--photos-dir", str(SCAN_MINI_DIR),
            "--mode", "mock",
            "--out-dir", str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "match_rate=1.000" in result.stdout
    assert (tmp_path / "out" / "report.json").exists()


# ----------------------------------------------------------------------------------
# qa/mock_case_script.sh — рехёрсал через настоящий mock_scan_server + настоящий curl
# ----------------------------------------------------------------------------------


def test_mock_case_script_against_real_mock_server(mock_server_factory):
    slug_map = json.loads((SCAN_MINI_DIR / "mock_map.json").read_text(encoding="utf-8"))
    base_url = mock_server_factory(slug_map=slug_map)

    result = subprocess.run(
        [str(QA_DIR / "mock_case_script.sh"), str(SCAN_MINI_DIR)],
        env={"API_URL": base_url, "PATH": "/usr/bin:/bin:/usr/local/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "match-rate: 3/4 = 75.0%" in result.stdout
    assert "OK — механика подтверждена" in result.stdout
