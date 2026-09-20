"""Row 847: permanent broken-link / HTTP-410 tombstone detector.

Classifies queue failures that will never succeed on retry (HTTP 404,
410 Gone, and site-specific "this content was deleted" signals) and
moves them to a terminal `tombstone` queue status. The auto-retry
scanner (bulk_downloader/runner_scheduler.py) excludes `tombstone`
status jobs from retry scans immediately.

Owns its own `db_tombstones` table and queue status transitions via
`db.db_conn()`, synchronizing active runner job memory when running.
"""
from __future__ import annotations

import re
from flask import jsonify, request

from . import db as _db

_DELETION_MARKERS = (
    "this content has been removed",
    "video has been deleted",
    "content no longer available",
    "this page has been removed",
    "the content you are looking for has been removed",
    "this item is no longer available",
    "404 not found",
    "410 gone",
    "video not found",
    "removed by uploader",
    "page not found",
)

_PERMANENT_STATUS = frozenset({404, 410})
_STATUS_CODE_RE = re.compile(r"\b(404|410)\b")


def classify(status_code=None, message: str = "") -> bool:
    """Return True if (status_code, message) is a permanent tombstone signal:
    HTTP 404/410, or a known site-specific deletion marker."""
    sc = status_code
    if sc is not None and not isinstance(sc, int):
        try:
            sc = int(sc)
        except (TypeError, ValueError):
            sc = None
    if sc in _PERMANENT_STATUS:
        return True
    msg = message.lower() if isinstance(message, str) else ""
    if any(marker in msg for marker in _DELETION_MARKERS):
        return True
    if sc is None and _STATUS_CODE_RE.search(msg):
        return True
    return False


def _ensure_table(cx):
    """Idempotent table creation."""
    cx.execute("""CREATE TABLE IF NOT EXISTS db_tombstones(
        site_id TEXT NOT NULL,
        url TEXT NOT NULL,
        reason TEXT DEFAULT '',
        ts TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
        PRIMARY KEY(site_id, url))""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_tombstones_site "
               "ON db_tombstones(site_id)")


def _sync_runner_jobs(site_id: str, url: str, status: str, message: str,
                      next_auto_retry_at: float, auto_retry_count: int | None = None,
                      _runner=None):
    runners_to_update = []
    if _runner is not None:
        runners_to_update.append(_runner)
    try:
        from .app_state import runners
        if runners and site_id in runners and runners[site_id] not in runners_to_update:
            runners_to_update.append(runners[site_id])
    except Exception:
        pass

    for r in runners_to_update:
        if hasattr(r, "jobs") and isinstance(r.jobs, dict) and url in r.jobs:
            writer = getattr(r, "_job_status_writer", None)
            ctx = writer() if callable(writer) else None
            try:
                if ctx:
                    with ctx as mark_changed:
                        r.jobs[url]["status"] = status
                        r.jobs[url]["message"] = message
                        r.jobs[url]["next_auto_retry_at"] = next_auto_retry_at
                        if auto_retry_count is not None:
                            r.jobs[url]["auto_retry_count"] = auto_retry_count
                        mark_changed()
                else:
                    r.jobs[url]["status"] = status
                    r.jobs[url]["message"] = message
                    r.jobs[url]["next_auto_retry_at"] = next_auto_retry_at
                    if auto_retry_count is not None:
                        r.jobs[url]["auto_retry_count"] = auto_retry_count
            except Exception:
                pass


def tombstone_url(site_id: str, url: str, reason: str = "", _runner=None) -> bool:
    """Record a permanent-delete decision and flip the queue row's status
    to the terminal `tombstone` status (bypassing retry). Synchronizes live
    runner state in memory if active."""
    with _db.db_conn() as cx:
        _ensure_table(cx)
        cx.execute(
            "INSERT OR REPLACE INTO db_tombstones(site_id,url,reason) "
            "VALUES(?,?,?)",
            (site_id, url, str(reason or "")))
        cur = cx.execute(
            "UPDATE queue SET status='tombstone', message=?, "
            "ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE site_id=? AND url=?",
            (str(reason or "tombstoned"), site_id, url))
        rowcount = cur.rowcount

    _sync_runner_jobs(site_id, url, status="tombstone",
                      message=str(reason or "tombstoned"),
                      next_auto_retry_at=-1,
                      _runner=_runner)

    return rowcount > 0


def is_tombstoned(site_id: str, url: str) -> bool:
    with _db.db_conn() as cx:
        _ensure_table(cx)
        row = cx.execute(
            "SELECT 1 FROM db_tombstones WHERE site_id=? AND url=?",
            (site_id, url)).fetchone()
    return row is not None


def untombstone(site_id: str, url: str, _runner=None) -> bool:
    """Reverse a tombstone: delete the db_tombstones record and put the
    queue row back to `pending` with retry counters cleared."""
    with _db.db_conn() as cx:
        _ensure_table(cx)
        cx.execute("DELETE FROM db_tombstones WHERE site_id=? AND url=?",
                   (site_id, url))
        cur = cx.execute(
            "UPDATE queue SET status='pending', retries=0, retry_after=0, "
            "message='untombstoned', "
            "ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE site_id=? AND url=? AND status='tombstone'",
            (site_id, url))
        rowcount = cur.rowcount

    _sync_runner_jobs(site_id, url, status="pending",
                      message="untombstoned",
                      next_auto_retry_at=0,
                      auto_retry_count=0,
                      _runner=_runner)

    return rowcount > 0


def api_queue_tombstone():
    """Body: {site_id, url, reason?}. Mark a queue job permanently dead."""
    from .app_queue import _check_csrf
    _check_csrf()
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    url = (body.get("url") or "").strip()
    reason = body.get("reason") or ""
    if not sid or not url:
        return jsonify({"ok": False, "error": "site_id and url are required"}), 400
    ok = tombstone_url(sid, url, reason)
    if not ok:
        return jsonify({"ok": False, "error": "no queue job for that site_id/url"}), 404
    return jsonify({"ok": True, "site_id": sid, "url": url})


def api_queue_untombstone():
    """Body: {site_id, url}. Reverse a tombstone back to pending."""
    from .app_queue import _check_csrf
    _check_csrf()
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not sid or not url:
        return jsonify({"ok": False, "error": "site_id and url are required"}), 400
    ok = untombstone(sid, url)
    if not ok:
        return jsonify({"ok": False, "error": "no tombstoned job for that site_id/url"}), 404
    return jsonify({"ok": True, "site_id": sid, "url": url})


def register_routes(app) -> int:
    """Idempotent route registration."""
    if "api_queue_tombstone" not in app.view_functions:
        app.add_url_rule(
            "/api/queue/tombstone",
            endpoint="api_queue_tombstone",
            view_func=api_queue_tombstone,
            methods=["POST"],
        )
    if "api_queue_untombstone" not in app.view_functions:
        app.add_url_rule(
            "/api/queue/tombstone/untombstone",
            endpoint="api_queue_untombstone",
            view_func=api_queue_untombstone,
            methods=["POST"],
        )
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint in {"api_queue_tombstone", "api_queue_untombstone"})
