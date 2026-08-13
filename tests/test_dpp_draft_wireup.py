"""D++ draft wire-up — surface cut-1 (config_seam/renditions) + cut-2
(recognize_protocol: protocols/ladder/segments/poster) into the build_template
DRAFT's review-only ``recognition`` block, additively.

Until now ``detect()`` computed ``config_seam``/``renditions`` and the new
``recognize_protocol`` produced the network ladder, but the builder dropped
them. This pins the draft to carry them so a reviewer sees the recovered ladder
+ protocol + what was rejected as poster/segment. Additive only — existing
``recognition`` keys (player_family/delivery/policy/...) are unchanged.

Golden-pin (RED before the wire-up, GREEN after). SYNTHETIC capture only; F2.
"""
import os
import sys
import tempfile
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import build_template_from_wacz as b  # noqa: E402

_TMP = tempfile.NamedTemporaryFile(suffix=".wacz", delete=False)
_TMP.write(b"synthetic")
_TMP.close()

_JW_HTML = (
    '<div id="jwplayer-0" class="jwplayer jw-reset"><div class="jw-icon-display"></div></div>'
    '<script>jwplayer("jwplayer-0").setup({playlist:[{sources:['
    '{file:"https://media.example.com/v/abc_1080.mp4?token=SEC",label:"1080p",height:1080,bitrate:5200000,type:"video/mp4"},'
    '{file:"https://media.example.com/v/abc_720.mp4?token=SEC",label:"720p",height:720,type:"video/mp4"}'
    ']}]});</script>'
)

_HLS_MASTER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028"\n'
    "v/1080.m3u8?token=A\n"
)

_NETWORK = [
    {"url": "https://cdn.example.com/hls/master.m3u8?token=A",
     "response_status": "200",
     "response_headers": [{"name": "content-type", "value": "application/vnd.apple.mpegurl"}],
     "response_body": _HLS_MASTER},
    {"url": "https://cdn.example.com/img/poster.jpg",
     "response_status": "200",
     "response_headers": [{"name": "content-type", "value": "image/jpeg"},
                          {"name": "content-length", "value": "42000"}]},
]


def _draft():
    orig = b._load_capture
    b._load_capture = lambda p: {
        "dom_log": [{"type": "full_snapshot", "html": _JW_HTML}],
        "network_log": _NETWORK,
        "action_timeline": [],
    }
    try:
        return b.build_template(Path(_TMP.name))
    finally:
        b._load_capture = orig


def _rec():
    r = (_draft().get("recognition") or {})
    assert r, "draft has no recognition block"
    return r


def test_existing_recognition_keys_preserved():
    r = _rec()
    for k in ("player_family", "delivery", "policy", "candidates"):
        assert k in r, f"regressed existing recognition key {k}"


def test_config_seam_surfaced():
    assert _rec().get("config_seam") == "jwplayer_playlist"


def test_renditions_merge_page_and_network():
    rends = _rec().get("renditions") or []
    shapes = {x.get("url_shape") for x in rends}
    # page/config-seam ladder (cut 1)
    assert "https://media.example.com/v/abc_1080.mp4" in shapes
    # network ladder (cut 2)
    assert "https://cdn.example.com/hls/v/1080.m3u8" in shapes


def test_protocols_and_primary_surfaced():
    r = _rec()
    assert "hls" in (r.get("protocols") or [])
    assert r.get("primary_protocol") == "hls"


def test_poster_rejected_not_a_candidate():
    r = _rec()
    cands = r.get("media_candidates") or []
    assert "https://cdn.example.com/img/poster.jpg" not in cands
    assert any("poster" in x.get("url_shape", "") or "image" in x.get("reason", "")
               for x in (r.get("rejected_media") or []))


def test_draft_recognition_is_f2_clean():
    r = _rec()
    blob = repr(r)
    # NO ASSERTION ON THE WHOLE BLOB, and the `or True` was hiding why.
    # MEASURED at v3.66.1097: repr(r) legitimately contains 'generic_token',
    # 'param_names': ['token'] and 'token_refresh' -- those are FIELD NAMES
    # describing what the recogniser found, not a leaked secret. So
    # `"token" not in blob.lower()` is simply false, and the second clause of
    # the original (`"sec" not in [s.lower() for s in ("SEC",)]`) was false
    # too, since "sec" IS in that list. The assertion could neither pass on its
    # own nor fail with the `or True` attached.
    # The real property is per-URL and is asserted in the loop below.
    # explicit: no signed token / query survives in any url_shape
    for x in (r.get("renditions") or []) + [{"url_shape": c} for c in (r.get("media_candidates") or [])]:
        us = x.get("url_shape", "")
        assert "token=" not in us and "?" not in us, us
