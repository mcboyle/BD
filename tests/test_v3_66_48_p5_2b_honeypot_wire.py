"""P5-2b self-feeding wire — resolve->runner->db_log honeypot-score hop.

Background
----------
R-P5-2 (v3.66.27) computes a honeypot score on resolved provider-embed
candidates; v3.66.36 added ``history.honeypot_score`` + the
``honeypot_threshold`` learner that reads it. The wire that carries the
resolve-time score onto the completion-time history row was deliberately
left inert (the §P5-2b defer-gate). This lands it.

The score only ever exists on candidates that went through
``provider_resolve.resolve_provider_embed`` (called *inside* deep_detect),
so the ONLY path it can reach the runner is the deep_detect fallback
(``_try_deep_detect_fallback``). The runner's primary
``find_best_download`` path never carries a score, so the common case is
unchanged (``honeypot_score`` stays NULL).

These tests cover the two runner-side hops that make the wire work:
  1. ``_try_deep_detect_fallback`` copies the chosen candidate's
     ``_honeypot_score`` / ``_honeypot_reason`` onto the ``best`` dict it
     returns (and copies NOTHING when the scorer didn't flag it).
  2. ``_apply_quality_preference`` preserves those keys when a quality
     preference swaps the winning candidate (otherwise the swap would
     drop the score before db_log sees it).

The final db_log stamping (``_do_download`` passing
``honeypot_score=best.get("_honeypot_score")``) is a one-line kwarg on a
call whose persistence is already covered by the v3.66.36 db_log tests;
it is exercised indirectly here via the dict that reaches it.
"""
import sys
from unittest.mock import patch

import pytest

# Opt back into the sys.modules wipe so the package re-reads env at import
# (same rationale as the other runner-integration suites; see conftest).
pytestmark = pytest.mark.bd_module_wipe


# ── Page / context stubs (mirror test_v3_66_12) ───────────────────────


class _FakeCtx:
    def __init__(self, cookies=None):
        self._cookies = cookies or []

    def cookies(self):
        return list(self._cookies)


class _FakePage:
    """Minimal Playwright Page stand-in. _try_deep_detect_fallback reads
    .content(), .url, .context.cookies()."""

    def __init__(self, html, url="https://example.test/p/1", cookies=None):
        self._html = html
        self.url = url
        self.context = _FakeCtx(cookies=cookies)

    def content(self):
        return self._html


class _FakeLocator:
    """Quality-preference swap requires the chosen candidate to expose a
    truthy ``locator``; a bare sentinel is enough."""

    def click(self):
        pass


def _build_runner():
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r.site_id = "test-site"
    r.config = {"deep_detect_fallback": True, "runner_use_live_dd": True}
    return r


def _report_with(candidate):
    """A deep_detect_live report whose buckets.accepted holds one
    candidate."""
    return {"buckets": {"accepted": [candidate]}, "blockers": {}}


# ── 1. fallback copies the score onto `best` ──────────────────────────


