"""Tests for audit metrics, independent of model packages and datasets."""
from qa.label_audit import percentile, summarize


def row(kind='known', truth='wine', slug='wine', candidates=None, status=200, error=None):
    return {'truth_kind': kind, 'true_slug': truth, 'http_status': status,
            'error': error, 'elapsed_ms': 100,
            'response': {'slug': slug, 'not_in_catalog': not bool(slug),
                         'matches': [{'slug': s} for s in (candidates or [])]}}


def test_precision_counts_absent_false_accepts_and_known_mistakes():
    result = summarize([row(), row(kind='absent'), row(slug='wrong'),
                        row(kind='absent', slug=None)])
    assert result['precision_accepted'] == 1 / 3
    assert result['recall_accepted_known'] == 1 / 2
    assert result['end_to_end_accuracy'] == 1 / 2
    assert result['false_accepts_absent'] == 1
    assert result['wrong_accepted_known'] == 1


def test_failed_requests_are_not_correct_rejections_or_candidates():
    result = summarize([row(kind='absent', slug=None, status=500),
                        row(status=500, candidates=['wine'])])
    assert result['correct_rejections'] == 0
    assert result['candidate_top1_correct'] == 0
    assert result['http_or_protocol_errors'] == 2
    assert result['end_to_end_accuracy'] == 0


def test_raw_candidates_are_separate_from_accepted_results():
    result = summarize([row(slug=None, candidates=['wine']),
                        row(slug=None, candidates=['other', 'wine']),
                        row(kind='unknown')])
    assert result['candidate_top1_correct'] == 1
    assert result['candidate_top5_correct'] == 2
    assert result['correct_accepted'] == 0
    assert result['precision_accepted'] is None
    assert result['unscored'] == 1


def test_invalid_json_shape_does_not_crash_report():
    invalid = row(error='Expected JSON object')
    invalid['response'] = []
    assert summarize([invalid])['http_or_protocol_errors'] == 1


def test_percentile_interpolation_and_empty():
    assert percentile([], .95) is None
    assert percentile([10], .95) == 10
    assert percentile([100, 0], .95) == 95
