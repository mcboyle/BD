"""Tests for v3.47.0 Phases 195-199.

  • 195 — crash recovery (.part orphan scan)
  • 196 — daily byte budget
  • 197 — cookie health monitor (classification + persistence)
  • 198 — selector drift detection
  • 199 — resumable mass-import
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _isolated_bd():
    tmp = tempfile.mkdtemp(prefix="bd-phases-test-")
    # v3.66.9: snapshot env + modules so teardown_module can restore
    # them. Without this, BD_INSTALL_DIR leaks to subsequent test files
    # (e.g. test_session_keeper.py uses chdir-only isolation; under the
    # dynamic DB-path resolver added in v3.66.9, a leaked BD_INSTALL_DIR
    # sends its DB writes to OUR tmpdir, not the test's tmpdir).
    if not _ISOLATED_BD_SAVED:
        _ISOLATED_BD_SAVED["env"] = {
            k: os.environ.get(k)
            for k in ("BD_INSTALL_DIR", "BD_DISABLE_KEEPALIVE")
        }
        _ISOLATED_BD_SAVED["modules"] = {
            k: v for k, v in sys.modules.items()
            if k.startswith("bulk_downloader")
        }
    os.environ["BD_INSTALL_DIR"] = tmp
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    for m in list(sys.modules):
        if m.startswith("bulk_downloader"):
            del sys.modules[m]
    return tmp


_ISOLATED_BD_SAVED = {}


def teardown_module(module):
    """v3.66.9: pytest hook — restore env + sys.modules after this
    file's tests run, so the leaked BD_INSTALL_DIR doesn't taint
    downstream tests."""
    if not _ISOLATED_BD_SAVED:
        return
    for k, v in _ISOLATED_BD_SAVED["env"].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in [k for k in list(sys.modules)
              if k.startswith("bulk_downloader")]:
        del sys.modules[m]
    sys.modules.update(_ISOLATED_BD_SAVED["modules"])
    _ISOLATED_BD_SAVED.clear()


# ─── Phase 195: crash recovery ─────────────────────────────────────────


class TestCrashRecovery(unittest.TestCase):

    def setup_method(self, method=None):
        self.tmp = _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import crash_recovery
        self.cr = crash_recovery
        # Create a fake site with a download_dir
        self.dl_dir = Path(self.tmp) / "dl"
        self.dl_dir.mkdir()
        self.s_cfg = {"sitea": {"name": "Site A",
                                 "download_dir": str(self.dl_dir)}}

    setUp = setup_method

    def _make_part(self, name, age_seconds, size_bytes=1024,
                  meta=None):
        p = self.dl_dir / name
        p.write_bytes(b"x" * size_bytes)
        ts = time.time() - age_seconds
        os.utime(p, (ts, ts))
        if meta is not None:
            meta_path = p.with_suffix(p.suffix + ".meta")
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return p

    def test_scan_skips_recent_files(self):
        self._make_part("recent.mp4.part", age_seconds=60)  # 1m old
        orphans = self.cr.scan_for_orphans(s_cfg=self.s_cfg, runners={})
        self.assertEqual(len(orphans), 0,
            "files younger than threshold should be skipped")

    def test_scan_finds_old_files(self):
        self._make_part("old.mp4.part", age_seconds=25 * 3600)
        orphans = self.cr.scan_for_orphans(s_cfg=self.s_cfg, runners={})
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["site_id"], "sitea")
        self.assertEqual(orphans[0]["size_bytes"], 1024)

    def test_scan_reads_meta_sidecar(self):
        self._make_part("with_meta.mp4.part", age_seconds=25 * 3600,
                        size_bytes=5000,
                        meta={"url": "https://x.com/v1",
                              "total_bytes": 10000})
        orphans = self.cr.scan_for_orphans(s_cfg=self.s_cfg, runners={})
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["url"], "https://x.com/v1")
        self.assertEqual(orphans[0]["progress_pct"], 50.0)

    def test_ignore_filters_subsequent_scans(self):
        p = self._make_part("foo.mp4.part", age_seconds=25 * 3600)
        orphans = self.cr.scan_for_orphans(s_cfg=self.s_cfg, runners={})
        self.assertEqual(len(orphans), 1)
        self.cr.mark_decision(str(p), "ignore", site_id="sitea")
        orphans2 = self.cr.scan_for_orphans(s_cfg=self.s_cfg, runners={})
        self.assertEqual(len(orphans2), 0,
            "ignored files should be skipped on subsequent scans")

    def test_delete_removes_part_and_sidecar(self):
        p = self._make_part("die.mp4.part", age_seconds=25 * 3600,
                            meta={"url": "https://x.com/v"})
        sidecar = p.with_suffix(p.suffix + ".meta")
        self.assertTrue(p.is_file() and sidecar.is_file())
        result = self.cr.delete_orphan(str(p))
        self.assertTrue(result["ok"])
        self.assertFalse(p.exists())
        self.assertFalse(sidecar.exists())

    def test_delete_idempotent_on_missing(self):
        result = self.cr.delete_orphan(str(self.dl_dir / "never.part"))
        self.assertTrue(result["ok"])
        self.assertIn("already absent", result.get("note", ""))

    def test_stats_aggregates_per_site(self):
        self._make_part("a.part", age_seconds=25 * 3600, size_bytes=1000)
        self._make_part("b.part", age_seconds=25 * 3600, size_bytes=2000)
        s = self.cr.stats(s_cfg=self.s_cfg, runners={})
        self.assertEqual(s["orphan_count"], 2)
        self.assertEqual(s["total_bytes"], 3000)
        self.assertEqual(s["by_site"]["sitea"]["count"], 2)

    def test_active_url_excluded(self):
        """If the URL associated with a .part is in a runner's job
        map, the file isn't orphaned even if old."""
        self._make_part("active.mp4.part", age_seconds=25 * 3600,
                        meta={"url": "https://x.com/active"})
        class FakeRunner:
            jobs = {"https://x.com/active": {"status": "running"}}
        runners = {"sitea": FakeRunner()}
        orphans = self.cr.scan_for_orphans(s_cfg=self.s_cfg,
                                            runners=runners)
        self.assertEqual(len(orphans), 0,
            "active URLs should exclude their .part from orphan scan")


