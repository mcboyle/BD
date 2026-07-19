"""Resumable mass-import (Phase 199).

`/api/sites/<sid>/load_urls` is synchronous — drop a 50k-URL list,
the request hangs for 30s while we parse + dedupe + insert. The page
locks up; if the operator's browser tab times out, they don't know
if it worked.

This module turns that into a background job:
  1. POST /api/import/start with the URL list (or file). Returns a
     job_id and 202.
  2. Worker thread streams URLs into the runner in chunks of 100,
     yielding to the event loop between chunks so the UI stays
     responsive.
  3. GET /api/import/status/<job_id> for progress polling, or
     SSE /api/import/stream/<job_id> for push updates.

Crash safety: jobs are recorded in a `mass_imports` table at start.
On boot, any job still in "running" state is re-flagged as "crashed"
so the operator knows the request was interrupted and can re-submit.

Caps:
  • Single job: 200,000 URLs (anything bigger hits per-DB-row insert
    cost more than worth it; split the file)
  • Concurrent jobs: 3 (any more and the queue gets thrashed)
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Optional

from . import db as _db


_TABLE_READY = False
_jobs_lock = threading.RLock()
_jobs: dict = {}  # job_id → {state, total, processed, added, dupes, skipped, error}
_MAX_URLS_PER_JOB = 200_000
_MAX_CONCURRENT_JOBS = 3
_CHUNK_SIZE = 100
# v3.62.x memory audit: finished jobs used to stay in _jobs for the
# life of the process — a slow unbounded leak over a long uptime. Keep
# only the most recent finished jobs in memory for status polling; the
# mass_imports table retains the full history for list_recent().
_MAX_RETAINED_JOBS = 50


def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS mass_imports (
                    job_id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    processed INTEGER NOT NULL DEFAULT 0,
                    added INTEGER NOT NULL DEFAULT 0,
                    dupes INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    error TEXT DEFAULT '',
                    started_ts REAL NOT NULL,
                    finished_ts REAL
                )
            """)
            # Flag any jobs still 'running' from a previous boot —
            # they were interrupted by the restart.
            cx.execute("""
                UPDATE mass_imports
                SET state = 'crashed', error = 'process restarted',
                    finished_ts = ?
                WHERE state = 'running'
            """, (time.time(),))
        _TABLE_READY = True
    except Exception:
        pass


def _running_jobs_count() -> int:
    with _jobs_lock:
        return sum(1 for j in _jobs.values()
                   if j.get("state") == "running")


def _prune_jobs():
    """Evict old finished jobs from the in-memory map so it cannot grow
    without bound over a long uptime. Running jobs are always kept;
    finished jobs beyond _MAX_RETAINED_JOBS (newest first) are dropped
    — their record still survives in the mass_imports table for
    list_recent(). Caller must hold _jobs_lock."""
    finished = [(jid, j) for jid, j in _jobs.items()
                if j.get("state") != "running"]
    if len(finished) <= _MAX_RETAINED_JOBS:
        return
    finished.sort(key=lambda kv: kv[1].get("finished_ts") or 0,
                  reverse=True)
    for jid, _j in finished[_MAX_RETAINED_JOBS:]:
        _jobs.pop(jid, None)


def _persist(job_id: str, state: dict):
    """Mirror in-memory state to the table so a status query after
    process restart can still find the job (in crashed state).
    Fail-silent."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                INSERT INTO mass_imports
                (job_id, site_id, state, total, processed, added,
                 dupes, skipped, error, started_ts, finished_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    state = excluded.state,
                    processed = excluded.processed,
                    added = excluded.added,
                    dupes = excluded.dupes,
                    skipped = excluded.skipped,
                    error = excluded.error,
                    finished_ts = excluded.finished_ts
            """, (job_id, state.get("site_id", ""),
                  state.get("state", "running"),
                  state.get("total", 0),
                  state.get("processed", 0),
                  state.get("added", 0),
                  state.get("dupes", 0),
                  state.get("skipped", 0),
                  state.get("error", "")[:500],
                  state.get("started_ts", time.time()),
                  state.get("finished_ts")))
    except Exception:
        pass


