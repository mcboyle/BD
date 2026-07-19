"""v3.66.1 — tests for deep_detect: helpers, URL classifier, and
the resolution-card extractor. Subsequent test files cover the
remaining detectors (HLS/DASH parsing, provider/player extraction,
login scoring, state blobs, honeypots, traps, DRM, bot defenses).
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Helpers: parse_size_bytes ───────────────────────────────────────


def test_parse_size_bytes_gb():
    from bulk_downloader.deep_detect import parse_size_bytes
    assert parse_size_bytes("4.85GB") == int(4.85 * 1024**3)
    assert parse_size_bytes("4.85 GB") == int(4.85 * 1024**3)


def test_parse_size_bytes_mb():
    from bulk_downloader.deep_detect import parse_size_bytes
    assert parse_size_bytes("638MB") == 638 * 1024**2


def test_parse_size_bytes_handles_units_case():
    from bulk_downloader.deep_detect import parse_size_bytes
    assert parse_size_bytes("1.5 gb") == int(1.5 * 1024**3)
    assert parse_size_bytes("1.5 GB") == int(1.5 * 1024**3)


def test_parse_size_bytes_none_when_no_match():
    from bulk_downloader.deep_detect import parse_size_bytes
    assert parse_size_bytes("no size in here") is None
    assert parse_size_bytes("") is None
    assert parse_size_bytes(None) is None


# ── Helpers: classify_resolution from raw (w, h) ─────────────────────


def test_classify_resolution_8k_from_dims():
    from bulk_downloader.deep_detect import classify_resolution
    r = classify_resolution(7680, 4320)
    assert r["label"] == "8k"
    assert r["rank"] == 8000


def test_classify_resolution_4k_from_dims():
    from bulk_downloader.deep_detect import classify_resolution
    assert classify_resolution(3840, 2160)["label"] == "4k"


def test_classify_resolution_5k_from_dims():
    from bulk_downloader.deep_detect import classify_resolution
    assert classify_resolution(5120, 2880)["label"] == "5k"


def test_classify_resolution_cinema4k_short_height():
    """4096x1716 is DCI 4K — should still classify as 4k even though
    height is below the strict 2k height."""
    from bulk_downloader.deep_detect import classify_resolution
    assert classify_resolution(4096, 1716)["label"] == "4k"


def test_classify_resolution_1080p():
    from bulk_downloader.deep_detect import classify_resolution
    assert classify_resolution(1920, 1080)["label"] == "1080p"


def test_classify_resolution_720p():
    from bulk_downloader.deep_detect import classify_resolution
    assert classify_resolution(1280, 720)["label"] == "720p"


def test_classify_resolution_none_for_zeros():
    from bulk_downloader.deep_detect import classify_resolution
    assert classify_resolution(0, 0) is None
    assert classify_resolution(1920, 0) is None


# ── Helpers: detect_resolution_from_text ────────────────────────────


def test_detect_res_from_explicit_dims():
    from bulk_downloader.deep_detect import detect_resolution_from_text
    assert detect_resolution_from_text("7680 x 4320")["label"] == "8k"
    assert detect_resolution_from_text("3840x2160")["label"] == "4k"
    assert detect_resolution_from_text("1920 × 1080")["label"] == "1080p"


def test_detect_res_from_p_label():
    from bulk_downloader.deep_detect import detect_resolution_from_text
    assert detect_resolution_from_text("video-4320p.mp4")["label"] == "8k"
    assert detect_resolution_from_text("v2160p")["label"] == "4k"
    assert detect_resolution_from_text("see 720p version")["label"] == "720p"


def test_detect_res_from_vocab_label():
    from bulk_downloader.deep_detect import detect_resolution_from_text
    assert detect_resolution_from_text("8K HEVC")["label"] == "8k"
    assert detect_resolution_from_text("ULTRA HD")["label"] == "4k"
    assert detect_resolution_from_text("FHD H264")["label"] == "1080p"


def test_detect_res_ambiguous_source_label():
    from bulk_downloader.deep_detect import detect_resolution_from_text
    r = detect_resolution_from_text("download original master")
    assert r["rank"] >= 9000
    assert r.get("ambiguous") is True


def test_detect_res_none_for_unrelated_text():
    from bulk_downloader.deep_detect import detect_resolution_from_text
    assert detect_resolution_from_text("just a normal sentence") is None
    assert detect_resolution_from_text("") is None


# ── Helpers: codec / fps parsing ────────────────────────────────────


def test_parse_codec_normalizes_synonyms():
    from bulk_downloader.deep_detect import parse_codec
    assert parse_codec("H264") == "h264"
    assert parse_codec("h.264") == "h264"
    assert parse_codec("x264") == "h264"
    assert parse_codec("H265") == "hevc"
    assert parse_codec("HEVC") == "hevc"
    assert parse_codec("h.265") == "hevc"
    assert parse_codec("x265") == "hevc"
    assert parse_codec("AV1") == "av1"
    assert parse_codec("vp9") == "vp9"
    assert parse_codec("ProRes 422") == "prores"


def test_parse_codec_none_when_absent():
    from bulk_downloader.deep_detect import parse_codec
    assert parse_codec("just text") is None
    assert parse_codec("") is None


def test_parse_fps_extracts_value():
    from bulk_downloader.deep_detect import parse_fps
    assert parse_fps("60fps") == 60.0
    assert parse_fps("60 fps") == 60.0
    assert parse_fps("59.94 fps") == 59.94
    assert parse_fps("24fps") == 24.0


def test_parse_fps_none_when_absent():
    from bulk_downloader.deep_detect import parse_fps
    assert parse_fps("no fps here") is None


# ── Helpers: URL decoding ───────────────────────────────────────────


def test_decode_url_protocol_relative():
    from bulk_downloader.deep_detect import decode_url
    assert decode_url("//cdn.example.com/x.mp4",
                      base_url="https://site.com/page") == \
        "https://cdn.example.com/x.mp4"


def test_decode_url_relative_with_base():
    from bulk_downloader.deep_detect import decode_url
    assert decode_url("file.zip",
                      base_url="https://x.com/dir/page") == \
        "https://x.com/dir/file.zip"


def test_decode_url_unicode_escapes():
    from bulk_downloader.deep_detect import decode_url
    raw = "https:\\u002F\\u002Fcdn.example.com\\u002Fvideo.m3u8"
    assert decode_url(raw) == "https://cdn.example.com/video.m3u8"


def test_decode_url_percent_encoded():
    from bulk_downloader.deep_detect import decode_url
    raw = "https%3A%2F%2Fcdn.example.com%2Fa.mp4"
    assert decode_url(raw) == "https://cdn.example.com/a.mp4"


def test_decode_url_html_entities():
    from bulk_downloader.deep_detect import decode_url
    raw = "https://x.com/v.mp4?title=hello&amp;type=mp4"
    assert "&amp;" not in decode_url(raw)


def test_decode_url_passes_through_absolute():
    from bulk_downloader.deep_detect import decode_url
    assert decode_url("https://x.com/x.mp4") == "https://x.com/x.mp4"


def test_decode_url_empty_returns_empty():
    from bulk_downloader.deep_detect import decode_url
    assert decode_url("") == ""


# ── Helpers: base64 unwrap ──────────────────────────────────────────


def test_maybe_decode_base64_decodes_valid_json():
    import base64
    from bulk_downloader.deep_detect import maybe_decode_base64
    payload = b'{"video":"x.mp4","quality":"4k"}'
    encoded = base64.b64encode(payload).decode()
    assert maybe_decode_base64(encoded) == payload.decode()


def test_maybe_decode_base64_rejects_too_short():
    from bulk_downloader.deep_detect import maybe_decode_base64
    assert maybe_decode_base64("aGVsbG8=") is None  # < 40 chars


def test_maybe_decode_base64_rejects_non_base64():
    from bulk_downloader.deep_detect import maybe_decode_base64
    assert maybe_decode_base64("not!base64!at!all" * 4) is None


# ── URL classifier ──────────────────────────────────────────────────


def test_classify_url_direct_file_mp4():
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://x.com/video.mp4")
    assert r["type"] == "direct_file"
    assert r["is_primary"] is True
    assert r["container"] == "mp4"


def test_classify_url_hls_manifest():
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://x.com/playlist.m3u8")
    assert r["type"] == "hls_manifest"


def test_classify_url_dash_manifest():
    from bulk_downloader.deep_detect import classify_url
    assert classify_url("https://x.com/v.mpd")["type"] == "dash_manifest"


def test_classify_url_smooth_streaming():
    from bulk_downloader.deep_detect import classify_url
    assert classify_url("https://x.com/v.ism")["type"] == \
        "smooth_streaming_manifest"


def test_classify_url_attachment_header_beats_extension():
    """A page link with no extension but Content-Disposition:
    attachment is a download regardless of URL shape."""
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://x.com/api/dl/42",
                     content_disposition="attachment; filename=foo.zip")
    assert r["type"] == "header_attachment"
    assert r["is_primary"] is True


def test_classify_url_extensionless_with_binary_mime():
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://x.com/api/file/123",
                     mime="application/octet-stream")
    assert r["type"] == "extensionless_file"


def test_classify_url_blob_transient():
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("blob:https://x.com/uuid")
    assert r["type"] == "blob_transient"
    assert r["is_primary"] is False


def test_classify_url_rtmp_legacy_stream():
    from bulk_downloader.deep_detect import classify_url
    assert classify_url("rtmp://stream.x.com/live")["type"] == \
        "legacy_stream"


def test_classify_url_segment_dedup_priority():
    """A `.ts` segment must classify as stream_segment, not direct
    file — the parent manifest is the correct candidate."""
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://x.com/seg-001.ts")
    assert r["type"] == "stream_segment"
    assert r["is_primary"] is False


def test_classify_url_subtitle_vtt():
    from bulk_downloader.deep_detect import classify_url
    assert classify_url("https://x.com/captions.vtt")["type"] == \
        "subtitle_track"


def test_classify_url_checksum_sidecar():
    from bulk_downloader.deep_detect import classify_url
    assert classify_url("https://x.com/app.exe.sha256")["type"] == \
        "checksum_sidecar"


def test_classify_url_provider_vimeo_beats_extension():
    """A player.vimeo.com URL should classify as vimeo_embed even if
    the URL happens to end in .mp4 — the file isn't directly
    fetchable cross-origin."""
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://player.vimeo.com/video/12345")
    assert r["type"] == "vimeo_embed"


def test_classify_url_provider_youtube():
    from bulk_downloader.deep_detect import classify_url
    assert classify_url("https://www.youtube.com/embed/abc")["type"] == \
        "youtube_embed"


def test_classify_url_provider_mux_stream():
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://stream.mux.com/abc.m3u8")
    assert r["type"] == "mux_stream"


def test_classify_url_provider_cloudflare_stream():
    from bulk_downloader.deep_detect import classify_url
    r = classify_url(
        "https://videodelivery.net/xyz/manifest/video.m3u8")
    assert r["type"] == "cloudflare_stream"


def test_classify_url_unknown_for_html_page():
    from bulk_downloader.deep_detect import classify_url
    assert classify_url("https://x.com/page?foo=bar")["type"] == "unknown"


# ── Resolution-card extractor: the screenshot pattern ──────────────


_SCREENSHOT_HTML = """
<html><body>
<div class="card">
  <button class="dl" data-href="/dl/v720.mp4">
    <span>↓ 1280 x 720</span>
  </button>
  <div class="meta">HD · H264 · 60fps · 638MB</div>
