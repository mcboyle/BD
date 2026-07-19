"""U23 — slow-query profiler (D-1) + index advisor (D-2). Both run
over the shared _HOT_QUERIES set against the live DB schema.
"""
from bulk_downloader import db
from bulk_downloader import dev_suite as ds


# ── slow-query profiler (D-1) ──────────────────────────────────────

def test_profiler_times_every_hot_query(clean_workdir):
    db.db_init()
    r = ds.slow_query_profiler(iterations=3)
    assert r["ok"] is True
    assert r["queries_profiled"] == len(ds._HOT_QUERIES)
    for q in r["results"]:
        # every query must run cleanly against the real schema
        assert "error" not in q, q
        assert "median_ms" in q
    # empty sandbox DB -> nothing slow
    assert r["slow_queries"] == []
    assert r["verdict"] == "all hot queries fast"


def test_profiler_clamps_iterations(clean_workdir):
    db.db_init()
    assert ds.slow_query_profiler(iterations=99999)["iterations"] == 50
    assert ds.slow_query_profiler(iterations="bad")["iterations"] == 5


def test_profiler_slow_ms_threshold_can_flag(clean_workdir):
    db.db_init()
    # a 0ms threshold makes every measurable query count as "slow" —
    # proves the slow-flag path is wired, not just the fast path
    r = ds.slow_query_profiler(iterations=3, slow_ms=0.0)
    assert len(r["slow_queries"]) >= 1


# ── index advisor (D-2) ────────────────────────────────────────────

def test_advisor_lists_indexes_for_every_table(clean_workdir):
    db.db_init()
    r = ds.index_advisor()
    assert r["ok"] is True
    for tbl in ("history", "queue", "session_history"):
        assert r["tables"][tbl]["index_count"] >= 1


def test_advisor_every_hot_query_uses_an_index(clean_workdir):
    db.db_init()
    r = ds.index_advisor()
    # the hot queries are matched to db.py's indexes by design
    assert r["full_scan_queries"] == []
    assert r["errored_queries"] == []
    assert r["advice"] == "every hot query uses an index"
    for p in r["hot_query_plans"]:
        assert p.get("uses_index") is True, p


# ── scan detection is non-vacuous ──────────────────────────────────

def test_explain_flags_a_genuine_full_scan(clean_workdir):
    db.db_init()
    with db.db_conn() as cx:
        # history.message has no index -> must be a full scan
        scan = ds._explain_query_plan(
            cx, "SELECT * FROM history WHERE message = ?", ("x",))
        # history.status IS indexed -> must NOT be a full scan
        idx = ds._explain_query_plan(
            cx, "SELECT * FROM history WHERE status = ?", ("ok",))
    assert scan["full_scan"] is True
    assert idx["full_scan"] is False
    assert idx["uses_index"] is True


# ── routes ─────────────────────────────────────────────────────────

def test_slow_queries_route(fresh_app):
    body = fresh_app.get("/api/dev/slow_queries?iterations=2").get_json()
    assert body["ok"] is True
    assert body["queries_profiled"] == len(ds._HOT_QUERIES)


def test_index_advisor_route(fresh_app):
    body = fresh_app.get("/api/dev/index_advisor").get_json()
    assert body["ok"] is True
    assert "tables" in body and "hot_query_plans" in body
