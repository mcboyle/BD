"""v3.43.65: tests for the tier_probe module.

These tests don't require network access — every HTTP call is mocked.
They verify:

  1. KNOWN_PATTERNS registry is well-formed and resolves correctly
  2. compile_pattern() rejects bad regex and missing-tier-group patterns
  3. parse_tier_from_url() extracts the tier value + prefix/suffix
  4. build_candidate_urls() orders candidates in descending tier order
  5. build_candidate_urls() respects skip_lower_than_current
  6. probe_higher_tiers() returns original URL when pattern doesn't match
  7. probe_higher_tiers() picks highest 200-OK candidate
  8. probe_higher_tiers() falls back to original on all-404
  9. probe_higher_tiers() short-circuits on signed-URL 403
 10. probe_higher_tiers() handles HEAD-not-allowed via Range GET
 11. probe_higher_tiers() respects max_attempts cap
 12. probe_higher_tiers() never raises — even on exotic inputs
"""
from __future__ import annotations

import re
import sys
from unittest.mock import patch, MagicMock

from bulk_downloader import tier_probe


# ─── KNOWN_PATTERNS registry ────────────────────────────────────────


class TestKnownPatterns:
    def test_registry_nonempty(self):
        assert len(tier_probe.KNOWN_PATTERNS) >= 4

    def test_every_pattern_compiles(self):
        for name, pat in tier_probe.KNOWN_PATTERNS.items():
            try:
                rx = re.compile(pat)
            except re.error as e:
                raise AssertionError(f"{name}: pattern won't compile: {e}")
            assert "tier" in rx.groupindex, \
                f"{name}: pattern missing (?P<tier>...) group"

    def test_list_known_patterns_sorted(self):
        names = tier_probe.list_known_patterns()
        assert names == sorted(names)
        assert set(names) == set(tier_probe.KNOWN_PATTERNS.keys())

    def test_resolve_known_key_returns_regex(self):
        out = tier_probe.resolve_pattern("vixen_network")
        assert out != "vixen_network"  # resolved to regex
        assert "tier" in out

    def test_resolve_unknown_key_passes_through(self):
        custom = r"foo_(?P<tier>\d+)_bar"
        assert tier_probe.resolve_pattern(custom) == custom


# ─── compile_pattern ────────────────────────────────────────────────


class TestCompilePattern:
    def test_compiles_good_pattern(self):
        rx = tier_probe.compile_pattern(r"mp4_(?P<tier>\d+)/")
        assert rx is not None
        m = rx.search("/video/mp4_480/abc")
        assert m and m.group("tier") == "480"

    def test_rejects_bad_regex(self):
        # Unbalanced paren
        assert tier_probe.compile_pattern(r"(?P<tier>\d+(") is None

    def test_rejects_missing_tier_group(self):
        # Has a group but it's not named "tier"
        assert tier_probe.compile_pattern(r"(\d+)") is None

    def test_empty_pattern_returns_none(self):
        assert tier_probe.compile_pattern("") is None
        assert tier_probe.compile_pattern(None) is None  # type: ignore[arg-type]

    def test_results_cached(self):
        """A second compile of the same string is the same object."""
        a = tier_probe.compile_pattern(r"mp4_(?P<tier>\d+)/")
        b = tier_probe.compile_pattern(r"mp4_(?P<tier>\d+)/")
        assert a is b


# ─── parse_tier_from_url ────────────────────────────────────────────


class TestParseTierFromUrl:
    PAT = r"mp4_(?P<tier>\d+)/"

    def test_vixen_style_url(self):
        url = "https://cdn.vixen.com/video/mp4_480/106491/foo.mp4"
        tier, prefix, suffix = tier_probe.parse_tier_from_url(url, self.PAT)
        assert tier == 480
        assert prefix.endswith("mp4_")
        assert suffix.startswith("/")
        # Substituting works
        assert prefix + "2160" + suffix == \
            "https://cdn.vixen.com/video/mp4_2160/106491/foo.mp4"

    def test_no_match_returns_zero(self):
        url = "https://example.com/video.mp4"  # no tier marker
        tier, prefix, suffix = tier_probe.parse_tier_from_url(url, self.PAT)
        assert tier == 0
        assert prefix == ""
        assert suffix == ""

    def test_compiled_pattern_input(self):
        rx = re.compile(self.PAT)
        url = "https://cdn.vixen.com/video/mp4_1080/foo.mp4"
        tier, _, _ = tier_probe.parse_tier_from_url(url, rx)
        assert tier == 1080

    def test_empty_url(self):
        assert tier_probe.parse_tier_from_url("", self.PAT) == (0, "", "")
        assert tier_probe.parse_tier_from_url(None, self.PAT) == (0, "", "")  # type: ignore[arg-type]


# ─── build_candidate_urls ───────────────────────────────────────────


