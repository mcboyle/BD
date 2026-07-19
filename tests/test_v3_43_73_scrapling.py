"""v3.43.73: tests for Scrapling adapter module.

Covers (without requiring scrapling to be installed):
  1. Availability functions return bools
  2. Fingerprint helpers: _normalize_text, _text_hash
  3. Stats: counters increment, reset works, success rate computed
  4. is_turnstile_page detection across signal types
  5. note_turnstile_detected bumps counter
  6. Fail-open: build_fingerprint / recover_selector / bypass_turnstile
     all return correct shapes when scrapling isn't installed
  7. RecoveryResult / BypassResult shape contracts
  8. _build_new_selector: id preferred, class fallback, tag last
  9. _candidate_score: tag match, text hash match, class overlap
 10. Flask routes registered
 11. Config fields registered
 12. bdctl cmd_scrapling_status exists
 13. Runner has _recover_selector method
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

from bulk_downloader import scrapling_adapter as s


# ─── Availability ──────────────────────────────────────────────────


class TestAvailability:
    def test_is_available_returns_bool(self):
        assert isinstance(s.is_available(), bool)

    def test_is_stealthy_fetcher_available_returns_bool(self):
        assert isinstance(s.is_stealthy_fetcher_available(), bool)

    def test_no_raise_on_either(self):
        try:
            s.is_available()
            s.is_stealthy_fetcher_available()
        except Exception:
            pytest.fail("availability check raised")


# ─── Text normalization + hashing ─────────────────────────────────


class TestTextHashing:
    def test_normalize_collapses_whitespace(self):
        assert s._normalize_text("  Hello   World  ") == "hello world"

    def test_normalize_lowercases(self):
        assert s._normalize_text("ABC") == "abc"

    def test_normalize_empty(self):
        assert s._normalize_text("") == ""
        assert s._normalize_text(None) == ""  # type: ignore[arg-type]

    def test_text_hash_stable(self):
        h1 = s._text_hash("Hello World")
        h2 = s._text_hash("  hello   world  ")
        assert h1 == h2  # normalized to the same string

    def test_text_hash_length(self):
        h = s._text_hash("anything")
        assert len(h) == 64  # SHA-256 hex

    def test_text_hash_empty(self):
        assert s._text_hash("") == ""
        assert s._text_hash("   ") == ""  # only whitespace → empty

    def test_text_hash_distinguishes_content(self):
        assert s._text_hash("foo") != s._text_hash("bar")


# ─── Stats ───────────────────────────────────────────────────────


class TestStats:
    def setup_method(self):
        s.reset_stats()

    def test_initial_zero(self):
        st = s.stats()
        assert st["fingerprints_built"] == 0
        assert st["recoveries_attempted"] == 0
        assert st["recovery_success_rate"] == 0.0

    def test_bump_fingerprints(self):
        s._bump("fingerprints_built")
        s._bump("fingerprints_built", by=2)
        assert s.stats()["fingerprints_built"] == 3

    def test_recovery_rate_calculation(self):
        s._bump("recoveries_attempted", by=4)
        s._bump("recoveries_succeeded", by=3)
        st = s.stats()
        assert st["recovery_success_rate"] == 0.75

    def test_turnstile_rate_calculation(self):
        s._bump("turnstile_detected", by=10)
        s._bump("turnstile_bypassed", by=8)
        st = s.stats()
        assert st["turnstile_success_rate"] == 0.8

    def test_reset(self):
        s._bump("fingerprints_built", by=5)
        s.reset_stats()
        assert s.stats()["fingerprints_built"] == 0


# ─── Turnstile detection ─────────────────────────────────────────


class TestTurnstileDetection:
    def setup_method(self):
        s.reset_stats()

    def test_detects_cf_turnstile_div(self):
        html = '<html><body><div class="cf-turnstile" data-sitekey="x"></div></body></html>'
        assert s.is_turnstile_page(html) is True

    def test_detects_challenges_iframe(self):
        html = '<iframe src="https://challenges.cloudflare.com/turnstile/v0/abc"></iframe>'
        assert s.is_turnstile_page(html) is True

    def test_detects_clearance_required(self):
        html = '<body class="cf-clearance-required">verify you are human</body>'
        assert s.is_turnstile_page(html) is True

    def test_normal_page_negative(self):
        html = '<html><body>Welcome to my website</body></html>'
        assert s.is_turnstile_page(html) is False

    def test_empty_negative(self):
        assert s.is_turnstile_page("") is False
        assert s.is_turnstile_page(None) is False  # type: ignore[arg-type]

    def test_case_insensitive(self):
        """Case-insensitive matching — CF varies casing."""
        html = '<DIV CLASS="CF-Turnstile"></DIV>'
        assert s.is_turnstile_page(html) is True

    def test_is_turnstile_page_does_NOT_bump_counter(self):
        """v3.43.73 design: detection is pure; counter bumps only at
        dispatch points so the stat reflects ACTIONS not CHECKS."""
        s.reset_stats()
        html = '<div class="cf-turnstile"></div>'
        for _ in range(10):
            s.is_turnstile_page(html)
        assert s.stats()["turnstile_detected"] == 0

    def test_note_turnstile_detected_bumps(self):
        s.reset_stats()
        s.note_turnstile_detected()
        s.note_turnstile_detected()
        assert s.stats()["turnstile_detected"] == 2


# ─── Fail-open: scrapling not installed ──────────────────────────


class TestFailOpenScrapingMissing:
    """All entry points must return correct shapes even when scrapling
    isn't importable."""

    def test_build_fingerprint_returns_none(self):
        with patch.object(s, "is_available", return_value=False):
            fp = s.build_fingerprint("<a>x</a>", "a")
            assert fp is None

    def test_build_fingerprint_empty_inputs(self):
        fp = s.build_fingerprint("", "a")
        assert fp is None
        fp = s.build_fingerprint("<a>x</a>", "")
        assert fp is None

    def test_recover_selector_returns_failed_result(self):
        with patch.object(s, "is_available", return_value=False):
            r = s.recover_selector("<html></html>", {"tag": "a"})
            assert r.ok is False
            assert r.error == "scrapling_not_installed"

    def test_recover_selector_invalid_inputs(self):
        # Empty html
        with patch.object(s, "is_available", return_value=True):
            r = s.recover_selector("", {"tag": "a"})
            assert r.ok is False
            assert r.error == "invalid_inputs"
            # Empty fingerprint
            r = s.recover_selector("<a></a>", None)
            assert r.ok is False
            # Fingerprint missing tag
            r = s.recover_selector("<a></a>", {"text_hash": "x"})
            assert r.ok is False
            assert r.error == "fingerprint_missing_tag"

    def test_bypass_turnstile_returns_failed_result(self):
        with patch.object(s, "is_stealthy_fetcher_available", return_value=False):
            r = s.bypass_turnstile("https://example.com")
            assert r.ok is False
            assert r.error == "stealthy_fetcher_unavailable"

    def test_bypass_turnstile_empty_url(self):
        with patch.object(s, "is_stealthy_fetcher_available", return_value=True):
            r = s.bypass_turnstile("")
            assert r.ok is False
            assert r.error == "empty_url"


