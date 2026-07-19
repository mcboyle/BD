"""v3.43.69: tests for the dl8-video + Badoink filename-prediction
extractor.

Covers:
  1. DL8_BRANDS / BADOINK_BRANDS catalogs (Badoink ⊂ DL8)
  2. is_dl8_url / is_badoink_url with subdomain variants
  3. parse_quality_label: 8K/7K/6K/5K/4K/HEVC/1080p mappings
  4. parse_dl8_video: happy path, self-closing sources, no element
  5. _SOURCE_TAG_RE regression: `/` in src= URLs doesn't break parse
  6. predict_badoink_filenames: every template, descending tier order,
     max_tier_hint cap, no-trailer-suffix returns empty
  7. detect_max_quality_tag: finds <a class="video-tag">8K</a>
  8. _pick_source: cascade order, proportional tolerance
  9. extract_from_html end-to-end: dl8_source path, badoink_predict
     path, malformed input fail-open
 10. probe_badoink_candidates mocked HTTP behavior
 11. Runner integration: method exists, config fields present
"""
from __future__ import annotations

import sys
# [SAST 3:13pm 13 may] removed unused: import pytest
from unittest.mock import patch, MagicMock

from bulk_downloader import extractors_dl8 as d


# ─── Brand catalogs ─────────────────────────────────────────────────


class TestBrandCatalogs:
    def test_dl8_has_all_six_brands(self):
        for brand in ("tmwvrnet.com", "badoinkvr.com", "babevr.com",
                      "vrcosplayx.com", "18vr.com", "realvr.com"):
            assert brand in d.DL8_BRANDS, f"missing {brand}"

    def test_badoink_is_strict_subset(self):
        """Every Badoink brand must also be a dl8 brand."""
        assert d.BADOINK_BRANDS.issubset(d.DL8_BRANDS)

    def test_tmwvrnet_in_dl8_not_in_badoink(self):
        """TmwVRnet uses dl8 but doesn't have the filename-prediction trick."""
        assert "tmwvrnet.com" in d.DL8_BRANDS
        assert "tmwvrnet.com" not in d.BADOINK_BRANDS


# ─── URL matchers ───────────────────────────────────────────────────


class TestIsDl8Url:
    def test_naked_tmwvrnet(self):
        assert d.is_dl8_url("https://tmwvrnet.com/scene/x") is True

    def test_www_subdomain(self):
        assert d.is_dl8_url("https://www.badoinkvr.com/scene/x") is True

    def test_18vr_numeric_brand(self):
        """18vr.com has a numeric prefix — eTLD+1 must still match."""
        assert d.is_dl8_url("https://www.18vr.com/scene/x") is True

    def test_non_dl8_returns_false(self):
        assert d.is_dl8_url("https://brazzers.com/x") is False
        assert d.is_dl8_url("https://wowgirls.com/x") is False

    def test_garbage_inputs(self):
        assert d.is_dl8_url("") is False
        assert d.is_dl8_url(None) is False  # type: ignore[arg-type]


class TestIsBadoinkUrl:
    def test_badoink_matches(self):
        assert d.is_badoink_url("https://badoinkvr.com/x") is True
        assert d.is_badoink_url("https://18vr.com/x") is True
        assert d.is_badoink_url("https://www.realvr.com/x") is True

    def test_tmwvrnet_not_badoink(self):
        """Important: tmwvrnet uses dl8 but is NOT in Badoink network."""
        assert d.is_badoink_url("https://tmwvrnet.com/x") is False

    def test_non_dl8_brands(self):
        assert d.is_badoink_url("https://example.com") is False


# ─── parse_quality_label ────────────────────────────────────────────


class TestParseQualityLabel:
    def test_8k(self):
        assert d.parse_quality_label("8K") == 4320

    def test_5k(self):
        assert d.parse_quality_label("5K") == 2880

    def test_4k(self):
        assert d.parse_quality_label("4K") == 2160

    def test_4k_hevc_combo(self):
        """'4K HEVC' label should still map to 2160 (resolution is 4K)."""
        assert d.parse_quality_label("4K HEVC") == 2160

    def test_1080p(self):
        assert d.parse_quality_label("1080p") == 1080

    def test_hevc_alone_defaults_to_2160(self):
        """Bare 'HEVC' (no resolution): assume 4K — most common for VR."""
        assert d.parse_quality_label("HEVC") == 2160

    def test_lowercase(self):
        assert d.parse_quality_label("8k") == 4320

    def test_with_whitespace(self):
        assert d.parse_quality_label("  5 K  ") == 2880

    def test_unknown_returns_zero(self):
        assert d.parse_quality_label("Mobile") == 0
        assert d.parse_quality_label("Trailer") == 0
        assert d.parse_quality_label("") == 0

    def test_none_input(self):
        assert d.parse_quality_label(None) == 0  # type: ignore[arg-type]


