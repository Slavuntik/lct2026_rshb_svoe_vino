"""A green health check cannot substitute for successful label inference."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('release_gate', Path(__file__).parents[1] / 'check-release.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize('body', [{'slug': ''}, {'slug': 'wrong', 'not_in_catalog': False},
                                  {'slug': None, 'not_in_catalog': True}, [], None])
def test_known_positive_never_accepts_empty_or_wrong_result(body):
    with pytest.raises(RuntimeError):
        gate.validate_scan(body, 'expected')


def test_negative_cannot_pass_with_a_card():
    with pytest.raises(RuntimeError):
        gate.validate_scan({'slug': 'wrong', 'not_in_catalog': False}, None)
    gate.validate_scan({'slug': None, 'not_in_catalog': True}, None)
    gate.validate_scan({'slug': 'expected', 'not_in_catalog': False}, 'expected')


def test_gate_really_uploads_positive_and_negative(tmp_path, monkeypatch):
    from PIL import Image
    image = tmp_path / 'reference.webp'
    Image.new('RGB', (32, 64), 'red').save(image)
    calls = []
    def scan(data, filename):
        calls.append((data, filename))
        return {'slug': 'expected' if len(calls) == 1 else None, 'not_in_catalog': len(calls) != 1}
    monkeypatch.setattr(gate, 'scan', scan)
    gate.check_labels({'RELEASE_SCAN_SMOKE_IMAGE': str(image), 'RELEASE_SCAN_SMOKE_SLUG': 'expected'})
    assert calls[0][0] == image.read_bytes()
    assert calls[1][1] == 'negative.png'
    assert calls[1][0] != calls[0][0]
