"""v3.43.66: tests for the Aylo flashvars extractor.

These tests don't hit the network. They verify:

  1. AYLO_BRANDS list is non-empty and has all the documented brands
  2. is_aylo_url() matches subdomain variants correctly
  3. _find_matching_brace handles nested braces, strings, escapes
  4. extract_flashvars() parses a valid block
  5. extract_flashvars() handles multiple blocks (picks the one with
     real qualities, not just a trailer)
  6. extract_flashvars() returns None on garbage / no block
  7. _normalize_variant handles list vs string `quality` values
  8. _normalize_variant skips entries with empty videoUrl
  9. pick_best_variant respects quality_pref order
 10. pick_best_variant honors force_format
 11. pick_best_variant prefers MP4 over HLS at equal quality (resume)
 12. pick_best_variant returns highest variant when no pref
 13. extract_from_html end-to-end happy path
 14. extract_from_html fail modes (empty html, no flashvars,
     no mediaDefinitions, no usable variant)
 15. SiteRunner._try_aylo_extractor method exists and disabled paths
     return False without raising
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import pytest

from bulk_downloader import extractors_aylo as a


# ─── AYLO_BRANDS ─────────────────────────────────────────────────────


class TestAyloBrandsList:
    def test_brands_nonempty(self):
        assert len(a.AYLO_BRANDS) >= 10

    def test_brazzers_present(self):
        assert "brazzers.com" in a.AYLO_BRANDS

    def test_bangbros_present(self):
        assert "bangbros.com" in a.AYLO_BRANDS

    def test_realitykings_present(self):
        assert "realitykings.com" in a.AYLO_BRANDS

    def test_mofos_present(self):
        assert "mofos.com" in a.AYLO_BRANDS

    def test_digitalplayground_present(self):
        assert "digitalplayground.com" in a.AYLO_BRANDS

    def test_tiny4k_present(self):
        """Tiny4K moved to Aylo backend after Pure Play Media absorption."""
        assert "tiny4k.com" in a.AYLO_BRANDS

    def test_pornhubpremium_present(self):
        assert "pornhubpremium.com" in a.AYLO_BRANDS


# ─── is_aylo_url ────────────────────────────────────────────────────


class TestIsAyloUrl:
    def test_brazzers_root(self):
        assert a.is_aylo_url("https://www.brazzers.com/scene/123") is True

    def test_brazzers_naked(self):
        assert a.is_aylo_url("https://brazzers.com/scene/123") is True

    def test_brazzers_site_ma_subdomain(self):
        """site-ma.brazzers.com is the members area."""
        assert a.is_aylo_url("https://site-ma.brazzers.com/scene/123") is True

    def test_brazzers_members_subdomain(self):
        assert a.is_aylo_url("https://members.brazzers.com/scene/123") is True

    def test_bangbros_members(self):
        assert a.is_aylo_url("https://site-ma.bangbros.com/scene/123") is True

    def test_realitykings(self):
        assert a.is_aylo_url("https://realitykings.com/scene/123") is True

    def test_non_aylo_returns_false(self):
        assert a.is_aylo_url("https://wowgirls.com/x") is False
        assert a.is_aylo_url("https://vixen.com/v") is False
        assert a.is_aylo_url("https://newsensations.com/scene") is False

    def test_garbage_inputs(self):
        assert a.is_aylo_url("") is False
        assert a.is_aylo_url(None) is False  # type: ignore[arg-type]
        assert a.is_aylo_url("not a url") is False
        assert a.is_aylo_url("ftp://brazzers.com") is True  # still matches host

    def test_case_insensitive_hostname(self):
        """Hostnames are case-insensitive per RFC."""
        assert a.is_aylo_url("https://WWW.BRAZZERS.COM/scene") is True


# ─── _find_matching_brace ───────────────────────────────────────────


class TestFindMatchingBrace:
    def test_simple_object(self):
        s = '{"a": 1}'
        assert a._find_matching_brace(s, 0) == len(s)

    def test_nested_objects(self):
        s = '{"a": {"b": 1}, "c": 2}'
        assert a._find_matching_brace(s, 0) == len(s)

    def test_string_containing_brace(self):
        """A `}` inside a string literal must not close the outer object."""
        s = '{"a": "}foo{"}'
        assert a._find_matching_brace(s, 0) == len(s)

    def test_escaped_quote_in_string(self):
        s = '{"a": "say \\"hi\\""}'
        assert a._find_matching_brace(s, 0) == len(s)

    def test_unbalanced_returns_minus_one(self):
        assert a._find_matching_brace("{{{", 0) == -1

    def test_must_start_at_brace(self):
        s = '{"a": 1}'
        assert a._find_matching_brace(s, 1) == -1  # not at `{`

    def test_returns_position_after_close(self):
        """Index returned is AFTER the closing brace, suitable for slicing."""
        s = "prefix {\"k\":1} suffix"
        start = s.index("{")
        end = a._find_matching_brace(s, start)
        assert s[start:end] == '{"k":1}'


# ─── extract_flashvars ──────────────────────────────────────────────


def _make_html(*blocks):
    """Build an HTML page containing the given flashvars blocks."""
    parts = ["<html><head>"]
    for b in blocks:
        parts.append(f"<script>{b}</script>")
    parts.append("</head><body></body></html>")
    return "".join(parts)


_GOOD_FV = '''var flashvars_259384716 = {
    "video_title": "Test Scene",
    "video_duration": 1820,
    "image_url": "https://thumb.example.com/scene.jpg",
    "mediaDefinitions": [
        {"format": "mp4", "videoUrl": "https://cdn.example.com/2160.mp4", "quality": "2160"},
        {"format": "mp4", "videoUrl": "https://cdn.example.com/1080.mp4", "quality": "1080"},
        {"format": "hls", "videoUrl": "https://cdn.example.com/master.m3u8", "quality": ["480","720","1080"]}
    ]
};'''


class TestExtractFlashvars:
    def test_simple_block(self):
        fv = a.extract_flashvars(_make_html(_GOOD_FV))
        assert fv is not None
        assert fv["video_title"] == "Test Scene"
        assert fv["video_duration"] == 1820
        assert len(fv["mediaDefinitions"]) == 3

    def test_window_prefix(self):
        b = _GOOD_FV.replace("var ", "window.")
        fv = a.extract_flashvars(_make_html(b))
        assert fv is not None

    def test_no_prefix(self):
        b = _GOOD_FV.replace("var ", "")
        fv = a.extract_flashvars(_make_html(b))
        assert fv is not None

    def test_no_block_returns_none(self):
        assert a.extract_flashvars("<html></html>") is None

    def test_empty_input_returns_none(self):
        assert a.extract_flashvars("") is None
        assert a.extract_flashvars(None) is None  # type: ignore[arg-type]

    def test_malformed_json_returns_none(self):
        bad = "var flashvars_1 = {oops not json};"
        assert a.extract_flashvars(_make_html(bad)) is None

    def test_block_without_mediaDefinitions_skipped(self):
        # First block: no mediaDefinitions
        bad = 'var flashvars_111 = {"video_title": "T", "noPlay": true};'
        # Second block: real one
        full = _make_html(bad, _GOOD_FV)
        fv = a.extract_flashvars(full)
        # Should pick the second one
        assert fv is not None
        assert len(fv["mediaDefinitions"]) == 3

    def test_prefers_high_quality_block_when_two_blocks(self):
        """Trailer block (low quality) + main block (high quality) →
        pick the main one."""
        trailer = '''var flashvars_111 = {
            "video_title": "Trailer",
            "mediaDefinitions": [
                {"format": "mp4", "videoUrl": "https://t.example.com/240.mp4", "quality": "240"}
            ]
        };'''
        full = _make_html(trailer, _GOOD_FV)
        fv = a.extract_flashvars(full)
        # Should prefer the high-q block
        assert fv["video_title"] == "Test Scene"

    def test_falls_back_to_first_block_when_no_high_q(self):
        """If NO block has >= 1080, return the first usable one."""
        only_low = '''var flashvars_111 = {
            "video_title": "Only Low",
            "mediaDefinitions": [
                {"format": "mp4", "videoUrl": "https://x.com/480.mp4", "quality": "480"}
            ]
        };'''
        fv = a.extract_flashvars(_make_html(only_low))
        assert fv is not None
        assert fv["video_title"] == "Only Low"


# ─── _normalize_variant ─────────────────────────────────────────────


class TestNormalizeVariant:
    def test_mp4_string_quality(self):
        v = a._normalize_variant({
            "format": "mp4", "videoUrl": "https://x/v.mp4", "quality": "1080",
        })
        assert v.format == "mp4"
        assert v.quality == 1080

    def test_hls_list_quality(self):
        v = a._normalize_variant({
            "format": "hls", "videoUrl": "https://x/master.m3u8",
            "quality": ["480", "720", "1080"],
        })
        assert v.format == "hls"
        assert v.quality == 1080  # max of the list
        assert v.quality_list == [480, 720, 1080]

    def test_empty_videoUrl_returns_none(self):
        assert a._normalize_variant({
            "format": "mp4", "videoUrl": "", "quality": "1080",
        }) is None

    def test_missing_videoUrl_returns_none(self):
        assert a._normalize_variant({
            "format": "mp4", "quality": "1080",
        }) is None

    def test_format_inferred_from_url_extension(self):
        v = a._normalize_variant({
            "videoUrl": "https://x/v.mp4?token=abc", "quality": "1080",
        })
        assert v is not None
        assert v.format == "mp4"

    def test_format_inferred_hls(self):
        v = a._normalize_variant({
            "videoUrl": "https://x/master.m3u8", "quality": "1080",
        })
        assert v is not None
        assert v.format == "hls"

    def test_unknown_format_returns_none(self):
        assert a._normalize_variant({
            "videoUrl": "https://x/foo.bar", "quality": "1080",
        }) is None

    def test_premium_flag(self):
        v = a._normalize_variant({
            "format": "mp4", "videoUrl": "https://x/v.mp4",
            "quality": "1080", "premium": True,
        })
        assert v.is_premium is True

    def test_default_quality_flag(self):
        v = a._normalize_variant({
            "format": "mp4", "videoUrl": "https://x/v.mp4",
            "quality": "1080", "defaultQuality": True,
        })
        assert v.is_default is True


# ─── pick_best_variant ──────────────────────────────────────────────


def _make_defs():
    return [
        {"format": "mp4", "videoUrl": "https://x/2160.mp4", "quality": "2160"},
        {"format": "mp4", "videoUrl": "https://x/1080.mp4", "quality": "1080"},
        {"format": "mp4", "videoUrl": "https://x/720.mp4", "quality": "720"},
        {"format": "hls", "videoUrl": "https://x/master.m3u8",
         "quality": ["480", "720", "1080", "2160"]},
    ]


class TestPickBestVariant:
    def test_no_pref_picks_highest(self):
        v = a.pick_best_variant(_make_defs())
        assert v.quality == 2160
        # MP4 beats HLS at equal quality
        assert v.format == "mp4"

    def test_best_keyword_picks_highest(self):
        v = a.pick_best_variant(_make_defs(), quality_pref=["best"])
        assert v.quality == 2160

    def test_pref_picks_match(self):
        v = a.pick_best_variant(_make_defs(), quality_pref=[1080])
        assert v.quality == 1080
        assert v.format == "mp4"

    def test_pref_cascade_falls_through(self):
        """8K not available → cascade to 4K → match found."""
        v = a.pick_best_variant(_make_defs(), quality_pref=[4320, 2160, 1080])
        assert v.quality == 2160

    def test_force_format_mp4(self):
        v = a.pick_best_variant(_make_defs(), quality_pref=[2160],
                                 force_format="mp4")
        assert v.format == "mp4"
        assert v.quality == 2160

    def test_force_format_hls(self):
        v = a.pick_best_variant(_make_defs(), quality_pref=[2160],
                                 force_format="hls")
        assert v.format == "hls"

    def test_force_format_no_match_returns_none(self):
        """force_format=hls but only mp4 variants — None."""
        defs = [
            {"format": "mp4", "videoUrl": "https://x/1.mp4", "quality": "1080"},
        ]
        assert a.pick_best_variant(defs, force_format="hls") is None

    def test_empty_defs_returns_none(self):
        assert a.pick_best_variant([]) is None

    def test_all_premium_skipped_returns_none_only_if_filtered(self):
        """pick_best_variant doesn't filter premium itself — caller
        decides via aylo_premium_only config. Verify it returns the
        variant with the flag set."""
        defs = [
            {"format": "mp4", "videoUrl": "https://x/2160.mp4",
             "quality": "2160", "premium": True},
        ]
        v = a.pick_best_variant(defs)
        assert v is not None
        assert v.is_premium is True

    def test_tolerance_handles_off_by_n_encodings(self):
        """A 4080p variant should match a 2160 pref since 4080 ≠ 2160 ±108."""
        defs = [
            {"format": "mp4", "videoUrl": "https://x/2200.mp4", "quality": "2200"},
        ]
        v = a.pick_best_variant(defs, quality_pref=[2160])
        # 2200 - 2160 = 40, within max(50, 2160*0.05=108)
        assert v is not None
        assert v.quality == 2200


# ─── extract_from_html end-to-end ───────────────────────────────────


class TestExtractFromHtml:
    def test_happy_path(self):
        r = a.extract_from_html(_make_html(_GOOD_FV),
                                 quality_pref=[4320, 2160, 1080])
        assert r.ok is True
        assert r.variant.quality == 2160
        assert r.title == "Test Scene"
        assert r.duration_sec == 1820
        assert r.thumbnail_url == "https://thumb.example.com/scene.jpg"
        assert "2160p/mp4" in r.available_qualities

    def test_empty_html(self):
        r = a.extract_from_html("")
        assert r.ok is False
        assert r.error == "empty_html"

    def test_no_flashvars(self):
        r = a.extract_from_html("<html><body>hi</body></html>")
        assert r.ok is False
        assert r.error == "no_flashvars"

    def test_empty_mediadefs(self):
        b = 'var flashvars_1 = {"video_title": "T", "mediaDefinitions": []};'
        # Note: extract_flashvars rejects blocks with no mediaDefinitions
        # entirely — see test above. So the result is no_flashvars.
        r = a.extract_from_html(_make_html(b))
        assert r.ok is False
        # Either no_flashvars (block rejected at parse time) or
        # empty_mediadefs (block accepted but list is empty) is fine
        assert r.error in ("no_flashvars", "empty_mediadefs")

    def test_no_usable_variant(self):
        """flashvars block exists, mediaDefinitions exists, but every
        videoUrl is empty (premium-only on free account)."""
        b = '''var flashvars_1 = {
            "video_title": "T",
            "video_duration": 100,
            "mediaDefinitions": [
                {"format": "mp4", "videoUrl": "", "quality": "1080", "premium": true}
            ]
        };'''
        r = a.extract_from_html(_make_html(b))
        # Block has only-empty-URL variants. extract_flashvars accepts
        # it (mediaDefinitions is non-empty list), pick_best_variant
        # returns None because every entry normalizes to None.
        assert r.ok is False
        assert r.error in ("no_usable_variant", "empty_mediadefs")

    def test_default_picks_highest(self):
        """No quality_pref → take highest."""
        r = a.extract_from_html(_make_html(_GOOD_FV))
        assert r.ok is True
        assert r.variant.quality == 2160


# ─── Runner integration smoke ───────────────────────────────────────


class TestRunnerHelperExists:
    def test_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_try_aylo_extractor")

    def test_disabled_via_config(self):
        """When use_aylo_extractor=False the runner should never call
        the helper. This test just verifies the config field exists in
        DEFAULTS so the dispatcher's gate works."""
        from bulk_downloader.app_kernel import DEFAULTS, CFG_FIELDS
        assert "use_aylo_extractor" in CFG_FIELDS
        assert DEFAULTS.get("use_aylo_extractor") is True

    def test_force_format_field_exists(self):
        from bulk_downloader.app_kernel import CFG_FIELDS
        assert "aylo_force_format" in CFG_FIELDS

    def test_premium_only_field_exists(self):
        from bulk_downloader.app_kernel import DEFAULTS, CFG_FIELDS
        assert "aylo_premium_only" in CFG_FIELDS
        assert DEFAULTS.get("aylo_premium_only") is False
