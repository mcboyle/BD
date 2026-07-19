"""Saved searches + scheduled alerts (Phase 93, Block K).

Builds on Phase 92's FTS5 search. The operator wants to express
standing interests ("alert me when blacked drops a new scene with
Riley Reid") without re-typing the query each time. This module:

  • Persists named search queries in a `saved_searches` table
  • Records the latest matched history row id per query
  • Re-runs each query on schedule and fires apprise notifications
    when there are new matches since last run
  • Exposes a CRUD API surface (add / remove / list / run-now)
  • Generates the "what's new this week" digest endpoint

Schema (created lazily in _ensure_table):

  saved_searches(
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    query TEXT NOT NULL,
    site_id TEXT,                -- optional scope
    status TEXT,                 -- optional scope (e.g. 'done')
    schedule TEXT,               -- 'hourly' | 'daily' | 'weekly' | 'manual'
    notify_via TEXT,             -- apprise URL or '' to skip
    last_run_ts REAL,
    last_seen_id INTEGER,        -- newest history.id at last run
    new_since_last INTEGER,      -- count of new matches at last run
    created_at REAL,
    enabled INTEGER DEFAULT 1
  )

Concurrency: this module is read-mostly. Writes happen on add/remove
(operator UI) and on the periodic run (one task at a time per query).
We do not lock the DB at the application layer — SQLite WAL handles
concurrent reads safely; writes serialize naturally.

Notification: re-uses BD's existing apprise notification surface
(notify_apprise.py). When no notify_via is set, the new matches are
still recorded and surfaced via /api/saved_searches/digest.
"""
from __future__ import annotations

import time
from typing import Optional


# Schedule string → minimum seconds between runs. "manual" never
# auto-runs; the operator triggers it via /api/saved_searches/<id>/run.
_SCHEDULE_INTERVALS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "manual": float("inf"),
}

# F3.1 action lane. "notify" = the historical behaviour (apprise on new
# matches). "enqueue" = feed new matches into the NORMAL download pipeline
# (same admission gates, same review path, same F1.5 dedup downstream).
_ACTIONS = {"notify", "enqueue"}
DEFAULT_DAILY_CAP = 25

# Enqueue is executed through a handler the app registers at startup, so this
# module never imports app.py (app already imports this module — registering a
# callback avoids the circular import). When unregistered, an enqueue rule is
# a SILENT no-op (mirrors the F3-suite "automation unavailable -> skip" rule);
# it never errors and never falls back to downloading without the gates.
_enqueue_handler = None  # Callable[[list[str]], int] | None


def set_enqueue_handler(fn) -> None:
    """Register the callback that pushes URLs into the normal pipeline.
    `fn(urls: list[str]) -> int` returns how many were actually accepted."""
    global _enqueue_handler
    _enqueue_handler = fn if callable(fn) else None


def _reset_enqueue_handler() -> None:
    """Test helper — clear the registered handler."""
    global _enqueue_handler
    _enqueue_handler = None


