from types import SimpleNamespace

import pytest

from app.config import Settings
from app.cv.interface import Match
from app.cv.service import run_photo_scan
from app.rag.mock import MockRetriever


@pytest.mark.parametrize("status", ["found", "not_found"])
@pytest.mark.parametrize("text", ["", "Шато Вымысел Каберне"])
def test_winescan_evidence_controls_acceptance_and_reuses_ocr(status, text, monkeypatch):
    from app.cv import service

    calls = []
    class Index:
        def search_with_details(self, image, top_k, normalize):
            calls.append(normalize)
            return SimpleNamespace(
                matches=[Match("shato-vymysel-cabernet", 0.99, 0.2, "real")],
                status=status, ocr_text=text,
            )
    class Verifier:
        def read_query_text(self, image):
            raise AssertionError("OCR must not run again, even for empty text")
    seen = []
    def rerank(matches, ocr, *args):
        seen.append(ocr)
        return matches
    monkeypatch.setattr(service, "_apply_text_rerank", rerank)
    result = run_photo_scan(
        image_bytes=b"test", image_index=Index(), verifier=Verifier(),
        retriever=MockRetriever(),
        settings=Settings(image_provider="winescan", cv_text_rerank=True),
        user_box_applied=True,
    )
    assert calls == [False]
    assert seen == [text]
    assert result.not_in_catalog == (status == "not_found")
    assert result.best_guess_slug == "shato-vymysel-cabernet"
    assert result.candidates
