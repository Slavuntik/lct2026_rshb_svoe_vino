"""Exercise real run_local.py with fake ports/processes/env; never starts services."""

from io import StringIO
import json
import os
from pathlib import Path
import runpy
import signal
import socket
import subprocess
import sys
import urllib.request

import dotenv
import pytest

ROOT = Path(__file__).resolve().parents[4]


def run_launcher(
    monkeypatch, device, existing_cpu, error="A local service exited", extra=None
):
    launched = []
    monkeypatch.setattr(dotenv, "dotenv_values", lambda _: {})
    monkeypatch.setattr(os, "environ", {"SHELF_DEVICE": device, **(extra or {})})
    monkeypatch.setattr(signal, "signal", lambda *a: None)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: None)
    monkeypatch.setattr(Path, "open", lambda *a, **kw: StringIO())
    monkeypatch.syspath_prepend(str(ROOT / "tools"))

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def connect_ex(self, address):
            return 1

    monkeypatch.setattr(socket, "socket", Socket)

    def urlopen(url, timeout):
        if "/shelf/health" in url:
            if existing_cpu or any(p["cwd"].name == "shelf-finder" for p in launched):
                body = {"ready": True, "profile": "baseline", "device": "cpu"}
            else:
                raise OSError("No local shelf process")
        else:
            body = {"status": "ok", "warm": True}
        return StringIO(json.dumps(body))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    class Process:
        pid = 999999999

        def __init__(self, command, **kw):
            launched.append({"command": command, **kw})
            self.calls = 0

        def poll(self):
            self.calls += 1
            return None if self.calls == 1 else 0

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)
    with pytest.raises(RuntimeError, match=error):
        runpy.run_path(str(ROOT / "tools/run_local.py"), run_name="__main__")
    return launched


def test_launcher_auto_preserves_visible_gpus_when_unset(monkeypatch):
    launched = run_launcher(monkeypatch, "auto", False)
    shelf = next(p for p in launched if p["cwd"].name == "shelf-finder")
    assert shelf["env"]["SHELF_DEVICE"] == "auto"
    assert "CUDA_VISIBLE_DEVICES" not in shelf["env"]
    print("ATTACK fresh auto launcher: does not override CUDA visibility")


def test_explicit_cuda_rejects_cpu_service(monkeypatch):
    launched = run_launcher(monkeypatch, "cuda", True, error="another profile/device")
    assert not launched
    print("ATTACK explicit SHELF_DEVICE=cuda: existing CPU service rejected")


@pytest.mark.parametrize(
    "extra,expected",
    [
        ({"CUDA_VISIBLE_DEVICES": "0"}, "0"),
        ({"CUDA_VISIBLE_DEVICES": "0", "SHELF_GPU": "1"}, "1"),
    ],
)
def test_launcher_preserves_mask_or_explicit_override(monkeypatch, extra, expected):
    launched = run_launcher(monkeypatch, "auto", False, extra=extra)
    shelf = next(p for p in launched if p["cwd"].name == "shelf-finder")
    assert shelf["env"]["CUDA_VISIBLE_DEVICES"] == expected


def test_cpu_masks_cuda_even_with_explicit_gpu(monkeypatch):
    launched = run_launcher(monkeypatch, "cpu", False, extra={"SHELF_GPU": "2"})
    shelf = next(p for p in launched if p["cwd"].name == "shelf-finder")
    assert shelf["env"]["CUDA_VISIBLE_DEVICES"] == ""
