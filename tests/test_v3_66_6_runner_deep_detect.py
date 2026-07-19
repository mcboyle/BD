"""v3.66.6 — Backlog #7 wiring tests.

Verifies SiteRunner._try_deep_detect_fallback and
SiteRunner._persist_deep_detect_selectors behave as designed without
having to spin up a real browser. We mock the Playwright page and the
deep_detect module so the test is fully offline-deterministic.

Implementation note on mocking deep_detect:

  The helper does `from . import deep_detect as _dd`. When the real
  bulk_downloader.deep_detect has been imported elsewhere (which it
  has — runner.py's tests, the orchestrator tests, etc.), the parent
  `bulk_downloader` package gets `.deep_detect` bound as an
  ATTRIBUTE. `from . import X` reads that attribute, NOT sys.modules,
  so patching sys.modules alone leaves the resolution unaffected. We
  patch the package attribute instead: `patch("bulk_downloader.deep_detect", ...)`.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


def _build_runner():
    """Construct a SiteRunner with minimal config. The class wants a
    site_id and a config dict; the SiteRunner __init__ does a lot of
    setup (queue, threads), so we sidestep it by allocating without
    __init__ and stamping the attributes we touch."""
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r.site_id = "test-site"
    r.config = {}
    return r


class _FakePage:
    """Minimal Playwright-page stand-in: just exposes .content() and
    .url, which is all _try_deep_detect_fallback touches before
    handing off to find_best_download."""
    def __init__(self, html, url="https://example.test/p/1"):
        self._html = html
        self.url = url
    def content(self):
        return self._html


def _fake_dd_module(deep_detect_fn):
    """Build a stand-in for the bulk_downloader.deep_detect module.
    A MagicMock works but is noisier; this is just clearer."""
    m = MagicMock()
    m.deep_detect = deep_detect_fn
    return m


class TestDeepDetectFallbackGating(unittest.TestCase):
    """The fallback is OPT-IN. Disabled by default; either
    `deep_detect_fallback=True` or `template_auto_detect_mode='deep'`
    turns it on."""

    def test_disabled_by_default(self):
        r = _build_runner()
        page = _FakePage("<html><body>nothing</body></html>")
        result = r._try_deep_detect_fallback(page, "https://x.test/", {})
        self.assertIsNone(result)

    def test_enabled_via_deep_detect_fallback_flag(self):
        r = _build_runner()
        r.config["deep_detect_fallback"] = True
        # Returns None because the HTML is empty / sub-200-chars,
        # not because of the gate.
        page = _FakePage("<html></html>")
        result = r._try_deep_detect_fallback(page, "https://x.test/", {})
        self.assertIsNone(result)

    def test_enabled_via_deep_mode(self):
        r = _build_runner()
        r.config["template_auto_detect_mode"] = "deep"
        page = _FakePage("<html></html>")
        result = r._try_deep_detect_fallback(page, "https://x.test/", {})
        self.assertIsNone(result)


class TestDeepDetectFallbackPath(unittest.TestCase):
    """Exercise the full path with a mocked deep_detect module."""

    def _make_runner(self):
        runner = _build_runner()
        runner.config["deep_detect_fallback"] = True
        # v3.66.12: the v3.66.6 tests target the offline-analyzer →
        # learned-selector path. Pin to offline mode here so they
        # keep doing that; the new live-mode path has its own tests
        # in test_v3_66_12_phase1_p0_runner_integration.py.
        runner.config["runner_use_live_dd"] = False
        return runner

    @property
    def long_html(self):
        return "<html><body>" + ("x" * 500) + "</body></html>"

    def test_returns_none_when_deep_detect_finds_nothing(self):
        runner = self._make_runner()
        page = _FakePage(self.long_html)
        fake = _fake_dd_module(lambda html, base_url="", **_kw: {
            "download_candidates": [],
            "blockers": {},
        })
        with patch("bulk_downloader.deep_detect", fake):
            result = runner._try_deep_detect_fallback(
                page, "https://x.test/", {})
        self.assertIsNone(result)

    def test_returns_none_when_candidates_lack_click_selector(self):
        runner = self._make_runner()
        page = _FakePage(self.long_html)
        fake = _fake_dd_module(lambda html, base_url="", **_kw: {
            "download_candidates": [
                {"source_type": "hls_variant",
                 "url": "https://x/m.m3u8", "score": 100},
                {"source_type": "state_url",
                 "url": "https://x/a.mp4", "score": 80},
            ],
            "blockers": {},
        })
        with patch("bulk_downloader.deep_detect", fake):
            result = runner._try_deep_detect_fallback(
                page, "https://x.test/", {})
        self.assertIsNone(result)

    def test_retries_find_best_download_with_new_selectors(self):
        runner = self._make_runner()
        page = _FakePage(self.long_html)
        cand = {
            "source_type": "resolution_download_card",
            "url": "https://x.test/dl/1080.mp4",
            "click_selector": "div.dl-card[data-q='1080p'] a",
            "quality_label": "1080p",
            "score": 145,
        }
        fake = _fake_dd_module(lambda html, base_url="", **_kw: {
            "download_candidates": [cand],
            "blockers": {},
        })
        fake_fbd = MagicMock(return_value={
            "locator": MagicMock(),
            "text": "1080p",
            "score": 145,
            "size": 0,
            "_via_learned": True,
        })
        with patch("bulk_downloader.deep_detect", fake), \
             patch("bulk_downloader.runner_extractors.find_best_download", fake_fbd):
            result = runner._try_deep_detect_fallback(
                page, "https://x.test/", {})
        self.assertIsNotNone(result)
        self.assertTrue(result["_via_deep_detect"])
        call_kwargs = fake_fbd.call_args.kwargs
        learned = call_kwargs.get("learned") or fake_fbd.call_args.args[-1]
        self.assertIn("div.dl-card[data-q='1080p'] a",
                      learned["row_selectors"])

    def test_returns_none_when_retry_misses(self):
        runner = self._make_runner()
        page = _FakePage(self.long_html)
        cand = {
            "source_type": "resolution_download_card",
            "url": "https://x.test/dl/1080.mp4",
            "click_selector": "div.gone",
            "quality_label": "1080p",
            "score": 145,
        }
        fake = _fake_dd_module(lambda html, base_url="", **_kw: {
            "download_candidates": [cand],
            "blockers": {},
        })
        fake_fbd = MagicMock(return_value=None)
        with patch("bulk_downloader.deep_detect", fake), \
             patch("bulk_downloader.runner_extractors.find_best_download", fake_fbd):
            result = runner._try_deep_detect_fallback(
                page, "https://x.test/", {})
        self.assertIsNone(result)

    def test_swallows_deep_detect_exceptions(self):
        # If the analyzer itself raises, the helper logs and returns
        # None — runner conservatism. Must NOT propagate up.
        runner = self._make_runner()
        page = _FakePage(self.long_html)
        def boom(html, base_url=""):
            raise RuntimeError("synthetic deep_detect failure")
        fake = _fake_dd_module(boom)
        with patch("bulk_downloader.deep_detect", fake):
            result = runner._try_deep_detect_fallback(
                page, "https://x.test/", {})
        self.assertIsNone(result)

    def test_swallows_content_exceptions(self):
        # Page.content() raising must NOT propagate.
        class BoomPage:
            url = "https://x.test/"
            def content(self):
                raise RuntimeError("playwright went away")
        runner = _build_runner()
        runner.config["deep_detect_fallback"] = True
        result = runner._try_deep_detect_fallback(
            BoomPage(), "https://x.test/", {})
        self.assertIsNone(result)


class TestPersistDeepDetectSelectors(unittest.TestCase):
    """The persistence helper merges discovered selectors into the
    learned-config dict idempotently and keeps an audit log."""

    def _setup(self):
        runner = _build_runner()
        cand = {
            "source_type": "resolution_download_card",
            "url": "https://x.test/dl/1080.mp4",
            "click_selector": "div.card a.dl",
            "quality_label": "1080p",
            "score": 145,
        }
        return runner, cand

    def test_appends_new_selectors(self):
        runner, cand = self._setup()
        runner._persist_deep_detect_selectors(
            ["a.new", "b.new"], "href", cand)
        row_sels = runner.config["learned"]["download"]["row_selectors"]
        self.assertEqual(row_sels, ["a.new", "b.new"])

    def test_idempotent_on_repeat(self):
        runner, cand = self._setup()
        for _ in range(3):
            runner._persist_deep_detect_selectors(
                ["a.new"], "href", cand)
        row_sels = runner.config["learned"]["download"]["row_selectors"]
        self.assertEqual(row_sels, ["a.new"])

    def test_preserves_existing_url_attribute(self):
        runner, cand = self._setup()
        runner.config = {
            "learned": {"download": {"url_attribute": "data-original-href"}}
        }
        runner._persist_deep_detect_selectors(
            ["a.new"], "href", cand)
        self.assertEqual(
            runner.config["learned"]["download"]["url_attribute"],
            "data-original-href")

    def test_records_audit_log_entry(self):
        runner, cand = self._setup()
        runner._persist_deep_detect_selectors(
            ["a.new"], "href", cand)
        log = runner.config["learned"]["download"]["deep_detect_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["selectors_added"], ["a.new"])
        self.assertEqual(log[0]["quality_label"], "1080p")
        self.assertEqual(log[0]["score"], 145)

    def test_audit_log_caps_at_20(self):
        runner, cand = self._setup()
        for i in range(25):
            runner._persist_deep_detect_selectors(
                [f"sel{i}"], "href", cand)
        log = runner.config["learned"]["download"]["deep_detect_log"]
        self.assertEqual(len(log), 20)
        self.assertEqual(log[-1]["selectors_added"], ["sel24"])


if __name__ == "__main__":
    unittest.main()
