"""T8 (Tier 3) — D-53 AI-call latency log + D-57 AI health history.
Two read-only views over aiassist's existing _health state (the
rolling AI-call metrics fed by aiassist._record_call). Neither tool
adds a collector — both only read aiassist.get_health().
"""
import os

from bulk_downloader import aiassist
from bulk_downloader import dev_suite as ds


def _reset_health():
    """Reset aiassist's module-level health state. _health persists
    across tests in one process, so each test starts from clean."""
    with aiassist._health_lock:
        aiassist._health["call_count"] = 0
        aiassist._health["fail_count"] = 0
        aiassist._health["last_check_at"] = 0
        aiassist._health["last_ok"] = False
        aiassist._health["last_error"] = ""
        aiassist._health["latency_ms"] = 0
        aiassist._health["recent_latencies"] = []


# ── D-53 ai_latency_log ────────────────────────────────────────────

def test_ai_latency_log_empty(clean_workdir):
    _reset_health()
    r = ds.ai_latency_log()
    assert r["ok"] is True
    assert r["tool"] == "ai_latency_log"
    assert r["sampled_calls"] == 0
    assert r["by_kind"] == []
    assert "no AI calls" in r["verdict"]


def test_ai_latency_log_groups_by_kind(clean_workdir):
    _reset_health()
    aiassist._record_call("suggest", 1000.0, True)
    aiassist._record_call("suggest", 3000.0, True)
    aiassist._record_call("classify", 500.0, True)
    aiassist._record_call("classify", 700.0, False, "parse error")
    r = ds.ai_latency_log()
    assert r["sampled_calls"] == 4
    kinds = {k["kind"]: k for k in r["by_kind"]}
    assert kinds["suggest"]["count"] == 2
    assert kinds["suggest"]["ok"] == 2
    assert kinds["suggest"]["max_ms"] == 3000.0
    assert kinds["classify"]["failed"] == 1
    assert kinds["classify"]["ok"] == 1


def test_ai_latency_log_flags_slow_calls(clean_workdir):
    _reset_health()
    aiassist._record_call("suggest", 500.0, True)
    aiassist._record_call("suggest", 12000.0, True)   # slow
    r = ds.ai_latency_log(slow_ms=8000)
    assert len(r["slow_calls"]) == 1
    assert r["slow_calls"][0]["ms"] == 12000.0
    assert "over 8000ms" in r["verdict"]


def test_ai_latency_log_none_slow_under_threshold(clean_workdir):
    _reset_health()
    aiassist._record_call("res", 200.0, True)
    r = ds.ai_latency_log(slow_ms=8000)
    assert r["slow_calls"] == []
    assert "none slow" in r["verdict"]


def test_ai_latency_log_percentiles_ordered(clean_workdir):
    _reset_health()
    for ms in (100, 200, 300, 400, 5000):
        aiassist._record_call("suggest", float(ms), True)
    r = ds.ai_latency_log()
    assert r["overall_p50_ms"] <= r["overall_p95_ms"]
    assert r["overall_p95_ms"] <= r["overall_max_ms"]
    assert r["overall_max_ms"] == 5000.0


# ── D-57 ai_health_history ─────────────────────────────────────────

def test_ai_health_history_empty(clean_workdir):
    _reset_health()
    r = ds.ai_health_history()
    assert r["ok"] is True
    assert r["tool"] == "ai_health_history"
    assert r["call_count"] == 0
    assert r["failure_rate_pct"] == 0.0
    assert "no AI calls" in r["verdict"]


def test_ai_health_history_counts_and_rate(clean_workdir):
    _reset_health()
    aiassist._record_call("suggest", 1000.0, True)
    aiassist._record_call("suggest", 1000.0, True)
    aiassist._record_call("classify", 1000.0, False, "boom")
    aiassist._record_call("res", 1000.0, False, "boom2")
    r = ds.ai_health_history()
    assert r["call_count"] == 4
    assert r["fail_count"] == 2
    assert r["failure_rate_pct"] == 50.0
    # the last recorded call failed
    assert r["last_ok"] is False
    assert r["last_error"] == "boom2"


def test_ai_health_history_all_ok(clean_workdir):
    _reset_health()
    aiassist._record_call("suggest", 800.0, True)
    r = ds.ai_health_history()
    assert r["fail_count"] == 0
    assert r["last_ok"] is True
    assert "all succeeded" in r["verdict"]


def test_ai_health_history_timeline(clean_workdir):
    _reset_health()
    aiassist._record_call("suggest", 100.0, True)
    aiassist._record_call("classify", 200.0, False, "err")
    r = ds.ai_health_history()
    assert r["recent_call_count"] == 2
    assert r["recent_fail_count"] == 1
    # timeline is oldest-first and carries ok/kind markers
    assert r["recent_timeline"][0]["kind"] == "suggest"
    assert r["recent_timeline"][1]["ok"] is False
    assert r["seconds_since_last_call"] is not None


# ── dev routes ─────────────────────────────────────────────────────

def test_ai_latency_route(fresh_app):
    j = fresh_app.get("/api/dev/ai_latency").get_json()
    assert j["ok"] is True
    assert j["tool"] == "ai_latency_log"


def test_ai_health_history_route(fresh_app):
    j = fresh_app.get("/api/dev/ai_health_history").get_json()
    assert j["ok"] is True
    assert j["tool"] == "ai_health_history"


def test_t8_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/ai_latency", "/api/dev/ai_health_history"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
