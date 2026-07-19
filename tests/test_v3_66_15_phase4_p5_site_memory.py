"""v3.66.15 — Phase 4 P5: persistent learning store for deep_detect.

Tests the write path (`record_deep_detect_outcome`), the read path
(`deep_detect_site_memory` + `deep_detect(site_memory=...)`), and the
runner integration (`_try_deep_detect_fallback` recording outcomes
and reading site memory).

Three test classes:

  TestRecordOutcome
    Pure-function tests of the write path. No deep_detect calls;
    feeds synthetic reports and asserts the resulting config shape.

  TestSiteMemoryBias
    End-to-end tests of the read path. Runs deep_detect twice — once
    cold, once warm with site memory — and asserts the score bias
    moved the right candidates without changing the overall shape.

  TestRunnerIntegration
    Mocks the deep_detect module and Playwright page to verify the
    runner reads + writes the learned.deep_detect block correctly.

All tests are deterministic; no network, no fixtures-on-disk
required, no clock dependency (timestamps are present in output
but tests never compare them directly).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.bd_module_wipe


# ---------------------------------------------------------------------------
# TestRecordOutcome — write-path unit tests
# ---------------------------------------------------------------------------

class TestRecordOutcome:
    def test_first_call_creates_block(self):
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        report = {
            "best_download": {
                "source_type": "hls_master",
                "url": "https://example.test/m.m3u8",
                "confidence": 0.85,
                "resolution": {"label": "1080p"},
            },
            "download_candidates": [{"url": "a"}, {"url": "b"}],
        }
        record_deep_detect_outcome(cfg, report)
        assert "learned" in cfg
        assert "deep_detect" in cfg["learned"]
        block = cfg["learned"]["deep_detect"]
        assert block["winning_source_types"] == {"hls_master": 1}
        assert block["stats"]["runs"] == 1
        assert block["stats"]["candidates_found"] == 2
        assert block["stats"]["candidates_picked"] == 1
        assert block["last_winner"]["source_type"] == "hls_master"
        assert block["last_winner"]["url"] == "https://example.test/m.m3u8"
        assert block["last_winner"]["confidence"] == 0.85
        assert block["preferred_resolution"] == "1080p"

    def test_second_call_increments_winning_source_types(self):
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        report = {"best_download": {"source_type": "hls_master",
                                    "url": "x", "confidence": 0.8}}
        record_deep_detect_outcome(cfg, report)
        record_deep_detect_outcome(cfg, report)
        assert cfg["learned"]["deep_detect"][
            "winning_source_types"]["hls_master"] == 2
        assert cfg["learned"]["deep_detect"]["stats"]["runs"] == 2

    def test_no_winner_still_counts_run(self):
        """A failed deep_detect (best_download is None) still counts
        as a run for stats — useful for tracking "this site fails
        often." Doesn't increment candidates_picked though."""
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        report = {"best_download": None, "download_candidates": []}
        record_deep_detect_outcome(cfg, report)
        block = cfg["learned"]["deep_detect"]
        assert block["stats"]["runs"] == 1
        assert block["stats"]["candidates_picked"] == 0
        assert block["winning_source_types"] == {}
        assert block["last_winner"] is None

    def test_disclaimers_tracked_in_blocker_history(self):
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        report = {
            "best_download": None,
            "disclaimers": [
                {"type": "login_form", "severity": "warn", "message": "..."},
                {"type": "drm", "severity": "error", "message": "..."},
            ],
        }
        record_deep_detect_outcome(cfg, report)
        bh = cfg["learned"]["deep_detect"]["blocker_history"]
        assert bh == {"login_form": 1, "drm": 1}

    def test_legacy_blockers_fallback(self):
        """When disclaimers (v3.66.14+) aren't present but legacy
        string-list blockers are, scan them for known tokens."""
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        report = {
            "best_download": None,
            "blockers": {"warnings": ["This page requires login to view"]},
        }
        record_deep_detect_outcome(cfg, report)
        # "login" token should be detected in the legacy warning
        assert "login" in cfg["learned"]["deep_detect"]["blocker_history"]

    def test_provider_embeds_tracked_with_last_id(self):
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        report = {
            "best_download": None,
            "provider_embeds": [
                {"provider": "vimeo", "ids": {"video_id": "12345"}},
                {"provider": "youtube",
                 "ids": {"video_id": "dQw4w9WgXcQ"}},
            ],
        }
        record_deep_detect_outcome(cfg, report)
        pes = cfg["learned"]["deep_detect"]["provider_embeds_seen"]
        assert pes["vimeo"]["count"] == 1
        assert pes["vimeo"]["last_id"] == "12345"
        assert pes["youtube"]["last_id"] == "dQw4w9WgXcQ"

    def test_provider_embeds_increment_count(self):
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        report = {
            "best_download": None,
            "provider_embeds": [
                {"provider": "vimeo", "ids": {"video_id": "1"}},
            ],
        }
        record_deep_detect_outcome(cfg, report)
        # Same provider, different video — count goes up, last_id changes
        report["provider_embeds"][0]["ids"]["video_id"] = "2"
        record_deep_detect_outcome(cfg, report)
        pes = cfg["learned"]["deep_detect"]["provider_embeds_seen"]
        assert pes["vimeo"]["count"] == 2
        assert pes["vimeo"]["last_id"] == "2"

    def test_resolution_mode_tracking(self):
        """preferred_resolution should be the mode of the history of
        winning resolution labels. Ties break toward the most-recent."""
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        # Three 1080p wins, then one 4k. Mode = 1080p.
        for _ in range(3):
            record_deep_detect_outcome(cfg, {
                "best_download": {"source_type": "hls_master", "url": "u",
                                  "confidence": 0.8,
                                  "resolution": {"label": "1080p"}},
            })
        record_deep_detect_outcome(cfg, {
            "best_download": {"source_type": "hls_master", "url": "u",
                              "confidence": 0.8,
                              "resolution": {"label": "4k"}},
        })
        assert cfg["learned"]["deep_detect"]["preferred_resolution"] == "1080p"
        # Two more 4k wins → 4k now ties with 1080p. Tie breaks to the
        # current label (4k since that's what we just incremented).
        for _ in range(2):
            record_deep_detect_outcome(cfg, {
                "best_download": {"source_type": "hls_master", "url": "u",
                                  "confidence": 0.8,
                                  "resolution": {"label": "4k"}},
            })
        # 3 × 1080p vs 3 × 4k → tied. Current-label-wins → 4k.
        assert cfg["learned"]["deep_detect"]["preferred_resolution"] == "4k"

    def test_non_dict_config_is_tolerated(self):
        from bulk_downloader.learn import record_deep_detect_outcome
        # Passing nonsense returns nonsense (no crash).
        assert record_deep_detect_outcome(None, {}) is None
        assert record_deep_detect_outcome([], {}) == []

    def test_non_dict_report_is_tolerated(self):
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {}
        record_deep_detect_outcome(cfg, None)
        # Nothing was recorded — config stays empty.
        assert cfg == {}

    def test_partial_existing_block_is_healed(self):
        """A pre-existing learned.deep_detect with missing keys (e.g.
        from a hand-edit or a forward-compat read) should be backfilled
        rather than crashing."""
        from bulk_downloader.learn import record_deep_detect_outcome
        cfg = {"learned": {"deep_detect": {"winning_source_types":
                                            {"x": 1}}}}
        record_deep_detect_outcome(cfg, {
            "best_download": {"source_type": "y", "url": "u",
                              "confidence": 0.5},
        })
        block = cfg["learned"]["deep_detect"]
        # Old key preserved, new keys added, stats initialized
        assert block["winning_source_types"]["x"] == 1
        assert block["winning_source_types"]["y"] == 1
        assert "stats" in block
        assert "blocker_history" in block
        assert "provider_embeds_seen" in block

    def test_max_source_types_cap(self):
        """winning_source_types should be capped to avoid unbounded
        memory growth."""
        from bulk_downloader.learn import (
            record_deep_detect_outcome, _DD_MAX_TOP_TYPES)
        cfg = {}
        # Record more distinct source types than the cap, each once
        for i in range(_DD_MAX_TOP_TYPES + 5):
            record_deep_detect_outcome(cfg, {
                "best_download": {"source_type": f"type_{i}",
                                  "url": "u", "confidence": 0.5},
            })
        wst = cfg["learned"]["deep_detect"]["winning_source_types"]
        assert len(wst) <= _DD_MAX_TOP_TYPES


