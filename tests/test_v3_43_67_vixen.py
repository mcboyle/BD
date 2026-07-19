"""v3.43.67: tests for the Vixen Media Group extractor.

Covers:
  1. VIXEN_BRANDS list completeness
  2. is_vixen_url() matches subdomain + naked variants
  3. extract_next_data() handles present/absent/malformed script tags
  4. find_video_record() walks varied pageProps shapes
  5. find_video_src_in_html() picks the first Vixen CDN <video src>
  6. _tier_from_url() handles all known Vixen URL shapes
  7. _looks_like_vixen_cdn() correctly identifies all 10 brand CDNs
  8. _pick_url_by_preference() honors cascade + proportional tolerance
  9. extract_from_html() end-to-end via __NEXT_DATA__ path
 10. extract_from_html() falls back to <video src> path
 11. extract_from_html() returns no_extractor_path when nothing works
 12. Runner _try_vixen_extractor method registered
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import pytest

from bulk_downloader import extractors_vixen as v


# ─── VIXEN_BRANDS ────────────────────────────────────────────────────


class TestVixenBrandsList:
    def test_brands_count_at_least_10(self):
        assert len(v.VIXEN_BRANDS) >= 10

    def test_core_four_brands(self):
        for brand in ("vixen.com", "blacked.com", "tushy.com", "deeper.com"):
            assert brand in v.VIXEN_BRANDS

    def test_raw_subbrand_variants_present(self):
        """Vixen has both blackedraw.com and tushyraw.com as separate
        domains, NOT just blacked.com/raw/ — they need their own entries."""
        assert "blackedraw.com" in v.VIXEN_BRANDS
        assert "tushyraw.com" in v.VIXEN_BRANDS

    def test_milfymd_present(self):
        assert "milfymd.com" in v.VIXEN_BRANDS


# ─── is_vixen_url ───────────────────────────────────────────────────


class TestIsVixenUrl:
    def test_naked_vixen(self):
        assert v.is_vixen_url("https://vixen.com/v/123") is True

    def test_members_subdomain(self):
        assert v.is_vixen_url("https://members.vixen.com/videos/x") is True

    def test_www_subdomain(self):
        assert v.is_vixen_url("https://www.blacked.com/v/y") is True

    def test_all_brands_match(self):
        for brand in v.VIXEN_BRANDS:
            assert v.is_vixen_url(f"https://www.{brand}/v/x") is True, \
                f"failed for {brand}"

    def test_non_vixen_returns_false(self):
        for u in ("https://brazzers.com/v",
                  "https://wowgirls.com/x",
                  "https://newsensations.com/y"):
            assert v.is_vixen_url(u) is False, f"matched wrongly: {u}"

    def test_garbage_inputs(self):
        assert v.is_vixen_url("") is False
        assert v.is_vixen_url(None) is False  # type: ignore[arg-type]
        assert v.is_vixen_url("not a url") is False


# ─── extract_next_data ──────────────────────────────────────────────


class TestExtractNextData:
    def test_simple_block(self):
        html = '<html><body><script id="__NEXT_DATA__" type="application/json">{"a": 1, "b": [1, 2]}</script></body></html>'
        data = v.extract_next_data(html)
        assert data == {"a": 1, "b": [1, 2]}

    def test_single_quoted_attr(self):
        html = "<script id='__NEXT_DATA__' type='application/json'>{\"x\": 1}</script>"
        data = v.extract_next_data(html)
        assert data == {"x": 1}

    def test_missing_script_returns_none(self):
        assert v.extract_next_data("<html><body>hi</body></html>") is None

    def test_empty_input_returns_none(self):
        assert v.extract_next_data("") is None
        assert v.extract_next_data(None) is None  # type: ignore[arg-type]

    def test_malformed_json_returns_none(self):
        html = '<script id="__NEXT_DATA__">{not valid json}</script>'
        assert v.extract_next_data(html) is None

    def test_handles_multiline_content(self):
        html = '''<script id="__NEXT_DATA__" type="application/json">
{
  "a": 1,
  "b": 2
}
</script>'''
        data = v.extract_next_data(html)
        assert data == {"a": 1, "b": 2}


# ─── find_video_record ──────────────────────────────────────────────


class TestFindVideoRecord:
    def test_direct_videoSrc_shape(self):
        tree = {"props": {"pageProps": {"video": {
            "title": "Scene",
            "videoSrc": "https://cdn.vixen.com/video/mp4_1080/x.mp4",
        }}}}
        rec = v.find_video_record(tree)
        assert rec is not None
        assert rec["title"] == "Scene"

    def test_nested_sources_array(self):
        tree = {"props": {"pageProps": {"data": {"video": {
            "title": "Scene",
            "sources": [
                {"url": "https://cdn.tushy.com/video/mp4_2160/x.mp4",
                 "resolution": 2160}
            ],
        }}}}}
        rec = v.find_video_record(tree)
        assert rec is not None

    def test_apollo_state_shape(self):
        """Apollo Client cache flattens video records into the state dict
        keyed by `Video:<id>`."""
        tree = {"props": {"pageProps": {"initialApolloState": {
            "Video:12345": {
                "name": "Scene",
                "videoUrl": "https://cdn.blacked.com/video/mp4_2160/x.mp4",
            }
        }}}}
        rec = v.find_video_record(tree)
        assert rec is not None

    def test_no_match_returns_none(self):
        tree = {"props": {"pageProps": {"unrelated": True}}}
        assert v.find_video_record(tree) is None

    def test_no_title_rejected(self):
        """Without a title/name/videoTitle key, dict isn't a video record
        even if it has a URL."""
        tree = {"x": {"videoSrc": "https://cdn.vixen.com/video/mp4_1080/x.mp4"}}
        assert v.find_video_record(tree) is None

    def test_non_vixen_cdn_rejected(self):
        """A title + URL combo where URL isn't a Vixen CDN doesn't count."""
        tree = {"x": {
            "title": "Scene",
            "videoSrc": "https://cdn.example.com/v.mp4",
        }}
        assert v.find_video_record(tree) is None

    def test_handles_list_at_top_level(self):
        tree = [
            {"unrelated": 1},
            {"props": {"pageProps": {"video": {
                "title": "Scene",
                "videoSrc": "https://cdn.vixen.com/video/mp4_1080/x.mp4",
            }}}},
        ]
        assert v.find_video_record(tree) is not None

    def test_max_depth_protection(self):
        """Pathologically nested tree shouldn't crash."""
        tree = {"a": None}
        cur = tree
        for _ in range(50):
            cur["a"] = {"a": None}
            cur = cur["a"]
        # Should return None without recursion error
        assert v.find_video_record(tree) is None


