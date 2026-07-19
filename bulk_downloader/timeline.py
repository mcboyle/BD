"""Activity timeline (Phase 153, Block Q).

Merges multiple event sources into one chronological feed for the
"What happened?" dashboard view. Sources:

  • history table (downloads done/failed/needs_review)
  • events table (subsystem events)
  • bitrot issues
  • achievements unlocked
  • alert_events (Phase 146)
  • maintenance_events (Phase 160)
  • cookie_relogin_log (Phase 131)

Each timeline entry has a uniform shape:
  {ts, source, kind, severity, title, description, link}

Caller pages via `since_ts`/`limit` cursors.
"""
from __future__ import annotations

import time
from typing import Optional


def _safe_query(sql: str, params: tuple = ()) -> list:
    """Run a query, return [] on any error (missing table, etc.)."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []


def _entries_from_history(since_ts: float, limit: int) -> list:
    """Successful + failed downloads."""
    rows = _safe_query("""SELECT id, ts, site_id, url, status,
                                  filename, file_size, message
                           FROM history
                           WHERE status IN ('done', 'failed', 'needs_review')
                             AND strftime('%s', ts) > ?
                           ORDER BY id DESC LIMIT ?""",
                       (str(int(since_ts)), int(limit)))
    out = []
    for r in rows:
        status = r.get("status")
        if status == "done":
            sev = "info"
            title = f"Download done: {(r.get('filename') or '').split('/')[-1][:60]}"
        elif status == "failed":
            sev = "warn"
            title = f"Download failed: {(r.get('message') or '')[:60]}"
        else:
            sev = "warn"
            title = f"Needs review: {(r.get('message') or '')[:60]}"
        out.append({
            "ts": r.get("ts"),
            "source": "history",
            "kind": status,
            "severity": sev,
            "title": title,
            "description": r.get("url", "")[:120],
            "site_id": r.get("site_id"),
            "link": f"/history?id={r.get('id')}",
        })
    return out


def _entries_from_alerts(limit: int) -> list:
    rows = _safe_query("""SELECT * FROM alert_events
                           WHERE kind = 'fired'
                           ORDER BY id DESC LIMIT ?""",
                       (int(limit),))
    out = []
    for r in rows:
        out.append({
            "ts": r.get("ts"),
            "source": "alerts",
            "kind": "alert_fired",
            "severity": "warn",
            "title": f"Alert: {r.get('message', '')}",
            "description": f"value={r.get('metric_value')}",
            "rule_id": r.get("rule_id"),
        })
    return out


def _entries_from_bitrot(limit: int) -> list:
    rows = _safe_query("""SELECT * FROM bitrot_issues
                           ORDER BY id DESC LIMIT ?""", (int(limit),))
    out = []
    for r in rows:
        out.append({
            "ts": r.get("first_seen") or r.get("last_seen"),
            "source": "bitrot",
            "kind": r.get("kind", "integrity_issue"),
            "severity": "warn",
            "title": f"Integrity issue: {r.get('kind', '')}",
            "description": (r.get("filename") or "")[:120],
        })
    return out


def _entries_from_achievements(limit: int) -> list:
    rows = _safe_query("""SELECT * FROM achievements_unlocked
                           ORDER BY unlocked_at DESC LIMIT ?""",
                       (int(limit),))
    out = []
    for r in rows:
        out.append({
            "ts": r.get("unlocked_at"),
            "source": "gamification",
            "kind": "achievement_unlocked",
            "severity": "info",
            "title": f"Achievement unlocked: {r.get('id')}",
            "description": "",
        })
    return out


def _entries_from_maintenance(limit: int) -> list:
    rows = _safe_query("""SELECT * FROM maintenance_events
                           ORDER BY id DESC LIMIT ?""", (int(limit),))
    out = []
    for r in rows:
        out.append({
            "ts": r.get("ts"),
            "source": "maintenance",
            "kind": r.get("kind", ""),
            "severity": "info",
            "title": f"Maintenance window {r.get('kind', '')}",
            "description": (r.get("message") or "")[:120],
        })
    return out


def _normalize_ts(entry: dict) -> float:
    """Coerce any ts representation into a float Unix timestamp.
    Handles strings ('2025-01-15T12:00:00'), numbers, None."""
    ts = entry.get("ts")
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        import datetime
        # SQLite default format: 'YYYY-MM-DD HH:MM:SS'
        cleaned = str(ts).replace(" ", "T").rstrip("Z")
        dt = datetime.datetime.fromisoformat(cleaned)
        return dt.timestamp()
    except Exception:
        return 0.0


def merged_timeline(*, since_ts: Optional[float] = None,
                   limit_per_source: int = 100,
                   limit_total: int = 200) -> list:
    """Build the unified timeline.

    `since_ts` filters: only show entries newer than this.
    `limit_per_source` caps how many entries to pull from each source.
    `limit_total` caps the final merged list."""
    if since_ts is None:
        since_ts = time.time() - 7 * 86400  # last week default
    all_entries = []
    all_entries.extend(_entries_from_history(since_ts, limit_per_source))
    all_entries.extend(_entries_from_alerts(limit_per_source))
    all_entries.extend(_entries_from_bitrot(limit_per_source))
    all_entries.extend(_entries_from_achievements(limit_per_source))
    all_entries.extend(_entries_from_maintenance(limit_per_source))
    # Filter by since_ts using normalized timestamps
    filtered = [e for e in all_entries if _normalize_ts(e) >= since_ts]
    # Sort merged by ts desc
    filtered.sort(key=_normalize_ts, reverse=True)
    return filtered[:limit_total]


def summary(*, since_hours: int = 24) -> dict:
    """Count entries per source over the last N hours."""
    since_ts = time.time() - since_hours * 3600
    entries = merged_timeline(since_ts=since_ts, limit_total=10000)
    by_source: dict = {}
    by_severity: dict = {}
    for e in entries:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        by_severity[e.get("severity", "info")] = \
            by_severity.get(e.get("severity", "info"), 0) + 1
    return {
        "since_hours": since_hours,
        "total": len(entries),
        "by_source": by_source,
        "by_severity": by_severity,
    }