# ─── parse_dl8_video ────────────────────────────────────────────────


class TestParseDl8Video:
    SIMPLE = '''<html><body>
<dl8-video title="Hot Scene" poster="https://cdn.x/p.jpg"
           projection="equirectangular" stereo="sbs">
  <source src="https://cdn.badoinkvr.com/SCENE_8k_180_180x180_3dh.mp4" quality="8K" type="video/mp4"/>
  <source src="https://cdn.badoinkvr.com/SCENE_5k_180_180x180_3dh.mp4" quality="5K" type="video/mp4"/>
</dl8-video>
</body></html>'''

    def test_parses_attributes(self):
        p = d.parse_dl8_video(self.SIMPLE)
        assert p is not None
        assert p.title == "Hot Scene"
        assert p.poster == "https://cdn.x/p.jpg"
        assert p.projection == "equirectangular"
        assert p.stereo == "sbs"

    def test_parses_sources(self):
        p = d.parse_dl8_video(self.SIMPLE)
        assert len(p.sources) == 2
        assert p.sources[0].quality_label == "8K"
        assert p.sources[0].quality_tier == 4320

    def test_source_url_with_slashes_not_broken(self):
        """v3.43.69 regression test: _SOURCE_TAG_RE must not exclude `/`
        from attribute values. URLs in src= are full of slashes."""
        p = d.parse_dl8_video(self.SIMPLE)
        for s in p.sources:
            assert "/" in s.url
            assert s.url.startswith("https://")

    def test_no_element_returns_none(self):
        assert d.parse_dl8_video("<html></html>") is None

    def test_element_no_sources(self):
        html = '<dl8-video title="X" poster="y.jpg"></dl8-video>'
        p = d.parse_dl8_video(html)
        assert p is not None
        assert p.title == "X"
        assert p.sources == []

    def test_self_closing_vs_open_close_source(self):
        """Both `<source ... />` and `<source ... >` should parse."""
        html = '''<dl8-video title="X">
<source src="https://cdn.tmwvrnet.com/v_5k.mp4" quality="5K" />
<source src="https://cdn.tmwvrnet.com/v_4k.mp4" quality="4K" >
</dl8-video>'''
        p = d.parse_dl8_video(html)
        assert len(p.sources) == 2

    def test_single_quoted_attrs(self):
        html = "<dl8-video title='X' poster='y'><source src='https://cdn.tmwvrnet.com/a.mp4' quality='4K'/></dl8-video>"
        p = d.parse_dl8_video(html)
        assert p.title == "X"
        assert len(p.sources) == 1

    def test_empty_input(self):
        assert d.parse_dl8_video("") is None
        assert d.parse_dl8_video(None) is None  # type: ignore[arg-type]


# ─── predict_badoink_filenames ──────────────────────────────────────


class TestPredictBadoinkFilenames:
    TRAILER_URL = "https://cdn.badoinkvr.com/path/SCENE_basename_trailer.mp4"

    def test_generates_candidates(self):
        cands = d.predict_badoink_filenames(self.TRAILER_URL)
        assert len(cands) > 0

    def test_descending_tier_order(self):
        cands = d.predict_badoink_filenames(self.TRAILER_URL)
        tiers = [c.tier for c in cands]
        # Must be descending (or equal-ties allowed)
        for i in range(len(tiers) - 1):
            assert tiers[i] >= tiers[i + 1]

    def test_includes_8k_at_top(self):
        cands = d.predict_badoink_filenames(self.TRAILER_URL)
        assert cands[0].tier == 4320

    def test_8k_suffix_in_url(self):
        cands = d.predict_badoink_filenames(self.TRAILER_URL)
        assert "_8k_180_180x180_3dh.mp4" in cands[0].url

    def test_basename_preserved(self):
        cands = d.predict_badoink_filenames(self.TRAILER_URL)
        for c in cands:
            assert "SCENE_basename" in c.url
            assert "_trailer" not in c.url

    def test_handles_1m_suffix(self):
        """Some trailer URLs end in _1m / _2m / _3m instead of _trailer."""
        url = "https://cdn.badoinkvr.com/path/SCENE_basename_2m.mp4"
        cands = d.predict_badoink_filenames(url)
        assert len(cands) > 0
        for c in cands:
            assert "_2m" not in c.url

    def test_query_string_stripped(self):
        """The trailer URL may have ?token=... — candidate URLs should
        NOT carry that query through."""
        url = "https://cdn.badoinkvr.com/path/SCENE_basename_trailer.mp4?token=abc"
        cands = d.predict_badoink_filenames(url)
        for c in cands:
            assert "?token" not in c.url
            assert "?" not in c.url

    def test_no_trailer_suffix_returns_empty(self):
        """If the URL doesn't have a strippable suffix, nothing to predict."""
        url = "https://cdn.badoinkvr.com/random.mp4"
        assert d.predict_badoink_filenames(url) == []

    def test_max_tier_hint_caps_candidates(self):
        """If page said 5K, don't bother with 8K probes."""
        cands = d.predict_badoink_filenames(self.TRAILER_URL, max_tier_hint=2880)
        for c in cands:
            assert c.tier <= 2880

    def test_max_tier_zero_unrestricted(self):
        """max_tier_hint=0 means no cap."""
        cands = d.predict_badoink_filenames(self.TRAILER_URL, max_tier_hint=0)
        assert any(c.tier == 4320 for c in cands)

    def test_empty_url(self):
        assert d.predict_badoink_filenames("") == []
        assert d.predict_badoink_filenames(None) == []  # type: ignore[arg-type]


