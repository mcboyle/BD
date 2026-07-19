"""Tests for the Tier-1 request-metrics and thread tools — latency
histogram (D-64), slow-endpoint flagger (D-67), error-rate panel
(D-62), exception ring-buffer (D-66), thread dump (D-59) and the
heuristic deadlock detector (D-60) — their /api/dev/* endpoints, and
the app.py request hook that feeds dev_metrics.
"""
from bulk_downloader import dev_metrics as dm
from bulk_downloader import dev_suite as ds


def _seed_requests():
    dm.reset()
    for ms in (10, 20, 30, 40, 1500):              # 5 calls to /api/a
        dm.record_request("GET", "/api/a", "/api/a", 200, ms)
    dm.record_request("GET", "/api/b", "/api/b", 404, 5)
    dm.record_request("POST", "/api/b", "/api/b", 500, 8)


# ── D-64 latency histogram ────────────────────────────────────────

def test_latency_histogram_computes_percentiles():
    _seed_requests()
    r = ds.latency_histogram()
    assert r["sampled_requests"] == 7
    a = next(x for x in r["routes"] if x["rule"] == "/api/a")
    assert a["count"] == 5
    assert a["max_ms"] == 1500
    assert a["p50_ms"] <= a["p95_ms"] <= a["p99_ms"] <= a["max_ms"]


def test_latency_histogram_empty():
    dm.reset()
    r = ds.latency_histogram()
    assert r["sampled_requests"] == 0
    assert "no requests" in r["verdict"]


# ── D-67 slow-endpoint flagger ────────────────────────────────────

def test_slow_endpoints_flags_over_threshold():
    _seed_requests()
    r = ds.slow_endpoints(threshold_ms=500)
    assert r["slow_request_count"] == 1
    assert r["routes"][0]["rule"] == "/api/a"
    assert r["routes"][0]["max_ms"] == 1500


def test_slow_endpoints_none_when_all_fast():
    _seed_requests()
    r = ds.slow_endpoints(threshold_ms=5000)
    assert r["slow_request_count"] == 0
    assert "no requests over" in r["verdict"]


# ── D-62 error-rate panel ─────────────────────────────────────────

def test_error_rate_counts_4xx_and_5xx():
    _seed_requests()
    r = ds.error_rate()
    assert r["total_4xx"] == 1
    assert r["total_5xx"] == 1
    b = next(x for x in r["routes_with_errors"]
             if x["rule"] == "/api/b")
    assert b["4xx"] == 1 and b["5xx"] == 1


# ── D-66 exception ring-buffer ────────────────────────────────────

def test_exception_log_newest_first():
    dm.reset()
    for name in ("first", "second", "third"):
        try:
            raise RuntimeError(name)
        except RuntimeError as e:
            dm.record_exception(e, "/api/x")
    r = ds.exception_log()
    assert r["captured"] == 3
    assert "third" in r["exceptions"][0]["message"]   # newest first
    dm.reset()


# ── D-59 thread dump ──────────────────────────────────────────────

def test_thread_dump_includes_stacks():
    r = ds.thread_dump()
    assert r["thread_count"] >= 1
    t0 = r["threads"][0]
    for key in ("ident", "name", "frame_count", "top_frame", "stack"):
        assert key in t0
    assert isinstance(t0["stack"], list)


# ── D-60 deadlock detector ────────────────────────────────────────

def test_deadlock_detector_clean_process():
    r = ds.deadlock_detector()
    # a healthy test process has no thread frozen at an identical
    # non-idle frame across both snapshots; the inspecting thread is
    # excluded, so it can never flag itself
    assert r["deadlock_suspected"] is False, r["stalled_suspects"]
    assert "stalled_suspects" in r
    assert "verdict" in r


# ── app.py request hook → dev_metrics integration ─────────────────

def test_request_hook_feeds_dev_metrics(fresh_app):
    dm.reset()
    resp = fresh_app.get("/api/dev/routes")
    assert resp.status_code == 200
    snap = dm.request_snapshot()
    assert any(rec["path"] == "/api/dev/routes" for rec in snap), snap


# ── endpoint smoke ────────────────────────────────────────────────

def test_tier1b_endpoints_respond(fresh_app):
    for path in ("/api/dev/latency", "/api/dev/slow_endpoints",
                 "/api/dev/error_rate", "/api/dev/exceptions",
                 "/api/dev/thread_dump", "/api/dev/deadlock_check"):
        resp = fresh_app.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert resp.get_json() is not None, f"{path} returned no JSON"