# ─── Phase 196: daily byte budget ─────────────────────────────────────


class TestDailyBudget(unittest.TestCase):

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import daily_budget
        self.db = daily_budget

    setUp = setup_method

    def test_record_and_read(self):
        self.db.record_site_bytes("s1", 1000)
        self.db.record_site_bytes("s1", 500)
        self.assertEqual(self.db.bytes_today("s1"), 1500)

    def test_zero_bytes_ignored(self):
        self.db.record_site_bytes("s1", 0)
        self.db.record_site_bytes("s1", -100)
        self.assertEqual(self.db.bytes_today("s1"), 0)

    def test_empty_site_id_ignored(self):
        # Should not crash + not record anything
        self.db.record_site_bytes("", 1000)
        self.assertEqual(self.db.bytes_today(""), 0)

    def test_is_over_budget_no_cap(self):
        self.db.record_site_bytes("s1", 1_000_000)
        result = self.db.is_over_budget("s1", site_cfg={})
        self.assertFalse(result["over"])
        self.assertEqual(result["budget_bytes"], 0)

    def test_is_over_budget_under_cap(self):
        self.db.record_site_bytes("s1", 3000)
        result = self.db.is_over_budget("s1",
            site_cfg={"daily_byte_budget": 5000})
        self.assertFalse(result["over"])
        self.assertEqual(result["pct_used"], 60.0)

    def test_is_over_budget_at_cap(self):
        self.db.record_site_bytes("s1", 5000)
        result = self.db.is_over_budget("s1",
            site_cfg={"daily_byte_budget": 5000})
        self.assertTrue(result["over"])
        self.assertEqual(result["pct_used"], 100.0)

    def test_is_over_budget_above_cap(self):
        self.db.record_site_bytes("s1", 7500)
        result = self.db.is_over_budget("s1",
            site_cfg={"daily_byte_budget": 5000})
        self.assertTrue(result["over"])
        self.assertLess(result["remaining_bytes"], 0)

    def test_reset_today_zeroes_counter(self):
        self.db.record_site_bytes("s1", 1000)
        self.assertEqual(self.db.bytes_today("s1"), 1000)
        self.assertTrue(self.db.reset_today("s1"))
        self.assertEqual(self.db.bytes_today("s1"), 0)

    def test_status_all_sorts_by_pct(self):
        self.db.record_site_bytes("low", 100)
        self.db.record_site_bytes("high", 4500)
        all_status = self.db.status_all({
            "low": {"name": "Low", "daily_byte_budget": 10000},
            "high": {"name": "High", "daily_byte_budget": 5000},
            "uncapped": {"name": "Free"},
        })
        # high should come first (90% used)
        self.assertEqual(all_status[0]["site_id"], "high")

    def test_history_returns_chronological(self):
        self.db.record_site_bytes("s1", 1000)
        h = self.db.history("s1", days=7)
        self.assertGreaterEqual(len(h), 1)
        # Today's entry should be in there
        ymd = self.db._today_ymd()
        self.assertTrue(any(r["ymd"] == ymd for r in h))


