"""Tests for bulk_downloader.dev_suite — the read-only inspection
tools and their /api/dev/* endpoints."""
from pathlib import Path

import pytest

from bulk_downloader import dev_suite as ds


# ── 1. route map ──────────────────────────────────────────────────

def test_route_map_lists_routes():
    from bulk_downloader.app import app
    rm = ds.route_map(app)
    assert rm["count"] > 50
    rules = {r["rule"] for r in rm["routes"]}
    assert "/api/dev/routes" in rules
    dev = next(r for r in rm["routes"] if r["rule"] == "/api/dev/routes")
    assert dev["dev_only"] is True


# ── 2. thread inventory ───────────────────────────────────────────

def test_thread_inventory_shape():
    inv = ds.thread_inventory()
    assert inv["count"] >= 1
    t0 = inv["threads"][0]
    for key in ("name", "daemon", "alive", "ident"):
        assert key in t0


# ── 3. DB overview ────────────────────────────────────────────────

def test_db_overview_reports_tables_and_mode(fresh_app):
    info = ds.db_overview()
    assert "tables" in info and info["table_count"] >= 1
    assert info["journal_mode"]   # 'wal' or 'memory' in tests
    assert all("table" in t and "rows" in t for t in info["tables"])
    assert isinstance(info["db_bytes"], int)


# ── 4. runner state ───────────────────────────────────────────────

def test_runner_state_reads_runner_attrs():
    import threading

    class _FakeRunner:
        _state = "idle"
        urls = ["u1", "u2"]
        jobs = {"a": 1}
        _worker_threads = []
        _pause = threading.Event()
        _stop = threading.Event()
        _rl_until = 0.0
    fr = _FakeRunner()
    fr._pause.set()        # set == running, not paused
    st = ds.runner_state({"site1": fr})
    assert st["runner_count"] == 1
    r = st["runners"][0]
    assert r["state"] == "idle"
    assert r["urls"] == 2
    assert r["paused"] is False


def test_runner_state_empty_when_no_runners():
    assert ds.runner_state({})["runner_count"] == 0
    assert ds.runner_state(None)["runner_count"] == 0


# ── 5. log tail ───────────────────────────────────────────────────

def test_log_tail_absent_file(clean_workdir):
    out = ds.log_tail(50)
    assert out["exists"] is False
    assert out["lines"] == []


