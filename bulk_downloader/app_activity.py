"""activity API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/activity views moved onto a Flask Blueprint.
Endpoint labels gain a "activity." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request
from .db import db_conn

activity_bp = Blueprint("activity", __name__)

def _m2_activity_query_fragments(*_a, **_k):
    """Delegate to app._m2_activity_query_fragments at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_activity_query_fragments")(*_a, **_k)

def _m2_avatar_color(*_a, **_k):
    """Delegate to app._m2_avatar_color at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_avatar_color")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@activity_bp.route("/api/activity/v2")
def api_activity_v2():
    """SPA-shaped activity list with delta_vs_prev_period.

    Params:
      window=24h|7d|30d|all  (default 24h)
      q=<substring>          optional substring; matches against
                             url/filename/message via LIKE.

    Returns recent completions (capped at 200) plus aggregate counts
    for the current and previous period. The previous-period count
    deliberately excludes the q filter — the delta is "how busy was
    this period vs. last period", not "how many hits to this search
    over time" (the latter is meaningless when q is empty across a
    window with no q-side history).
    """
    s_cfg = _app_s_cfg()
    import sqlite3 as _sqlite3
    window = (request.args.get("window") or "24h").lower()
    q = (request.args.get("q") or "").strip()
    window_days = {"24h": 1, "7d": 7, "30d": 30, "all": None}.get(window)
    if window not in {"24h", "7d", "30d", "all"}:
        return jsonify({"ok": False,
                        "error": f"invalid window: {window!r}"}), 400
    try:
        where, params = _m2_activity_query_fragments(window_days, q)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with db_conn() as cx:
            rows = cx.execute(
                "SELECT id, ts, site_id, filename, status, file_size, message "
                f"  FROM history {where_sql} "
                " ORDER BY ts DESC LIMIT 200",
                params,
            ).fetchall()
            # current_count uses the SAME filter as the row list, so
            # "showing 23 of 178" is a coherent statement.
            current_count = cx.execute(
                f"SELECT COUNT(*) FROM history {where_sql}",
                params,
            ).fetchone()[0] or 0
            # prev_count: ignore q, just the prior-period window count.
            # Used for the "vs previous period" delta on the totals
            # widget. Mixing q in here would be confusing — "23 vs 0
            # last period" would shout "search matched lots of things"
            # not "the system did less yesterday".
            if window_days is None:
                prev_count = None
            else:
                prev_count = cx.execute(
                    "SELECT COUNT(*) FROM history "
                    "  WHERE ts >= datetime('now', ?) "
                    "    AND ts <  datetime('now', ?)",
                    (f"-{window_days * 2} days", f"-{window_days} days")
                ).fetchone()[0] or 0
        items = []
        for r in rows:
            r = dict(r)
            sid = r.get("site_id") or ""
            cfg = s_cfg.get(sid, {}) or {}
            name = cfg.get("name") or sid
            r["site_name"] = name
            r["avatar_color"] = _m2_avatar_color(name)
            items.append(r)
        if prev_count is None:
            delta_abs = None
            delta_pct = None
        else:
            # Compare unfiltered current vs unfiltered prev so the delta
            # represents system activity, not search-result counts. The
            # unfiltered current count is a single extra query — cheap.
            if q:
                with db_conn() as cx:
                    unfiltered_current = cx.execute(
                        "SELECT COUNT(*) FROM history "
                        "  WHERE ts >= datetime('now', ?)",
                        (f"-{window_days} days",)
                    ).fetchone()[0] or 0
                base_for_delta = unfiltered_current
            else:
                base_for_delta = current_count
            delta_abs = base_for_delta - prev_count
            delta_pct = (round(100.0 * delta_abs / prev_count, 1)
                          if prev_count > 0 else None)
        return jsonify({
            "ok": True,
            "window": window,
            "q": q,
            "count_current_period": current_count,
            "count_prev_period": prev_count,
            "delta_abs": delta_abs,
            "delta_pct": delta_pct,
            "items": items,
        })
    except _sqlite3.Error as e:
        return jsonify({"ok": False,
                        "error": f"db_error: {type(e).__name__}: {e}"}), 503
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 503
@activity_bp.route("/api/activity/v2/export.csv")
def api_activity_v2_export_csv():
    """CSV export of the activity list. Same filters as
    /api/activity/v2 (window, q) but NOT capped at 200 — the export
    is the "give me everything that matched" affordance the JSON
    endpoint cannot be.

    Header includes id/ts/site/filename/status/size/message in that
    order. Content-Disposition makes the browser save the file."""
    s_cfg = _app_s_cfg()
    import csv
    import io
    import sqlite3 as _sqlite3
    window = (request.args.get("window") or "24h").lower()
    q = (request.args.get("q") or "").strip()
    window_days = {"24h": 1, "7d": 7, "30d": 30, "all": None}.get(window)
    if window not in {"24h", "7d", "30d", "all"}:
        return jsonify({"ok": False,
                        "error": f"invalid window: {window!r}"}), 400
    # Hard cap on rows even on export — 100k rows is the practical
    # ceiling for an in-memory CSV. The SPA can paginate via
    # /api/history/search for bulk DB exports beyond this.
    EXPORT_CAP = 100_000
    try:
        where, params = _m2_activity_query_fragments(window_days, q)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with db_conn() as cx:
            rows = cx.execute(
                "SELECT id, ts, site_id, filename, status, file_size, message "
                f"  FROM history {where_sql} "
                f" ORDER BY ts DESC LIMIT {EXPORT_CAP}",
                params,
            ).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "ts", "site_id", "site_name",
                    "filename", "status", "file_size_bytes", "message"])
        for r in rows:
            r = dict(r)
            sid = r.get("site_id") or ""
            cfg = s_cfg.get(sid, {}) or {}
            name = cfg.get("name") or sid
            w.writerow([
                r.get("id"), r.get("ts"), sid, name,
                r.get("filename"), r.get("status"),
                r.get("file_size") or 0,
                (r.get("message") or "")[:500],
            ])
        body = buf.getvalue()
        import time as _t
        fname = f"bulkdl-activity-{window}-{int(_t.time())}.csv"
        resp = Response(body, mimetype="text/csv; charset=utf-8")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{fname}"'
        )
        return resp
    except _sqlite3.Error as e:
        return jsonify({"ok": False,
                        "error": f"db_error: {type(e).__name__}: {e}"}), 503
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 503
@activity_bp.route("/api/activity/v2/site_health")
def api_activity_v2_site_health():
    """Per-site daily completion counts. Query params:
      days   (default 14, max 90) — window size in calendar days
      status (default '*', the literal string) — '*' counts all,
             otherwise a single status name to filter on.

    Returns {ok, days, status, by_site: {site_id: [n0, n1, ..., nN]}}
    where the array runs OLDEST → NEWEST and has length == days. A
    site with no activity in the window does not appear in by_site.
    """
    from datetime import datetime, timedelta, timezone
    try:
        days = max(1, min(90, int(request.args.get("days", 14))))
    except (TypeError, ValueError):
        days = 14
    status = (request.args.get("status") or "*").strip()
    # Allow-list for the status filter — never interpolate into SQL.
    ALLOWED_STATUSES = ("done", "failed", "stopped", "needs_review", "*")
    if status not in ALLOWED_STATUSES:
        return jsonify({
            "ok": False,
            "error": f"unknown status: {status!r}",
            "allowed": list(ALLOWED_STATUSES),
        }), 400
    # Build the UTC ISO timestamp for the lower bound (inclusive).
    now_utc = datetime.now(timezone.utc)
    # Use date-only boundary: "today" minus days-1 (so a 14-day window
    # gives 14 calendar days inclusive of today). We compute the
    # cutoff at midnight UTC of the start day so the SQL filter is a
    # simple `ts >= ?`.
    start_date = (now_utc - timedelta(days=days - 1)).date()
    cutoff_iso = f"{start_date.isoformat()}T00:00:00"
    # Pre-build the canonical day list so we can fill zeros.
    day_keys = [
        (start_date + timedelta(days=i)).isoformat()
        for i in range(days)
    ]
    by_site: dict[str, list[int]] = {}
    try:
        from .db import db_conn
        with db_conn() as cx:
            if status == "*":
                rows = cx.execute(
                    """SELECT site_id, substr(ts, 1, 10) AS day, COUNT(*) AS n
                       FROM history
                       WHERE ts >= ?
                       GROUP BY site_id, day""",
                    (cutoff_iso,),
                ).fetchall()
            else:
                rows = cx.execute(
                    """SELECT site_id, substr(ts, 1, 10) AS day, COUNT(*) AS n
                       FROM history
                       WHERE ts >= ? AND status = ?
                       GROUP BY site_id, day""",
                    (cutoff_iso, status),
                ).fetchall()
        # Bucket into per-site arrays. Each row has (site_id, day, n).
        # Build a {site_id: {day: n}} interim, then materialize as a
        # per-day list with the canonical day_keys ordering.
        interim: dict[str, dict[str, int]] = {}
        for r in rows:
            sid = r[0] or ""
            day = r[1] or ""
            n = int(r[2] or 0)
            if not sid or not day:
                continue
            interim.setdefault(sid, {})[day] = n
        for sid, day_map in interim.items():
            by_site[sid] = [day_map.get(d, 0) for d in day_keys]
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }), 503
    return jsonify({
        "ok": True,
        "days": days,
        "status": status,
        "start_date": start_date.isoformat(),
        "by_site": by_site,
    })

def register_routes(app) -> int:
    app.register_blueprint(activity_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("activity."))

