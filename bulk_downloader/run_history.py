"""B1 (post-v3.66.365) — run-history / event substrate.

Persists job lifecycle + outcomes through the EXISTING db.py / db_conn() (no new
DB file). Two tables:

  job_runs   — one row per tracked download run (site_id, url, status, timing)
  run_events — the per-run timeline (start / progress / finish / arbitrary kinds)

Design invariants:

  * ADVISORY ONLY. Every write entry point is wrapped so a DB error (locked,
    missing table, disk-full) is swallowed and logged, never raised. A history
    problem must never take down the actual download path. record_run_start()
    returns a falsy id on failure; the event/finish writers no-op.

  * EVENT-BUS REUSE. emit_lifecycle() rides the existing per-runner event feed
    (runner.log_event -> _event_log -> /api/events_all + useEventStream). No new
    feed and no new stream — job-lifecycle events become just another `kind`
    ("run_start" / "run_finish" / ...) visible through get_events().

Read surfaces live in app.py: GET /api/runs, GET /api/runs/<id>/timeline.
"""
from __future__ import annotations

import logging
import time

from . import db

log = logging.getLogger("bulk_downloader.run_history")


def init():
    """Create the run-history tables if absent. Idempotent (safe to call on
    every boot). Mirrors db.db_init()'s CREATE TABLE IF NOT EXISTS pattern."""
    try:
        with db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS job_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
                finished_at TEXT DEFAULT NULL,
                reason_code TEXT DEFAULT NULL)""")
            # Cut 4: additive reason_code column for existing DBs. SQLite has no
            # ADD COLUMN IF NOT EXISTS, so swallow the duplicate-column error on
            # a DB that already has it.
            try:
                cx.execute("ALTER TABLE job_runs ADD COLUMN reason_code TEXT DEFAULT NULL")
            except Exception:
                pass
            cx.execute("""CREATE TABLE IF NOT EXISTS run_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                ts TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
                event_type TEXT NOT NULL,
                detail TEXT DEFAULT '')""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_runs_site_id ON job_runs(site_id, id DESC)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_runs_status  ON job_runs(status)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_revt_run     ON run_events(run_id, id)")
    except Exception as e:
        # init failing is non-fatal: the store stays unavailable and every
        # advisory writer below will simply no-op.
        log.warning("run_history.init failed (advisory): %s: %s",
                    type(e).__name__, e)


# ── write side (all advisory / fail-open) ───────────────────────────────

def record_run_start(site_id, url=""):
    """Open a run row. Returns the new run id, or a falsy value (None) on
    failure — never raises."""
    try:
        with db.db_conn() as cx:
            cur = cx.execute(
                "INSERT INTO job_runs(site_id, url, status) VALUES(?,?,?)",
                (str(site_id), str(url or ""), "running"))
            rid = cur.lastrowid
            cx.execute(
                "INSERT INTO run_events(run_id, event_type, detail) VALUES(?,?,?)",
                (rid, "start", str(url or "")))
            return rid
    except Exception as e:
        log.warning("record_run_start failed (advisory): %s: %s",
                    type(e).__name__, e)
        return None


def record_run_event(run_id, event_type, detail=""):
    """Append an event to a run's timeline. No-op on failure; never raises."""
    if not run_id:
        return
    try:
        with db.db_conn() as cx:
            cx.execute(
                "INSERT INTO run_events(run_id, event_type, detail) VALUES(?,?,?)",
                (int(run_id), str(event_type), str(detail or "")))
    except Exception as e:
        log.warning("record_run_event failed (advisory): %s: %s",
                    type(e).__name__, e)


def record_run_finish(run_id, status, reason_code=None):
    """Close a run row + drop a 'finish' event. No-op on failure; never raises.

    Cut 4: persists `reason_code` (an operator failure code from
    failure_reasons) when given, so /api/runs?status=failed can group failures.
    """
    if not run_id:
        return
    try:
        with db.db_conn() as cx:
            cx.execute(
                "UPDATE job_runs SET status=?, reason_code=?, "
                "finished_at=strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id=?",
                (str(status), (str(reason_code) if reason_code else None),
                 int(run_id)))
            cx.execute(
                "INSERT INTO run_events(run_id, event_type, detail) VALUES(?,?,?)",
                (int(run_id), "finish", str(status)))
    except Exception as e:
        log.warning("record_run_finish failed (advisory): %s: %s",
                    type(e).__name__, e)


# ── event-bus integration (advisory) ────────────────────────────────────

def emit_lifecycle(runner, phase, run_id=None, url=None, message=""):
    """Surface a job-lifecycle event on the EXISTING per-runner feed.

    Records a run_events row (when run_id is given) AND fires
    runner.log_event("run_<phase>", ...) so the event shows up in get_events()
    / /api/events_all exactly like every other kind. Fail-open: a broken feed
    or a broken DB never propagates."""
    try:
        if run_id:
            record_run_event(run_id, phase, message or (url or ""))
    except Exception:
        pass
    try:
        runner.log_event(f"run_{phase}", message or "", url=url,
                         extra={"run_id": run_id} if run_id else None)
    except Exception as e:
        log.debug("emit_lifecycle log_event failed (advisory): %s: %s",
                  type(e).__name__, e)


# ── read side (used by the app routes) ──────────────────────────────────

def list_runs(limit=200, status=None):
    """Most-recent-first list of runs. Returns [] on any failure.

    Cut 4: optional `status` filter (e.g. "failed") and every row carries
    `reason_code` (NULL for non-failures / pre-Cut-4 rows).
    """
    try:
        with db.db_conn() as cx:
            cols = ("id, site_id, url, status, started_at, finished_at, "
                    "reason_code")
            if status:
                rows = cx.execute(
                    f"SELECT {cols} FROM job_runs WHERE status=? "
                    "ORDER BY id DESC LIMIT ?",
                    (str(status), int(limit))).fetchall()
            else:
                rows = cx.execute(
                    f"SELECT {cols} FROM job_runs ORDER BY id DESC LIMIT ?",
                    (int(limit),)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_runs failed (advisory): %s: %s", type(e).__name__, e)
        return []


def get_run(run_id):
    """Single run row as a dict, or None if absent / on failure."""
    try:
        with db.db_conn() as cx:
            r = cx.execute(
                "SELECT id, site_id, url, status, started_at, finished_at, "
                "reason_code FROM job_runs WHERE id=?", (int(run_id),)).fetchone()
            return dict(r) if r else None
    except Exception as e:
        log.warning("get_run failed (advisory): %s: %s", type(e).__name__, e)
        return None


def get_timeline(run_id):
    """The ordered event list for a run. Returns [] on any failure."""
    try:
        with db.db_conn() as cx:
            rows = cx.execute(
                "SELECT id, run_id, ts, event_type, detail "
                "FROM run_events WHERE run_id=? ORDER BY id",
                (int(run_id),)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("get_timeline failed (advisory): %s: %s",
                    type(e).__name__, e)
        return []
