"""v3.66.671 -- RUN-6: per-site run-cost accounting (bytes + time + retries).

cost_economics already tracks $-subscription/ROI; daily_budget tracks bytes for
throttling. RUN-6's gap is a per-site *run accounting* rollup that also carries
time and retries. This cut adds `run_accounting_summary(site_id, window_days)`
and `run_accounting_all(...)`, aggregated from the queue ledger (terminal rows
retain retries / file_size / ts_added / ts_updated until "Clear Done"):

  * bytes       = SUM(file_size)         (exact)
  * retries     = SUM(retries)           (exact)
  * runs        = COUNT(terminal rows)   (exact)
  * elapsed_seconds = SUM(ts_updated - ts_added)  (enqueue->terminal wall proxy)

Rolled into report_all so the ROI dashboard carries them. Window-filtered on
ts_updated. Isolated temp DB; zero-arg tests.
"""
from __future__ import annotations

import tempfile
import time
from datetime import datetime, timedelta

import bulk_downloader.db as db
from bulk_downloader import cost_economics as ce


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _isolated_db():
    db.DB_PATH = tempfile.mktemp(prefix="run6_", suffix=".db")
    db.db_init()


def _seed_queue(site_id, url, *, status, retries, file_size, added, updated):
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO queue(site_id,url,status,retries,file_size,ts_added,ts_updated) "
            "VALUES(?,?,?,?,?,?,?)",
            (site_id, url, status, retries, file_size, _iso(added), _iso(updated)))


def test_run_accounting_summary_aggregates_bytes_retries_runs():
    saved = db.DB_PATH
    try:
        _isolated_db()
        now = datetime.utcnow()
        _seed_queue("s1", "u1", status="done", retries=1, file_size=1000,
                    added=now - timedelta(seconds=30), updated=now - timedelta(seconds=20))
        _seed_queue("s1", "u2", status="failed", retries=3, file_size=500,
                    added=now - timedelta(seconds=60), updated=now - timedelta(seconds=40))
        _seed_queue("s1", "u3", status="pending", retries=0, file_size=99,
                    added=now, updated=now)  # non-terminal -> excluded

        s = ce.run_accounting_summary("s1", window_days=30)
        assert s["runs"] == 2, s
        assert s["bytes"] == 1500, s
        assert s["retries"] == 4, s
        assert s["elapsed_seconds"] >= 10, s   # ~10s + ~20s of enqueue->terminal
    finally:
        db.DB_PATH = saved


def test_run_accounting_window_excludes_old_rows():
    saved = db.DB_PATH
    try:
        _isolated_db()
        now = datetime.utcnow()
        _seed_queue("s1", "recent", status="done", retries=0, file_size=100,
                    added=now - timedelta(days=1), updated=now - timedelta(days=1))
        _seed_queue("s1", "old", status="done", retries=0, file_size=9999,
                    added=now - timedelta(days=90), updated=now - timedelta(days=90))

        s = ce.run_accounting_summary("s1", window_days=30)
        assert s["runs"] == 1 and s["bytes"] == 100, s
    finally:
        db.DB_PATH = saved


def test_run_accounting_all_lists_sites():
    saved = db.DB_PATH
    try:
        _isolated_db()
        now = datetime.utcnow()
        _seed_queue("sa", "u", status="done", retries=0, file_size=10,
                    added=now, updated=now)
        _seed_queue("sb", "u", status="done", retries=2, file_size=20,
                    added=now, updated=now)
        rows = ce.run_accounting_all(window_days=30)
        by = {r["site_id"]: r for r in rows}
        assert by["sa"]["bytes"] == 10 and by["sb"]["retries"] == 2, rows
    finally:
        db.DB_PATH = saved