def _today_bucket() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _ensure_table():
    """Lazy table creation. Idempotent — safe to call on every
    operation; CREATE IF NOT EXISTS is cheap."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS saved_searches(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                query TEXT NOT NULL,
                site_id TEXT DEFAULT '',
                status TEXT DEFAULT '',
                schedule TEXT DEFAULT 'manual',
                notify_via TEXT DEFAULT '',
                last_run_ts REAL DEFAULT 0,
                last_seen_id INTEGER DEFAULT 0,
                new_since_last INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                enabled INTEGER DEFAULT 1
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_ss_schedule "
                       "ON saved_searches(schedule, enabled)")
            # F3.1 (additive): action lane + per-rule daily enqueue cap +
            # provenance counters. CREATE IF NOT EXISTS won't add columns to
            # an existing table, so ALTER any that are missing (mirrors the
            # migrations.py honeypot_score additive pattern). Defaults keep
            # every existing rule on the byte-identical notify path.
            cols = {r[1] for r in cx.execute(
                "PRAGMA table_info(saved_searches)").fetchall()}
            for ddl_name, ddl in (
                ("action", "action TEXT DEFAULT 'notify'"),
                ("daily_cap", "daily_cap INTEGER DEFAULT 25"),
                ("enqueued_count", "enqueued_count INTEGER DEFAULT 0"),
                ("enqueued_day", "enqueued_day TEXT DEFAULT ''"),
                ("enqueued_total", "enqueued_total INTEGER DEFAULT 0"),
            ):
                if ddl_name not in cols:
                    cx.execute(f"ALTER TABLE saved_searches ADD COLUMN {ddl}")
    except Exception as e:
        import sys
        sys.stderr.write(f"[saved_searches] init failed: {e}\n")


def add(*, name: str, query: str, site_id: str = "", status: str = "",
        schedule: str = "manual", notify_via: str = "",
        action: str = "notify",
        daily_cap: int = DEFAULT_DAILY_CAP) -> Optional[int]:
    """Create a new saved search. Returns row id or None on failure.
    Schedule must be one of the keys in _SCHEDULE_INTERVALS. `action` is
    'notify' (default) or 'enqueue'; an unknown action coerces to 'notify'.
    `daily_cap` bounds enqueues per UTC day (clamped >= 0)."""
    if not name or not name.strip():
        return None
    if not query or not query.strip():
        return None
    if schedule not in _SCHEDULE_INTERVALS:
        schedule = "manual"
    if action not in _ACTIONS:
        action = "notify"
    try:
        daily_cap = max(0, int(daily_cap))
    except (TypeError, ValueError):
        daily_cap = DEFAULT_DAILY_CAP
    _ensure_table()
    # Anchor last_seen_id to current max history.id so the first run
    # only flags NEW matches, not the whole history backlog.
    initial_seen = _current_max_history_id()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""INSERT INTO saved_searches(
                name, query, site_id, status, schedule, notify_via,
                action, daily_cap, last_seen_id, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name.strip(), query.strip(), site_id, status,
                 schedule, notify_via, action, daily_cap,
                 initial_seen, now))
            return cur.lastrowid
    except Exception as e:
        import sys
        sys.stderr.write(f"[saved_searches] add failed: {e}\n")
        return None


def remove(*, search_id: Optional[int] = None,
          name: Optional[str] = None) -> bool:
    """Delete by id or name. Returns True if a row was removed."""
    _ensure_table()
    if not search_id and not name:
        return False
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            if search_id:
                cur = cx.execute("DELETE FROM saved_searches WHERE id = ?", (int(search_id),))
            else:
                cur = cx.execute("DELETE FROM saved_searches WHERE name = ?", (name,))
            return cur.rowcount > 0
    except Exception:
        return False


def list_all(*, enabled_only: bool = False) -> list:
    """Return all saved searches as list of dicts. Newest first."""
    _ensure_table()
    try:
        from . import db as _db
        sql = "SELECT * FROM saved_searches"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at DESC"
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql).fetchall()]
    except Exception:
        return []


def update(search_id: int, **fields) -> bool:
    """Partial update. Accepted fields: name, query, site_id, status,
    schedule, notify_via, enabled, action, daily_cap. Unknown fields
    silently ignored. An out-of-range action is dropped (not coerced) so a
    bad PATCH never silently flips a rule's lane."""
    allowed = {"name", "query", "site_id", "status", "schedule",
               "notify_via", "enabled", "action", "daily_cap"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    if "action" in changes and changes["action"] not in _ACTIONS:
        changes.pop("action")
    if "daily_cap" in changes:
        try:
            changes["daily_cap"] = max(0, int(changes["daily_cap"]))
        except (TypeError, ValueError):
            changes.pop("daily_cap")
    if not changes:
        return False
    _ensure_table()
    try:
        from . import db as _db
        sets = ", ".join(f"{k} = ?" for k in changes)
        params = list(changes.values()) + [int(search_id)]
        with _db.db_conn() as cx:
            cur = cx.execute(f"UPDATE saved_searches SET {sets} WHERE id = ?", params)
            return cur.rowcount > 0
    except Exception:
        return False


def _current_max_history_id() -> int:
    """Return max(history.id) or 0 if empty. Used as the anchor when
    creating a saved search."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("SELECT COALESCE(MAX(id), 0) AS m FROM history").fetchone()
        if row is None:
            return 0
        return int(row[0] if not hasattr(row, "keys") else row["m"])
    except Exception:
        return 0


def _do_enqueue(search: dict, new_rows: list, *, enqueue_fn=None) -> tuple:
    """Feed new-match URLs into the normal pipeline via the registered (or
    injected) handler, honouring the per-rule daily cap. Returns
    (enqueued_count, capped_bool). Fail-soft: any error -> (0, False).

    Provenance is recorded rule-side (enqueued_count / enqueued_day /
    enqueued_total on the saved_searches row); jobs are not stamped, so the
    runner stays untouched. F1.5 dedup runs downstream in _process_one, so a
    URL already 'done' is skipped there, not here.
    """
    handler = enqueue_fn or _enqueue_handler
    if handler is None:
        return (0, False)  # silent no-op when automation isn't wired
    try:
        # De-dup within this batch, preserve order, keep only real URLs.
        seen = set()
        urls = []
        for r in new_rows:
            u = (r.get("url") or "").strip()
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        if not urls:
            return (0, False)
        # Daily cap (UTC day bucket). Reset the counter on a new day.
        today = _today_bucket()
        cap = int(search.get("daily_cap", DEFAULT_DAILY_CAP) or 0)
        used = int(search.get("enqueued_count", 0) or 0)
        if (search.get("enqueued_day") or "") != today:
            used = 0
        remaining = max(0, cap - used) if cap > 0 else 0
        capped = len(urls) > remaining
        batch = urls[:remaining]
        if not batch:
            # Still persist the day reset so a stale counter doesn't linger.
            _persist_enqueue_counters(int(search["id"]), today, used, 0)
            return (0, capped)
        accepted = handler(batch)
        try:
            accepted = int(accepted)
        except (TypeError, ValueError):
            accepted = len(batch)
        _persist_enqueue_counters(
            int(search["id"]), today, used + accepted, accepted)
        return (accepted, capped)
    except Exception as e:
        import sys
        sys.stderr.write(f"[saved_searches] enqueue failed: {e}\n")
        return (0, False)


def _persist_enqueue_counters(search_id: int, day: str,
                              new_used: int, delta_total: int) -> None:
    """Write the daily counter + day bucket + lifetime total. Fail-soft."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute(
                "UPDATE saved_searches SET enqueued_count = ?, "
                "enqueued_day = ?, enqueued_total = "
                "COALESCE(enqueued_total, 0) + ? WHERE id = ?",
                (int(new_used), day, int(delta_total), int(search_id)))
    except Exception:
        pass