# ─── Phase 197: cookie health monitor ──────────────────────────────────


class TestCookieHealthClassification(unittest.TestCase):
    """Tests the pure classification logic — no network calls needed."""

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader import cookie_health
        self.ch = cookie_health

    setUp = setup_method

    def test_200_clean_body_green(self):
        s, _ = self.ch._classify_response(
            http_code=200, final_url="https://site.com/account",
            body_head=b"<html>Welcome back, Alice</html>",
            login_url_hint="")
        self.assertEqual(s, "green")

    def test_401_is_red(self):
        s, n = self.ch._classify_response(
            http_code=401, final_url="x", body_head=b"", login_url_hint="")
        self.assertEqual(s, "red")
        self.assertIn("401", n)

    def test_403_is_red(self):
        s, _ = self.ch._classify_response(
            http_code=403, final_url="x", body_head=b"", login_url_hint="")
        self.assertEqual(s, "red")

    def test_login_url_in_final_path_yellow(self):
        s, _ = self.ch._classify_response(
            http_code=200,
            final_url="https://site.com/login?return=/account",
            body_head=b"", login_url_hint="")
        self.assertEqual(s, "yellow")

    def test_login_url_hint_match_yellow(self):
        s, _ = self.ch._classify_response(
            http_code=200, final_url="https://site.com/login/",
            body_head=b"", login_url_hint="https://site.com/login")
        self.assertEqual(s, "yellow")

    def test_2plus_signals_in_body_yellow(self):
        body = (b"<form>"
                b'<input name="username">'
                b'<input name="password">'
                b"</form>")
        s, n = self.ch._classify_response(
            http_code=200, final_url="https://x.com/", body_head=body,
            login_url_hint="")
        self.assertEqual(s, "yellow")
        self.assertIn("login-page signals", n)

    def test_single_signal_not_enough(self):
        """One match is not strong enough — many real pages have
        a 'Logout' button or 'name="password"' for change-password flow."""
        body = b'<a href="/logout">log out</a>'
        s, _ = self.ch._classify_response(
            http_code=200, final_url="https://x.com/account",
            body_head=body, login_url_hint="")
        # One signal alone isn't enough to flip to yellow
        self.assertEqual(s, "green")

    def test_500_is_yellow_not_red(self):
        """Server errors aren't cookies' fault."""
        s, _ = self.ch._classify_response(
            http_code=500, final_url="x", body_head=b"", login_url_hint="")
        self.assertEqual(s, "yellow")


