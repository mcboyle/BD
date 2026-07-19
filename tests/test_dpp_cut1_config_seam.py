"""D++ cut 1 (Layer A) — config-seam → rendition-ladder extraction.

The family registry (player_recognition.py + player_families.py) already
classifies WHICH framework. What was missing: parsing that framework's config
seam to recover the real rendition ladder. This adds a pure
``extract_config_seam(html, family=None)`` and surfaces ``config_seam`` +
``renditions`` additively from ``detect()``.

Honest scope (Layer A, page-only): inline-source frameworks (jwplayer
``setup({playlist})``, video.js ``<source>``/``data-setup``, generic
``<video><source>``) carry the actual ladder in the page → parse it. Manifest
loaders (hls.js ``loadSource``, dash.js ``initialize``, shaka ``load``) carry
only a manifest POINTER in the page — the ladder is in the .m3u8/.mpd, which is
the network pass (cut 2). So those yield a seam + a manifest ``url_shape`` with
NO per-rendition rows (not a fabricated ladder).

F2: every ``url_shape`` is query-stripped — a signed token in a source URL must
never survive into a rendition row.

Rendition row schema (normalized, uniform across frameworks):
  {resolution:int|None, bitrate:int|None, codec:str|None,
   container:str|None, protocol:str, url_shape:str}

SYNTHETIC fixtures only — no real captures, no network.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _rends(html, family=None):
    out = pr.extract_config_seam(html, family=family)
    assert isinstance(out, dict), "extract_config_seam must return a dict"
    assert "seam" in out and "renditions" in out
    assert isinstance(out["renditions"], list)
    return out


def _by_res(rends):
    return {r.get("resolution"): r for r in rends}


# --------------------------------------------------------------------------- #
# JWPlayer inline setup({playlist:[{sources:[...]}]})
# --------------------------------------------------------------------------- #
_JW_HTML = """
<div id="jwplayer-0" class="jwplayer jw-reset"></div>
<script>
jwplayer("jwplayer-0").setup({
  playlist: [{
    sources: [
      {file: "https://media.example.com/v/abc_1080.mp4?token=SECRET_SIG&exp=99", label: "1080p", height: 1080, bitrate: 5200000, type: "video/mp4"},
      {file: "https://media.example.com/v/abc_720.mp4?token=SECRET_SIG", label: "720p", height: 720, bitrate: 2800000, type: "video/mp4"},
      {file: "https://media.example.com/v/abc_480.mp4", label: "480p", height: 480}
    ]
  }]
});
</script>
"""


def test_jwplayer_seam_kind():
    out = _rends(_JW_HTML, family="jwplayer")
    assert out["seam"] == "jwplayer_playlist"


def test_jwplayer_extracts_three_renditions():
    out = _rends(_JW_HTML, family="jwplayer")
    assert len(out["renditions"]) == 3
    res = _by_res(out["renditions"])
    assert set(res) == {1080, 720, 480}


def test_jwplayer_rendition_carries_bitrate_and_container():
    res = _by_res(_rends(_JW_HTML, family="jwplayer")["renditions"])
    assert res[1080]["bitrate"] == 5200000
    assert res[1080]["container"] == "mp4"
    assert res[1080]["protocol"] == "progressive"


def test_jwplayer_url_shape_is_query_stripped_F2():
    # The signed token in the source URL must NOT survive into url_shape.
    for r in _rends(_JW_HTML, family="jwplayer")["renditions"]:
        assert "token" not in r["url_shape"].lower()
        assert "secret_sig" not in r["url_shape"].lower()
        assert "?" not in r["url_shape"]
    res = _by_res(_rends(_JW_HTML, family="jwplayer")["renditions"])
    assert res[1080]["url_shape"] == "https://media.example.com/v/abc_1080.mp4"


# --------------------------------------------------------------------------- #
# video.js — <source> elements and data-setup JSON
# --------------------------------------------------------------------------- #
_VJS_SOURCE_HTML = """
<video class="video-js vjs-default-skin">
  <source src="https://cdn.example.com/s/x_1080.mp4" type='video/mp4; codecs="avc1.640028"' data-res="1080">
  <source src="https://cdn.example.com/s/x_720.webm" type="video/webm">