def criteria_for(search_id: int) -> Optional[dict]:
    """CAP-3 (v3.66.667): return the filter-relevant criteria of a saved search
    ({query, site_id, status}) by id, or None if the search is missing or
    disabled. Used by scheduled_exports to chain an export to a live search."""
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT query, site_id, status, enabled "
                "FROM saved_searches WHERE id = ?", (int(search_id),)).fetchone()
        if row is None:
            return None
        d = dict(row)
        if not d.get("enabled", 1):
            return None
        return {"query": d.get("query") or "",
                "site_id": d.get("site_id") or "",
                "status": d.get("status") or ""}
    except Exception:
        return None


def run_one(search_id: int, *, enqueue_fn=None) -> dict:
    """Re-run one saved search. Returns {ok, search, new_matches: int,
    new_rows: [...], notified, action, enqueued, enqueue_capped}. Updates
    last_seen_id and last_run_ts on the saved_searches row. `enqueue_fn`
    (test seam) overrides the registered enqueue handler."""
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("SELECT * FROM saved_searches WHERE id = ?",
                             (int(search_id),)).fetchone()
        if row is None:
            return {"ok": False, "error": "search not found"}
        search = dict(row)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if not search.get("enabled", 1):
        return {"ok": True, "search": search, "new_matches": 0,
                "new_rows": [], "notified": False, "skipped": "disabled"}
    last_seen = int(search.get("last_seen_id", 0) or 0)
    # Re-run the FTS5 query and filter to rows newer than last_seen.
    try:
        from .db import db_search_fts
        results = db_search_fts(search["query"],
                                site_id=search.get("site_id") or None,
                                status=search.get("status") or None,
                                limit=500)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    new_rows = [r for r in results if int(r.get("id", 0)) > last_seen]
    n = len(new_rows)
    new_max = max([int(r["id"]) for r in new_rows], default=last_seen)
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""UPDATE saved_searches SET last_run_ts = ?,
                          last_seen_id = ?, new_since_last = ?
                          WHERE id = ?""",
                       (now, new_max, n, int(search_id)))
    except Exception:
        pass
    # Action lane: 'enqueue' feeds new matches into the normal pipeline
    # (gates + F1.5 dedup apply downstream); 'notify' (default) fires apprise.
    # The two are mutually exclusive — an enqueue rule does not also notify.
    enqueued = 0
    enqueue_capped = False
    notified = False
    action = (search.get("action") or "notify")
    if action == "enqueue":
        enqueued, enqueue_capped = _do_enqueue(
            search, new_rows, enqueue_fn=enqueue_fn)
    elif n > 0 and search.get("notify_via"):
        try:
            from . import notify_apprise as _ap
            sample = "\n".join(f"• {r.get('filename') or r.get('url') or '?'}"
                                for r in new_rows[:5])
            msg = (f"Saved search '{search['name']}' has {n} new "
                   f"match{'es' if n != 1 else ''}:\n\n{sample}")
            if n > 5:
                msg += f"\n…and {n - 5} more"
            _ap.send_to_url(search["notify_via"], title=f"BD: {search['name']}", body=msg)
            notified = True
        except Exception as e:
            import sys
            sys.stderr.write(f"[saved_searches] notify failed: {e}\n")
    return {"ok": True, "search": search, "new_matches": n,
            "new_rows": new_rows, "notified": notified,
            "action": action, "enqueued": enqueued,
            "enqueue_capped": enqueue_capped}


def run_due() -> dict:
    """Run all enabled saved searches whose schedule interval has
    elapsed since last_run_ts. Designed to be called from a periodic
    scheduler thread (every N minutes). Returns aggregate stats."""
    _ensure_table()
    now = time.time()
    candidates = list_all(enabled_only=True)
    out = {"checked": 0, "ran": 0, "skipped": 0, "errors": 0,
           "total_new_matches": 0}
    for s in candidates:
        out["checked"] += 1
        sched = s.get("schedule", "manual")
        interval = _SCHEDULE_INTERVALS.get(sched, float("inf"))
        last = float(s.get("last_run_ts", 0) or 0)
        if (now - last) < interval:
            out["skipped"] += 1
            continue
        try:
            r = run_one(int(s["id"]))
            if r.get("ok"):
                out["ran"] += 1
                out["total_new_matches"] += int(r.get("new_matches", 0))
            else:
                out["errors"] += 1
        except Exception:
            out["errors"] += 1
    return out


def digest(*, hours_back: int = 168) -> dict:
    """'What's new since...' aggregate. Returns counts per saved
    search of matches in the last N hours, not gated by schedule."""
    _ensure_table()
    cutoff = time.time() - hours_back * 3600
    out = {"hours_back": hours_back, "searches": []}
    for s in list_all(enabled_only=True):
        try:
            from .db import db_search_fts, db_conn
            results = db_search_fts(s["query"],
                                    site_id=s.get("site_id") or None,
                                    status=s.get("status") or None,
                                    limit=500)
            # filter by ts > cutoff (history.ts is ISO string —
            # cheaper to just compare timestamps as ISO strings)
            import datetime
            cutoff_iso = datetime.datetime.fromtimestamp(cutoff).strftime("%Y-%m-%dT%H:%M:%S")
            recent = [r for r in results if (r.get("ts") or "") >= cutoff_iso]
            out["searches"].append({
                "id": s["id"], "name": s["name"],
                "query": s["query"], "matches": len(recent),
                "sample_filename": (recent[0].get("filename") if recent else None),
            })
        except Exception:
            out["searches"].append({
                "id": s["id"], "name": s["name"],
                "query": s["query"], "matches": 0, "error": True,
            })
    return out