class TestCookieHealthPersistence(unittest.TestCase):

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import cookie_health
        self.ch = cookie_health

    setUp = setup_method

    def test_record_persists(self):
        self.ch._record("s1", status="green", note="ok",
                        http_code=200, response_url="https://x.com")
        all_ = self.ch.status_all()
        self.assertEqual(len(all_), 1)
        self.assertEqual(all_[0]["site_id"], "s1")
        self.assertEqual(all_[0]["status"], "green")

    def test_record_preserves_last_green_ts_on_red(self):
        """When a site flips green → red, last_green_ts should stay
        so the UI can say 'last verified ok 3 days ago'."""
        self.ch._record("s1", status="green", note="ok")
        # Read what we stored
        first = self.ch.status_all()[0]
        green_ts = first["last_green_ts"]
        self.assertIsNotNone(green_ts)

        time.sleep(0.05)
        self.ch._record("s1", status="red", note="cookies expired")
        second = self.ch.status_all()[0]
        self.assertEqual(second["status"], "red")
        self.assertEqual(second["last_green_ts"], green_ts)

    def test_status_all_sorts_red_first(self):
        self.ch._record("ok_site", status="green", note="ok")
        self.ch._record("broken_site", status="red", note="auth fail")
        self.ch._record("warn_site", status="yellow", note="login page")
        out = self.ch.status_all()
        order = [r["site_id"] for r in out]
        self.assertEqual(order[0], "broken_site",
            "red should sort first")
        self.assertEqual(order[1], "warn_site")
        self.assertEqual(order[2], "ok_site")


# ─── Phase 198: selector drift detection ──────────────────────────────


class TestSelectorDrift(unittest.TestCase):

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import selector_drift
        self.sd = selector_drift

    setUp = setup_method

    def test_initial_state_is_clean(self):
        s = self.sd.status_for("nowhere")
        self.assertEqual(s["consecutive_failures"], 0)
        self.assertFalse(s["flagged_stale"])

    def test_records_consecutive_failures(self):
        for i in range(3):
            self.sd.record_zero_match("s1", ".btn-download",
                                       f"https://x.com/v{i}")
        s = self.sd.status_for("s1")
        self.assertEqual(s["consecutive_failures"], 3)
        self.assertFalse(s["flagged_stale"],
            "3 failures shouldn't flag stale (threshold=5)")

    def test_flags_at_threshold(self):
        for i in range(5):
            self.sd.record_zero_match("s1", ".btn", f"https://x.com/v{i}")
        s = self.sd.status_for("s1")
        self.assertEqual(s["consecutive_failures"], 5)
        self.assertTrue(s["flagged_stale"])
        self.assertTrue(self.sd.is_stale("s1"))

    def test_success_resets_counter(self):
        for _ in range(4):
            self.sd.record_zero_match("s1", ".btn", "https://x.com/v")
        self.assertEqual(self.sd.status_for("s1")["consecutive_failures"], 4)
        self.sd.record_success("s1")
        s = self.sd.status_for("s1")
        self.assertEqual(s["consecutive_failures"], 0)
        self.assertFalse(s["flagged_stale"])

    def test_success_clears_stale_flag(self):
        for _ in range(5):
            self.sd.record_zero_match("s1", ".btn", "https://x.com/v")
        self.assertTrue(self.sd.is_stale("s1"))
        self.sd.record_success("s1")
        self.assertFalse(self.sd.is_stale("s1"))

    def test_reset_removes_state(self):
        for _ in range(3):
            self.sd.record_zero_match("s1", ".btn", "https://x.com/v")
        self.assertTrue(self.sd.reset("s1"))
        s = self.sd.status_for("s1")
        self.assertEqual(s["consecutive_failures"], 0)

    def test_status_all_sorts_stale_first(self):
        # Site A: clean
        self.sd.record_success("clean")
        # Site B: 5 failures (stale)
        for _ in range(5):
            self.sd.record_zero_match("stale_site", ".btn",
                                       "https://x.com/v")
        # Site C: 2 failures (not stale)
        for _ in range(2):
            self.sd.record_zero_match("warn_site", ".btn",
                                       "https://x.com/v")
        out = self.sd.status_all()
        self.assertEqual(out[0]["site_id"], "stale_site")


# ─── Phase 199: resumable mass-import ──────────────────────────────────