class TestFallbackCarriesHoneypotScore:
    HTML = "<html><body>" + "x" * 500 + "</body></html>"

    def _run(self, candidate, returned_best):
        r = _build_runner()
        page = _FakePage(self.HTML)
        from bulk_downloader import deep_detect

        def fake_dd_live(html, **kwargs):
            return _report_with(candidate)

        with patch.object(deep_detect, "deep_detect_live", fake_dd_live), \
                patch("bulk_downloader.runner_extractors.find_best_download",
                      lambda *a, **k: returned_best):
            return r._try_deep_detect_fallback(page, "https://x.test/", {})

    def test_score_copied_when_present(self):
        cand = {
            "click_selector": "a.dl",
            "url": "https://cdn.test/v.mp4",
            "score": 720,
            "_honeypot_score": 0.73,
            "_honeypot_reason": "decoy-link-density",
        }
        best = {"score": 720, "locator": _FakeLocator()}
        out = self._run(cand, best)
        assert out is not None
        assert out.get("_honeypot_score") == 0.73
        assert out.get("_honeypot_reason") == "decoy-link-density"
        # The fallback tag is still set (we didn't clobber existing wiring).
        assert out.get("_via_deep_detect") is True

    def test_reason_optional_score_alone(self):
        cand = {
            "click_selector": "a.dl",
            "url": "https://cdn.test/v.mp4",
            "score": 1080,
            "_honeypot_score": 0.55,
        }
        best = {"score": 1080, "locator": _FakeLocator()}
        out = self._run(cand, best)
        assert out.get("_honeypot_score") == 0.55
        assert "_honeypot_reason" not in out

    def test_no_score_key_when_unflagged(self):
        # A clean candidate (scorer didn't flag it) carries no
        # _honeypot_score; the runner must NOT invent one — the history
        # column has to stay NULL for the common case.
        cand = {
            "click_selector": "a.dl",
            "url": "https://cdn.test/v.mp4",
            "score": 1080,
        }
        best = {"score": 1080, "locator": _FakeLocator()}
        out = self._run(cand, best)
        assert out is not None
        assert "_honeypot_score" not in out
        assert "_honeypot_reason" not in out

    def test_highest_scored_clickable_supplies_the_score(self):
        # Two clickable candidates; the higher-scored one wins selector
        # ordering AND supplies the honeypot score.
        lo = {"click_selector": "a.lo", "url": "https://c/l.mp4",
              "score": 480, "_honeypot_score": 0.20}
        hi = {"click_selector": "a.hi", "url": "https://c/h.mp4",
              "score": 1080, "_honeypot_score": 0.66}
        r = _build_runner()
        page = _FakePage(self.HTML)
        from bulk_downloader import deep_detect

        def fake_dd_live(html, **kwargs):
            return {"buckets": {"accepted": [lo, hi]}, "blockers": {}}

        with patch.object(deep_detect, "deep_detect_live", fake_dd_live), \
                patch("bulk_downloader.runner_extractors.find_best_download",
                      lambda *a, **k: {"score": 1080, "locator": _FakeLocator()}):
            out = r._try_deep_detect_fallback(page, "https://x.test/", {})
        assert out.get("_honeypot_score") == 0.66  # hi, not lo


# ── 2. quality-preference swap preserves the score ────────────────────


class TestQualityPrefPreservesHoneypotScore:
    def _runner(self):
        from bulk_downloader.runner import SiteRunner
        return SiteRunner.__new__(SiteRunner)

    def test_swap_preserves_score_and_reason(self):
        r = self._runner()
        # `best` is the high scorer with a honeypot score; the preference
        # asks for 720, which matches a different candidate in
        # _all_candidates. The swap must carry the score across.
        c720 = {"score": 720, "locator": _FakeLocator(), "text": "720p"}
        best = {
            "score": 1080,
            "locator": _FakeLocator(),
            "_all_candidates": [c720, {"score": 1080, "locator": _FakeLocator()}],
            "_honeypot_score": 0.61,
            "_honeypot_reason": "size-mismatch",
        }
        chosen = r._apply_quality_preference(best, "720")
        assert chosen["score"] == 720          # swap happened
        assert chosen is not best
        assert chosen.get("_honeypot_score") == 0.61
        assert chosen.get("_honeypot_reason") == "size-mismatch"

    def test_no_swap_returns_best_unchanged(self):
        r = self._runner()
        best = {
            "score": 1080,
            "locator": _FakeLocator(),
            "_all_candidates": [{"score": 1080, "locator": _FakeLocator()}],
            "_honeypot_score": 0.61,
        }
        # Preference 'best' keeps the top scorer; returns same object.
        chosen = r._apply_quality_preference(best, "best")
        assert chosen.get("_honeypot_score") == 0.61

    def test_swap_without_score_does_not_add_key(self):
        r = self._runner()
        c720 = {"score": 720, "locator": _FakeLocator(), "text": "720p"}
        best = {
            "score": 1080,
            "locator": _FakeLocator(),
            "_all_candidates": [c720],
        }
        chosen = r._apply_quality_preference(best, "720")
        assert chosen["score"] == 720
        assert "_honeypot_score" not in chosen


# ── 3. trap-detection query shape (lock the learner's contract) ───────


class TestTrapQueryContract:
    """The wire only matters because honeypot_threshold reads back rows
    written as status='done' with a real positive file_size. Lock that
    the learner's SELECT is what we think it is, so a future db_log
    refactor that changes the status string is caught here."""

    def test_threshold_reader_selects_done_tiny_scored_rows(self):
        from bulk_downloader import honeypot_threshold as ht
        captured = {}

        class _Cur:
            def execute(self, sql, params):
                captured["sql"] = sql
                captured["params"] = params
                return self

            def fetchall(self):
                return []

        ht.trap_scores_for_site("s1", conn=_Cur())
        sql = captured["sql"]
        assert "status = 'done'" in sql
        assert "honeypot_score IS NOT NULL" in sql
        assert "file_size > 0" in sql
        assert "file_size < ?" in sql