# ─── Result type contracts ───────────────────────────────────────


class TestResultShapes:
    def test_recovery_result_shape(self):
        r = s.RecoveryResult(ok=True, selector="div", score=0.9)
        assert r.ok is True
        assert r.selector == "div"
        assert r.score == 0.9
        assert r.candidates_considered == 0
        assert r.error == ""

    def test_bypass_result_shape(self):
        r = s.BypassResult(ok=True, cookies=[{"name": "cf_clearance", "value": "x"}])
        assert r.ok is True
        assert len(r.cookies) == 1
        assert r.html == ""
        assert r.elapsed_s == 0.0

    def test_scrapling_stats_dataclass(self):
        st = s.ScraplingStats()
        assert st.fingerprints_built == 0
        assert st.turnstile_detected == 0


# ─── Candidate scoring ───────────────────────────────────────────


class TestCandidateScore:
    def _mock_el(self, tag="a", text="", attrib=None):
        m = MagicMock()
        m.tag = tag
        m.text = text
        m.attrib = attrib or {}
        return m

    def test_tag_match_scores(self):
        el = self._mock_el(tag="a", text="")
        fp = {"tag": "a", "text_hash": "", "attrs": {}, "ancestor_tags": []}
        # Just tag match gets 0.15
        score = s._candidate_score(el, fp)
        assert score >= 0.15

    def test_text_hash_match_scores_high(self):
        el = self._mock_el(tag="a", text="Hello World")
        fp = {
            "tag": "a",
            "text_hash": s._text_hash("Hello World"),
            "attrs": {}, "ancestor_tags": [],
        }
        score = s._candidate_score(el, fp)
        # tag (0.15) + text_hash (0.40) = 0.55
        assert score >= 0.55

    def test_text_preview_partial_credit(self):
        """If hash doesn't match exactly but preview is contained,
        partial credit."""
        el = self._mock_el(tag="a", text="prefix Hello World suffix")
        fp = {
            "tag": "a",
            "text_hash": "0" * 64,  # won't match anything
            "text_preview": "hello world",
            "attrs": {}, "ancestor_tags": [],
        }
        score = s._candidate_score(el, fp)
        # tag (0.15) + text_preview (0.20) = 0.35
        assert 0.30 <= score <= 0.40

    def test_class_overlap_scores(self):
        el = self._mock_el(tag="a", attrib={"class": "btn btn-primary"})
        fp = {
            "tag": "a", "text_hash": "",
            "attrs": {"class": ["btn", "btn-primary"]},
            "ancestor_tags": [],
        }
        score = s._candidate_score(el, fp)
        # tag (0.15) + class_set full overlap (0.15) = 0.30
        assert score >= 0.25

    def test_no_match_zero(self):
        el = self._mock_el(tag="span", text="completely different")
        fp = {
            "tag": "a", "text_hash": s._text_hash("Hello"),
            "attrs": {}, "ancestor_tags": [],
        }
        score = s._candidate_score(el, fp)
        # Tag doesn't match, text doesn't match → very low score
        assert score < 0.10


