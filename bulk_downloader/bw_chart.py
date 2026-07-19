"""Bandwidth visualizer (Phase 156, Block Q).

Time-series chart data for the operator dashboard. Three series:

  • bytes_per_hour — bytes downloaded grouped by hour over the last N days
  • jobs_per_hour — completed jobs per hour
  • failure_rate — % failures per hour

Returns plot-ready data structures (labels + values arrays). Designed
for the operator's preferred chart lib (Chart.js, Recharts).
"""
from __future__ import annotations

import datetime
import time
from typing import Optional


def hourly_bandwidth(*, hours: int = 168,
                    site_id: Optional[str] = None) -> dict:
    """Return bytes-per-hour grouped over the last `hours` hours.
    Default 168 = 1 week."""
    try:
        from . import db as _db
        sql = """SELECT strftime('%Y-%m-%d %H:00', ts) AS hour_bucket,
                        COALESCE(SUM(file_size), 0) AS bytes
                  FROM history
                  WHERE status = 'done'
                    AND ts >= datetime('now', ?)"""
        params: list = [f"-{int(hours)} hours"]
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " GROUP BY hour_bucket ORDER BY hour_bucket ASC"
        with _db.db_conn() as cx:
            rows = cx.execute(sql, params).fetchall()
    except Exception:
        return {"labels": [], "bytes": [], "error": "DB"}
    # Build dense series (fill missing hours with 0).
    #
    # v3.62.1 BUGFIX: the SQL above buckets rows with SQLite's
    # strftime(... 'ts'), and the ts column is stamped by SQLite's
    # datetime('now') / strftime('now') -- which is UTC. The dense
    # series below must therefore also be built in UTC, or the lookup
    # keys won't match the SQL bucket keys on any machine whose local
    # time differs from UTC (every non-UTC timezone). Previously this
    # used datetime.datetime.now() (local time), so on a UTC-offset
    # machine every buckets.get() missed and total_bytes came back 0.
    # Use utcnow() to match the database.
    buckets: dict = {(r[0] if not hasattr(r, "keys") else r["hour_bucket"]):
                     int(r[1] if not hasattr(r, "keys") else r["bytes"])
                     for r in rows}
    now = datetime.datetime.now(datetime.timezone.utc).replace(
        minute=0, second=0, microsecond=0, tzinfo=None)
    labels = []
    values = []
    for i in range(hours, 0, -1):
        ts = now - datetime.timedelta(hours=i - 1)
        key = ts.strftime("%Y-%m-%d %H:00")
        labels.append(key)
        values.append(buckets.get(key, 0))
    return {
        "labels": labels,
        "bytes": values,
        "mb": [round(v / (1024**2), 1) for v in values],
        "gb": [round(v / (1024**3), 2) for v in values],
        "site_id": site_id,
        "total_bytes": sum(values),
    }


def hourly_jobs(*, hours: int = 168,
               site_id: Optional[str] = None) -> dict:
    """Jobs-per-hour, split by outcome (done / failed / needs_review)."""
    try:
        from . import db as _db
        sql = """SELECT strftime('%Y-%m-%d %H:00', ts) AS h,
                        status,
                        COUNT(*) AS n
                  FROM history
                  WHERE ts >= datetime('now', ?)
                    AND status IN ('done', 'failed', 'needs_review')"""
        params: list = [f"-{int(hours)} hours"]
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " GROUP BY h, status ORDER BY h ASC"
        with _db.db_conn() as cx:
            rows = cx.execute(sql, params).fetchall()
    except Exception:
        return {"labels": [], "done": [], "failed": [], "needs_review": []}
    buckets: dict = {}
    for r in rows:
        h = r[0] if not hasattr(r, "keys") else r["h"]
        s = r[1] if not hasattr(r, "keys") else r["status"]
        n = int(r[2] if not hasattr(r, "keys") else r["n"])
        buckets.setdefault(h, {})[s] = n
    # v3.62.1 BUGFIX: same UTC/local mismatch as hourly_bandwidth -
    # SQL buckets in UTC (SQLite ts), so build the dense series in UTC.
    now = datetime.datetime.now(datetime.timezone.utc).replace(
        minute=0, second=0, microsecond=0, tzinfo=None)
    labels, done_s, fail_s, rev_s = [], [], [], []
    for i in range(hours, 0, -1):
        ts = now - datetime.timedelta(hours=i - 1)
        key = ts.strftime("%Y-%m-%d %H:00")
        b = buckets.get(key, {})
        labels.append(key)
        done_s.append(b.get("done", 0))
        fail_s.append(b.get("failed", 0))
        rev_s.append(b.get("needs_review", 0))
    return {
        "labels": labels,
        "done": done_s,
        "failed": fail_s,
        "needs_review": rev_s,
        "site_id": site_id,
    }


def hourly_failure_rate(*, hours: int = 168,
                       site_id: Optional[str] = None) -> dict:
    """Percent failure per hour."""
    jobs = hourly_jobs(hours=hours, site_id=site_id)
    rates = []
    for i in range(len(jobs["labels"])):
        total = jobs["done"][i] + jobs["failed"][i] + jobs["needs_review"][i]
        if total == 0:
            rates.append(None)
        else:
            rates.append(round(100 * jobs["failed"][i] / total, 1))
    return {
        "labels": jobs["labels"],
        "failure_pct": rates,
        "site_id": site_id,
    }


def site_share(*, hours: int = 168) -> dict:
    """Pie-chart data: each site's share of total downloads."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT site_id,
                                        COALESCE(SUM(file_size), 0) AS b,
                                        COUNT(*) AS n
                                  FROM history
                                  WHERE status = 'done'
                                    AND ts >= datetime('now', ?)
                                  GROUP BY site_id
                                  ORDER BY b DESC""",
                              (f"-{int(hours)} hours",)).fetchall()
        return {
            "sites": [
                {"site_id": r[0] if not hasattr(r, "keys") else r["site_id"],
                 "bytes": int(r[1] if not hasattr(r, "keys") else r[1]),
                 "count": int(r[2] if not hasattr(r, "keys") else r[2])}
                for r in rows
            ],
            "since_hours": hours,
        }
    except Exception:
        return {"sites": []}


def summary_card(*, hours: int = 24) -> dict:
    """One-call summary card data: total bytes, total jobs, fail rate,
    busiest hour."""
    bw = hourly_bandwidth(hours=hours)
    jobs = hourly_jobs(hours=hours)
    total_done = sum(jobs["done"])
    total_failed = sum(jobs["failed"])
    fail_pct = (100 * total_failed / max(1, total_done + total_failed))
    # Busiest hour
    if bw["bytes"]:
        peak_idx = bw["bytes"].index(max(bw["bytes"]))
        peak_label = bw["labels"][peak_idx]
        peak_gb = bw["gb"][peak_idx]
    else:
        peak_label, peak_gb = None, 0
    return {
        "total_bytes": bw["total_bytes"],
        "total_gb": round(bw["total_bytes"] / (1024**3), 2),
        "total_done": total_done,
        "total_failed": total_failed,
        "failure_rate_pct": round(fail_pct, 1),
        "peak_hour": peak_label,
        "peak_hour_gb": peak_gb,
        "since_hours": hours,
    }
