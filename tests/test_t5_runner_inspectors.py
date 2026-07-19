"""T5 (Tier 3) — D-13 retry-schedule visualizer + D-14 worker-thread
profiler + D-17 account-pool viewer. Three read-only runner-state
tools in Group B, all driven by the app.runners dict (passed in to
avoid a circular import) and the live queue DB.
"""
import threading
import time

from bulk_downloader import db
from bulk_downloader import dev_suite as ds


class _FakeRunner:
    """Minimal stand-in for a SiteRunner — only the attributes the T5
    tools read. Built per-test so each can set what it needs."""
    def __init__(self, config=None, worker_threads=None,
                 hung_workers=None, active_account_idx=0,
                 auto_retry_alive=False):
        self.config = config or {}
        self._worker_threads = worker_threads or []
        self._hung_workers = hung_workers or []
        self._active_account_idx = active_account_idx

        class _T:
            def __init__(self, alive):
                self._alive = alive

            def is_alive(self):
                return self._alive
        self._auto_retry_thread = _T(auto_retry_alive)


# ── D-13 retry_schedule_inspect ────────────────────────────────────

def test_retry_schedule_empty_runners(clean_workdir):
    db.db_init()
    r = ds.retry_schedule_inspect(runners={})
    assert r["ok"] is True
    assert r["tool"] == "retry_schedule_inspect"
    assert r["sites"] == []
    assert r["total_scheduled_retries"] == 0


def test_retry_schedule_parses_config(clean_workdir):
    db.db_init()
    rn = _FakeRunner(config={"auto_retry_schedule": "1h,4h,24h",
                             "auto_retry_max_attempts": 5},
                     auto_retry_alive=True)
    r = ds.retry_schedule_inspect(runners={"siteA": rn})
    s = r["sites"][0]
    assert s["schedule_seconds"] == [3600, 14400, 86400]
    assert s["schedule_human"] == ["1h", "4h", "1d"]
    assert s["max_attempts"] == 5
    assert s["auto_retry_running"] is True


def test_retry_schedule_default_when_unset(clean_workdir):
    db.db_init()
    rn = _FakeRunner(config={})
    r = ds.retry_schedule_inspect(runners={"siteA": rn})
    # an unset schedule falls back to the runner's 1h/4h/24h default
    assert r["sites"][0]["schedule_seconds"] == [3600, 14400, 86400]
    assert r["sites"][0]["max_attempts"] == 3


def test_retry_schedule_counts_live_scheduled_retries(clean_workdir):
    db.db_init()
    future = time.time() + 7200          # due in 2h
    past = time.time() - 60              # already due — not "scheduled"
    db.queue_upsert("siteA", "https://a.example/sched",
                    status="pending", retry_after=future)
    db.queue_upsert("siteA", "https://a.example/duenow",
                    status="pending", retry_after=past)
    rn = _FakeRunner(config={"auto_retry_schedule": "1h"})
    r = ds.retry_schedule_inspect(runners={"siteA": rn})
    s = r["sites"][0]
    # only the future-dated pending row counts as a scheduled retry
    assert s["scheduled_retries"] == 1
    assert s["soonest_retry_in"] is not None
    assert r["total_scheduled_retries"] == 1


# ── D-14 worker_thread_profile ─────────────────────────────────────

def test_worker_profile_empty_runners(clean_workdir):
    r = ds.worker_thread_profile(runners={})
    assert r["ok"] is True
    assert r["tool"] == "worker_thread_profile"
    assert r["sites"] == []
    assert r["total_worker_threads"] == 0


def test_worker_profile_counts_alive_and_dead(clean_workdir):
    alive = threading.Thread(target=lambda: time.sleep(2),
                             name="worker-alive", daemon=True)
    alive.start()
    dead = threading.Thread(target=lambda: None, name="worker-dead",
                            daemon=True)
    dead.start()
    dead.join()                          # now genuinely not alive
    try:
        rn = _FakeRunner(config={"max_concurrent": 4},
                         worker_threads=[alive, dead])
        r = ds.worker_thread_profile(runners={"siteA": rn})
        s = r["sites"][0]
        assert s["worker_threads"] == 2
        assert s["alive"] == 1
        assert s["dead"] == 1
        assert s["daemon"] == 2
        assert s["over_configured_max"] is False
        assert r["total_alive"] == 1
    finally:
        alive.join()


