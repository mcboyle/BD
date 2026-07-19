"""deep_detect malformed-page numeric robustness.

  F-REC03-02  manifests.parse_hls_master: BANDWIDTH=inf makes int(float("inf"))
              raise OverflowError -- NOT caught by the (ValueError, TypeError)
              handler -- so the whole master-playlist parse crashes. A non-finite
              FRAME-RATE was also stored verbatim (inf).
  F-REC02-01  candidates._flatten_download_candidates: the JSON-LD resolution
              field did a SECOND, UNGUARDED int(width)/int(height) cast (its
              score-bonus twin is guarded), so a non-integer width crashed the
              whole flatten on a malformed JSON-LD page.

Pure/deterministic parser tests on crafted input.
"""
from bulk_downloader.deep_detect.manifests import parse_hls_master
from bulk_downloader.deep_detect.candidates import _flatten_download_candidates


def test_parse_hls_master_survives_overflow_bandwidth_and_nonfinite_framerate():
    manifest = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=inf,RESOLUTION=1920x1080,FRAME-RATE=inf\n"
        "http://example.com/v.m3u8\n"
    )
    # pristine: int(float("inf")) -> OverflowError -> parse_hls_master crashes.
    res = parse_hls_master(manifest)
    variants = res.get("variants", [])
    assert variants, "the variant must still parse (no crash)"
    v = variants[0]
    assert v["bandwidth"] == 0, "an overflowing BANDWIDTH must degrade to 0"
    assert v["frame_rate"] is None, (
        "a non-finite FRAME-RATE must be treated as missing (F-REC03-02)"
    )


def _flatten_only_jsonld(media):
    return _flatten_download_candidates(
        resolution_cards=[], hls_master=None, dash_mpd=None, state_urls=[],
        provider_embeds=[], player_configs=[], jsonld_media=media, post_reveal=[],
    )


def test_flatten_download_candidates_survives_noninteger_jsonld_dimensions():
    media = [{"content_url": "http://x/v.mp4", "width": "abc", "height": "1080"}]
    # pristine: the unguarded resolution-field int("abc") -> ValueError -> crash.
    flat = _flatten_only_jsonld(media)
    jl = [c for c in flat if c.get("source_type") == "json_ld_media"]
    assert jl, "the JSON-LD candidate must still be emitted (no crash)"
    assert jl[0]["resolution"] is None, (
        "a non-integer width/height must fall back to None resolution "
        "(F-REC02-01)"
    )
