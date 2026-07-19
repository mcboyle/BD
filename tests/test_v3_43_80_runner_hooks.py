"""Tests verifying runner.py _update_job integration hooks (v3.43.80).

The previous test_v3_43_80_modules.py covered each module in isolation.
This file proves the integration points: when the runner calls
_update_job, the new modules actually receive the events they
expect (webhooks fired, vpn_stats recorded, plugins invoked,
fingerprints logged).

We exercise _update_job directly without spinning up a real worker
loop, since the loop's other gates (browser, VPN, cookies) aren't
relevant to verifying the hook surface.
"""
import os
import sys
import tempfile
import time

import pytest


# v3.66.6: same env+module-isolation autouse pattern as
# test_v3_43_80_modules.py. _fresh() below mutates BD_INSTALL_DIR
# and purges bulk_downloader.* from sys.modules; without restoration
# these leak into downstream test files when the harness runs
# serially (nproc=1 / --workers=1).
#
# v3.66.8 (item #5 of v3.66.7 backlog): added cwd save/restore +
# chdir into the tempdir from _fresh(). See test_v3_43_80_modules.py
# for the full diagnosis — same root cause, same fix. tl;dr:
# bulk_downloader.constants.DB_PATH is relative, the repo ships with
# a populated downloader_history.db, so tests that don't chdir away
# from the repo root saw 1446+ pre-existing 'done' rows on top of
# their seed and miscounted.
@pytest.fixture(autouse=True)
def _restore_env_and_modules():
    saved_env = {k: os.environ.get(k) for k in (
        "BD_INSTALL_DIR", "BD_DISABLE_KEEPALIVE")}
    saved_mods = {m: sys.modules[m] for m in list(sys.modules)
                  if m.startswith("bulk_downloader")}
    saved_cwd = os.getcwd()
    try:
        yield
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for m in list(sys.modules):
            if m.startswith("bulk_downloader") and m not in saved_mods:
                del sys.modules[m]
        sys.modules.update(saved_mods)
        # Restore cwd. The tempdir may have been cleaned up; tolerate.
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass


def _fresh():
    """Fresh install dir + reload + minimal runner.

    v3.66.8: cwd is also moved into the tempdir so the relative
    DB_PATH resolves into per-test isolation. See modules file for
    full rationale.
    """
    tmpdir = tempfile.mkdtemp(prefix="bd-hooks-")
    os.environ["BD_INSTALL_DIR"] = tmpdir
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    os.chdir(tmpdir)
    saved_modules = {k: v for k, v in sys.modules.items()
                     if k.startswith("bulk_downloader")}
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader.db import db_init
    db_init()
    return tmpdir
    for _bd_mod in [_m for _m in list(sys.modules)
                    if _m.startswith("bulk_downloader")]:
        del sys.modules[_bd_mod]
    sys.modules.update(saved_modules)


def _make_runner(site_id="vixen"):
    """Create a SiteRunner with minimal config — enough for _update_job
    to work but no browser, no workers."""
    from bulk_downloader import runner as _r
    site_cfg = {"name": site_id.title(), "download_dir": "/tmp",
                "headless": True}
    return _r.SiteRunner(site_id, site_cfg)


# ─── Webhook fires on terminal status ─────────────────────────────────

class TestWebhookIntegration:
    def test_done_fires_webhook(self):
        _fresh()
        from bulk_downloader import webhooks as _wh
        from bulk_downloader.db import db_log
        # Subscribe to download.done
        sub_id = _wh.add_subscription(
            url="http://127.0.0.1/hook",
            events=["download.done"])
        assert sub_id is not None
        # Log a row + invoke _update_job manually
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        r._update_job("http://x/v", "done", "",
                       filename="/dl/x.mp4", file_size=1000)
        # Queue should now have 1 pending webhook delivery
        s = _wh.stats()
        assert s["queue_size"] == 1

    def test_failed_fires_webhook(self):
        _fresh()
        from bulk_downloader import webhooks as _wh
        sub_id = _wh.add_subscription(
            url="http://127.0.0.1/hook",
            events=["download.failed"])
        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        r._update_job("http://x/v", "failed", "HTTP 429")
        assert _wh.stats()["queue_size"] == 1

    def test_unrelated_status_doesnt_fire(self):
        _fresh()
        from bulk_downloader import webhooks as _wh
        _wh.add_subscription(url="http://127.0.0.1/hook",
                              events=["download.done"])
        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        # 'pending' → 'running' is non-terminal, shouldn't fire
        r._update_job("http://x/v", "running", "")
        assert _wh.stats()["queue_size"] == 0