# ---------------------------------------------------------------------------
# TestSiteMemoryBias — read-path end-to-end tests
# ---------------------------------------------------------------------------

class TestSiteMemoryBias:
    """Run deep_detect with and without site_memory, verify the bias
    applies as documented."""

    SIMPLE_HLS = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "1080p/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\n"
        "720p/index.m3u8\n"
    )

    def test_no_memory_default_score(self):
        from bulk_downloader.deep_detect import deep_detect
        out = deep_detect(self.SIMPLE_HLS,
                          base_url="https://x.test/master.m3u8")
        assert out["buckets"]["best"]["source_type"] == "hls_manifest"
        # baseline score for an HLS master variant is documented as 100
        assert out["buckets"]["best"]["score"] == 100

    def test_memory_bonus_applies(self):
        from bulk_downloader.deep_detect import deep_detect
        memory = {
            "winning_source_types": {"hls_manifest": 2},
            "preferred_resolution": None,
            "blocker_history": {},
            "provider_embeds_seen": {},
            "last_winner": None,
            "stats": {"runs": 2, "candidates_found": 4,
                      "candidates_picked": 2},
        }
        out = deep_detect(self.SIMPLE_HLS,
                          base_url="https://x.test/master.m3u8",
                          site_memory=memory)
        # +2 bonus for 2 prior wins (cap is 5)
        assert out["buckets"]["best"]["score"] == 102
        reasons = out["buckets"]["best"].get("reasons") or []
        assert any("+repeat_winner" in r for r in reasons)

    def test_memory_bonus_capped(self):
        """Even with many prior wins, the bonus is capped at +5."""
        from bulk_downloader.deep_detect import deep_detect
        memory = {
            "winning_source_types": {"hls_manifest": 100},  # huge
            "preferred_resolution": None,
            "blocker_history": {},
            "provider_embeds_seen": {},
            "last_winner": None,
            "stats": {},
        }
        out = deep_detect(self.SIMPLE_HLS,
                          base_url="https://x.test/master.m3u8",
                          site_memory=memory)
        assert out["buckets"]["best"]["score"] == 105

    def test_memory_none_is_noop(self):
        """site_memory=None is the legacy stateless behaviour."""
        from bulk_downloader.deep_detect import deep_detect
        a = deep_detect(self.SIMPLE_HLS,
                        base_url="https://x.test/master.m3u8")
        b = deep_detect(self.SIMPLE_HLS,
                        base_url="https://x.test/master.m3u8",
                        site_memory=None)
        assert a["buckets"]["best"]["score"] == b["buckets"]["best"]["score"]
        assert a["buckets"]["best"]["source_type"] == b["buckets"]["best"][
            "source_type"]

    def test_memory_does_not_affect_unrelated_source_types(self):
        """If memory has winning_source_types={vimeo_embed: 5}, an HLS
        master page should see no bias since hls_manifest isn't in the
        memory."""
        from bulk_downloader.deep_detect import deep_detect
        memory = {
            "winning_source_types": {"vimeo_embed": 5},
            "preferred_resolution": None,
            "blocker_history": {},
            "provider_embeds_seen": {},
            "last_winner": None,
            "stats": {},
        }
        out = deep_detect(self.SIMPLE_HLS,
                          base_url="https://x.test/master.m3u8",
                          site_memory=memory)
        # No bias — score stays at baseline
        assert out["buckets"]["best"]["score"] == 100

    def test_preferred_resolution_defaulted_from_memory(self):
        """When caller didn't pass prefer_resolution (i.e. it's
        'highest'), memory's preferred_resolution should be threaded
        in and bias the ranking.

        Note: the HLS-as-input fast path does NOT run the preference-
        bias block (which lives on the HTML page path only). So we
        use an HTML page with anchor-resolution-card variants to
        exercise the bias path.
        """
        from bulk_downloader.deep_detect import deep_detect
        html = ("<html><body>"
                "<a href=\"https://x.test/v_720p.mp4\">720p</a>"
                "<a href=\"https://x.test/v_1080p.mp4\">1080p</a>"
                "</body></html>")
        # Cold (no memory): defaults to "highest" → 1080p wins.
        cold = deep_detect(html, base_url="https://x.test/")
        cold_h = (cold["buckets"]["best"].get("resolution") or {}).get("rank")
        # Warm (memory.preferred_resolution="720p"): caller's
        # default "highest" → "720p" via memory, bias makes 720p win.
        memory = {
            "winning_source_types": {},
            "preferred_resolution": "720p",
            "blocker_history": {},
            "provider_embeds_seen": {},
            "last_winner": None,
            "stats": {},
        }
        warm = deep_detect(html, base_url="https://x.test/",
                            site_memory=memory)
        warm_h = (warm["buckets"]["best"].get("resolution") or {}).get("rank")
        assert cold_h == 1080, (
            f"cold expected 1080p rank, got {cold_h}")
        assert warm_h == 720, (
            f"warm expected 720p (rank=720), got {warm_h}")

    def test_caller_prefer_resolution_overrides_memory(self):
        """If the caller explicitly passes prefer_resolution, the
        memory's preference is ignored — explicit caller intent wins."""
        from bulk_downloader.deep_detect import deep_detect
        html = ("<html><body>"
                "<a href=\"https://x.test/v_720p.mp4\">720p</a>"
                "<a href=\"https://x.test/v_1080p.mp4\">1080p</a>"
                "</body></html>")
        memory = {
            "winning_source_types": {},
            "preferred_resolution": "720p",
            "blocker_history": {},
            "provider_embeds_seen": {},
            "last_winner": None,
            "stats": {},
        }
        out = deep_detect(html, base_url="https://x.test/",
                          prefer_resolution="1080p",
                          site_memory=memory)
        h = (out["buckets"]["best"].get("resolution") or {}).get("rank")
        assert h == 1080, f"expected 1080p (rank=1080), got {h}"