# ─── find_video_src_in_html ─────────────────────────────────────────


class TestFindVideoSrc:
    def test_finds_vixen_cdn_src(self):
        html = '<video class="x" src="https://cdn.vixen.com/video/mp4_480/x.mp4"></video>'
        url = v.find_video_src_in_html(html)
        assert url is not None
        assert "mp4_480" in url

    def test_finds_tushy_cdn(self):
        html = '<video src="https://cdn.tushy.com/video/mp4_2160/y.mp4"></video>'
        assert v.find_video_src_in_html(html) is not None

    def test_skips_non_vixen_cdn(self):
        html = '<video src="https://cdn.example.com/v.mp4"></video>'
        assert v.find_video_src_in_html(html) is None

    def test_picks_first_match(self):
        html = '''
        <video src="https://cdn.blacked.com/previewvideos/106488/preview.mp4"></video>
        <video src="https://cdn.vixen.com/video/mp4_480/main.mp4"></video>
        '''
        # Both match the Vixen-CDN test; we pick the first
        url = v.find_video_src_in_html(html)
        assert url is not None
        assert "previewvideos" in url

    def test_decodes_html_entities(self):
        """`&amp;` in src should be decoded to `&` before checking."""
        html = '<video src="https://cdn.vixen.com/video/mp4_480/x.mp4?foo=1&amp;bar=2"></video>'
        url = v.find_video_src_in_html(html)
        assert "&" in url and "&amp;" not in url

    def test_no_video_tag(self):
        assert v.find_video_src_in_html("<html></html>") is None

    def test_empty_input(self):
        assert v.find_video_src_in_html("") is None