def test_worker_profile_flags_over_max(clean_workdir):
    t = threading.Thread(target=lambda: None, daemon=True)
    t.start(); t.join()
    rn = _FakeRunner(config={"max_concurrent": 1},
                     worker_threads=[t, t, t])
    r = ds.worker_thread_profile(runners={"siteA": rn})
    assert r["sites"][0]["over_configured_max"] is True


def test_worker_profile_counts_hung(clean_workdir):
    rn = _FakeRunner(config={"max_concurrent": 2},
                     hung_workers=[{"url": "u1"}, {"url": "u2"}])
    r = ds.worker_thread_profile(runners={"siteA": rn})
    assert r["sites"][0]["hung_workers"] == 2
    assert r["total_hung_workers"] == 2
    assert "hung" in r["verdict"]


# ── D-17 account_pool_inspect ──────────────────────────────────────

def test_account_pool_empty_runners(clean_workdir):
    r = ds.account_pool_inspect(runners={})
    assert r["ok"] is True
    assert r["tool"] == "account_pool_inspect"
    assert r["total_accounts"] == 0


def test_account_pool_no_accounts_configured(clean_workdir):
    rn = _FakeRunner(config={})
    r = ds.account_pool_inspect(runners={"siteA": rn})
    s = r["sites"][0]
    assert s["account_count"] == 0
    assert s["active_index"] is None


def test_account_pool_reports_active_and_cooldown(clean_workdir):
    now = time.time()
    rn = _FakeRunner(
        config={"accounts": [
            {"username": "alice_real", "password": "secret1"},
            {"username": "bob_real", "password": "secret2",
             "cooldown_until": now + 3600},
        ]},
        active_account_idx=1)
    r = ds.account_pool_inspect(runners={"siteA": rn})
    s = r["sites"][0]
    assert s["account_count"] == 2
    assert s["active_index"] == 1
    assert s["accounts"][0]["active"] is False
    assert s["accounts"][1]["active"] is True
    assert s["accounts"][0]["cooled_down"] is False
    assert s["accounts"][1]["cooled_down"] is True
    assert s["accounts"][1]["cooldown_remaining"] is not None
    assert s["usable_now"] == 1
    assert r["total_cooled_down"] == 1


def test_account_pool_never_emits_credentials(clean_workdir):
    rn = _FakeRunner(config={"accounts": [
        {"username": "supersecret_user", "password": "topsecretpw"}]})
    r = ds.account_pool_inspect(runners={"siteA": rn})
    blob = repr(r)
    # the password must not appear anywhere in the result
    assert "topsecretpw" not in blob
    # the username must be masked, not emitted whole
    assert "supersecret_user" not in blob
    masked = r["sites"][0]["accounts"][0]["username_masked"]
    assert masked.startswith("su")
    assert "*" in masked


# ── dev routes ─────────────────────────────────────────────────────

def test_retry_schedule_route(fresh_app):
    db.db_init()
    j = fresh_app.get("/api/dev/retry_schedule").get_json()
    assert j["ok"] is True
    assert j["tool"] == "retry_schedule_inspect"
    assert "sites" in j


def test_worker_profile_route(fresh_app):
    j = fresh_app.get("/api/dev/worker_profile").get_json()
    assert j["ok"] is True
    assert j["tool"] == "worker_thread_profile"


def test_account_pool_route(fresh_app):
    j = fresh_app.get("/api/dev/account_pool").get_json()
    assert j["ok"] is True
    assert j["tool"] == "account_pool_inspect"


def test_t5_routes_are_dev_gated():
    import os
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/retry_schedule", "/api/dev/worker_profile",
                   "/api/dev/account_pool"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