# ─── Plugin hooks fire ────────────────────────────────────────────────

class TestPluginIntegration:
    def test_done_calls_plugin_hook(self):
        _fresh()
        from bulk_downloader import plugins as _pl
        _pl.reset()
        received = []

        @_pl.hook("download.done")
        def on_done(payload):
            received.append(payload)

        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        r._update_job("http://x/v", "done", "",
                       filename="/dl/a.mp4", file_size=500)
        assert len(received) == 1
        assert received[0]["site_id"] == "vixen"
        assert received[0]["url"] == "http://x/v"
        assert received[0]["filename"] == "/dl/a.mp4"
        assert received[0]["file_size"] == 500

    def test_plugin_exception_doesnt_break_update(self):
        _fresh()
        from bulk_downloader import plugins as _pl
        _pl.reset()

        @_pl.hook("download.failed")
        def buggy_hook(payload):
            raise RuntimeError("plugin error")

        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        # Should NOT raise despite the buggy hook
        r._update_job("http://x/v", "failed", "test")
        # _update_job records state on the in-memory runner object,
        # not directly in the DB. Verify the in-memory side completed.
        assert r.jobs.get("http://x/v", {}).get("status") == "failed"


# ─── VPN stats recorded ───────────────────────────────────────────────

class TestVpnStatsIntegration:
    def test_records_when_profile_set_on_runner(self):
        _fresh()
        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        r.current_vpn_profile = "tokyo-1"
        r._update_job("http://x/v", "done", "",
                       filename="/dl/a.mp4", file_size=100)
        from bulk_downloader import vpn_stats as _vs
        report = _vs.profile_report()
        assert any(row["vpn_profile"] == "tokyo-1"
                   and row["site_id"] == "vixen"
                   and row["successes"] == 1
                   for row in report)

    def test_no_record_when_no_profile(self):
        _fresh()
        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        # current_vpn_profile not set
        r._update_job("http://x/v", "done", "",
                       filename="/dl/a.mp4", file_size=100)
        from bulk_downloader import vpn_stats as _vs
        report = _vs.profile_report()
        # No record without an active profile
        assert not any(row["site_id"] == "vixen" for row in report)


# ─── Circuit breaker (pre-existing) still fires ───────────────────────

class TestCircuitBreakerIntegration:
    def test_observe_called_on_success(self):
        _fresh()
        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://example.com/v", "pending")
        r = _make_runner("vixen")
        r._update_job("http://example.com/v", "done", "",
                       filename="/dl/a.mp4", file_size=100)
        from bulk_downloader import circuit_breaker as _cb
        rep = _cb.report()
        # circuit_breaker keys by host:port (port 80 for http, 443 for https)
        assert any("example.com" in k for k in rep.keys()), \
            f"expected example.com host in report, got {list(rep.keys())}"
        # Should have at least one sample recorded
        key = next(k for k in rep.keys() if "example.com" in k)
        assert rep[key]["samples"] >= 1


# ─── Provenance (pre-existing) ────────────────────────────────────────

class TestProvenanceIntegration:
    def test_records_on_done_with_filename(self):
        _fresh()
        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        r._update_job("http://x/v", "done", "",
                       filename="/dl/a.mp4", file_size=2048)
        from bulk_downloader import provenance as _prov
        s = _prov.stats()
        # provenance.stats() exposes the count under 'rows'
        assert s.get("rows", 0) == 1

    def test_no_record_when_no_filename(self):
        _fresh()
        from bulk_downloader.db import db_log
        db_log("vixen", "V", "http://x/v", "pending")
        r = _make_runner("vixen")
        r._update_job("http://x/v", "done", "")  # no filename in extras
        from bulk_downloader import provenance as _prov
        s = _prov.stats()
        assert s.get("rows", 0) == 0
