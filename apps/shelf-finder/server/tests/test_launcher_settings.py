import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    "shelf_settings", Path(__file__).resolve().parents[4] / "tools/shelf_settings.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_local_and_remote_target():
    assert module.shelf_target({}, 8086) == ("http://127.0.0.1:8086", False)
    assert module.shelf_target({"VINCHIK_SHELF_URL": "https://gpu.example/"}, 8086) == (
        "https://gpu.example",
        True,
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/api",
        "https://user:secret@host",
        "https://host/v1",
        "https://host?key=x",
    ],
)
def test_bad_target(url):
    with pytest.raises(ValueError):
        module.shelf_target({"VINCHIK_SHELF_URL": url}, 8086)