# ---------------------------------------------------------------------------
# TestSiteMemoryAccessor — deep_detect_site_memory
# ---------------------------------------------------------------------------

class TestSiteMemoryAccessor:
    def test_empty_config_returns_empty_block(self):
        from bulk_downloader.learn import deep_detect_site_memory
        mem = deep_detect_site_memory({})
        assert "winning_source_types" in mem
        assert mem["winning_source_types"] == {}
        assert mem["preferred_resolution"] is None
        assert mem["stats"]["runs"] == 0

    def test_recorded_config_round_trips(self):
        from bulk_downloader.learn import (
            record_deep_detect_outcome, deep_detect_site_memory)
        cfg = {}
        record_deep_detect_outcome(cfg, {
            "best_download": {"source_type": "hls_master", "url": "u",
                              "confidence": 0.9},
        })
        mem = deep_detect_site_memory(cfg)
        assert mem["winning_source_types"] == {"hls_master": 1}

    def test_returned_dict_is_shallow_copy(self):
        """Mutating the returned dict should NOT mutate the config —
        callers (deep_detect) should be able to treat it as read-only."""
        from bulk_downloader.learn import (
            record_deep_detect_outcome, deep_detect_site_memory)
        cfg = {}
        record_deep_detect_outcome(cfg, {
            "best_download": {"source_type": "x", "url": "u",
                              "confidence": 0.5},
        })
        mem = deep_detect_site_memory(cfg)
        mem["winning_source_types"] = {"trash": 999}
        # Original config unchanged at the top level
        assert cfg["learned"]["deep_detect"][
            "winning_source_types"] == {"x": 1}

    def test_non_dict_config_returns_empty_block(self):
        from bulk_downloader.learn import deep_detect_site_memory
        assert deep_detect_site_memory(None)["stats"]["runs"] == 0
        assert deep_detect_site_memory("string")["stats"]["runs"] == 0

    def test_malformed_learned_block_returns_empty(self):
        from bulk_downloader.learn import deep_detect_site_memory
        cfg = {"learned": "not a dict"}
        assert deep_detect_site_memory(cfg)["stats"]["runs"] == 0
        cfg = {"learned": {"deep_detect": "not a dict"}}
        assert deep_detect_site_memory(cfg)["stats"]["runs"] == 0


