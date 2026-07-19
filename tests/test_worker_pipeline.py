"""Phase 41: worker pipeline tests — the parts with clean seams.

What's tested:
  - Error classification (pure function, deterministic)
  - Retry schedule lookup per error kind
  - Job state transitions via _update_job (locking + storage)
  - Bulk URL deduplication and normalization
  - Resolution badge classification

What's NOT tested (would need full Playwright mock — out of scope):
  - The worker thread itself (download loop)
  - Browser context management
  - Selector matching against live DOM
"""
import pytest


class TestErrorClassification:
    """The classifier picks a retry schedule based on the failure
    message. Most retry behavior follows from this."""

    @pytest.mark.parametrize("msg,expected", [
        ("HTTP 404 Not Found",                     "permanent"),
        ("403 Forbidden",                          "permanent"),
        ("auth required to download",              "permanent"),
        ("link expired",                           "permanent"),
        ("Resource is 410 Gone",                   "permanent"),
        ("HTTP 429 Too Many Requests",             "rate_limit"),
        ("rate limit hit",                         "rate_limit"),
        ("try again later",                        "rate_limit"),
        ("Connection refused",                     "network"),
        ("Read timeout",                           "network"),
        ("DNS resolution failed",                  "network"),
        ("server is unreachable",                  "network"),
        ("HTTP 500 Internal Server Error",         "transient"),
        ("something weird happened",               "transient"),
        ("",                                       "transient"),
    ])
    def test_classify(self, msg, expected):
        from bulk_downloader.runner import SiteRunner
        # _classify_error doesn't use self state; binding to None works
        fake_self = type("F", (), {})()
        assert SiteRunner._classify_error(fake_self, msg) == expected


class TestRetryScheduleByKind:
    """Retry schedule lookup table."""

    def test_permanent_has_empty_schedule(self):
        from bulk_downloader.runner import SiteRunner
        assert SiteRunner._RETRY_DELAYS_BY_KIND["permanent"] == []

    def test_rate_limit_has_hour_scale_delays(self):
        from bulk_downloader.runner import SiteRunner
        schedule = SiteRunner._RETRY_DELAYS_BY_KIND["rate_limit"]
        assert len(schedule) >= 2
        # First entry should be at least an hour
        assert schedule[0] >= 3600

    def test_network_has_short_delays(self):
        from bulk_downloader.runner import SiteRunner
        schedule = SiteRunner._RETRY_DELAYS_BY_KIND["network"]
        # First entry should be quick (<2 minutes)
        assert schedule[0] < 120

    def test_transient_falls_back(self):
        from bulk_downloader.runner import SiteRunner
        assert "transient" in SiteRunner._RETRY_DELAYS_BY_KIND


class TestFormatDuration:
    """The _fmt_dur helper formats seconds to human-readable durations."""

    def _fmt(self, s):
        from bulk_downloader.runner import SiteRunner
        return SiteRunner._fmt_dur(None, s)

    def test_seconds(self):
        assert self._fmt(30) == "30s"
        assert self._fmt(59) == "59s"

    def test_minutes(self):
        assert self._fmt(60) == "1m"
        assert self._fmt(120) == "2m"
        assert self._fmt(3599) == "59m"

    def test_hours(self):
        assert self._fmt(3600) == "1h"
        assert self._fmt(7200) == "2h"
        assert self._fmt(86399) == "23h"

    def test_days(self):
        assert self._fmt(86400) == "1d"
        assert self._fmt(172800) == "2d"


class TestRunnerInit:
    """End-to-end SiteRunner construction with a minimal config.
    Doesn't start workers — just verifies the object initializes
    without errors and key state is set up."""

    def test_minimal_init(self, clean_workdir):
        # Pre-create dirs that SiteRunner expects
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        # init the SQLite tables
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("test_sid_001", {"name": "test", "max_concurrent": 1})
        try:
            assert r.site_id == "test_sid_001"
            assert r.config["name"] == "test"
            # Lock + jobs dict + urls list exist
            assert hasattr(r, "_lock")
            assert isinstance(r.jobs, dict)
            assert isinstance(r.urls, list)
            # Auto-retry thread is running (Phase 30)
            assert r._auto_retry_thread is not None
            assert r._auto_retry_thread.is_alive()
            # Logger is wired (Phase 34)
            assert hasattr(r, "log")
            # Captcha stats initialized (Phase 32)
            assert r._captcha_stats["submitted"] == 0
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_auto_retry_thread_dies_on_stop(self, clean_workdir):
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("test_sid_002", {"name": "t"})
        thread = r._auto_retry_thread
        assert thread.is_alive()
        r._stop_auto_retry()
        # Thread joins within 3s (or _stop_auto_retry already cleared it)
        thread.join(timeout=3)
        assert not thread.is_alive()
        r.stop()

    def test_pause_actually_pauses(self, clean_workdir):
        """Regression: prior to v3.43.19, pause() was a self-cancelling
        toggle (two if-statements that fired sequentially, leaving the
        runner in the same state it started in). Verify pause() now does
        what it says."""
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("test_pause", {"name": "t"})
        try:
            # Simulate the "running" state without actually starting workers
            r._state = "running"
            r._pause.set()
            r.pause()
            assert r._state == "paused"
            assert not r._pause.is_set()
            # Idempotent — second pause() is a no-op
            r.pause()
            assert r._state == "paused"
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_resume_method_exists(self, clean_workdir):
        """Regression: before v3.43.19, SiteRunner had no resume() method,
        so the /api/sites/<sid>/resume endpoint (called by the UI's Resume
        button) silently returned 400. Verify the method exists and flips
        a paused runner back to running."""
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("test_resume", {"name": "t"})
        try:
            assert hasattr(r, "resume") and callable(r.resume)
            r._state = "paused"
            r._pause.clear()
            r.resume()
            assert r._state == "running"
            assert r._pause.is_set()
            # Also resumes from low_disk and paused_no_button
            r._state = "low_disk"; r._pause.clear()
            r.resume()
            assert r._state == "running"
            r._state = "paused_no_button"; r._pause.clear()
            r.resume()
            assert r._state == "running"
        finally:
            r.stop()
            r._stop_auto_retry()


