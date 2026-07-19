"""v3.66.154 — rich builder recovers the full resolution ladder.

On capA the rich builder produced ``resolutions_seen:[2160]`` even though the
stream offered 240–2160. Two gaps: ``RES_RE`` keys on a trailing ``p``
(``2160p``) so bare media-segment heights (``AVC_2160.mp4``) were dropped, and
manifest bodies were never parsed — yet an HLS master / DASH MPD enumerates the
whole rendition ladder even when only one rung was streamed. This suite proves
heights are now recovered from segment URLs and manifest bodies, that junk is
filtered, and that a capture with no manifest is reported faithfully (no
invented ladder).

No browser, no network — synthetic captures only.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import build_template_from_wacz as b  # noqa: E402

_HLS_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240
v240/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
v360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480
v480/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
v720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080
v1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=16000000,RESOLUTION=3840x2160
v2160/index.m3u8
"""

_MPD = (
    '<?xml version="1.0"?><MPD><Period><AdaptationSet>'
    '<Representation id="1" height="480" width="854" />'
    '<Representation id="2" height="1080" width="1920" />'
    '<Representation id="3" height="1440" width="2560" />'
    "</AdaptationSet></Period></MPD>"
)


# ── _manifest_resolutions ────────────────────────────────────────────────
def test_hls_master_ladder() -> None:
    r = b._manifest_resolutions(_HLS_MASTER, "https://cdn/x/master.m3u8")
    assert r == {240, 360, 480, 720, 1080, 2160}


def test_mpd_ladder() -> None:
    r = b._manifest_resolutions(_MPD, "https://cdn/x/manifest.mpd")
    assert r == {480, 1080, 1440}


def test_manifest_body_as_bytes() -> None:
    r = b._manifest_resolutions(_HLS_MASTER.encode("utf-8"), "https://cdn/x/master.m3u8")
    assert 2160 in r and 240 in r


def test_manifest_sniffed_without_extension() -> None:
    # served at a query URL with no .m3u8 suffix — sniff #EXTM3U
    r = b._manifest_resolutions(_HLS_MASTER, "https://cdn/x/playlist?id=9")
    assert r == {240, 360, 480, 720, 1080, 2160}


def test_manifest_junk_heights_filtered() -> None:
    junk = '<MPD><Representation height="0" /><Representation height="99999" />' \
           '<Representation height="1080" /></MPD>'
    assert b._manifest_resolutions(junk, "x.mpd") == {1080}


def test_manifest_empty_body() -> None:
    assert b._manifest_resolutions("", "x.m3u8") == set()
    assert b._manifest_resolutions(None, "x.m3u8") == set()


# ── _network_patterns ────────────────────────────────────────────────────
def test_segment_urls_contribute_resolutions() -> None:
    nl = [
        {"url": "https://cdn/x/m/9/AVC_540.mp4", "response_status": 200},
        {"url": "https://cdn/x/m/9/VP9_360.mp4", "response_status": 200},
    ]
    net = b._network_patterns(nl)
    assert set(net["resolutions_seen"]) == {360, 540}
    assert ".../AVC_{resolution}.mp4" in net["media_patterns"]
    assert ".../VP9_{resolution}.mp4" in net["media_patterns"]


def test_manifest_plus_segments_plus_api_merge() -> None:
    nl = [
        {"url": "https://media/x/api/v1/movie/9/download-resolution/1080?token=S&sig=A",
         "response_status": 200},
        {"url": "https://cdn/x/m/9/AVC_540.mp4", "response_status": 200},
        {"url": "https://cdn/x/hls/9/master.m3u8", "response_status": 200,
         "response_body": _HLS_MASTER},
        {"url": "https://cdn/x/dash/9/manifest.mpd", "response_status": 200,
         "response_body": _MPD},
    ]
    net = b._network_patterns(nl)
    # union of HLS {240,360,480,720,1080,2160} + MPD {480,1080,1440}
    #           + segment {540} + API {1080}
    assert net["resolutions_seen"] == [2160, 1440, 1080, 720, 540, 480, 360, 240]


def test_no_manifest_reports_only_what_was_seen() -> None:
    # capA-shape: a download-resolution API call, no manifest body, no segments.
    # Must report exactly that rung — never invent a ladder.
    nl = [
        {"url": "https://media/x/api/v1/movie/9/download-resolution/2160?token=S&sig=A",
         "response_status": 200},
    ]
    net = b._network_patterns(nl)
    assert net["resolutions_seen"] == [2160]


def test_manifest_without_body_yields_no_resolutions() -> None:
    # the .m3u8 was requested but its body was not captured -> no heights,
    # only the media pattern is recorded
    nl = [{"url": "https://cdn/x/hls/9/master.m3u8", "response_status": 200}]
    net = b._network_patterns(nl)
    assert net["resolutions_seen"] == []
    assert ".../{manifest}.m3u8" in net["media_patterns"]


# ── end-to-end: build_template from a synthetic .wacz ─────────────────────
def _make_wacz(capture: dict) -> Path:
    d = Path(tempfile.mkdtemp(prefix="bld154_"))
    wacz = d / "synthetic.wacz"
    with zipfile.ZipFile(wacz, "w") as z:
        z.writestr("capture.json", json.dumps(capture))
    return wacz


def test_build_template_resolution_priority_from_manifest() -> None:
    cap = {
        "url": "https://app.reptyle.com/movies/9",
        "host": "app.reptyle.com",
        "captured_at": "2026-06-05T00:00:00Z",
        "dom_log": [],
        "network_log": [
            {"url": "https://media.reptyle.com/api/v1/movie/9/download-resolution/1080?token=S&sig=A",
             "response_status": 200},
            {"url": "https://cdn.reptyle.com/hls/9/master.m3u8", "response_status": 200,
             "response_body": _HLS_MASTER},
        ],
    }
    wacz = _make_wacz(cap)
    try:
        tpl = b.build_template(wacz)
        # ladder from the master, ordered by the builder's priority list
        assert tpl["resolution_priority"] == [2160, 1080, 720, 480, 360, 240]
        assert tpl["network_discovery"]["resolutions_seen"] == [2160, 1080, 720, 480, 360, 240]
        assert ".../{manifest}.m3u8" in tpl["network_discovery"]["media_patterns"]
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