# ─── _build_new_selector ─────────────────────────────────────────


class TestBuildNewSelector:
    def _el(self, attrib):
        m = MagicMock()
        m.attrib = attrib
        return m

    def test_prefers_id(self):
        el = self._el({"id": "myid", "class": "x y"})
        assert s._build_new_selector(el, "a") == "#myid"

    def test_falls_back_to_class(self):
        el = self._el({"class": "btn btn-primary"})
        # First class wins
        assert s._build_new_selector(el, "a") == "a.btn"

    def test_falls_back_to_tag(self):
        el = self._el({})
        assert s._build_new_selector(el, "a") == "a"

    def test_skips_hover_classes(self):
        el = self._el({"class": "hover:bg-blue myclass"})
        assert s._build_new_selector(el, "div") == "div.myclass"


# ─── Flask routes ────────────────────────────────────────────────


class TestFlaskRoutes:
    def test_status_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/scrapling/status" in rules

    def test_fingerprint_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/scrapling/fingerprint" in rules

    def test_recover_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/scrapling/recover" in rules

    def test_turnstile_check_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/scrapling/turnstile_check" in rules


# ─── Config registration ────────────────────────────────────────


class TestConfigFields:
    def test_use_scrapling_recovery_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_scrapling_recovery" in CFG_FIELDS
        assert DEFAULTS.get("use_scrapling_recovery") is False

    def test_use_scrapling_turnstile_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_scrapling_turnstile" in CFG_FIELDS
        assert DEFAULTS.get("use_scrapling_turnstile") is False

    def test_min_score_default(self):
        from bulk_downloader.app_kernel import DEFAULTS
        assert DEFAULTS.get("scrapling_recovery_min_score") == 0.6


# ─── Runner integration ─────────────────────────────────────────


class TestRunnerHooks:
    def test_recover_selector_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_recover_selector")

    def test_scrapling_imports_in_runner(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_SCRAPLING_AVAILABLE")


# ─── bdctl ──────────────────────────────────────────────────────


class TestBdctl:
    def test_cmd_scrapling_status_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_scrapling_status")
        assert callable(bdctl.cmd_scrapling_status)


# ─── Module __all__ ──────────────────────────────────────────────


class TestPublicAPI:
    def test_all_listed(self):
        for name in ("is_available", "build_fingerprint",
                     "recover_selector", "is_turnstile_page",
                     "note_turnstile_detected", "bypass_turnstile",
                     "stats", "RecoveryResult", "BypassResult"):
            assert name in s.__all__, f"{name} missing from __all__"

    def test_all_names_exist(self):
        for name in s.__all__:
            assert hasattr(s, name), f"__all__ lists {name} but it doesn't exist"
