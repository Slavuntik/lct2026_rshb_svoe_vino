"""Адаптер нашего сканера под контракт ImageIndex монорепо.

Тесты идут без моделей и без сети: сканер подменяется заглушкой, потому что проверяется
именно шов — перевод нашего результата в `Match(slug, score, gap, view)` и поведение,
которого требует контракт (битый файл -> ValueError, отказ от нормализации -> кадр целиком).
"""

import io
import json
from dataclasses import dataclass

import pytest
from PIL import Image

from winescan.integration.vinchik_index import WHOLE_FRAME, WinescanImageIndex


@dataclass
class _Result:
    top5: list[dict]


class _FakeScanner:
    """Заглушка сканера: запоминает, с какой рамкой её позвали."""

    def __init__(self, top5):
        self._top5 = top5
        self.calls = []

        class _Config:
            indexes = ("siglip2-so400m-patch14-384__yaw-30_-15_0_15_30",)

        self.config = _Config()

    def scan(self, image, relative_box=None):
        self.calls.append(relative_box)
        return _Result(top5=list(self._top5))


def _png(size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _candidates():
    return [
        {"slug": "massandra-muskatel-belyy", "score": 0.91},
        {"slug": "massandra-muskatel-chernyy", "score": 0.88},
        {"slug": "fanagoria-cabernet", "score": 0.70},
    ]


def test_search_translates_our_result_into_contract_matches():
    index = WinescanImageIndex(scanner=_FakeScanner(_candidates()))

    matches = index.search(_png(), top_k=3)

    assert [m.slug for m in matches] == [c["slug"] for c in _candidates()]
    assert [m.view for m in matches] == ["real"] * 3
    assert matches[0].score == pytest.approx(0.91)
    # без переписи семей gap — отрыв от следующего кандидата выдачи
    assert matches[0].gap == pytest.approx(0.03)
    assert matches[-1].gap is None, "у последнего кандидата конкурентов нет: контрактное доминирование"


def test_top_k_limits_candidates():
    index = WinescanImageIndex(scanner=_FakeScanner(_candidates()))

    assert len(index.search(_png(), top_k=2)) == 2


def test_families_census_changes_gap_to_the_foreign_competitor(tmp_path):
    """С переписью семей gap меряется до первого кандидата ВНЕ семьи top-1 — правило монорепо."""
    families = tmp_path / "families.json"
    families.write_text(json.dumps({"families": {"massandra-muskatel": [
        "massandra-muskatel-belyy", "massandra-muskatel-chernyy"]}}), encoding="utf-8")  # fmt: skip
    index = WinescanImageIndex(scanner=_FakeScanner(_candidates()), families_json=families)

    matches = index.search(_png(), top_k=3)

    # сосед по семье пропущен: отрыв считается до «Фанагории» (0,91 - 0,70)
    assert matches[0].gap == pytest.approx(0.21)


def test_normalize_false_means_whole_frame():
    scanner = _FakeScanner(_candidates())
    index = WinescanImageIndex(scanner=scanner)

    index.search(_png(), normalize=True)
    index.search(_png(), normalize=False)

    assert scanner.calls == [None, WHOLE_FRAME]


def test_broken_file_raises_value_error():
    index = WinescanImageIndex(scanner=_FakeScanner(_candidates()))

    with pytest.raises(ValueError):
        index.search(b"not an image at all")


def test_index_version_reports_the_gallery():
    index = WinescanImageIndex(scanner=_FakeScanner(_candidates()))

    assert "yaw" in index.index_version


@pytest.mark.parametrize("method, args", [("embed", (b"x",)), ("build", ({}, "v1")), ("add", ("slug", []))])
def test_write_side_of_the_contract_is_refused_explicitly(method, args):
    """Контракт шире, чем читающий адаптер: отказ должен быть явным, а не тихой заглушкой."""
    index = WinescanImageIndex(scanner=_FakeScanner(_candidates()))

    with pytest.raises(NotImplementedError):
        getattr(index, method)(*args)


def test_details_preserve_rejection_and_request_local_ocr():
    from types import SimpleNamespace

    scanner = _FakeScanner(_candidates())
    responses = iter([
        SimpleNamespace(top5=_candidates(), status="not_found",
                        confidence={"ocr_text": "first label", "decision_reason": "visual"}),
        SimpleNamespace(top5=_candidates(), status="found", confidence={"ocr_text": ""}),
    ])
    scanner.scan = lambda *a, **kw: next(responses)
    index = WinescanImageIndex(scanner=scanner)
    first = index.search_with_details(_png())
    second = index.search_with_details(_png())
    assert first.status == "not_found"
    assert first.matches[0].slug == _candidates()[0]["slug"]
    assert first.ocr_text == "first label"
    assert second.ocr_text == ""