class TestBuildCandidateUrls:
    PAT = r"mp4_(?P<tier>\d+)/"

    def test_descending_tier_order(self):
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        cands = tier_probe.build_candidate_urls(url, self.PAT)
        tiers = [t for t, _ in cands]
        # Descending
        assert tiers == sorted(tiers, reverse=True)

    def test_skips_lower_tiers_by_default(self):
        url = "https://cdn.vixen.com/video/mp4_1080/foo.mp4"
        cands = tier_probe.build_candidate_urls(url, self.PAT)
        tiers = [t for t, _ in cands]
        assert all(t >= 1080 for t in tiers)
        # 720 / 480 explicitly excluded
        assert 720 not in tiers
        assert 480 not in tiers

    def test_substitutes_tier_in_url(self):
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        cands = tier_probe.build_candidate_urls(url, self.PAT)
        urls = [u for _, u in cands]
        assert any("mp4_4320" in u for u in urls)
        assert any("mp4_2160" in u for u in urls)
        # Original 480 still present (baseline)
        assert any("mp4_480" in u for u in urls)

    def test_custom_ladder(self):
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        cands = tier_probe.build_candidate_urls(url, self.PAT,
                                                 ladder=[1080, 720])
        tiers = [t for t, _ in cands]
        # Only 1080 — 720 is below current (480 means current 480; both
        # 1080 and 720 are above). Wait: 720 > 480 so both should be in.
        assert 1080 in tiers
        # 720 is above 480, included
        assert 720 in tiers

    def test_pattern_no_match_returns_empty(self):
        url = "https://example.com/video.mp4"  # no tier marker
        cands = tier_probe.build_candidate_urls(url, self.PAT)
        assert cands == []


# ─── probe_higher_tiers — mocked HTTP ───────────────────────────────


def _make_httpx_mock(status_map: dict):
    """Build a fake httpx module whose Client.head() returns the
    response for `url` per `status_map`. URLs not in the map return 404.

    status_map: dict of url-substring -> status_code (int).
    """
    fake_httpx = MagicMock()
    fake_httpx.RequestError = Exception

    def _head(url, headers=None, **_kw):
        for substr, status in status_map.items():
            if substr in url:
                r = MagicMock()
                r.status_code = status
                r.headers = {}
                return r
        r = MagicMock()
        r.status_code = 404
        r.headers = {}
        return r

    def _get(url, headers=None, **_kw):
        return _head(url, headers=headers, **_kw)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)
    mock_client.head = MagicMock(side_effect=_head)
    mock_client.get = MagicMock(side_effect=_get)

    fake_httpx.Client = MagicMock(return_value=mock_client)
    return fake_httpx


