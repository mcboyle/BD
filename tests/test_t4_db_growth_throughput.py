"""T4 (Tier 3) — D-10 DB-growth grapher + D-15 queue-throughput meter.
Two read-only DB time-series tools in Group B. D-10 reports the
current DB size breakdown plus a forward growth projection from the
recent history-row arrival rate; D-15 buckets completed history rows
into a throughput series. Neither uses a background sampler — both
derive their series from data already in the DB.
"""
import datetime

from bulk_downloader import db
from bulk_downloader import dev_suite as ds


def _log_at(site_id, url, status, ts):
    """Insert a history row with an explicit UTC ts (db_log stamps
    'now', so a custom past/dated ts needs a direct insert)."""
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id,site_name,url,status,ts) "
            "VALUES(?,?,?,?,?)",
            (site_id, site_id, url, status, ts))


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── D-10 db_growth_report ──────────────────────────────────────────

def test_db_growth_empty_db(clean_workdir):
    db.db_init()
    r = ds.db_growth_report()
    assert r["ok"] is True
    assert r["tool"] == "db_growth_report"
    assert r["total_bytes"] >= 0
    assert "history" in r["row_counts"]
    assert r["history_rows_total"] == 0
    # nothing to project from on an empty DB — must be honest, not 0
    assert r["projected_growth_mb_30d"] is None
    assert "not projectable" in r["verdict"]


def test_db_growth_reports_size_breakdown(clean_workdir):
    db.db_init()
    db.db_log("s", "S", "u1", "done")
    r = ds.db_growth_report()
    assert set(r["size_bytes"].keys()) == {"db", "wal", "shm"}
    # the .db file itself must be non-empty after init + a write
    assert r["size_bytes"]["db"] > 0
    assert r["total_mb"] == round(r["total_bytes"] / (1024 * 1024), 3)


def test_db_growth_projects_from_recent_rate(clean_workdir):
    db.db_init()
    now = datetime.datetime.utcnow()
    # 14 rows spread one-per-day over the look-back window -> 1 row/day
    for i in range(14):
        _log_at("s", f"u{i}", "done",
                _iso(now - datetime.timedelta(days=i)))
    r = ds.db_growth_report(days=14)
    assert r["history_rows_in_window"] == 14
    assert r["history_rows_per_day"] == 1.0
    assert r["est_bytes_per_history_row"] is not None
    # projection is rate(1/day) * 30 * bytes-per-row -> positive
    assert r["projected_growth_mb_30d"] > 0
    assert "projected" in r["verdict"]


def test_db_growth_clamps_days(clean_workdir):
    db.db_init()
    assert ds.db_growth_report(days=99999)["lookback_days"] == 365
    assert ds.db_growth_report(days="bad")["lookback_days"] == 14


# ── D-15 queue_throughput ──────────────────────────────────────────

def test_throughput_empty_history(clean_workdir):
    db.db_init()
    r = ds.queue_throughput()
    assert r["ok"] is True
    assert r["tool"] == "queue_throughput"
    assert r["total_completed"] == 0
    assert r["series"] == []
    assert r["peak_per_bucket"] == 0
    assert r["by_status"] == {}


def test_throughput_buckets_by_hour(clean_workdir):
    db.db_init()
    now = datetime.datetime.utcnow()
    # 3 rows this hour, 1 row an hour ago -> two hourly buckets
    for i in range(3):
        _log_at("s", f"now{i}", "done", _iso(now))
    _log_at("s", "prev", "done", _iso(now - datetime.timedelta(hours=1)))
    r = ds.queue_throughput(hours=6, bucket="hour")
    assert r["total_completed"] == 4
    assert r["bucket_count"] == 2
    assert r["peak_per_bucket"] == 3


def test_throughput_buckets_by_day(clean_workdir):
    db.db_init()
    now = datetime.datetime.utcnow()
    _log_at("s", "a", "done", _iso(now))
    _log_at("s", "b", "done", _iso(now - datetime.timedelta(days=1)))
    r = ds.queue_throughput(hours=24 * 7, bucket="day")
    assert r["bucket"] == "day"
    assert r["total_completed"] == 2
    assert r["bucket_count"] == 2


def test_throughput_window_excludes_old_rows(clean_workdir):
    db.db_init()
    now = datetime.datetime.utcnow()
    _log_at("s", "recent", "done", _iso(now))
    # a row 48h old must fall outside a 24h window
    _log_at("s", "stale", "done",
            _iso(now - datetime.timedelta(hours=48)))
    r = ds.queue_throughput(hours=24)
    assert r["total_completed"] == 1


def test_throughput_status_split(clean_workdir):
    db.db_init()
    now = datetime.datetime.utcnow()
    _log_at("s", "ok1", "done", _iso(now))
    _log_at("s", "ok2", "done", _iso(now))
    _log_at("s", "bad", "error", _iso(now))
    r = ds.queue_throughput(hours=6)
    assert r["by_status"]["done"] == 2
    assert r["by_status"]["error"] == 1


def test_throughput_site_filter(clean_workdir):
    db.db_init()
    now = datetime.datetime.utcnow()
    _log_at("siteA", "a1", "done", _iso(now))
    _log_at("siteB", "b1", "done", _iso(now))
    r = ds.queue_throughput(hours=6, site_id="siteA")
    assert r["site_filter"] == "siteA"
    assert r["total_completed"] == 1


def test_throughput_clamps_hours(clean_workdir):
    db.db_init()
    assert ds.queue_throughput(hours=999999)["lookback_hours"] == 24 * 90
    assert ds.queue_throughput(hours="bad")["lookback_hours"] == 24


# ── dev routes ─────────────────────────────────────────────────────

def test_db_growth_route(fresh_app):
    db.db_init()
    j = fresh_app.get("/api/dev/db_growth?days=7").get_json()
    assert j["ok"] is True
    assert j["lookback_days"] == 7
    assert "size_bytes" in j


def test_queue_throughput_route(fresh_app):
    db.db_init()
    j = fresh_app.get(
        "/api/dev/queue_throughput?hours=12&bucket=day").get_json()
    assert j["ok"] is True
    assert j["bucket"] == "day"
    assert "series" in j


def test_t4_routes_are_dev_gated():
    import os
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/db_growth", "/api/dev/queue_throughput"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