# ─── _tier_from_url ─────────────────────────────────────────────────


class TestTierFromUrl:
    def test_mp4_2160_segment(self):
        assert v._tier_from_url("https://cdn.vixen.com/video/mp4_2160/x.mp4") == 2160

    def test_mp4_480_segment(self):
        assert v._tier_from_url("https://cdn.vixen.com/video/mp4_480/x.mp4") == 480

    def test_vixen_named_suffix(self):
        assert v._tier_from_url(
            "https://cdn.vixen.com/video/mp4_2160/x/y/VIXEN_106491_2160P.mp4"
        ) == 2160

    def test_no_tier_returns_zero(self):
        assert v._tier_from_url("https://cdn.vixen.com/video/x.mp4") == 0

    def test_empty_returns_zero(self):
        assert v._tier_from_url("") == 0


# ─── _looks_like_vixen_cdn ──────────────────────────────────────────


class TestLooksLikeVixenCdn:
    def test_all_brand_cdns(self):
        for brand in ("vixen", "blacked", "tushy", "deeper", "slayed",
                      "wifey", "milfymd", "vixenplus", "blackedraw",
                      "tushyraw"):
            assert v._looks_like_vixen_cdn(f"https://cdn.{brand}.com/x") is True

    def test_vixencdn_aggregate(self):
        """Some scenes load from a unified `cdn.vixencdn.com`."""
        assert v._looks_like_vixen_cdn("https://cdn.vixencdn.com/x") is True

    def test_subdomain_variants(self):
        assert v._looks_like_vixen_cdn("https://stream-cdn.vixen.com/x") is True

    def test_non_vixen_returns_false(self):
        assert v._looks_like_vixen_cdn("https://cdn.brazzers.com/x") is False
        assert v._looks_like_vixen_cdn("https://example.com/x") is False


# ─── _pick_url_by_preference ────────────────────────────────────────


class TestPickUrlByPreference:
    URLS = [
        ("https://cdn.vixen.com/mp4_480/x.mp4", 480),
        ("https://cdn.vixen.com/mp4_1080/x.mp4", 1080),
        ("https://cdn.vixen.com/mp4_2160/x.mp4", 2160),
    ]

    def test_no_pref_picks_highest(self):
        url, tier = v._pick_url_by_preference(self.URLS, None)
        assert tier == 2160

    def test_best_keyword_picks_highest(self):
        url, tier = v._pick_url_by_preference(self.URLS, ["best"])
        assert tier == 2160

    def test_pref_picks_exact_match(self):
        url, tier = v._pick_url_by_preference(self.URLS, [1080])
        assert tier == 1080

    def test_pref_cascade_falls_through(self):
        """8K not available → 4K is."""
        url, tier = v._pick_url_by_preference(self.URLS, [4320, 2160, 1080])
        assert tier == 2160

    def test_proportional_tolerance(self):
        """target=2160, candidate=2200 → within max(50, 108)=108. Match."""
        urls = [("https://cdn.vixen.com/mp4_2200/x.mp4", 2200)]
        url, tier = v._pick_url_by_preference(urls, [2160])
        assert tier == 2200

    def test_dedup_on_url(self):
        urls = self.URLS + [self.URLS[0]]  # duplicate
        url, tier = v._pick_url_by_preference(urls, [480])
        assert tier == 480

    def test_empty_returns_empty_tuple(self):
        url, tier = v._pick_url_by_preference([], None)
        assert url == "" and tier == 0