# ─── detect_max_quality_tag ─────────────────────────────────────────


class TestDetectMaxQualityTag:
    def test_finds_8k_tag(self):
        html = '<a class="video-tag">8K</a> <a class="video-tag">VR</a>'
        assert d.detect_max_quality_tag(html) == 4320

    def test_finds_highest_when_multiple(self):
        html = '<a class="video-tag">4K</a> <a class="video-tag">8K</a>'
        assert d.detect_max_quality_tag(html) == 4320

    def test_no_tag_returns_zero(self):
        html = '<a class="not-video-tag">8K</a>'
        assert d.detect_max_quality_tag(html) == 0

    def test_empty_input(self):
        assert d.detect_max_quality_tag("") == 0


# ─── _pick_source ───────────────────────────────────────────────────


class TestPickSource:
    def _make_sources(self, *tiers):
        return [d.Dl8Source(url=f"https://x/v_{t}.mp4",
                            quality_label=f"{t}p", quality_tier=t)
                for t in tiers]

    def test_no_pref_picks_highest(self):
        s = d._pick_source(self._make_sources(720, 1080, 2160), None)
        assert s.quality_tier == 2160

    def test_best_keyword_picks_highest(self):
        s = d._pick_source(self._make_sources(720, 2160), ["best"])
        assert s.quality_tier == 2160

    def test_pref_cascade(self):
        srcs = self._make_sources(720, 1080, 2160)
        s = d._pick_source(srcs, [4320, 2160])
        assert s.quality_tier == 2160

    def test_pref_pick_lower(self):
        srcs = self._make_sources(720, 1080, 2160)
        s = d._pick_source(srcs, [1080])
        assert s.quality_tier == 1080

    def test_empty_sources(self):
        assert d._pick_source([], [1080]) is None


# ─── extract_from_html end-to-end ───────────────────────────────────


class TestExtractFromHtmlMemberPage:
    """Standard dl8 path: member-area page with full tier URLs."""

    HTML = '''<html><body>
<dl8-video title="Hot Scene" poster="https://cdn.x/p.jpg">
  <source src="https://cdn.badoinkvr.com/SCENE_8k_180_180x180_3dh.mp4" quality="8K"/>
  <source src="https://cdn.badoinkvr.com/SCENE_5k_180_180x180_3dh.mp4" quality="5K"/>
</dl8-video>
</body></html>'''

    def test_happy_path(self):
        r = d.extract_from_html(self.HTML,
                                 page_url="https://tmwvrnet.com/scene/1",
                                 quality_pref=[4320, 2160])
        assert r.ok is True
        assert r.via == "dl8_source"
        assert r.tier == 4320
        assert r.title == "Hot Scene"
        assert r.poster == "https://cdn.x/p.jpg"
        assert "_8k_180_180x180_3dh" in r.url

    def test_no_html(self):
        r = d.extract_from_html("")
        assert r.ok is False
        assert r.error == "empty_html"

    def test_no_dl8_element(self):
        r = d.extract_from_html("<html><body>nothing here</body></html>")
        assert r.ok is False
        assert r.error == "no_dl8_element"