</video>
"""

_VJS_DATASETUP_HTML = """
<video class="video-js" data-setup='{"sources":[
  {"src":"https://cdn.example.com/d/y_2160.mp4?sig=Z","type":"video/mp4","label":"2160p"},
  {"src":"https://cdn.example.com/d/master.m3u8","type":"application/x-mpegURL"}
]}'></video>
"""


def test_videojs_source_elements():
    out = _rends(_VJS_SOURCE_HTML, family="videojs")
    assert out["seam"] in ("videojs_source", "html5_source")
    assert len(out["renditions"]) == 2
    # codec parsed from the type=... codecs="..." attribute when present
    mp4 = next(r for r in out["renditions"] if r["container"] == "mp4")
    assert mp4["codec"] == "avc1.640028"
    assert mp4["resolution"] == 1080  # from data-res


def test_videojs_datasetup_json_and_query_strip():
    out = _rends(_VJS_DATASETUP_HTML, family="videojs")
    shapes = {r["url_shape"] for r in out["renditions"]}
    assert "https://cdn.example.com/d/y_2160.mp4" in shapes  # ?sig=Z stripped
    assert all("sig=" not in s for s in shapes)
    # the .m3u8 source is recognized as an hls manifest pointer (no fabricated ladder)
    hls = next(r for r in out["renditions"] if r["protocol"] == "hls")
    assert hls["url_shape"].endswith("master.m3u8")


# --------------------------------------------------------------------------- #
# generic <video><source> progressive (no brand)
# --------------------------------------------------------------------------- #
def test_generic_html5_source_progressive():
    html = '<video><source src="https://h.example.com/clip.mp4" type="video/mp4"></video>'
    out = _rends(html)  # family auto/None
    assert out["seam"] in ("html5_source", "videojs_source")
    assert len(out["renditions"]) == 1
    r = out["renditions"][0]
    assert r["protocol"] == "progressive"
    assert r["container"] == "mp4"
    assert r["url_shape"] == "https://h.example.com/clip.mp4"


# --------------------------------------------------------------------------- #
# manifest-loader frameworks: seam + manifest pointer, NO per-rendition rows
# (ladder lives in the manifest = cut 2's network pass; do not fabricate it)
# --------------------------------------------------------------------------- #
def test_hlsjs_loadsource_is_manifest_pointer_only():
    html = '<video id="v"></video><script>var h=new Hls();h.loadSource("https://cdn.example.com/hls/master.m3u8?t=Q");</script>'
    out = _rends(html, family="hlsjs")
    assert out["seam"] == "hlsjs_loadsource"
    assert out["renditions"] == []  # honest: ladder is in the manifest, not the page
    assert out.get("manifest_url_shape") == "https://cdn.example.com/hls/master.m3u8"
    assert out.get("manifest_protocol") == "hls"


def test_dashjs_initialize_is_manifest_pointer_only():
    html = '<script>player.initialize(document.querySelector("video"), "https://cdn.example.com/dash/stream.mpd", true);</script>'
    out = _rends(html, family="dashjs")
    assert out["seam"] == "dashjs_initialize"
    assert out["renditions"] == []
    assert out.get("manifest_url_shape") == "https://cdn.example.com/dash/stream.mpd"
    assert out.get("manifest_protocol") == "dash"


def test_unknown_framework_no_seam_no_crash():
    out = _rends("<div>nothing playable here</div>")
    assert out["seam"] is None
    assert out["renditions"] == []


# --------------------------------------------------------------------------- #
# detect() surfaces config_seam + renditions ADDITIVELY (backward-compatible)
# --------------------------------------------------------------------------- #
def test_detect_surfaces_config_seam_and_renditions():
    r = pr.detect(_JW_HTML)
    # existing keys must remain present (no regression to the public shape)
    for k in ("player_family", "selectors", "delivery", "candidates", "confidence"):
        assert k in r
    # new additive keys
    assert "config_seam" in r and "renditions" in r
    assert r["config_seam"] == "jwplayer_playlist"
    assert len(r["renditions"]) == 3
    # F2: still query-stripped through detect()
    assert all("token" not in x["url_shape"].lower() for x in r["renditions"])


def test_detect_renditions_empty_when_no_seam():
    r = pr.detect('<div class="video-js"></div>')  # brand class, but no sources
    assert r["renditions"] == []
    assert r["config_seam"] in (None, "videojs_source", "html5_source")
