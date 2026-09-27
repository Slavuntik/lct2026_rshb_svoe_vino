"""Тесты гейта уверенности и реплея архива сканов (qa/real_photos_archive_eval.py).

Проверяется то, на чём стоит вывод отчёта `reports/ml-lead-floor-sweep.md`:

1. предикат `confident()` — БИТ В БИТ семантика боевого `cv.text_fusion.fuse()`
   (`app/cv/service.py::_run_photo_scan_fusion`): пол по CV-скору top-1 И
   (`gap is None` = доминирование ⇒ маржа СЧИТАЕТСЯ пройденной, contracts/image-scan.md
   v0.4.7 §2) ИЛИ gap >= CV_FUSION_GAP_FLOOR; плюс обход по `ocr_verified`;
2. монотонность развёртки: понижение пола НИКОГДА не отнимает уверенный ответ —
   поэтому «потерять верное фото» при снижении порога невозможно по построению,
   и вся цена снижения — только новые уверенные ОШИБКИ;
3. разбор таблицы вердиктов qa-manual (119 строк, классы истины);
4. реплей гейта по сайдкарам архива воспроизводит корзины стенда 253/253.

Пункты 3–4 требуют данных кейса (вне git) и сами себя пропускают без них.

Запуск:  qa/.venv/bin/pytest tests/test_floor_sweep.py -q   (из каталога qa/)
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import real_photos_archive_eval as arc  # noqa: E402

HAS_ARCHIVE = (arc.ARCHIVE / "index.jsonl").exists()
HAS_REPORT = arc.REPORT.exists()


# --- 1. предикат гейта -----------------------------------------------------

@pytest.mark.parametrize(
    "cv, gap, floor, want",
    [
        (0.85, 0.10, 0.80, True),    # обычный уверенный
        (0.79, 0.10, 0.80, False),   # провал пола
        (0.79, 0.10, 0.78, True),    # тот же кадр при более низком поле
        (0.85, 0.01, 0.80, False),   # провал маржи
        (0.85, None, 0.80, True),    # gap=None — ДОМИНИРОВАНИЕ, маржа пройдена (v0.4.7 §2)
        (0.79, None, 0.80, False),   # при null-gap отказ возможен только по полу
        (0.80, 0.03, 0.80, True),    # границы включающие (>=, не >)
        (None, 0.10, 0.80, False),   # совпадений нет вовсе
    ],
)
def test_gate_matches_production_semantics(cv, gap, floor, want):
    assert arc.confident(cv, gap, floor) is want


def test_gate_ocr_verified_bypasses_margin_but_not_floor():
    """service.py: `cv_score >= floor AND (ocr_verified OR gap is None OR gap >= gap_floor)`
    — верификатор обходит МАРЖУ, но не ПОЛ."""
    assert arc.confident(0.85, 0.001, 0.80, ocr_verified=True) is True
    assert arc.confident(0.70, 0.001, 0.80, ocr_verified=True) is False


def test_gate_gap_floor_is_the_production_value():
    assert arc.GAP_FLOOR == 0.03  # CV_FUSION_GAP_FLOOR, развёртка его НЕ трогает


# --- 2. монотонность развёртки --------------------------------------------

@pytest.mark.parametrize("gap", [None, 0.0, 0.02, 0.03, 0.5])
def test_lowering_floor_never_takes_a_confident_answer_away(gap):
    for cv in (0.70, 0.7606, 0.78, 0.7999, 0.80, 0.95):
        seq = [arc.confident(cv, gap, f) for f in sorted(arc.FLOORS)]
        # по возрастанию пола уверенность может только гаснуть: True...True False...False
        assert seq == sorted(seq, reverse=True), (cv, gap, seq)


def test_floors_cover_the_asked_range():
    assert {0.76, 0.77, 0.78, 0.79, 0.80}.issubset(set(arc.FLOORS))


# --- 3. вердикты qa-manual -------------------------------------------------

@pytest.mark.skipif(not HAS_REPORT, reason="нет reports/qa-manual-scan-archive.md")
def test_verdict_table_parses_into_119_photos_with_known_classes():
    rows = arc.load_verdicts()
    assert len(rows) == 119
    assert len({r["id"] for r in rows}) == 119, "дублей кадров в таблице быть не должно"
    counts = {c: sum(r["cls"] == c for r in rows) for c in ("in_ok", "in_bad", "none", "skip")}
    # 57 «верно» + 15 «ПРОПУСК», из них 2 — где правильный слаг НЕ был top-1 (U08, F11)
    assert counts == {"in_ok": 70, "in_bad": 2, "none": 46, "skip": 1}
    assert sum(counts.values()) == 119


@pytest.mark.skipif(not HAS_REPORT, reason="нет reports/qa-manual-scan-archive.md")
def test_known_top1_wrong_photos_are_not_counted_as_rescued():
    """U08/F11 — вино в каталоге ЕСТЬ, но top-1 неверен: снижение порога делает их
    уверенной ОШИБКОЙ, а не спасённым фото. Регрессия этого списка ломает вывод."""
    rows = {r["id"]: r for r in arc.load_verdicts()}
    for rid in arc.TOP1_WRONG:
        assert rows[rid]["cls"] == "in_bad"
        assert rows[rid]["verdict"] == "ПРОПУСК"


# --- 4. реплей корзин архива ----------------------------------------------

@pytest.mark.skipif(not HAS_ARCHIVE, reason="нет case-data/scan-archive/scans")
def test_replay_reproduces_every_bucket_of_the_stand():
    side = arc.load_sidecars()
    assert len(side) == 253
    mismatched = [i for i, s in side.items()
                  if arc.confident(s["top1_score"], s.get("gap"), 0.80, s.get("ocr_verified"))
                  != (s["bucket"] == "confident")]
    assert mismatched == [], "формула гейта разошлась с боевой — цифры развёртки недействительны"


@pytest.mark.skipif(not (HAS_ARCHIVE and HAS_REPORT), reason="нет данных кейса")
def test_every_verdict_row_has_a_sidecar():
    side, rows = arc.load_sidecars(), arc.load_verdicts()
    assert [r["id"] for r in rows if r["id"] not in side] == []


@pytest.mark.skipif(not (HAS_ARCHIVE and HAS_REPORT), reason="нет данных кейса")
def test_archive_sweep_is_monotone_in_the_floor():
    """Ниже пол — не меньше верных карточек и не больше честных отказов (обе стороны)."""
    side, rows = arc.load_sidecars(), arc.load_verdicts()
    prev_ok, prev_hon = None, None
    for f in sorted(arc.FLOORS, reverse=True):
        ok = hon = 0
        for r in rows:
            if r["cls"] == "skip":
                continue
            s = side[r["id"]]
            c = arc.confident(s["top1_score"], s.get("gap"), f, s.get("ocr_verified"))
            ok += c and r["cls"] == "in_ok"
            hon += (not c) and r["cls"] == "none"
        if prev_ok is not None:
            assert ok >= prev_ok and hon <= prev_hon, f"немонотонно на floor={f}"
        prev_ok, prev_hon = ok, hon


@pytest.mark.skipif(not (HAS_ARCHIVE and HAS_REPORT), reason="нет данных кейса")
def test_vechernitsa_is_untouched_by_the_floor_in_the_swept_range():
    """«Вечерница» (Дивноморское, вина в каталоге НЕТ): все 4 кадра имеют CV-скор
    0.819–0.860, т.е. выше любого пола развёртки — две уверенные ошибки и два
    отказа-по-марже не меняются ни при 0.76, ни при 0.80."""
    side, rows = arc.load_sidecars(), {r["qid"]: r for r in arc.load_verdicts()}
    ids = [rows[q]["id"] for q in ("C36", "C43", "U03", "U05")]
    for f in (0.76, 0.77, 0.78, 0.79, 0.80):
        got = [arc.confident(side[i]["top1_score"], side[i].get("gap"), f, side[i].get("ocr_verified"))
               for i in ids]
        assert got == [True, True, False, False], f"порог {f} изменил исход «Вечерницы»: {got}"
