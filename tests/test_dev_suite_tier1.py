"""Tests for the Tier-1 diagnostic inspector tools — structured-log
search (D-63), stuck-job detector (D-12), rate-limit state inspector
(D-44), session-keeper heartbeat monitor (D-23), and the Ollama model
inventory (D-51) — and their /api/dev/* endpoints.

All five are read-only.
"""
import datetime as dt
import sqlite3
import threading

from bulk_downloader import dev_suite as ds


# ── D-63 structured log search ────────────────────────────────────

def _write_log(workdir):
    logdir = workdir / "logs"
    logdir.mkdir(exist_ok=True)
    (logdir / "bulk_downloader.log").write_text(
        "2026-05-19 00:00:01 INFO  bulk_downloader.app: app started\n"
        "2026-05-19 00:00:02 ERROR bulk_downloader.runner: dl failed\n"
        "2026-05-19 00:00:03 WARNING bulk_downloader.db: slow query\n"
        "2026-05-19 00:00:04 INFO  bulk_downloader.runner: dl ok\n",
        encoding="utf-8")


def test_log_search_absent_file(clean_workdir):
    r = ds.log_search()
    assert r["exists"] is False
    assert r["matches"] == []


def test_log_search_filters_by_level(clean_workdir):
    _write_log(clean_workdir)
    r = ds.log_search(level="error")
    assert r["match_count"] == 1
    assert r["matches"][0]["level"] == "ERROR"
    assert "dl failed" in r["matches"][0]["message"]


def test_log_search_filters_by_logger_and_text(clean_workdir):
    _write_log(clean_workdir)
    assert ds.log_search(logger="runner")["match_count"] == 2
    assert ds.log_search(contains="slow query")["match_count"] == 1


def test_log_search_honours_limit_and_orders_newest_first(clean_workdir):
    _write_log(clean_workdir)
    r = ds.log_search(limit=1)
    assert r["match_count"] == 1
    # newest line first
    assert "dl ok" in r["matches"][0]["message"]


# ── D-12 stuck-job detector ───────────────────────────────────────

def test_stuck_jobs_flags_only_old_running_rows(fresh_app):
    now_utc = dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S")
    cx = sqlite3.connect("downloader_history.db")
    cx.execute("INSERT INTO queue(site_id,url,status,ts_updated) "
               "VALUES(?,?,?,?)",
               ("s1", "http://x/old", "running", "2020-01-01T00:00:00"))
    cx.execute("INSERT INTO queue(site_id,url,status,ts_updated) "
               "VALUES(?,?,?,?)",
               ("s1", "http://x/fresh", "running", now_utc))
    cx.execute("INSERT INTO queue(site_id,url,status,ts_updated) "
               "VALUES(?,?,?,?)",
               ("s1", "http://x/done", "done", "2020-01-01T00:00:00"))
    cx.commit()
    cx.close()
    r = ds.stuck_jobs(older_than=1800)
    assert r["running_total"] == 2
    assert r["stuck_count"] == 1
    assert r["stuck_jobs"][0]["url"] == "http://x/old"


def test_stuck_jobs_clean_when_nothing_running(fresh_app):
    r = ds.stuck_jobs()
    assert r["stuck_count"] == 0
    assert "no jobs running" in r["verdict"]


# ── D-44 rate-limit state inspector ───────────────────────────────

def test_rate_limit_state_reports_per_runner():
    import time

    class _R:
        def __init__(self, until):
            self._rl_until = until

    runners = {"limited": _R(time.time() + 3600),
               "free": _R(0.0)}
    r = ds.rate_limit_state(runners)
    assert r["runner_count"] == 2
    assert r["rate_limited_now"] == 1
    limited = next(s for s in r["sites"] if s["site_id"] == "limited")
    assert limited["rate_limited"] is True
    assert limited["seconds_remaining"] > 0
    free = next(s for s in r["sites"] if s["site_id"] == "free")
    assert free["rate_limited"] is False


def test_rate_limit_state_empty():
    r = ds.rate_limit_state({})
    assert r["runner_count"] == 0
    assert "no sites" in r["verdict"]


# ── D-23 session-keeper heartbeat monitor ─────────────────────────

def test_keeper_monitor_shape_with_no_keepers():
    r = ds.keeper_monitor()
    # no keepers run in the unit suite — but the tool must still
    # return a well-formed result
    assert "keeper_count" in r
    assert isinstance(r["keepers"], list)
    assert "verdict" in r


def test_keeper_monitor_reads_a_fake_keeper(monkeypatch):
    from bulk_downloader import session_keeper as sk

    class _FakeKeeper:
        site_id = "s9"
        account_idx = 0
        _browser = object()        # a handle is present
        _browser_started_at = 1000.0
        state = {"state": "refreshing", "last_heartbeat_ts": 0.0,
                 "last_login_ts": 0.0, "consecutive_failures": 2,
                 "last_detail": "ok"}

    monkeypatch.setattr(sk, "_keepers", {("s9", 0): _FakeKeeper()})
    r = ds.keeper_monitor()
    assert r["keeper_count"] == 1
    k = r["keepers"][0]
    assert k["site_id"] == "s9"
    assert k["state"] == "refreshing"
    assert k["browser_handle_present"] is True
    assert k["consecutive_failures"] == 2
    assert r["with_failures"] == 1


# ── D-51 Ollama / AI model inventory ──────────────────────────────

def test_ollama_inventory_shape():
    r = ds.ollama_inventory()
    # AI may be disabled or unreachable in the sandbox — either way
    # the tool returns a well-formed inventory.
    for key in ("provider", "enabled", "reachable",
                "configured_models", "installed_models",
                "missing_models", "verdict"):
        assert key in r, key
    assert isinstance(r["verdict"], str) and r["verdict"]


# ── endpoint smoke ────────────────────────────────────────────────

def test_tier1_endpoints_respond(fresh_app):
    for path in ("/api/dev/log_search", "/api/dev/stuck_jobs",
                 "/api/dev/rate_limits", "/api/dev/keepers",
                 "/api/dev/ollama"):
        resp = fresh_app.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert resp.get_json() is not None, f"{path} returned no JSON"