# ---------------------------------------------------------------------------
# TestRunnerIntegration — verify runner reads + writes site memory
# ---------------------------------------------------------------------------

class TestRunnerIntegration:
    """The runner's _try_deep_detect_fallback should pass site_memory
    to deep_detect_live AND call record_deep_detect_outcome on the
    result. We mock the deep_detect module to verify both happen.

    Note on the bd_module_wipe interaction: the file-level
    `pytestmark = pytest.mark.bd_module_wipe` clears `bulk_downloader.*`
    from sys.modules before each test. The runner's
    `_try_deep_detect_fallback` then does `from . import deep_detect
    as _dd`, which reads the `deep_detect` ATTRIBUTE off the
    `bulk_downloader` package — but `patch("bulk_downloader.deep_detect",
    ...)` requires that attribute to exist before patching. So each
    test below force-imports `bulk_downloader.deep_detect` first to
    set the attribute, then patches it.
    """

    def _build_runner(self, config=None):
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner.__new__(SiteRunner)
        r.site_id = "test-site"
        r.config = config or {"deep_detect_fallback": True,
                              "runner_use_live_dd": False}
        return r

    class _FakePage:
        def __init__(self, html, url="https://example.test/p/1"):
            self._html = html; self.url = url
        def content(self): return self._html

    def test_runner_records_outcome_after_run(self):
        from unittest.mock import patch
        # Force-import to ensure bulk_downloader.deep_detect attribute
        # exists post-wipe (see class docstring).
        import bulk_downloader.deep_detect  # noqa: F401
        r = self._build_runner()
        page = self._FakePage("<html><body>" + ("x" * 500) + "</body></html>")
        fake_report = {
            "best_download": {"source_type": "hls_master",
                              "url": "https://x/m.m3u8",
                              "confidence": 0.85,
                              "click_selector": "a.dl"},
            "download_candidates": [{
                "url": "https://x/m.m3u8",
                "source_type": "hls_master",
                "click_selector": "a.dl",
                "score": 100,
            }],
            "blockers": {},
            "warnings": [],
            "rejected": [],
        }
        from unittest.mock import MagicMock
        fake_dd = MagicMock()
        fake_dd.deep_detect = lambda html, base_url="", **kw: fake_report
        fake_dd.deep_detect_live = lambda html, **kw: fake_report
        with patch("bulk_downloader.deep_detect", fake_dd):
            # The fallback path also calls find_best_download; mock
            # it so the call doesn't fail at the post-DD stage.
            with patch.object(r, "_persist_deep_detect_selectors",
                              return_value=None):
                with patch("bulk_downloader.runner_extractors.find_best_download",
                           return_value={"url": "https://x/m.m3u8",
                                         "score": 100}):
                    result = r._try_deep_detect_fallback(
                        page, "https://example.test/p/1", {})
        # The runner should have recorded the outcome into the config
        block = r.config.get("learned", {}).get("deep_detect")
        assert block is not None
        assert block["winning_source_types"].get("hls_master") == 1
        assert block["stats"]["runs"] == 1
        # And it should have returned a `best`-shaped result
        assert result is not None

    def test_runner_passes_site_memory_to_deep_detect(self):
        """The runner should read the config's learned.deep_detect
        block and forward it as site_memory to the analyzer."""
        from unittest.mock import patch, MagicMock
        # Force-import to ensure bulk_downloader.deep_detect attribute
        # exists post-wipe (see class docstring).
        import bulk_downloader.deep_detect  # noqa: F401
        # Pre-seed the config with memory
        cfg = {
            "deep_detect_fallback": True,
            "runner_use_live_dd": False,
            "learned": {"deep_detect": {
                "winning_source_types": {"hls_master": 3},
                "preferred_resolution": "1080p",
                "blocker_history": {},
                "provider_embeds_seen": {},
                "last_winner": None,
                "stats": {"runs": 3, "candidates_found": 9,
                          "candidates_picked": 3, "last_run": None},
            }},
        }
        r = self._build_runner(config=cfg)
        page = self._FakePage("<html><body>" + ("x" * 500) + "</body></html>")
        captured = {}
        def fake_dd(html, base_url="", **kw):
            captured["site_memory"] = kw.get("site_memory")
            return {"best_download": None, "download_candidates": [],
                    "blockers": {}, "warnings": [], "rejected": []}
        fake_module = MagicMock()
        fake_module.deep_detect = fake_dd
        fake_module.deep_detect_live = fake_dd
        with patch("bulk_downloader.deep_detect", fake_module):
            r._try_deep_detect_fallback(page, "https://example.test/", {})
        assert captured["site_memory"] is not None
        assert captured["site_memory"]["winning_source_types"][
            "hls_master"] == 3