class TestMassImport(unittest.TestCase):

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import mass_import
        self.mi = mass_import
        # Mock load_urls function that records calls + simulates work
        self.calls = []
        def fake_load_urls(urls, folder_scan=False):
            self.calls.append((len(urls), folder_scan))
            time.sleep(0.001)
            # Pretend half are new, half dupes
            added = len(urls) // 2
            dupes = len(urls) - added
            return (added, dupes, 0)
        self.fake_load = fake_load_urls

    setUp = setup_method

    def test_start_returns_job_id(self):
        res = self.mi.start_import(site_id="s1",
            urls=["https://x.com/v1", "https://x.com/v2"],
            load_urls_fn=self.fake_load)
        self.assertTrue(res["ok"])
        self.assertIn("job_id", res)
        self.assertEqual(res["total"], 2)

    def test_empty_url_list_rejected(self):
        res = self.mi.start_import(site_id="s1", urls=[],
            load_urls_fn=self.fake_load)
        self.assertFalse(res["ok"])
        self.assertIn("no URLs", res["error"])

    def test_oversized_list_rejected(self):
        huge = ["https://x.com/v"] * (self.mi._MAX_URLS_PER_JOB + 1)
        res = self.mi.start_import(site_id="s1", urls=huge,
            load_urls_fn=self.fake_load)
        self.assertFalse(res["ok"])
        self.assertIn("too many URLs", res["error"])

    def test_job_runs_to_completion(self):
        urls = [f"https://x.com/v{i}" for i in range(250)]
        res = self.mi.start_import(site_id="s1", urls=urls,
            load_urls_fn=self.fake_load)
        job_id = res["job_id"]
        # Wait for completion (250 URLs in chunks of 100 = 3 chunks)
        for _ in range(50):
            s = self.mi.status(job_id)
            if s["state"] == "done":
                break
            time.sleep(0.05)
        s = self.mi.status(job_id)
        self.assertEqual(s["state"], "done", f"state stuck at {s}")
        self.assertEqual(s["processed"], 250)
        # 100//2 + 100//2 + 50//2 = 50 + 50 + 25 = 125
        self.assertEqual(s["added"], 125,
            f"unexpected added count: {s['added']}")
        self.assertEqual(s["dupes"], 125)

    def test_status_unknown_returns_none(self):
        self.assertIsNone(self.mi.status("never-existed"))

    def test_cancel_running_job(self):
        # Slow load_urls so we can cancel mid-flight
        def slow_load(urls, folder_scan=False):
            time.sleep(0.1)
            return (len(urls), 0, 0)
        urls = [f"https://x.com/v{i}" for i in range(500)]
        res = self.mi.start_import(site_id="s1", urls=urls,
            load_urls_fn=slow_load)
        job_id = res["job_id"]
        # Let one chunk start
        time.sleep(0.05)
        self.assertTrue(self.mi.cancel(job_id))
        # Wait for worker to notice
        for _ in range(30):
            s = self.mi.status(job_id)
            if s["state"] == "cancelled":
                break
            time.sleep(0.1)
        self.assertEqual(self.mi.status(job_id)["state"], "cancelled")
        # Cancelling again should return False (no longer running)
        self.assertFalse(self.mi.cancel(job_id))

    def test_chunks_are_size_100(self):
        urls = [f"https://x.com/v{i}" for i in range(250)]
        res = self.mi.start_import(site_id="s1", urls=urls,
            load_urls_fn=self.fake_load)
        # Wait for completion
        for _ in range(50):
            s = self.mi.status(res["job_id"])
            if s["state"] == "done":
                break
            time.sleep(0.05)
        chunk_sizes = [c[0] for c in self.calls]
        self.assertEqual(chunk_sizes, [100, 100, 50])

    def test_recent_returns_history(self):
        urls = ["https://x.com/v"]
        self.mi.start_import(site_id="s1", urls=urls,
            load_urls_fn=self.fake_load)
        time.sleep(0.1)
        recent = self.mi.list_recent()
        self.assertGreaterEqual(len(recent), 1)
        self.assertEqual(recent[0]["site_id"], "s1")


if __name__ == "__main__":
    unittest.main()