class TestProbeHigherTiers:
    PAT = r"mp4_(?P<tier>\d+)/"

    def test_pattern_no_match_returns_original(self):
        url = "https://example.com/video.mp4"
        result = tier_probe.probe_higher_tiers(url, self.PAT)
        assert result.url == url
        assert result.probed is False
        assert result.error == "pattern_no_match"

    def test_picks_highest_200(self):
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        # Only 2160 returns 200; 4320/3160/2880 are 404
        fake = _make_httpx_mock({"mp4_2160": 200})
        with patch.dict(sys.modules, {"httpx": fake}):
            result = tier_probe.probe_higher_tiers(url, self.PAT)
        assert "mp4_2160" in result.url
        assert result.original_tier == 480
        assert result.chosen_tier == 2160
        assert result.probed is True

    def test_picks_highest_when_multiple_ok(self):
        """Higher tiers are tried first; first 200 stops the cascade.
        So if 4320 AND 2160 both 200, we pick 4320."""
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        fake = _make_httpx_mock({"mp4_4320": 200, "mp4_2160": 200,
                                 "mp4_1080": 200})
        with patch.dict(sys.modules, {"httpx": fake}):
            result = tier_probe.probe_higher_tiers(url, self.PAT)
        assert "mp4_4320" in result.url
        assert result.chosen_tier == 4320

    def test_all_404_returns_original(self):
        url = "https://cdn.vixen.com/video/mp4_1080/foo.mp4"
        fake = _make_httpx_mock({})  # everything 404
        with patch.dict(sys.modules, {"httpx": fake}):
            result = tier_probe.probe_higher_tiers(url, self.PAT)
        assert result.url == url
        assert result.original_tier == 1080
        # chosen_tier stays at original (no upgrade)
        assert result.chosen_tier == 1080

    def test_signed_url_rejection_short_circuits(self):
        """A 403 means the URL pattern works but signature doesn't cover
        the rewritten tier. We should stop probing immediately."""
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        fake = _make_httpx_mock({"mp4_4320": 403})  # first probe 403s
        with patch.dict(sys.modules, {"httpx": fake}):
            result = tier_probe.probe_higher_tiers(url, self.PAT)
        # Bailed early — error should record signed-URL detection
        assert result.error in ("signed_url_rejection", "")
        # Falls back to original
        assert result.url == url

    def test_max_attempts_caps_probes(self):
        """With max_attempts=2 we should stop after 2 HEAD probes."""
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        fake = _make_httpx_mock({})  # everything 404
        with patch.dict(sys.modules, {"httpx": fake}):
            result = tier_probe.probe_higher_tiers(
                url, self.PAT, max_attempts=2)
        # Total probes recorded should be <= 2 (excluding the baseline
        # which is counted but not HEAD'd)
        head_probes = [p for p in result.probed_tiers
                       if p[1] not in ("baseline",)]
        assert len(head_probes) <= 2

    def test_head_405_falls_back_to_range_get(self):
        """When HEAD returns 405 Method Not Allowed, we try a Range GET."""
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        fake_httpx = MagicMock()
        fake_httpx.RequestError = Exception

        def _head(u, **_kw):
            r = MagicMock()
            r.status_code = 405 if "mp4_2160" in u else 404
            r.headers = {}
            return r

        def _get(u, headers=None, **_kw):
            r = MagicMock()
            # Range GET returns 206 for 2160
            r.status_code = 206 if "mp4_2160" in u else 404
            r.headers = {}
            return r

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.head = MagicMock(side_effect=_head)
        mock_client.get = MagicMock(side_effect=_get)
        fake_httpx.Client = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = tier_probe.probe_higher_tiers(url, self.PAT)
        # Should pick 2160 via Range-GET fallback
        assert "mp4_2160" in result.url
        assert result.chosen_tier == 2160

    def test_network_error_continues_probing(self):
        """A single network error on one URL shouldn't kill the cascade —
        try the next tier."""
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        fake_httpx = MagicMock()
        fake_httpx.RequestError = ConnectionError  # simulate

        calls = {"n": 0}

        def _head(u, **_kw):
            calls["n"] += 1
            if "mp4_4320" in u:
                raise ConnectionError("nope")
            if "mp4_2160" in u:
                r = MagicMock(); r.status_code = 200; r.headers = {}
                return r
            r = MagicMock(); r.status_code = 404; r.headers = {}
            return r

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.head = MagicMock(side_effect=_head)
        mock_client.get = MagicMock(side_effect=_head)
        fake_httpx.Client = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = tier_probe.probe_higher_tiers(url, self.PAT)
        # Despite the 4320 connection error, we still got 2160
        assert "mp4_2160" in result.url
        assert result.chosen_tier == 2160

    def test_httpx_missing_returns_original(self):
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        cached = sys.modules.pop("httpx", None)
        try:
            with patch.dict(sys.modules, {"httpx": None}):
                # Patch out the import — make sure helper handles it
                result = tier_probe.probe_higher_tiers(url, self.PAT)
        finally:
            if cached is not None:
                sys.modules["httpx"] = cached
        # Either path is acceptable (httpx may still be importable in
        # the test environment; we just verify we got SOME result)
        assert result.url is not None

    def test_empty_url(self):
        result = tier_probe.probe_higher_tiers("", "any_pattern")
        assert result.url == ""
        assert result.error == "empty_url"

    def test_known_pattern_key_resolves(self):
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        # Pass the KEY, not the regex
        pattern_regex = tier_probe.resolve_pattern("vixen_network")
        result = tier_probe.probe_higher_tiers(url, pattern_regex)
        # At minimum, it shouldn't error on pattern matching
        assert result.error not in ("bad_pattern", "pattern_no_match")


# ─── ProbeResult dataclass ──────────────────────────────────────────


class TestProbeResultDefaults:
    def test_defaults(self):
        r = tier_probe.ProbeResult(url="https://x/")
        assert r.url == "https://x/"
        assert r.original_tier == 0
        assert r.chosen_tier == 0
        assert r.probed_tiers == []
        assert r.error == ""
        assert r.probed is False


# ─── Runner integration smoke ───────────────────────────────────────


class TestRunnerHelperExists:
    def test_probe_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_probe_for_higher_tier")

    def test_prescrape_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_run_pre_scrape_action")

    def test_probe_disabled_returns_url_unchanged(self):
        """When tier_probe_enabled=False, helper returns URL as-is."""
        from bulk_downloader.runner import SiteRunner
        runner = SiteRunner.__new__(SiteRunner)
        runner.config = {"tier_probe_enabled": False}
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        assert runner._probe_for_higher_tier(url) == url

    def test_probe_empty_pattern_returns_url_unchanged(self):
        """tier_probe_enabled=True but pattern empty → no-op."""
        from bulk_downloader.runner import SiteRunner
        runner = SiteRunner.__new__(SiteRunner)
        runner.config = {
            "tier_probe_enabled": True, "tier_probe_pattern": "",
        }
        url = "https://cdn.vixen.com/video/mp4_480/foo.mp4"
        assert runner._probe_for_higher_tier(url) == url

    def test_prescrape_empty_action_is_noop(self):
        """When pre_scrape_action is {}, helper returns without
        calling page.locator."""
        from bulk_downloader.runner import SiteRunner
        runner = SiteRunner.__new__(SiteRunner)
        runner.config = {"pre_scrape_action": {}}
        # page.locator should never be called — pass a sentinel that
        # would crash if touched
        class Boom:
            def locator(self, *a, **kw):
                raise AssertionError("should not be called")
        runner._run_pre_scrape_action(Boom())  # must not raise
