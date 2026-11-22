"""Shared fixtures: load the MCP server scripts with Photoshop/proxy dependencies stubbed.

The servers are plain scripts with hyphenated names and module-level side effects
(socket config, font scan), so they are loaded by path with `core`, `socket_client`
and `fonts` replaced in sys.modules. Each test patches `sendCommand` on the loaded
module with a FakePS that records commands and returns canned responses.
"""
import base64
import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

MCP_DIR = Path(__file__).resolve().parents[1]


def _stub_modules(monkeypatch):
    core = types.ModuleType("core")
    core.init = lambda app, sock: None
    core.createCommand = lambda action, options: {
        "application": "photoshop", "action": action, "options": options}
    core.sendCommand = lambda cmd: pytest.fail(f"sendCommand not patched: {cmd}")
    sock = types.ModuleType("socket_client")
    sock.configure = lambda **kw: None
    fonts = types.ModuleType("fonts")
    fonts.list_all_fonts_postscript = lambda: []
    for m in (core, sock, fonts):
        monkeypatch.setitem(sys.modules, m.__name__, m)


def _load(monkeypatch, filename, modname):
    _stub_modules(monkeypatch)
    spec = importlib.util.spec_from_file_location(modname, MCP_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakePS:
    """Stands in for core.sendCommand: records commands, returns queued responses."""

    def __init__(self):
        self.calls, self._responses = [], []

    def queue(self, *responses):
        self._responses.extend(responses)
        return self

    def __call__(self, cmd):
        self.calls.append(cmd)
        if not self._responses:
            pytest.fail(f"unexpected extra command: {cmd['action']}")
        return self._responses.pop(0)


def ok(payload):
    return {"status": "SUCCESS", "response": payload}


def fail(msg="boom"):
    return {"status": "FAILURE", "message": msg}


def jpeg_payload(img: Image.Image) -> dict:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"base64Image": b64, "dataUrl": f"data:image/jpeg;base64,{b64}",
            "width": img.width, "height": img.height, "format": "jpeg"}


def gradient(w=512, h=341, offset=(0, 0, 0)) -> Image.Image:
    """A smooth landscape-ish test image; offset shifts R/G/B to mimic exposure/WB edits."""
    import numpy as np
    x, y = np.meshgrid(np.linspace(0, 1, w), np.linspace(0, 1, h))
    base = np.stack([40 + 150 * x + 20 * y, 50 + 120 * y, 90 + 100 * (1 - y)], axis=2)
    arr = np.clip(base + np.array(offset)[None, None, :], 0, 255).astype("uint8")
    return Image.fromarray(arr, "RGB")


@pytest.fixture
def fake():
    return FakePS()


@pytest.fixture
def bp(monkeypatch, fake):
    mod = _load(monkeypatch, "ps-batch-play.py", "ps_batch_play")
    monkeypatch.setattr(mod, "sendCommand", fake)
    return mod


@pytest.fixture
def ps(monkeypatch, fake):
    mod = _load(monkeypatch, "ps-mcp.py", "ps_mcp")
    monkeypatch.setattr(mod, "sendCommand", fake)
    return mod
