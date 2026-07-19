"""Wave 168 — generic structural player recognition + registry scaffold.

Family-independent recognition: recognize controls by FUNCTION (aria/role/
class-token) and media by structure (<video>/<source>/blob), harvest resolution
tokens generically, classify APIs by response content-type. Brand families are
169+. SYNTHETIC fixtures only. Never empty, never enabled, F2-clean.
"""
import os, sys, json, shutil, tempfile, zipfile
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import player_recognition as pr            # noqa: E402
import build_template_from_wacz as btw      # noqa: E402
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets  # noqa: E402


def test_native_video_progressive():
    html = '<div><video controls><source src="https://cdn.example.com/a.mp4" type="video/mp4"></video></div>'
    rec = pr.detect(html)
    assert rec["player_family"] == "native_custom"
    assert rec["delivery"] == "progressive"
    assert rec["selectors"].get("player", {}).get("container")


def test_mse_blob():
    html = '<video src="blob:https://app.example.com/abc-123"></video>'
    rec = pr.detect(html)
    assert rec["player_family"] == "mse_blob_custom"
    assert any("segment" in n.lower() or "blob" in n.lower() or "mse" in n.lower() for n in rec["notes"])


def test_iframe_embed_not_introspected():
    html = '<iframe src="https://player.vimeo.com/video/123456"></iframe>'
    rec = pr.detect(html, iframe_hosts=["player.vimeo.com"])
    # pack C recognizes the specific embed; generic iframe_embed is the fallback
    # for any embed host without a dedicated family.
    assert rec["player_family"] in ("vimeo", "iframe_embed")
    assert rec["policy"] == "third_party_review_only"
    # internals of a cross-origin embed are never fabricated
    assert not rec["selectors"].get("quality")
    assert not rec["selectors"].get("settings")


def test_controls_by_function():
    html = ('<video></video>'
            '<button aria-label="Play"></button>'
            '<button aria-label="Settings"></button>'
            '<button aria-label="Fullscreen"></button>')
    sel = pr.generic_selectors(html)
    # canonical control key is play_button (the orphan `play` key was removed in
    # the 294 registry consolidation)
    assert sel.get("player", {}).get("play_button")
    assert sel.get("settings") or sel.get("quality", {}).get("open_menu")
    assert sel.get("player", {}).get("fullscreen")


def test_resolution_token_harvest():
    html = ('<ul class="menu"><li>1080p</li><li>720p</li><li>480p</li></ul>'
            '<video></video>')
    sel = pr.generic_selectors(html)
    res = sel.get("quality", {}).get("available_resolutions") or []
    assert set(res) >= {1080, 720, 480}


def test_classify_apis_by_content_type():
    net = [
        {"url": "https://x/playlist", "response_headers": [{"name": "content-type", "value": "application/vnd.apple.mpegurl"}]},
        {"url": "https://x/manifest", "response_headers": [{"name": "Content-Type", "value": "application/dash+xml"}]},
        {"url": "https://x/seg.mp4", "response_headers": [{"name": "content-type", "value": "video/mp4"}]},
        {"url": "https://x/api/info", "response_headers": [{"name": "content-type", "value": "application/json"}]},
    ]
    cls = pr.classify_apis(net)
    assert cls.get("hls") and cls.get("dash") and cls.get("progressive") and cls.get("json_api")


def test_graceful_degradation_never_empty_and_clean():
    html = '<div class="totally-unknown-widget"><span>play</span></div>'
    rec = pr.detect(html)
    assert rec["player_family"] in ("native_custom", "unknown")
    assert rec["notes"], "must emit a review note when recognition is thin"
    assert scan_artifact_secrets(rec) == []


def _make_wacz(capture):
    d = Path(tempfile.mkdtemp()); w = d / "s.wacz"
    with zipfile.ZipFile(w, "w") as z:
        z.writestr("archive/capture.json", json.dumps(capture))
    return w


def test_build_template_unions_generic_and_attaches_recognition():
    # rrweb-node capture: branded quality (theoplayer aria) + a generic fullscreen button
    el = lambda t, a=None, c=None, i=1: {"type": 2, "id": i, "tagName": t, "attributes": a or {}, "childNodes": c or []}
    snap = {"type": 0, "id": 0, "childNodes": [el("html", {}, [el("body", {}, [
        el("div", {"aria-label": "video player"}),
        el("video", {}),
        el("button", {"aria-label": "Fullscreen"}),
    ])])]}
    cap = {"url": "https://app.example.com/x", "host": "app.example.com",
           "captured_at": "2026-06-08T00:00:00Z", "dom_log_count": 1, "network_log_count": 0,
           "dom_log": [{"type": "full_snapshot", "data": {"node": snap}}], "network_log": []}
    wacz = _make_wacz(cap)
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    assert "recognition" in draft and draft["recognition"].get("player_family")
    # native <video> wins the container by function-priority (no brand family
    # here -> native_custom); the generic fullscreen control is unioned in.
    assert draft["selectors"]["player"]["container"] == "video"
    assert draft["selectors"]["player"].get("fullscreen")
    assert scan_artifact_secrets(draft) == []