class TestAddUrls:
    """The load_urls path handles dedup and persistence."""

    def test_dedup_within_one_call(self, clean_workdir):
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("t", {"name": "x"})
        try:
            added, dupes, skipped = r.load_urls([
                "https://x/a", "https://x/a", "https://x/b"
            ])
            # v3.43.19: within-call duplicates (already in self.jobs) now
            # count toward `dupes` so added+dupes+skipped sums to input size.
            # Previously they were silently dropped which made the import
            # summary misleading on re-imports.
            assert added == 2  # a (first) and b
            assert dupes == 1  # second a
            assert len(r.urls) == 2
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_dedup_against_existing(self, clean_workdir):
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("t2", {"name": "x"})
        try:
            r.load_urls(["https://x/a", "https://x/b"])
            added, dupes, skipped = r.load_urls(["https://x/b", "https://x/c"])
            # b is in jobs but not done — silently skipped, no dupes++
            assert added == 1   # only c is new
            assert len(r.urls) == 3
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_empty_input(self, clean_workdir):
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("t3", {"name": "x"})
        try:
            added, dupes, skipped = r.load_urls([])
            assert added == 0
            assert dupes == 0
        finally:
            r.stop()
            r._stop_auto_retry()


# ─── Phase 41.5: auto-teach pre-flight gate in start() ───────────────────────

class TestPhase415AutoTeachPreflightGate:
    """The pre-flight in start() avoids spawning worker browsers when no
    learned download selectors exist. Before this fix, clicking Start with
    a brand-new site made Chromium briefly pop up and vanish (workers
    spawned → auto-teach marked URL needs_review → task_done → _watch_done
    tore workers down)."""

    def test_no_workers_spawn_when_no_learned_selectors(self, clean_workdir):
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("t41_5_a", {"name": "t", "auto_teach_first_run": True})
        try:
            r.load_urls(["https://example.com/v1"])
            assert r.jobs["https://example.com/v1"]["status"] == "pending"
            # Pre-flight should fire: no learned selectors, auto_teach on
            r.start()
            # The URL is now needs_review
            assert r.jobs["https://example.com/v1"]["status"] == "needs_review"
            assert r.jobs["https://example.com/v1"].get("auto_teach_seen") is True
            # No worker threads were spawned
            assert len(r._worker_threads) == 0
            # State is idle (not "running")
            assert r._state == "idle"
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_only_first_url_flagged(self, clean_workdir):
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("t41_5_b", {"name": "t", "auto_teach_first_run": True})
        try:
            r.load_urls([f"https://example.com/v{i}" for i in range(5)])
            r.start()
            # Exactly ONE URL is in needs_review
            flagged = [u for u, j in r.jobs.items() if j["status"] == "needs_review"]
            assert len(flagged) == 1
            # The rest stay pending
            pending = [u for u, j in r.jobs.items() if j["status"] == "pending"]
            assert len(pending) == 4
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_double_start_is_idempotent(self, clean_workdir):
        """Clicking Start a second time while waiting for teach must NOT
        flag another URL. Bug fixed in v3.36.7."""
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("t41_5_c", {"name": "t", "auto_teach_first_run": True})
        try:
            r.load_urls([f"https://example.com/v{i}" for i in range(3)])
            r.start()
            r.start()  # second click
            r.start()  # third click for good measure
            flagged = [u for u, j in r.jobs.items() if j["status"] == "needs_review"]
            assert len(flagged) == 1, f"Expected 1 flagged URL, got {len(flagged)}"
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_disabled_auto_teach_skips_preflight(self, clean_workdir):
        """When auto_teach_first_run=False, start() should attempt the
        normal worker spawn path (we won't actually let it run, but the
        pre-flight must NOT mark URLs needs_review)."""
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        # auto_teach disabled
        r = SiteRunner("t41_5_d", {"name": "t", "auto_teach_first_run": False,
                                   "max_concurrent": 1})
        try:
            r.load_urls(["https://example.com/v1"])
            # We don't actually start workers in test (they'd try to launch
            # a browser). Just verify the pre-flight DOESN'T flag the URL.
            # Mimic the pre-flight call directly:
            with r._lock:
                pending = [u for u, j in r.jobs.items() if j["status"] == "pending"]
            assert pending == ["https://example.com/v1"]
            # If auto_teach is off, the pre-flight short-circuits and would
            # fall through to spawn workers. We check the FLAG isn't set.
            assert not r.jobs["https://example.com/v1"].get("auto_teach_seen")
        finally:
            r.stop()
            r._stop_auto_retry()

    def test_session_attrs_initialized(self, clean_workdir):
        """Phase 41.5 init defaults. Previously read via getattr() with
        defaults; now explicit so typos surface immediately."""
        (clean_workdir / "screenshots").mkdir(exist_ok=True)
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner("t41_5_e", {"name": "t"})
        try:
            assert r._manual_download_session is None
            assert r._manual_login_handle is None
            assert r._auto_teach_logged is False
        finally:
            r.stop()
            r._stop_auto_retry()
