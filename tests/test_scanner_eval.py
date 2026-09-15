import pytest

from winescan.eval.scanner_eval import decision_summary


def _record(expected, status, answer=""):
    return {"expected_slug": expected, "status": status, "answer_slug": answer}


def test_decision_summary_counts_answers_rejects_and_out_of_catalog():
    records = [
        _record("a", "found", "a"),
        _record("b", "found", "c"),
        _record("d", "not_found"),
        _record("e", "found", "e"),
        _record("", "not_found"),
        _record("", "found", "x"),
    ]

    summary = decision_summary(records)

    assert summary["in_catalog"] == 4 and summary["out_of_catalog"] == 2
    assert summary["answered_correct"] == pytest.approx(0.5)
    assert summary["answered_wrong"] == pytest.approx(0.25)
    assert summary["rejected"] == pytest.approx(0.25)
    assert summary["precision_of_answers"] == pytest.approx(2 / 3)
    assert summary["out_of_catalog_rejected"] == pytest.approx(0.5)
    assert summary["open_set_accuracy"] == pytest.approx(3 / 6)


def test_decision_summary_without_out_of_catalog():
    summary = decision_summary([_record("a", "found", "a")])

    assert summary["out_of_catalog_rejected"] is None and summary["open_set_accuracy"] == 1.0