# ─── extract_from_html end-to-end ───────────────────────────────────


class TestExtractFromHtmlNextData:
    SAMPLE = '''<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"video":{
  "title":"Pull Chapter 1",
  "duration":2632,
  "poster":"https://t.example.com/p.jpg",
  "videoSrc":"https://cdn.vixen.com/video/mp4_2160/106491/abc/VIXEN_106491_2160P.mp4?token=x",
  "sources":[
    {"resolution":480,"url":"https://cdn.vixen.com/video/mp4_480/106491/abc/x.mp4"},
    {"resolution":1080,"url":"https://cdn.vixen.com/video/mp4_1080/106491/abc/x.mp4"},
    {"resolution":2160,"url":"https://cdn.vixen.com/video/mp4_2160/106491/abc/x.mp4"}
  ]
}}}}
</script>
</body></html>'''

    def test_happy_path(self):
        r = v.extract_from_html(self.SAMPLE, quality_pref=[4320, 2160, 1080])
        assert r.ok is True
        assert r.via == "next_data"
        assert r.tier == 2160
        assert r.title == "Pull Chapter 1"
        assert r.duration_sec == 2632
        assert r.thumbnail_url == "https://t.example.com/p.jpg"
        assert 2160 in r.available_tiers

    def test_picks_highest_when_no_pref(self):
        r = v.extract_from_html(self.SAMPLE)
        assert r.ok is True
        assert r.tier == 2160

    def test_picks_lower_when_pref_excludes_higher(self):
        r = v.extract_from_html(self.SAMPLE, quality_pref=[1080])
        assert r.ok is True
        assert r.tier == 1080


class TestExtractFromHtmlVideoSrc:
    SAMPLE = '''<html><head>
<title>Pull Chapter 1: VIXEN</title>
<meta property="og:image" content="https://t.example.com/p.jpg">
<meta property="og:description" content="An amazing scene">
</head><body>
<video class="vjs-tech" src="https://cdn.vixen.com/video/mp4_480/106491/abc/VIXEN_106491_480P.mp4?token=y"></video>
</body></html>'''

    def test_happy_path(self):
        r = v.extract_from_html(self.SAMPLE, quality_pref=[2160])
        assert r.ok is True
        assert r.via == "video_src"
        assert r.tier == 480  # only one URL, tier-probe would climb later
        assert r.title == "Pull Chapter 1: VIXEN"
        assert r.thumbnail_url == "https://t.example.com/p.jpg"
        assert "amazing" in r.description

    def test_hls_url_flagged_correctly(self):
        html = '<video src="https://cdn.vixen.com/master.m3u8"></video>'
        r = v.extract_from_html(html)
        assert r.ok is True
        assert r.is_hls is True


class TestExtractFailureModes:
    def test_empty_html(self):
        r = v.extract_from_html("")
        assert r.ok is False
        assert r.error == "empty_html"

    def test_no_extractor_path(self):
        r = v.extract_from_html("<html><body>no video</body></html>")
        assert r.ok is False
        assert r.error == "no_extractor_path"

    def test_malformed_next_data_falls_to_video_src(self):
        """Garbage __NEXT_DATA__ shouldn't crash — should fall through
        to video-src path."""
        html = '''<html>
<script id="__NEXT_DATA__">{malformed</script>
<video src="https://cdn.vixen.com/video/mp4_1080/x.mp4"></video>
</html>'''
        r = v.extract_from_html(html)
        assert r.ok is True
        assert r.via == "video_src"


# ─── Runner integration ─────────────────────────────────────────────


class TestRunnerHelper:
    def test_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_try_vixen_extractor")

    def test_config_field_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_vixen_extractor" in CFG_FIELDS
        assert DEFAULTS.get("use_vixen_extractor") is True
