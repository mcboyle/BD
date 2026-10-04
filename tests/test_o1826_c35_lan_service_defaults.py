"""O1826 C35 -- no fleet LAN IP as a product default; satellite_video fixes.

M132/M057/M055: with the endpoint env var unset, satellite_video,
embeddings_client and dom_structure_analyzer must open NO connection (the
offload is off) and decide locally. NEG: a configured endpoint IS used.
M133: a non-faststart MP4 (moov after the 64 KB head) that the satellite
calls invalid is inconclusive -> local ffprobe decides. NEG: a faststart
file's invalid verdict still stands.
M134: an offload failure is logged, not swallowed.
"""
from __future__ import annotations

import http.client
import importlib
import json
import logging
import os
import struct

import pytest

BD_GATE_SCOPE = "module"

ENV = {
    "satellite_video": "SATELLITE_VIDEO_ENDPOINT",
    "embeddings_client": "EMBEDDINGS_ENDPOINT",
    "dom_structure_analyzer": "DOM_ANALYZER_ENDPOINT",
}
LOCAL_VERDICT = (True, "local-ffprobe-sentinel")
HTML = '<html><body><div class="player"><video class="main" src="a.mp4"></video></div></body></html>'


@pytest.fixture
def fresh():
    """Reload bulk_downloader.<name> under a given endpoint env (None = unset);
    restore the env and reload again at teardown."""
    saved = {var: os.environ.get(var) for var in ENV.values()}
    touched = []

    def _load(name, value=None):
        var = ENV[name]
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value
        mod = importlib.import_module(f"bulk_downloader.{name}")
        touched.append(mod)
        return importlib.reload(mod)

    yield _load
    for var, val in saved.items():
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val
    for mod in touched:
        importlib.reload(mod)


@pytest.fixture
def satellite(monkeypatch):
    """Fake http.client connections: record every request, answer with ``reply``."""
    state = {"calls": [], "reply": {"valid": True, "has_video": True}, "refuse": False}

    class _Resp:
        status = 200

        def read(self):
            return json.dumps(state["reply"]).encode("utf-8")

    class _Conn:
        def __init__(self, host, port, timeout=None):
            self.host, self.port = host, port

        def request(self, method, path, body=None, headers=None):
            state["calls"].append((self.host, self.port, path, json.loads(body)))
            if state["refuse"]:
                raise ConnectionRefusedError("c35 refused")

        def getresponse(self):
            return _Resp()

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", _Conn)
    monkeypatch.setattr(http.client, "HTTPSConnection", _Conn)
    return state


@pytest.fixture
def local_ffprobe(monkeypatch):
    from bulk_downloader import integrity
    seen = []

    def _probe(path):
        seen.append(path)
        return LOCAL_VERDICT

    monkeypatch.setattr(integrity, "_verify_with_ffprobe", _probe)
    return seen


@pytest.fixture
def egress(monkeypatch):
    """Record every ai_provider urlopen (the embeddings/DOM transport)."""
    state = {"urls": [], "reply": {}}

    class _Resp:
        status = 200

        def read(self):
            return json.dumps(state["reply"]).encode("utf-8")

    def _open(req, timeout=None):
        state["urls"].append(req.full_url)
        return _Resp()

    monkeypatch.setattr("bulk_downloader.ai_provider.urlopen", _open)
    return state


def _box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _mp4(tmp_path, faststart):
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
    moov = _box(b"moov", _box(b"mvhd", b"\x00" * 100))
    mdat = _box(b"mdat", b"\x00" * (200 * 1024))
    p = tmp_path / ("fast.mp4" if faststart else "tail.mp4")
    p.write_bytes(ftyp + (moov + mdat if faststart else mdat + moov))
    return p


# --- M132 satellite_video ---------------------------------------------------

def test_satellite_video_unset_env_opens_no_connection(fresh, satellite, local_ffprobe, tmp_path):
    sv = fresh("satellite_video")
    p = _mp4(tmp_path, faststart=True)
    assert sv.validate_video_rpc(p) == LOCAL_VERDICT
    assert satellite["calls"] == [], f"LAN offload contacted with env unset: {satellite['calls']}"
    assert local_ffprobe == [str(p)]
    assert "10.0.70." not in sv.DEFAULT_ENDPOINT