def test_log_tail_returns_last_lines(clean_workdir):
    logdir = Path("logs")
    logdir.mkdir(exist_ok=True)
    (logdir / "bulk_downloader.log").write_text(
        "\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    out = ds.log_tail(10)
    assert out["exists"] is True
    assert out["line_count"] == 10
    assert out["lines"][-1] == "line 499"


# ── 6. effective settings ─────────────────────────────────────────

def test_effective_settings_shape():
    s = ds.effective_settings()
    assert "env" in s and "python" in s and "dev_mode" in s
    assert "BD_DISABLE_KEEPALIVE" in s["env"]
    assert isinstance(s["debug_flag_present"], bool)


# ── 7. config dump (redaction) ────────────────────────────────────

def test_config_dump_redacts_secrets():
    app_cfg = {"global_max_concurrent": 3, "api_token": "sk-secret123"}
    site_cfg = {"siteA": {"name": "siteA", "password": "hunter2",
                          "username": "bob"}}
    dump = ds.config_dump(app_cfg, site_cfg)
    assert dump["app_config"]["global_max_concurrent"] == 3
    assert dump["app_config"]["api_token"] == "***redacted***"
    assert dump["sites"]["siteA"]["password"] == "***redacted***"
    # non-secret fields survive
    assert dump["sites"]["siteA"]["name"] == "siteA"
    assert dump["sites"]["siteA"]["username"] == "bob"
    assert dump["site_count"] == 1


def test_config_dump_keeps_empty_secret_values_visible():
    # an empty password should not become the redaction marker
    dump = ds.config_dump({}, {"s": {"password": ""}})
    assert dump["sites"]["s"]["password"] == ""


# ── 8. process info ───────────────────────────────────────────────

def test_process_info_shape():
    info = ds.process_info()
    assert info["pid"] > 0
    assert info["thread_count"] >= 1
    assert info["uptime_seconds"] is None or info["uptime_seconds"] >= 0


# ── endpoints ─────────────────────────────────────────────────────

def test_dev_suite_endpoints_respond(fresh_app):
    for path in ("/api/dev/routes", "/api/dev/threads",
                 "/api/dev/db_stats", "/api/dev/runner_state",
                 "/api/dev/logtail", "/api/dev/env",
                 "/api/dev/config", "/api/dev/proc"):
        r = fresh_app.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"
        assert r.get_json() is not None, f"{path} returned no JSON"


def test_logtail_endpoint_honours_n(fresh_app):
    r = fresh_app.get("/api/dev/logtail?n=5")
    assert r.status_code == 200
    # no log file in the test workdir → exists False, still valid JSON
    assert "exists" in r.get_json()


# ── 9. invariant self-audit ───────────────────────────────────────

def test_invariant_audit_runs_all_checks(fresh_app):
    result = ds.invariant_audit()
    names = {c["check"] for c in result["checks"]}
    # the keeper no-resume invariant must always be checked and hold
    assert "keeper_no_resume" in names
    no_resume = next(c for c in result["checks"]
                     if c["check"] == "keeper_no_resume")
    assert no_resume["status"] == "ok", no_resume["detail"]
    # resolution-table and vault invariants must hold
    for name in ("resolution_tables_present", "vault_resolver_present",
                 "dispatch_method_present"):
        c = next(c for c in result["checks"] if c["check"] == name)
        assert c["status"] == "ok", f"{name}: {c['detail']}"
    assert "verdict" in result


# ── 10. template audit ────────────────────────────────────────────

def test_template_audit_all_templates_valid():
    result = ds.template_audit()
    # 27 -> 28: the PM-handoff 2026-09-06 template gap report added
    # login_africancasting (members.africancasting.com, ahd_ prefixed fields,
    # which no generic matcher reaches). Updated by RECOMPUTING the
    # population, never by loosening the pin to an inequality.
    assert result["template_count"] == 28
    # the v3.62.2 guards already enforce this — the tool must agree
    assert result["with_issues"] == 0, result["verdict"]


# ── 11. leak scan ─────────────────────────────────────────────────

def test_leak_scan_shape(clean_workdir):
    result = ds.leak_scan()
    assert "findings" in result and "verdict" in result
    assert isinstance(result["rate_limit_files"], list)


# ── 12. config integrity ──────────────────────────────────────────

def test_config_integrity_flags_empty_and_dup():
    cfg = {
        "siteA": {"url": "http://example.com"},
        "siteB": {"url": "http://example.com"},   # dup URL
        "siteC": {},                              # empty
        "siteD": {"url": "http://other.com",
                  "password": "@cred:my_label"},
    }
    result = ds.config_integrity(cfg)
    assert "siteC" in result["empty_configs"]
    assert "http://example.com" in result["duplicate_urls"]
    assert "@cred:my_label" in result["cred_references"]
    assert result["issues"]   # non-empty → problems found


def test_config_integrity_clean_config():
    result = ds.config_integrity({"s1": {"url": "http://a.com"}})
    assert result["issues"] == []
    assert "consistent" in result["verdict"]


# ── batch-B endpoints ─────────────────────────────────────────────

def test_validation_endpoints_respond(fresh_app):
    for path in ("/api/dev/invariants", "/api/dev/template_audit",
                 "/api/dev/leak_scan", "/api/dev/config_check"):
        r = fresh_app.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"
        assert r.get_json() is not None, f"{path} returned no JSON"


# ── 13. force GC ──────────────────────────────────────────────────

def test_force_gc_reports_freed():
    result = ds.force_gc()
    assert result["ok"] is True
    assert result["objects_before"] > 0
    assert "objects_freed" in result
    assert isinstance(result["unreachable_collected"], int)


# ── 14. WAL checkpoint ────────────────────────────────────────────

def test_wal_checkpoint_runs(fresh_app):
    result = ds.wal_checkpoint("TRUNCATE")
    # in the test env the DB may be in memory/wal — either way the
    # call must return a structured result, not raise
    assert "ok" in result
    if result["ok"]:
        assert result["mode"] == "TRUNCATE"
        assert "wal_bytes_after" in result


def test_wal_checkpoint_rejects_bad_mode():
    result = ds.wal_checkpoint("BOGUS")
    assert result["ok"] is False
    assert "mode" in result["error"].lower()


# ── 15. log level ─────────────────────────────────────────────────

def test_set_log_level_roundtrip():
    original = ds.get_log_level()["level"]
    try:
        res = ds.set_log_level("DEBUG")
        assert res["ok"] is True
        assert res["level"] == "DEBUG"
        assert ds.get_log_level()["level"] == "DEBUG"
    finally:
        ds.set_log_level(original)
    assert ds.get_log_level()["level"] == original


def test_set_log_level_rejects_unknown():
    res = ds.set_log_level("LOUD")
    assert res["ok"] is False
    assert "valid" in res


# ── 16a. SSE status ───────────────────────────────────────────────

def test_sse_status_shape(fresh_app):
    st = ds.sse_status()
    # no client connected in a test → zero, but the shape must hold
    assert "connected_clients" in st
    assert isinstance(st["clients"], list)


# ── 16b. SQL console ──────────────────────────────────────────────

def test_sql_console_runs_select(fresh_app):
    res = ds.sql_console("SELECT 1 AS one")
    assert res["ok"] is True
    assert res["columns"] == ["one"]
    assert res["rows"] == [[1]]


def test_sql_console_rejects_writes():
    for bad in ("DELETE FROM queue",
                "UPDATE queue SET status='x'",
                "DROP TABLE queue",
                "INSERT INTO queue VALUES (1)"):
        res = ds.sql_console(bad)
        assert res["ok"] is False, f"should reject: {bad}"


def test_sql_console_rejects_multiple_statements():
    res = ds.sql_console("SELECT 1; DROP TABLE queue")
    assert res["ok"] is False
    assert "single" in res["error"].lower()


def test_sql_console_rejects_pragma_and_non_select():
    assert ds.sql_console("PRAGMA journal_mode")["ok"] is False
    assert ds.sql_console("VACUUM")["ok"] is False
    assert ds.sql_console("")["ok"] is False


def test_sql_console_allows_columns_named_like_keywords(fresh_app):
    # a column literally named 'updated_at' must not trip the keyword
    # filter (token-boundary match, not substring)
    res = ds.sql_console("SELECT 1 AS updated_at, 2 AS created_at")
    assert res["ok"] is True
    assert res["columns"] == ["updated_at", "created_at"]


def test_sql_console_honours_limit(fresh_app):
    # a CTE that would yield many rows, capped by limit
    res = ds.sql_console(
        "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL "
        "SELECT n+1 FROM c WHERE n < 500) SELECT n FROM c", limit=10)
    assert res["ok"] is True
    assert res["row_count"] == 10
    assert res["truncated"] is True


# ── batch-C endpoints ─────────────────────────────────────────────

def test_maintenance_endpoints_respond(fresh_app):
    # GET endpoints
    assert fresh_app.get("/api/dev/sse_status").status_code == 200
    assert fresh_app.get("/api/dev/log_level").status_code == 200
    # POST: force GC
    r = fresh_app.post("/api/dev/gc")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    # POST: WAL checkpoint
    r = fresh_app.post("/api/dev/wal_checkpoint", json={"mode": "PASSIVE"})
    assert r.status_code == 200
    # POST: log level set + restore
    original = fresh_app.get("/api/dev/log_level").get_json()["level"]
    r = fresh_app.post("/api/dev/log_level", json={"level": "WARNING"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    fresh_app.post("/api/dev/log_level", json={"level": original})
    # POST: SQL console
    r = fresh_app.post("/api/dev/sql", json={"query": "SELECT 1 AS x"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    r = fresh_app.post("/api/dev/sql", json={"query": "DROP TABLE queue"})
    assert r.status_code == 400
