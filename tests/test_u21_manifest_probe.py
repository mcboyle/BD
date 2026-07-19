"""U21 — HLS/DASH manifest probe (D-32). The parser (text= path) is
fully unit-tested here; the URL-fetch path ships build-verified-only
(it needs a live network, like the live_tests checks).
"""
from bulk_downloader import dev_suite as ds


_HLS_MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2149280,RESOLUTION=1280x720,"
    'CODECS="avc1.640020,mp4a.40.2",FRAME-RATE=29.970\n'
    "720p/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=6221600,RESOLUTION=1920x1080,"
    'CODECS="avc1.640028,mp4a.40.2"\n'
    "1080p/index.m3u8\n"
    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English"\n'
)

_HLS_MEDIA = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:10\n"
    "#EXTINF:9.009,\nseg0.ts\n"
    "#EXTINF:9.009,\nseg1.ts\n"
    "#EXTINF:3.500,\nseg2.ts\n"
    "#EXT-X-ENDLIST\n"
)

_HLS_LIVE = (  # no #EXT-X-ENDLIST -> live
    "#EXTM3U\n#EXT-X-TARGETDURATION:6\n"
    "#EXTINF:6.0,\nseg0.ts\n"
)

_DASH = (
    '<?xml version="1.0"?>\n'
    '<MPD type="static" mediaPresentationDuration="PT10M30S">\n'
    ' <Period><AdaptationSet mimeType="video/mp4">\n'
    '  <Representation id="v1" width="1920" height="1080" '
    'bandwidth="6000000" codecs="avc1.640028"/>\n'
    '  <Representation id="v2" width="1280" height="720" '
    'bandwidth="3000000" codecs="avc1.64001f"/>\n'
    " </AdaptationSet></Period>\n</MPD>\n"
)


# ── HLS ───────────────────────────────────────────────────────────

def test_probe_hls_master_lists_variants_by_bandwidth():
    r = ds.manifest_probe(text=_HLS_MASTER)
    assert r["ok"] is True
    assert r["format"] == "hls"
    assert r["playlist_kind"] == "master"
    assert r["variant_count"] == 2
    # sorted highest bandwidth first
    assert r["variants"][0]["resolution"] == "1920x1080"
    assert r["variants"][0]["bandwidth_bps"] == 6221600
    # a codec string containing a comma is kept whole
    assert "," in r["variants"][0]["codecs"]
    assert r["alternate_renditions"] == 1


def test_probe_hls_media_counts_segments_and_duration():
    r = ds.manifest_probe(text=_HLS_MEDIA)
    assert r["playlist_kind"] == "media"
    assert r["segment_count"] == 3
    assert r["total_duration_seconds"] == 21.5
    assert r["target_duration"] == "10"
    assert r["is_vod"] is True


def test_probe_hls_live_when_no_endlist():
    r = ds.manifest_probe(text=_HLS_LIVE)
    assert r["is_vod"] is False
    assert r["is_live"] is True


# ── DASH ──────────────────────────────────────────────────────────

def test_probe_dash_lists_representations():
    r = ds.manifest_probe(text=_DASH)
    assert r["ok"] is True
    assert r["format"] == "dash"
    assert r["representation_count"] == 2
    assert r["adaptation_set_count"] == 1
    top = r["representations"][0]
    assert top["width"] == 1920 and top["height"] == 1080
    assert top["bandwidth_bps"] == 6000000


def test_probe_dash_static_is_vod():
    r = ds.manifest_probe(text=_DASH)
    assert r["mpd_type"] == "static"
    assert r["is_vod"] is True
    assert r["media_presentation_duration"] == "PT10M30S"


# ── input handling ────────────────────────────────────────────────

def test_probe_rejects_unrecognised_text():
    r = ds.manifest_probe(text="not a manifest at all")
    assert r["ok"] is False
    assert "not recognised" in r["error"]


def test_probe_rejects_non_http_url():
    r = ds.manifest_probe(url="ftp://example.com/x.m3u8")
    assert r["ok"] is False
    assert "http" in r["error"]


def test_probe_needs_an_input():
    r = ds.manifest_probe()
    assert r["ok"] is False


# ── route ─────────────────────────────────────────────────────────

def test_manifest_probe_route_needs_url(fresh_app):
    resp = fresh_app.get("/api/dev/manifest_probe")
    assert resp.status_code == 400


def test_manifest_probe_route_rejects_bad_scheme(fresh_app):
    resp = fresh_app.get(
        "/api/dev/manifest_probe?url=ftp://x/y.m3u8")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False