def test_integrity_routes_video_locally_when_unset(fresh, satellite, local_ffprobe, tmp_path):
    fresh("satellite_video")
    from bulk_downloader import integrity
    p = _mp4(tmp_path, faststart=True)
    assert integrity.verify_media_integrity(str(p)) == LOCAL_VERDICT
    assert satellite["calls"] == []


def test_satellite_video_configured_endpoint_is_used(fresh, satellite, local_ffprobe, tmp_path):
    sv = fresh("satellite_video", "http://sat.example.lan:9123/api/video/validate")
    p = _mp4(tmp_path, faststart=True)
    assert sv.validate_video_rpc(p) == (True, "")
    assert [(h, port, path) for h, port, path, _ in satellite["calls"]] == [
        ("sat.example.lan", 9123, "/api/video/validate")]
    assert local_ffprobe == []


# --- M133 non-faststart MP4 -------------------------------------------------

def test_moov_at_end_invalid_verdict_is_inconclusive(fresh, satellite, local_ffprobe, tmp_path):
    sv = fresh("satellite_video", "http://sat.example.lan:9123/v")
    satellite["reply"] = {"valid": False, "has_video": False, "error": "moov atom not found"}
    p = _mp4(tmp_path, faststart=False)
    assert b"moov" not in p.read_bytes()[:65536]
    assert sv.validate_video_rpc(p) == LOCAL_VERDICT
    assert len(satellite["calls"]) == 1
    assert local_ffprobe == [str(p)]


def test_faststart_invalid_verdict_still_stands(fresh, satellite, local_ffprobe, tmp_path):
    sv = fresh("satellite_video", "http://sat.example.lan:9123/v")
    satellite["reply"] = {"valid": False, "has_video": True, "error": "corrupt stsz"}
    p = _mp4(tmp_path, faststart=True)
    assert sv.validate_video_rpc(p) == (False, "corrupt stsz")
    assert local_ffprobe == []


# --- M134 offload failure is logged ------------------------------------------

def test_offload_failure_is_logged(fresh, satellite, local_ffprobe, tmp_path, caplog):
    sv = fresh("satellite_video", "http://sat.example.lan:9123/v")
    satellite["refuse"] = True
    p = _mp4(tmp_path, faststart=True)
    with caplog.at_level(logging.DEBUG, logger="bulk_downloader.satellite_video"):
        assert sv.validate_video_rpc(p) == LOCAL_VERDICT
    msgs = [r.getMessage() for r in caplog.records if r.name == "bulk_downloader.satellite_video"]
    assert any("c35 refused" in m and "sat.example.lan" in m for m in msgs), msgs


# --- M057 embeddings_client -------------------------------------------------

def test_embeddings_unset_env_stays_local(fresh, egress):
    ec = fresh("embeddings_client")
    from bulk_downloader import embeddings as local
    assert ec.embed("hello") == local.embed("hello", dims=local.DEFAULT_DIMS)
    assert egress["urls"] == [], f"LAN GPU contacted with env unset: {egress['urls']}"
    assert "10.0.70." not in ec.DEFAULT_ENDPOINT


def test_embeddings_configured_endpoint_is_used(fresh, egress):
    ec = fresh("embeddings_client", "http://gpu.example.lan:8081/api/embeddings")
    egress["reply"] = {"embedding": [0.5, -0.25]}
    assert ec.embed("hello") == [0.5, -0.25]
    assert egress["urls"] == ["http://gpu.example.lan:8081/api/embeddings"]


# --- M055 dom_structure_analyzer --------------------------------------------

def test_dom_analyzer_unset_env_uses_rule_based(fresh, egress):
    dsa = fresh("dom_structure_analyzer")
    out = dsa.analyze_dom_structure(HTML)
    assert egress["urls"] == [], f"LAN inference contacted with env unset: {egress['urls']}"
    assert out["source"] == "rule_based_fallback"
    assert out["selectors"]
    assert "10.0.70." not in dsa.DEFAULT_INFERENCE_ENDPOINT


def test_dom_analyzer_configured_endpoint_is_used(fresh, egress):
    dsa = fresh("dom_structure_analyzer", "http://127.0.0.1:11434")
    egress["reply"] = {"response": '{"selectors": ["video.main"]}'}
    out = dsa.analyze_dom_structure(HTML)
    assert egress["urls"] == ["http://127.0.0.1:11434/api/generate"]
    assert out["source"] == "inference"
    assert out["selectors"] == ["video.main"]
