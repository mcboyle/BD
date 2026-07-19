"""workflow_diagnostic — characterization tests.

Pins the three layers (timeline / phases / template-diff) and the audit's safety rules:
independent evidence before diff, confidence on every step, blind-spot labeling (not inferred
absence), and noise exclusion from inference. Browser-free, stdlib + project modules.
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import workflow_diagnostic as W
from bulk_downloader.wacz_export import write_wacz

_T = 1_000_000


def _ready_capture():
    return {
        "host": "app.reptyle.com", "url": "https://app.reptyle.com/movies/9", "title": "Reptyle",
        "network_log": [
            {"timestamp": _T + 10, "type": "document", "method": "GET",
             "url": "https://app.reptyle.com/movies/9", "response_status": 200},
            {"timestamp": _T + 50, "type": "xhr", "method": "POST",
             "url": "https://app.reptyle.com/api/login", "response_status": 200},
            {"timestamp": _T + 120, "type": "xhr", "method": "GET",
             "url": "https://api2.reptyle.com/v2/movies/9/master.m3u8", "response_status": 200},
            {"timestamp": _T + 200, "type": "media", "method": "GET",
             "url": "https://cdn.reptyle.com/seg/0001.ts", "response_status": 200},
            {"timestamp": _T + 260, "type": "media", "method": "GET",
             "url": "https://cdn.reptyle.com/seg/0002.ts", "response_status": 200},
            {"timestamp": _T + 30, "type": "xhr", "method": "GET",
             "url": "https://www.google-analytics.com/collect?v=1", "response_status": 204},
        ],
        "dom_log": [
            {"timestamp": _T + 5, "type": "meta", "source": -1,
             "data": {"href": "https://app.reptyle.com/movies/9", "width": 1280, "height": 720}},
            {"timestamp": _T + 8, "type": "full_snapshot", "source": -1, "data": {}},
            {"timestamp": _T + 100, "type": "incremental", "source": 2, "data": {"source": 2}},
        ],
        "websocket_log": [{"request_id": "w1", "url": "wss://api2.reptyle.com/live",
                           "created_ms": _T + 150, "closed_ms": _T + 400, "frame_count": 3}],
        "storage_snapshot": {"local_storage": {"auth_token": "<scrubbed>"}, "session_storage": {}, "at": _T},
    }


def _gold_template():
    return {"host": "app.reptyle.com", "api": {"base": "https://api2.reptyle.com"},
            "network_patterns": {"manifest": ["api2.reptyle.com/.*master\\.m3u8"],
                                 "segment": ["cdn.reptyle.com/seg/.*\\.ts"]},
            "selectors": {"player": {"play": "button.play"}}}


# ── readiness (lead output) ───────────────────────────────────────
def test_ready_capture_is_ready():
    a = W.analyze(_ready_capture(), _gold_template())
    td = a["template_diff"]
    assert td["template_provided"] and td["readiness"] == "ready"
    assert td["missing_steps"] == []
    assert td["steps"]["manifest_fetched"]["observed"] and td["steps"]["manifest_fetched"]["confidence"] == "high"
    assert td["steps"]["api_base_reached"]["observed"]


def test_incomplete_capture_flagged_incomplete():
    cap = _ready_capture()
    # drop segments and the manifest -> playback/segment-stream missing
    cap["network_log"] = [e for e in cap["network_log"]
                          if not (e["url"].endswith(".ts") or e["url"].endswith(".m3u8"))]
    a = W.analyze(cap, _gold_template())
    td = a["template_diff"]
    assert td["readiness"] == "incomplete"
    assert "manifest_fetched" in td["missing_steps"] or "segment_stream" in td["missing_steps"]
    # independent evidence reflects the gap (no confirmation bias)
    assert td["observed_independent"]["manifests"] == []
    assert td["observed_independent"]["segments"] == 0


def test_independent_evidence_present_before_diff():
    a = W.analyze(_ready_capture(), _gold_template())
    td = a["template_diff"]
    oi = td["observed_independent"]
    # observed evidence is computed from the capture alone (keys exist regardless of template)
    assert set(oi) == {"hosts", "manifests", "segments", "interactions", "navigations"}


def test_no_template_gives_observed_only_no_verdict():
    a = W.analyze(_ready_capture(), None)
    td = a["template_diff"]
    assert td["template_provided"] is False
    assert "observed_independent" in td and "readiness" not in td


# ── phases + confidence labels ────────────────────────────────────
def test_every_phase_has_a_confidence_label():
    a = W.analyze(_ready_capture(), None)
    valid = {"high", "medium", "low", "blind"}
    for name, s in a["phases"].items():
        assert s["confidence"] in valid, (name, s["confidence"])
        assert isinstance(s["reason"], str) and s["reason"]


def test_playback_and_segment_are_high_confidence():
    a = W.analyze(_ready_capture(), None)
    assert a["phases"]["playback"]["confidence"] == "high"
    assert a["phases"]["segment_stream"]["confidence"] == "high"


def test_auth_absence_is_blind_not_absent():
    cap = _ready_capture()
    cap["network_log"] = [e for e in cap["network_log"] if "login" not in e["url"]]
    a = W.analyze(cap, None)
    auth = a["phases"]["auth"]
    assert auth["observed"] is False and auth["confidence"] == "blind"  # not asserted-absent


# ── noise exclusion ───────────────────────────────────────────────
def test_noise_excluded_from_hosts_and_flagged_in_timeline():
    a = W.analyze(_ready_capture(), _gold_template())
    hosts = a["template_diff"]["observed_independent"]["hosts"]
    assert not any("google-analytics" in h for h in hosts)
    ga = [e for e in a["timeline"] if "google-analytics" in (e.get("url") or "")]
    assert ga and ga[0]["noise"] is True  # kept but flagged


# ── timeline ordering ─────────────────────────────────────────────
def test_timeline_is_time_ordered():
    a = W.analyze(_ready_capture(), None)
    ts = [e["ts"] for e in a["timeline"] if e["ts"] is not None]
    assert ts == sorted(ts)


# ── blind-spot block always present ───────────────────────────────
def test_missing_signals_always_reported():
    a = W.analyze(_ready_capture(), _gold_template())
    assert any("Single capture" in m for m in a["missing_signals"])
    assert any("bodies" in m.lower() for m in a["missing_signals"])


# ── WACZ input path ───────────────────────────────────────────────
def test_loads_from_wacz():
    cap = _ready_capture()
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "cap.wacz")
        write_wacz(cap, out)
        loaded = W.load_capture(Path(out))
    a = W.analyze(loaded, _gold_template())
    assert a["template_diff"]["readiness"] == "ready"


# ── render does not throw + leads with readiness ──────────────────
def test_render_markdown_leads_with_template_diff():
    a = W.analyze(_ready_capture(), _gold_template())
    md = W.render_markdown(a)
    assert "Template-diff (Reptyle-readiness)" in md
    assert md.index("Template-diff") < md.index("## Phases") < md.index("## Timeline")


# ── fMP4 / DASH segment recognition (Cloudflare Stream) ──────────
def _cloudflare_stream_capture():
    """Capture where playback used Cloudflare Stream fMP4/DASH segments
    (seg_N.mp4 / init.mp4) — NO HLS .ts. Mirrors the real reptyle capture that
    the HLS-only classifier scored as 0 segments (false negative)."""
    base = "https://customer-bk3o9te23pydwwcb.cloudflarestream.com/0421687814ca/"
    return {
        "host": "app.reptyle.com", "url": "https://app.reptyle.com/movies/32088", "title": "Reptyle",
        "network_log": [
            {"timestamp": _T + 10, "type": "document", "method": "GET",
             "url": "https://app.reptyle.com/movies/32088", "response_status": 200},
            {"timestamp": _T + 50, "type": "xhr", "method": "GET",
             "url": "https://api2.reptyle.com/api/v1/movie/32088/watch", "response_status": 200},
            # manifest (Cloudflare Stream signed-JWT path, no .m3u8 ext) — counted as manifest by ext-less rule? no; include a real one:
            {"timestamp": _T + 90, "type": "xhr", "method": "GET",
             "url": "https://api2.reptyle.com/v2/movies/32088/master.m3u8", "response_status": 200},
            # fMP4 / DASH segments — the bit the HLS classifier misses:
            {"timestamp": _T + 120, "type": "media", "method": "GET",
             "url": base + "video/240/init.mp4?p=eyJ0", "response_status": 200},
            {"timestamp": _T + 140, "type": "media", "method": "GET",
             "url": base + "video/240/seg_1.mp4?p=eyJ0", "response_status": 200},
            {"timestamp": _T + 160, "type": "media", "method": "GET",
             "url": base + "audio/131/seg_1.mp4?p=eyJ0", "response_status": 200},
            {"timestamp": _T + 180, "type": "media", "method": "GET",
             "url": base + "video/480/seg_3.mp4?p=eyJ0", "response_status": 200},
            # noise that must NOT be counted as a segment:
            {"timestamp": _T + 30, "type": "xhr", "method": "GET",
             "url": "https://analytics.google.com/g/collect?v=2&tid=G-X", "response_status": 204},
            {"timestamp": _T + 200, "type": "xhr", "method": "GET",
             "url": "https://vod1.cachefly.net/ZGlybWF0Y2g9dHJ1ZQ==", "response_status": 200},
        ],
        "dom_log": [],
        "websocket_log": [],
        "storage_snapshot": {"local_storage": {}, "session_storage": {}, "at": _T},
    }


def test_fmp4_segments_recognized_as_stream():
    a = W.analyze(_cloudflare_stream_capture(), _gold_template())
    step = a["template_diff"]["steps"]["segment_stream"]
    assert step["observed"] is True, "Cloudflare fMP4 segments should count as a stream"
    assert step["confidence"] == W.HIGH


def test_fmp4_segments_drive_readiness():
    a = W.analyze(_cloudflare_stream_capture(), _gold_template())
    td = a["template_diff"]
    assert td["observed_independent"]["segments"] >= 2, "observed must include fMP4/DASH"
    assert "segment_stream" not in td["missing_steps"], "segment_stream must not be flagged missing"


def test_fmp4_detector_rejects_analytics_and_direct_download():
    cap = _cloudflare_stream_capture()
    found = W._fmp4_dash_segments(cap)
    assert len(found) == 4, f"expected 4 real segments, got {len(found)}"
    assert not any("analytics.google" in u or "cachefly" in u for u in found)