</div>
<div class="card">
  <button class="dl" data-href="/dl/v1080.mp4">
    <span>↓ 1920 x 1080</span>
  </button>
  <div class="meta">FHD · H264 · 60fps · 3.21GB</div>
</div>
<div class="card">
  <button class="dl" data-href="/dl/v2160.mp4">
    <span>↓ 3840 x 2160</span>
  </button>
  <div class="meta">4K · H264 · 60fps · 5.54GB</div>
</div>
<div class="card">
  <button class="dl" data-href="/dl/v4320.mp4">
    <span>↓ 7680 x 4320</span>
  </button>
  <div class="meta">8K · HEVC · 60fps · 4.85GB</div>
</div>
</body></html>
"""


def test_screenshot_pattern_produces_four_candidates():
    from bulk_downloader.deep_detect import extract_resolution_cards
    cards = extract_resolution_cards(_SCREENSHOT_HTML,
                                     base_url="https://x.com/")
    assert len(cards) == 4


def test_screenshot_pattern_8k_hevc_ranks_above_4k_h264():
    """The core bug-fix the user asked us to handle: bigger file on
    disk is NOT the winner. 4K H.264 at 5.54GB must rank BELOW 8K
    HEVC at 4.85GB."""
    from bulk_downloader.deep_detect import extract_resolution_cards
    cards = extract_resolution_cards(_SCREENSHOT_HTML,
                                     base_url="https://x.com/")
    assert cards[0]["resolution"]["label"] == "8k"
    assert cards[1]["resolution"]["label"] == "4k"
    assert cards[0]["score"] > cards[1]["score"]
    assert cards[0]["codec"] == "hevc"
    assert cards[1]["codec"] == "h264"


def test_screenshot_pattern_extracts_dimensions():
    from bulk_downloader.deep_detect import extract_resolution_cards
    cards = extract_resolution_cards(_SCREENSHOT_HTML,
                                     base_url="https://x.com/")
    assert cards[0]["width"] == 7680
    assert cards[0]["height"] == 4320


def test_screenshot_pattern_extracts_fps_and_size():
    from bulk_downloader.deep_detect import extract_resolution_cards
    cards = extract_resolution_cards(_SCREENSHOT_HTML,
                                     base_url="https://x.com/")
    eightk = cards[0]
    assert eightk["fps"] == 60.0
    assert eightk["size_bytes"] == int(4.85 * 1024**3)


def test_screenshot_pattern_resolves_relative_urls():
    from bulk_downloader.deep_detect import extract_resolution_cards
    cards = extract_resolution_cards(_SCREENSHOT_HTML,
                                     base_url="https://x.com/page")
    assert cards[0]["url"] == "https://x.com/dl/v4320.mp4"


# ── Resolution-card extractor: variants ─────────────────────────────


def test_vocabulary_only_anchors_dont_pollute_each_other():
    """Three peer anchors, each carrying only a label, must produce
    THREE distinct rankings — not all-the-same because each one sees
    every other's text via parent."""
    from bulk_downloader.deep_detect import extract_resolution_cards
    html = """
    <a href="/dl?q=4k" class="cta">4K Download</a>
    <a href="/dl?q=fhd" class="cta">FHD Download</a>
    <a href="/dl?q=8k" class="cta">8K Download</a>
    """
    cards = extract_resolution_cards(html, base_url="https://x.com/")
    labels = [c["resolution"]["label"] for c in cards]
    # Should have three distinct labels — 8k > 4k > 1080p (FHD)
    assert "8k" in labels
    assert "4k" in labels
    assert "1080p" in labels  # FHD