class TestExtractFromHtmlBadoinkPredict:
    """Badoink trailer page: predict member-area URLs from public trailer."""

    HTML = '''<html><body>
<a class="video-tag">8K</a>
<dl8-video title="Hot Scene" poster="https://cdn.x/p.jpg">
  <source src="https://cdn.badoinkvr.com/HotScene_basename_trailer.mp4" quality="Trailer"/>
</dl8-video>
</body></html>'''

    def test_returns_candidates(self):
        r = d.extract_from_html(
            self.HTML, page_url="https://badoinkvr.com/scene/hot/",
            quality_pref=[4320],
        )
        assert r.ok is True
        assert r.via == "badoink_predict"
        assert r.url == ""  # caller HEAD-probes the candidates
        assert len(r.badoink_candidates) > 0

    def test_first_candidate_is_8k(self):
        r = d.extract_from_html(
            self.HTML, page_url="https://badoinkvr.com/scene/hot/",
        )
        assert r.badoink_candidates[0].tier == 4320

    def test_predict_disabled_falls_back_to_trailer(self):
        """When predict_badoink=False, just use the trailer URL itself."""
        r = d.extract_from_html(
            self.HTML, page_url="https://badoinkvr.com/scene/hot/",
            predict_badoink=False,
        )
        assert r.ok is True
        assert r.via == "dl8_source"
        assert "_trailer" in r.url

    def test_tmwvrnet_uses_dl8_path_not_badoink(self):
        """tmwvrnet has dl8-video but isn't Badoink-network — must NOT
        attempt filename prediction even if sources look trailer-y."""
        r = d.extract_from_html(
            self.HTML, page_url="https://tmwvrnet.com/scene/1",
        )
        assert r.ok is True
        assert r.via == "dl8_source"
        assert r.badoink_candidates == []


# ─── probe_badoink_candidates (mocked HTTP) ─────────────────────────


class TestProbeBadoinkCandidates:
    def _mock_httpx(self, status_map):
        """Build a fake httpx whose head() returns status per URL substring."""
        fake = MagicMock()
        fake.RequestError = Exception

        def _head(url, headers=None, **kw):
            for substr, status in status_map.items():
                if substr in url:
                    r = MagicMock(); r.status_code = status; r.headers = {}
                    return r
            r = MagicMock(); r.status_code = 404; r.headers = {}
            return r

        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=None)
        client.head = MagicMock(side_effect=_head)
        client.get = MagicMock(side_effect=_head)
        fake.Client = MagicMock(return_value=client)
        return fake

    def test_picks_first_200(self):
        cands = [
            d.BadoinkCandidate(url="https://x/v_8k.mp4", tier=4320, suffix="_8k"),
            d.BadoinkCandidate(url="https://x/v_5k.mp4", tier=2880, suffix="_5k"),
        ]
        # Only 5k responds 200
        fake = self._mock_httpx({"_5k": 200})
        with patch.dict(sys.modules, {"httpx": fake}):
            picked = d.probe_badoink_candidates(cands)
        assert picked is not None
        assert picked.tier == 2880

    def test_8k_wins_when_both_ok(self):
        """Candidates probed in descending order; first 200 wins."""
        cands = [
            d.BadoinkCandidate(url="https://x/v_8k.mp4", tier=4320, suffix="_8k"),
            d.BadoinkCandidate(url="https://x/v_5k.mp4", tier=2880, suffix="_5k"),
        ]
        fake = self._mock_httpx({"_8k": 200, "_5k": 200})
        with patch.dict(sys.modules, {"httpx": fake}):
            picked = d.probe_badoink_candidates(cands)
        assert picked.tier == 4320

    def test_all_404_returns_none(self):
        cands = [
            d.BadoinkCandidate(url="https://x/v_8k.mp4", tier=4320, suffix="_8k"),
            d.BadoinkCandidate(url="https://x/v_5k.mp4", tier=2880, suffix="_5k"),
        ]
        fake = self._mock_httpx({})  # all 404
        with patch.dict(sys.modules, {"httpx": fake}):
            picked = d.probe_badoink_candidates(cands)
        assert picked is None

    def test_403_aborts_cascade(self):
        """403 means auth issue; don't keep probing — they'll all 403."""
        cands = [
            d.BadoinkCandidate(url="https://x/v_8k.mp4", tier=4320, suffix="_8k"),
            d.BadoinkCandidate(url="https://x/v_5k.mp4", tier=2880, suffix="_5k"),
        ]
        fake = self._mock_httpx({"_8k": 403, "_5k": 200})
        with patch.dict(sys.modules, {"httpx": fake}):
            picked = d.probe_badoink_candidates(cands)
        # We bail on 403 — even though 5k would 200, we never check it
        assert picked is None

    def test_empty_candidates_returns_none(self):
        assert d.probe_badoink_candidates([]) is None


# ─── Runner integration ─────────────────────────────────────────────


class TestRunnerHelper:
    def test_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_try_dl8_extractor")

    def test_config_fields_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_dl8_extractor" in CFG_FIELDS
        assert "dl8_predict_badoink_filenames" in CFG_FIELDS
        assert DEFAULTS.get("use_dl8_extractor") is True
        assert DEFAULTS.get("dl8_predict_badoink_filenames") is True
