"""BP-VH1 (v3.66.282): the Capture-Test verdict must validate that a 2xx
response is actually MEDIA before reporting `done`. A 2xx HTML interstitial /
login-wall with bytes is the false-positive class this closes.

Zero-arg functions; repo root via __file__. Pure staticmethods — no SiteRunner
instance needed.
"""
from bulk_downloader.runner import SiteRunner


def test_looks_like_media_magic_table():
    M = SiteRunner._looks_like_media
    # content-type fast paths
    assert M("video/mp4", b"") is True
    assert M("audio/mpeg", b"") is True
    assert M("application/vnd.apple.mpegurl", b"") is True
    assert M("application/dash+xml", b"") is True
    # magic-byte recognition (ambiguous / octet-stream content-type)
    assert M("application/octet-stream", b"\x00\x00\x00\x18ftypmp42") is True   # ISO-BMFF
    assert M("application/octet-stream", b"#EXTM3U\n#EXT-X:VERSION:3") is True   # HLS
    assert M("application/octet-stream", b"\x1a\x45\xdf\xa3") is True            # EBML webm/mkv
    assert M("", b"FLV\x01") is True
    assert M("", b"OggS") is True
    # explicit NON-media — even with bytes present
    assert M("text/html", b"<!DOCTYPE html><html><head>") is False
    assert M("application/json", b'{"error":"login required"}') is False
    assert M("application/octet-stream", b"<html>interstitial</html>") is False
    assert M("text/plain", b"not a video") is False


def test_probe_outcome_gate():
    O = SiteRunner._probe_outcome
    # 2xx + real media -> done
    assert O(200, 8192, "video/mp4", b"\x00\x00\x00\x18ftypisom") == "done"
    assert O(206, 8192, "application/octet-stream", b"#EXTM3U") == "done"
    # 2xx but NOT media (HTML login-wall) -> non_media (routes to needs_review)
    assert O(200, 4096, "text/html", b"<html><body>Sign in") == "non_media"
    assert O(200, 4096, "application/json", b'{"x":1}') == "non_media"
    # non-2xx or zero bytes -> fail
    assert O(403, 0, "", b"") == "fail"
    assert O(200, 0, "video/mp4", b"") == "fail"
