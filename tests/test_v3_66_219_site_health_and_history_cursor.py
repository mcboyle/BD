"""v3.66.219 — F2-a (F2.1 failure clustering + F2.2 per-site health) and
F4.4 (history cursor pagination, additive).

Custom runner: zero-arg functions, no pytest builtins. DB-backed tests run
against the runner's per-run BD_HOME (db_init creates the tables); each test
seeds only the rows it needs. No real clock/disk/network dependence.
"""

import time

from bulk_downloader import db
from bulk_downloader import app_data_layer as D


def _fresh_db():
    db.db_init()
    # Clear the two tables these tests touch so repeated runs are clean.
    with db.db_conn() as cx:
        cx.execute("DELETE FROM session_history")
        try:
            cx.execute("DELETE FROM history")
        except Exception:
            pass


# ── F2.1 failure clustering ──────────────────────────────────────────

def test_failure_clusters_counts_and_order():
    _fresh_db()
    for et in ("login", "heartbeat_ok",
               "heartbeat_fail", "heartbeat_fail", "heartbeat_fail",
               "needs_takeover"):
        db.session_event_record("siteA", 0, et, "")
    db.session_event_record("siteB", 0, "auto_relogin_fail", "")
    fc = db.db_session_failure_clusters(7)
    assert fc["total_failures"] == 5, fc["total_failures"]
    cl = fc["clusters"]
    # Most urgent (highest count) first.
    assert cl[0]["site_id"] == "siteA"
    assert cl[0]["event_type"] == "heartbeat_fail"
    assert cl[0]["count"] == 3
    # Success events are not clustered.
    types = {(c["site_id"], c["event_type"]) for c in cl}
    assert ("siteA", "heartbeat_ok") not in types
    assert ("siteA", "login") not in types


def test_failure_clusters_per_site_denominator():
    _fresh_db()
    for et in ("login", "heartbeat_ok", "heartbeat_ok", "heartbeat_fail"):
        db.session_event_record("s", 0, et, "")
    fc = db.db_session_failure_clusters(7)
    ps = fc["per_site"]["s"]
    assert ps["failures"] == 1
    assert ps["successes"] == 3
    assert ps["by_type"]["heartbeat_fail"] == 1


def test_failure_clusters_window_excludes_old():
    _fresh_db()
    # Insert one ancient failure directly with an old ts.
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO session_history(ts, site_id, account_idx, event_type, detail) "
            "VALUES(?,?,?,?,?)",
            (time.time() - 40 * 86400, "old", 0, "heartbeat_fail", ""))
    db.session_event_record("recent", 0, "heartbeat_fail", "")
    fc = db.db_session_failure_clusters(7)
    sites = {c["site_id"] for c in fc["clusters"]}
    assert "recent" in sites
    assert "old" not in sites


def test_failure_clusters_missing_table_failsoft():
    # Even if the table read raises, the helper returns the empty shape.
    db.db_init()
    fc = db.db_session_failure_clusters(7)
    assert set(fc.keys()) >= {"clusters", "per_site", "total_failures"}
    assert isinstance(fc["clusters"], list)


# ── F2.2 per-site health scoring ─────────────────────────────────────

def test_site_health_worst_first_and_labels():
    _fresh_db()
    # churny site: 4 failures -> lower score
    for et in ("login", "heartbeat_fail", "heartbeat_fail",
               "heartbeat_fail", "needs_takeover"):
        db.session_event_record("churny", 0, et, "")
    # clean site: only successes
    for et in ("login", "heartbeat_ok", "heartbeat_ok"):
        db.session_event_record("clean", 0, et, "")
    out = D.collect_site_health(lookback_days=7)
    assert out["site_count"] == 2
    # worst (lowest score) first
    assert out["sites"][0]["site_id"] == "churny"
    assert out["sites"][0]["health_score"] <= out["sites"][1]["health_score"]
    # labels derive from score thresholds
    for s in out["sites"]:
        if s["health_score"] < 40:
            assert s["health_label"] == "critical"
        elif s["health_score"] < 75:
            assert s["health_label"] == "warn"
        else:
            assert s["health_label"] == "ok"


def test_site_health_unchecked_site_cannot_outrank_dominant_failure_cluster():
    _fresh_db()
    # Reproduce the live F2a mismatch: an unchecked red site scores 40 while a
    # site with a dominant failure cluster scores 50.  Numeric score alone must
    # not let the no-data site become the report's "worst site".
    with db.db_conn() as cx:
        cx.execute(
            "CREATE TABLE IF NOT EXISTS auth_health ("
            "site_id TEXT PRIMARY KEY, status TEXT NOT NULL, last_check_ts REAL, "
            "last_green_ts REAL, last_http_code INTEGER, note TEXT DEFAULT '', "
            "response_url TEXT DEFAULT '')")
        cx.execute(
            "INSERT OR REPLACE INTO auth_health(site_id, status, last_check_ts) "
            "VALUES(?,?,?)", ("unchecked", "red", None))
    for _ in range(93):
        db.session_event_record("dominant_failure", 0, "heartbeat_fail", "")

    out = D.collect_site_health(lookback_days=7)
    sites = {site["site_id"]: site for site in out["sites"]}

    assert sites["unchecked"]["health_score"] == 40
    assert sites["unchecked"]["failures"] == 0
    assert sites["unchecked"]["last_check_age_sec"] is None
    assert sites["dominant_failure"]["health_score"] == 50
    assert sites["dominant_failure"]["failures"] == 93
    assert out["clusters"][0]["site_id"] == "dominant_failure"
    assert out["sites"][0]["site_id"] == "dominant_failure"