def test_parent_wrapping_child_dedups_to_one_candidate():
    """`<a><button>...</button></a>` is one click target, not two."""
    from bulk_downloader.deep_detect import extract_resolution_cards
    html = """
    <a href="/dl/8k.mp4">
      <button><span>7680 × 4320</span></button>
      <div>8K · HEVC · 4.85GB</div>
    </a>
    """
    cards = extract_resolution_cards(html, base_url="https://x.com/")
    assert len(cards) == 1
    assert cards[0]["url"] == "https://x.com/dl/8k.mp4"


def test_button_without_url_requires_click():
    """A button that reveals the URL via JS sets requires_click=True
    and warns the caller."""
    from bulk_downloader.deep_detect import extract_resolution_cards
    html = """
    <button class="dl" onclick="reveal('8k')">
      <span>7680 × 4320</span>
    </button>
    <div>8K · HEVC · 4.85GB</div>
    """
    cards = extract_resolution_cards(html, base_url="https://x.com/")
    assert len(cards) == 1
    assert cards[0]["url"] is None
    assert cards[0]["requires_click"] is True
    assert any("click" in w.lower() for w in cards[0]["warnings"])


def test_ambiguous_label_ranks_high():
    """'Source' / 'Master' / 'Original' should win against named
    resolutions because the user explicitly asked for the best one
    the site offers."""
    from bulk_downloader.deep_detect import extract_resolution_cards
    html = """
    <a href="/dl/source.mov">Source / Original Master (ProRes)</a>
    <a href="/dl/v.mp4">4K H.264 60fps 3.5GB</a>
    """
    cards = extract_resolution_cards(html, base_url="https://x.com/")
    # The ProRes/source link must score above the 4K H.264 link
    top = cards[0]
    assert top["url"] == "https://x.com/dl/source.mov"


def test_empty_html_returns_empty_list():
    from bulk_downloader.deep_detect import extract_resolution_cards
    assert extract_resolution_cards("", base_url="https://x.com/") == []
    assert extract_resolution_cards("   ", base_url="https://x.com/") == []


def test_html_without_any_resolutions_returns_empty():
    from bulk_downloader.deep_detect import extract_resolution_cards
    html = """<html><body>
    <a href="/about">About us</a>
    <a href="/contact">Contact</a>
    </body></html>"""
    assert extract_resolution_cards(html, base_url="https://x.com/") == []


def test_onclick_url_extraction():
    """When a button has no URL attribute but onclick='...x.mp4...',
    we should still extract the URL from the onclick literal."""
    from bulk_downloader.deep_detect import extract_resolution_cards
    html = """
    <button onclick="window.location='/files/8k.mp4'">
      <span>7680 × 4320</span>
    </button>
    <div>8K HEVC 4.85GB</div>
    """
    cards = extract_resolution_cards(html, base_url="https://x.com/")
    assert len(cards) == 1
    assert cards[0]["url"] == "https://x.com/files/8k.mp4"
    # We got a URL out of onclick, so requires_click should be False
    assert cards[0]["requires_click"] is False
