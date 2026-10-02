"""O1671 r8c (AUDIT-12 #6): a non-numeric ``height``/``tbr`` in a yt-dlp format
made info_to_extractor_result raise (ValueError/TypeError/OverflowError) from
the shim, outside its try blocks, instead of degrading. The shim must never
raise on a malformed format: a bad number ranks as 0 and the format stays usable.
"""
import json

import pytest

from bulk_downloader.ytdlp_extractor import info_to_extractor_result, make_ytdlp_extractor

BD_GATE_SCOPE = "module"
PAGE = "https://site.example/watch/1"


def _fmt(url, height, tbr):
    return {"url": url, "protocol": "https", "vcodec": "avc1", "acodec": "mp4a",
            "height": height, "tbr": tbr, "ext": "mp4"}


def _shim(info):
    payload = json.dumps(info) + "\n"
    return make_ytdlp_extractor({}, run=lambda cmd: (0, payload, ""), resolve=lambda: ["yt-dlp"])


BAD = [("720p", None), (720, "high"), (float("inf"), 1.0), ([1], 1.0), ({"h": 1}, [2])]


@pytest.mark.parametrize("height,tbr", BAD, ids=[repr(b) for b in BAD])
def test_shim_does_not_raise_on_non_numeric_format_fields(height, tbr):
    url = "https://cdn.example/only.mp4"
    result = _shim({"title": "clip", "formats": [_fmt(url, height, tbr)]})(PAGE)
    assert result == {"video_url": url, "is_hls": False, "title": "clip", "ext": "mp4"}


def test_bad_height_ranks_below_a_real_height():
    bad, good = "https://cdn.example/bad.mp4", "https://cdn.example/720.mp4"
    result = info_to_extractor_result({"formats": [_fmt(good, 720, 900.0), _fmt(bad, "1080p", 99999.0)]})
    assert result["video_url"] == good


def test_bad_tbr_ranks_below_a_real_tbr_at_same_height():
    bad, good = "https://cdn.example/bad.mp4", "https://cdn.example/good.mp4"
    result = info_to_extractor_result({"formats": [_fmt(good, 720, 500.0), _fmt(bad, 720, "high")]})
    assert result["video_url"] == good


def test_top_level_fallback_with_non_numeric_height():
    url = "https://cdn.example/top.mp4"
    result = _shim({"url": url, "protocol": "https", "height": "720p", "tbr": "n/a", "ext": "mp4"})(PAGE)
    assert result == {"video_url": url, "is_hls": False, "ext": "mp4"}


def test_numeric_strings_and_numbers_still_rank_positive_control():
    low, high = "https://cdn.example/720.mp4", "https://cdn.example/1080.mp4"
    result = info_to_extractor_result({"formats": [_fmt(high, "1080", "2500.5"), _fmt(low, 720, 4000.0)]})
    assert result["video_url"] == high