def start_import(*, site_id: str, urls: list, load_urls_fn: Callable,
                folder_scan: bool = False) -> dict:
    """Kick off a background mass-import. `load_urls_fn` is the
    runner's load_urls() method; we call it in chunks so the
    runner's existing dedupe/insert logic is reused.

    Returns: {ok, job_id, total} on success,
             {ok: False, error} on rejection."""
    if not urls:
        return {"ok": False, "error": "no URLs"}
    if len(urls) > _MAX_URLS_PER_JOB:
        return {"ok": False,
                "error": f"too many URLs ({len(urls)}); "
                         f"split into batches of {_MAX_URLS_PER_JOB}"}
    if _running_jobs_count() >= _MAX_CONCURRENT_JOBS:
        return {"ok": False,
                "error": f"too many concurrent imports running "
                         f"(max {_MAX_CONCURRENT_JOBS})"}

    _ensure_table()
    job_id = uuid.uuid4().hex[:16]
    state = {
        "job_id": job_id,
        "site_id": site_id,
        "state": "running",
        "total": len(urls),
        "processed": 0,
        "added": 0,
        "dupes": 0,
        "skipped": 0,
        "error": "",
        "started_ts": time.time(),
        "finished_ts": None,
    }
    with _jobs_lock:
        _jobs[job_id] = state
        _prune_jobs()   # bound the in-memory map (memory audit)
    _persist(job_id, state)

    def _worker():
        try:
            for i in range(0, len(urls), _CHUNK_SIZE):
                # Check for cancellation
                with _jobs_lock:
                    current = _jobs.get(job_id, {})
                    if current.get("state") == "cancelled":
                        return
                chunk = urls[i:i + _CHUNK_SIZE]
                try:
                    result = load_urls_fn(chunk, folder_scan=folder_scan)
                    if len(result) == 3:
                        a, d, s = result
                    else:
                        a, d, s = (*result, 0)
                except Exception as e:
                    with _jobs_lock:
                        _jobs[job_id]["state"] = "error"
                        _jobs[job_id]["error"] = (
                            f"chunk at {i}: {str(e)[:200]}")
                        _jobs[job_id]["finished_ts"] = time.time()
                        _persist(job_id, _jobs[job_id])
                    return
                with _jobs_lock:
                    _jobs[job_id]["processed"] += len(chunk)
                    _jobs[job_id]["added"] += a
                    _jobs[job_id]["dupes"] += d
                    _jobs[job_id]["skipped"] += s
                    _persist(job_id, _jobs[job_id])
                # Yield briefly to the UI/SSE so progress updates flow
                time.sleep(0.01)
            with _jobs_lock:
                _jobs[job_id]["state"] = "done"
                _jobs[job_id]["finished_ts"] = time.time()
                _persist(job_id, _jobs[job_id])
        except Exception as e:
            with _jobs_lock:
                _jobs[job_id]["state"] = "error"
                _jobs[job_id]["error"] = str(e)[:200]
                _jobs[job_id]["finished_ts"] = time.time()
                _persist(job_id, _jobs[job_id])

    t = threading.Thread(target=_worker, daemon=True,
                          name=f"mass-import-{job_id}")
    t.start()
    return {"ok": True, "job_id": job_id, "total": len(urls)}


def status(job_id: str) -> Optional[dict]:
    """Get current state. Returns None if job_id unknown.
    Looks in memory first (fast path), falls back to DB (for jobs
    from prior boots)."""
    with _jobs_lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])  # copy
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            row = cx.execute("""
                SELECT * FROM mass_imports WHERE job_id = ?
            """, (job_id,)).fetchone()
        if not row:
            return None
        return dict(row)
    except Exception:
        return None


def cancel(job_id: str) -> bool:
    """Request cancellation. The worker checks between chunks, so
    cancellation happens within ~100 URLs of the next chunk boundary.
    Returns True if the job was running + got the cancel signal."""
    with _jobs_lock:
        if job_id not in _jobs:
            return False
        if _jobs[job_id].get("state") != "running":
            return False
        _jobs[job_id]["state"] = "cancelled"
        _jobs[job_id]["finished_ts"] = time.time()
        _persist(job_id, _jobs[job_id])
    return True


def list_recent(*, limit: int = 20) -> list:
    """Recent jobs across this run + persisted history. Most recent first."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT * FROM mass_imports
                ORDER BY started_ts DESC
                LIMIT ?
            """, (int(limit),)).fetchall()
        return [dict(r) for r in rs]
    except Exception:
        return []