def test_site_health_score_clamped_and_deterministic():
    _fresh_db()
    # A genuinely critical site: red auth-health AND failures. The 4-input
    # v1 model is intentionally bounded (failure count alone caps at -40),
    # so reaching "critical" (<40) requires the red-color input (-60).
    with db.db_conn() as cx:
        cx.execute(
            "CREATE TABLE IF NOT EXISTS auth_health ("
            "site_id TEXT PRIMARY KEY, status TEXT NOT NULL, last_check_ts REAL, "
            "last_green_ts REAL, last_http_code INTEGER, note TEXT DEFAULT '', "
            "response_url TEXT DEFAULT '')")
        cx.execute(
            "INSERT OR REPLACE INTO auth_health(site_id, status, last_check_ts) "
            "VALUES(?,?,?)", ("ruined", "red", time.time()))
    for _ in range(50):
        db.session_event_record("ruined", 0, "heartbeat_fail", "")
    a = D.collect_site_health(lookback_days=7)
    b = D.collect_site_health(lookback_days=7)
    sa = [x for x in a["sites"] if x["site_id"] == "ruined"][0]
    sb = [x for x in b["sites"] if x["site_id"] == "ruined"][0]
    # Score/label are deterministic; last_check_age_sec is "age as of now"
    # and legitimately advances between calls, so compare the scored fields.
    assert sa["health_score"] == sb["health_score"]
    assert sa["health_label"] == sb["health_label"]
    assert a["clusters"] == b["clusters"]
    s = sa
    assert 0 <= s["health_score"] <= 100
    assert s["health_label"] == "critical"


def test_site_health_fail_rate():
    _fresh_db()
    for et in ("login", "heartbeat_ok", "heartbeat_fail"):
        db.session_event_record("r", 0, et, "")
    out = D.collect_site_health(lookback_days=7)
    s = [x for x in out["sites"] if x["site_id"] == "r"][0]
    # 1 failure / (1 fail + 2 success) = 1/3
    assert abs(s["fail_rate"] - (1.0 / 3.0)) < 1e-9


def test_site_health_empty_is_clean():
    _fresh_db()
    out = D.collect_site_health(lookback_days=7)
    assert out["site_count"] == 0
    assert out["clusters"] == []
    assert out["total_failures"] == 0


# ── F4.4 history cursor pagination (additive) ────────────────────────

def _client():
    from bulk_downloader import app as A
    A.db_init()
    return A.app.test_client()


def test_history_bare_array_contract_unchanged():
    c = _client()
    r = c.get("/api/history")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)  # legacy bare-array contract


def test_history_paginate_returns_envelope():
    c = _client()
    r = c.get("/api/history?paginate=1&limit=25")
    assert r.status_code == 200
    j = r.get_json()
    assert isinstance(j, dict)
    assert set(j.keys()) == {"rows", "next_cursor"}
    assert isinstance(j["rows"], list)


def test_history_cursor_strictly_older():
    # Seed >limit rows so a real cursor is returned, then page through.
    _fresh_db()
    with db.db_conn() as cx:
        for i in range(5):
            cx.execute(
                "INSERT INTO history(site_id, site_name, url, status, ts) "
                "VALUES(?,?,?,?,?)",
                ("s", "s", "u%d" % i, "done",
                 "2026-01-01T00:00:0%d" % i))
    c = _client()
    r1 = c.get("/api/history?paginate=1&limit=2")
    j1 = r1.get_json()
    assert len(j1["rows"]) == 2
    assert j1["next_cursor"] is not None
    first_ids = [row["id"] for row in j1["rows"]]
    r2 = c.get("/api/history?cursor=%d&limit=2" % j1["next_cursor"])
    j2 = r2.get_json()
    # strictly-older: every id on page 2 is < the cursor
    for row in j2["rows"]:
        assert row["id"] < j1["next_cursor"]
        assert row["id"] not in first_ids


def test_history_cursor_end_returns_none():
    c = _client()
    # A cursor below any real id yields an empty final page.
    r = c.get("/api/history?cursor=1&limit=10")
    j = r.get_json()
    assert j["next_cursor"] is None


def test_history_bad_cursor_falls_back():
    c = _client()
    r = c.get("/api/history?cursor=notanint&limit=5")
    assert r.status_code == 200
    j = r.get_json()
    # bad cursor -> treated as first page (after_id None), still an envelope
    assert set(j.keys()) == {"rows", "next_cursor"}
