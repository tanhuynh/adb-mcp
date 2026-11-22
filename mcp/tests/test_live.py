"""Live smoke tests against the real proxy + Photoshop. Skipped unless PS_LIVE=1.

Setup: proxy running (adb-proxy-socket), plugin connected, and a document opened from
LrC as a raw smart object is active. PS_LAYER_ID selects the layer (default 2).
Run:   PS_LIVE=1 uv run --extra dev pytest -m live -v
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("PS_LIVE") != "1", reason="set PS_LIVE=1 to run against Photoshop"),
]
MCP_DIR = Path(__file__).resolve().parents[1]
LAYER = int(os.environ.get("PS_LAYER_ID", "2"))


def _load_real(filename, modname):
    sys.path.insert(0, str(MCP_DIR))
    for name in ("core", "socket_client", "fonts"):   # drop stubs left by unit tests
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(modname, MCP_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bp():
    return _load_real("ps-batch-play.py", "ps_batch_play_live")


@pytest.fixture(scope="module")
def ps():
    return _load_real("ps-mcp.py", "ps_mcp_live")


def test_raw_baseline(bp):
    r = bp.get_raw_baseline()
    assert r["source"] in ("document_xmp_at_open", "unavailable")
    if r["source"] == "document_xmp_at_open":
        assert "WhiteBalance" in r["baseline"]


def test_check_stale_is_stable(bp):
    bp.check_stale(LAYER, mark=True)
    for _ in range(2):
        r = bp.check_stale(LAYER)
        assert r["stale"] is False, r


def test_document_image_16bit_ok(ps):
    img = ps.get_document_image(max_size=512)
    assert isinstance(img, ps.Image), img


def test_layer_image_keeps_aspect(ps):
    r = ps.sendCommand(ps.createCommand("getLayerImage", {"layerId": LAYER, "maxSize": 512}))
    assert r["status"] == "SUCCESS", r
    w, h = r["response"]["width"], r["response"]["height"]
    assert w == 512 and h < 512, (w, h)     # landscape source; height scaled, not forced
