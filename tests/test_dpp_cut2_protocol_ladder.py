"""D++ cut 2 (Layer B) — protocol + framework-independent rendition ladder +
poster/MSE disambiguation, recognized from the NETWORK log (not the player).

NEW pure `player_recognition.recognize_protocol(network_log)`:
  {
    "protocols": [sorted subset of hls|dash|progressive|mss|mse_blob],
    "primary": str|None,                       # manifest > progressive > mse_blob
    "renditions": [ {resolution,bitrate,codec,container,protocol,url_shape} ],
    "ll_hls": bool,                            # #EXT-X-PART seen
    "resumable": bool,                         # Accept-Ranges: bytes / 206 on media
    "segments": {"fmp4": int, "ts": int},      # counted (carry: naive read 0)
    "media_candidates": [url_shape],           # passed poster/MSE disambiguation
    "rejected": [ {"url_shape":..., "reason":...} ],
  }

Reuses the FROZEN extraction_core.manifest_resolutions (a guard — called, never
edited). Pure/stdlib; F2 — every url_shape query-stripped. SYNTHETIC fixtures.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402


def _e(url, *, ct=None, status="200", body=None, clen=None, ranges=False):
    rh = []
    if ct:
        rh.append({"name": "content-type", "value": ct})
    if clen is not None:
        rh.append({"name": "content-length", "value": str(clen)})
    if ranges:
        rh.append({"name": "accept-ranges", "value": "bytes"})
    e = {"url": url, "response_status": status, "response_headers": rh}
    if body is not None:
        e["response_body"] = body
    return e


def _rp(net):
    out = pr.recognize_protocol(net)
    assert isinstance(out, dict)
    for k in ("protocols", "primary", "renditions", "ll_hls", "resumable",
              "segments", "media_candidates", "rejected"):
        assert k in out, f"missing key {k}"
    return out


def _by_res(rows):
    return {r.get("resolution"): r for r in rows}


# --------------------------------------------------------------------------- #
# HLS master manifest → variant ladder
# --------------------------------------------------------------------------- #
_HLS_MASTER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=5200000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"\n'
    "v/1080.m3u8?token=A\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,CODECS="avc1.4d401f"\n'
    "v/720.m3u8?token=A\n"
)


def test_hls_master_protocol_and_primary():
    out = _rp([_e("https://cdn.example.com/hls/master.m3u8?token=A",
                  ct="application/vnd.apple.mpegurl", body=_HLS_MASTER)])
    assert "hls" in out["protocols"]
    assert out["primary"] == "hls"


def test_hls_master_variant_ladder():
    out = _rp([_e("https://cdn.example.com/hls/master.m3u8?token=A",
                  ct="application/vnd.apple.mpegurl", body=_HLS_MASTER)])
    res = _by_res(out["renditions"])
    assert set(res) == {1080, 720}
    assert res[1080]["bitrate"] == 5200000
    assert res[1080]["codec"].startswith("avc1.640028")
    assert res[1080]["protocol"] == "hls"
    # variant URI resolved against the master + query-stripped (F2)
    assert res[1080]["url_shape"] == "https://cdn.example.com/hls/v/1080.m3u8"
    assert all("token" not in r["url_shape"] for r in out["renditions"])


def test_ll_hls_flagged():
    body = _HLS_MASTER + "#EXT-X-PART:DURATION=0.5,URI=\"p.m4s\"\n"
    out = _rp([_e("https://cdn.example.com/hls/master.m3u8",
                  ct="application/vnd.apple.mpegurl", body=body)])
    assert out["ll_hls"] is True


# --------------------------------------------------------------------------- #
# DASH .mpd → Representation ladder
# --------------------------------------------------------------------------- #
_MPD = (
    '<?xml version="1.0"?><MPD><Period><AdaptationSet>'
    '<Representation id="1" width="1920" height="1080" bandwidth="5000000" codecs="avc1.640028"/>'
    '<Representation id="2" width="1280" height="720" bandwidth="2500000" codecs="avc1.4d401f"/>'
    '</AdaptationSet></Period></MPD>'
)


def test_dash_mpd_ladder():
    out = _rp([_e("https://cdn.example.com/dash/stream.mpd?sig=Z",
                  ct="application/dash+xml", body=_MPD)])
    assert "dash" in out["protocols"]
    res = _by_res(out["renditions"])
    assert set(res) == {1080, 720}
    assert res[720]["bitrate"] == 2500000
    assert res[720]["protocol"] == "dash"
    assert all("sig" not in r["url_shape"] for r in out["renditions"])


# --------------------------------------------------------------------------- #
# progressive direct media → candidate
# --------------------------------------------------------------------------- #
def test_progressive_direct_media_is_candidate():
    out = _rp([_e("https://h.example.com/v/clip_1080.mp4?t=Q",
                  ct="video/mp4", clen=80_000_000)])
    assert "progressive" in out["protocols"]
    assert "https://h.example.com/v/clip_1080.mp4" in out["media_candidates"]
    r = _by_res(out["renditions"]).get(1080)
    assert r is not None and r["protocol"] == "progressive" and r["container"] == "mp4"


def test_progressive_resumable_flag():
    out = _rp([_e("https://h.example.com/v/clip.mp4", ct="video/mp4",
                  clen=80_000_000, status="206", ranges=True)])
    assert out["resumable"] is True


# --------------------------------------------------------------------------- #
# poster / sub-threshold disambiguation (directly targets the 271.4 KB note)
# --------------------------------------------------------------------------- #
def test_image_poster_rejected_not_candidate():
    out = _rp([_e("https://h.example.com/poster.jpg", ct="image/jpeg", clen=40_000)])
    assert "https://h.example.com/poster.jpg" not in out["media_candidates"]
    assert any("image" in x["reason"] or "poster" in x["reason"] for x in out["rejected"])


def test_subthreshold_preview_body_rejected():
    # 271.4 KB "video" that is really a poster/preview — must NOT be the download.
    out = _rp([_e("https://h.example.com/preview.mp4", ct="video/mp4", clen=271_400)])
    assert "https://h.example.com/preview.mp4" not in out["media_candidates"]
    assert any("sub-threshold" in x["reason"] or "small" in x["reason"] for x in out["rejected"])


# --------------------------------------------------------------------------- #
# fMP4 / CMAF segment counting (carry: naive count read segment_stream:0) + MSE
# --------------------------------------------------------------------------- #
def test_fmp4_segments_counted_not_zero():
    net = [
        _e("https://h.example.com/s/init.mp4", ct="video/mp4", clen=900),
        _e("https://h.example.com/s/seg-1.m4s", ct="video/iso.segment", clen=500_000),
        _e("https://h.example.com/s/seg-2.m4s", ct="video/iso.segment", clen=500_000),
    ]
    out = _rp(net)
    assert out["segments"]["fmp4"] >= 2, out["segments"]
    # segments are NOT direct-download candidates (MSE/segment-fed)
    assert not any(c.endswith(".m4s") for c in out["media_candidates"])


def test_mse_blob_segments_no_direct_download():
    # only fMP4 segments + an HLS manifest = segment-fed; the candidate is the
    # manifest, never a single segment as a "download".
    net = [
        _e("https://h.example.com/m/master.m3u8", ct="application/vnd.apple.mpegurl",
           body="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\nv.m3u8\n"),
        _e("https://h.example.com/m/seg-1.m4s", ct="video/iso.segment", clen=500_000),
    ]
    out = _rp(net)
    assert "hls" in out["protocols"]
    assert not any(c.endswith(".m4s") for c in out["media_candidates"])


def test_empty_log_is_safe():
    out = _rp([])
    assert out["protocols"] == [] and out["renditions"] == [] and out["primary"] is None
