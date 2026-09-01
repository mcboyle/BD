"""Crash recovery (Phase 195).

Half-finished downloads leave `.part` files on disk + `.meta` sidecars
holding resume state. When the worker crashes mid-download (power
loss, OS reboot, OOM kill), these stick around forever.

The runner's resume logic handles the "operator manually restarts the
download" case, but there's no UI surfacing of orphaned files. They
just take up disk silently.

This module scans each site's download_dir for `.part` files, joins
against the history table to find which ones have no active job, and
returns them with metadata for the Review-tab card.

Two thresholds:
  • <24h old: probably still active, hide from the orphan view
  • ≥24h old: candidate for review (resume / delete / ignore)

Resumption uses the existing runner path — just re-enqueueing the URL
re-engages resume logic. Delete is a plain unlink with the sidecar.
Ignore just marks the file in the orphans table as "operator chose to
keep" so future scans skip it.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from . import db as _db


_ORPHAN_AGE_THRESHOLD_S = 24 * 3600  # 24h
_TABLE_READY = False


def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        with _db.db_conn() as cx:
            # Tracks per-file operator decisions so we don't re-prompt.
            # `decision` is one of: 'ignore' (keep, don't ask again),
            # 'deleted' (record of past delete for audit), 'resumed'.
            cx.execute("""
                CREATE TABLE IF NOT EXISTS crash_recovery_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    ts REAL NOT NULL,
                    site_id TEXT DEFAULT ''
                )
            """)
            cx.execute("""
                CREATE INDEX IF NOT EXISTS idx_crash_recovery_path
                ON crash_recovery_decisions(path)
            """)
        _TABLE_READY = True
    except Exception:
        # Fail-open: scan still works, just can't persist decisions
        pass


def _ignored_paths() -> set:
    """Files the operator already told us to leave alone."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT path FROM crash_recovery_decisions
                WHERE decision = 'ignore'
            """).fetchall()
        return {r["path"] for r in rs}
    except Exception:
        return set()


def _active_urls(runners: dict) -> set:
    """URLs currently in any runner's job map. Files for these URLs
    might be legitimately in progress; we exclude them from orphans
    regardless of file age."""
    active = set()
    for sid, runner in (runners or {}).items():
        try:
            for url in (getattr(runner, "jobs", {}) or {}).keys():
                active.add(url)
        except Exception:
            continue
    return active


def _read_meta_sidecar(part_path: Path) -> dict:
    """Read the `.part.meta` JSON sidecar that the runner writes to
    track resume state. Returns {} if missing/malformed."""
    meta_path = part_path.with_suffix(part_path.suffix + ".meta")
    if not meta_path.is_file():
        return {}
    try:
        import json
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan_for_orphans(*, s_cfg: dict, runners: dict,
                    age_threshold_s: int = _ORPHAN_AGE_THRESHOLD_S) -> list:
    """Walk every site's download_dir looking for orphaned `.part`
    files. Returns a list of dicts:

        [{path, site_id, site_name, size_bytes, age_seconds,
          url (from .meta sidecar if available),
          downloaded_bytes (from sidecar),
          total_bytes (from sidecar),
          progress_pct (0-100, computed)}]

    Excludes:
      • Files where the URL is still in an active job map
      • Files younger than age_threshold_s
      • Files the operator has marked 'ignore'

    Sorted oldest-first (most likely to be truly stuck)."""
    ignored = _ignored_paths()
    active_urls = _active_urls(runners)
    now = time.time()
    orphans = []

    for sid, cfg in (s_cfg or {}).items():
        dl_dir = (cfg or {}).get("download_dir", "")
        if not dl_dir:
            continue
        dl_path = Path(dl_dir)
        if not dl_path.is_dir():
            continue
        site_name = (cfg.get("name") or sid)

        try:
            # rglob is recursive — operators sometimes nest into
            # subdirs by template (e.g. /{site}/{date}/file.mp4.part)
            for part_path in dl_path.rglob("*.part"):
                try:
                    if not part_path.is_file():
                        continue
                    path_str = str(part_path)
                    if path_str in ignored:
                        continue
                    stat = part_path.stat()
                    age_s = now - stat.st_mtime
                    if age_s < age_threshold_s:
                        continue
                    meta = _read_meta_sidecar(part_path)
                    url = (meta or {}).get("url") or ""
                    if url and url in active_urls:
                        # Still being worked on — skip
                        continue
                    total = (meta or {}).get("total_bytes") or 0
                    downloaded = stat.st_size
                    pct = 0
                    if total > 0:
                        pct = min(100, round(downloaded * 100.0 / total, 1))
                    orphans.append({
                        "path": path_str,
                        "site_id": sid,
                        "site_name": site_name,
                        "size_bytes": stat.st_size,
                        "age_seconds": age_s,
                        "url": url,
                        "total_bytes": total,
                        "downloaded_bytes": downloaded,
                        "progress_pct": pct,
                    })
                except OSError:
                    continue
        except OSError:
            continue

    orphans.sort(key=lambda o: -o["age_seconds"])
    return orphans


def mark_decision(path: str, decision: str, *,
                  site_id: str = "") -> bool:
    """Record what the operator chose to do with this orphan. Used
    for 'ignore' (don't show again) and for audit on 'delete' /
    'resume' actions."""
    if decision not in ("ignore", "deleted", "resumed"):
        return False
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                INSERT OR REPLACE INTO crash_recovery_decisions
                (path, decision, ts, site_id)
                VALUES (?, ?, ?, ?)
            """, (path, decision, time.time(), site_id))
        return True
    except Exception:
        return False


def delete_orphan(path: str) -> dict:
    """Delete a .part file + its .meta and .owner sidecars. Idempotent —
    missing files don't raise. Returns {ok, deleted_bytes}."""
    p = Path(path)
    if not p.exists():
        return {"ok": True, "deleted_bytes": 0,
                "note": "already absent"}
    try:
        deleted_bytes = p.stat().st_size if p.is_file() else 0
        p.unlink(missing_ok=True)
        # Sidecar
        meta_path = p.with_suffix(p.suffix + ".meta")
        meta_path.unlink(missing_ok=True)
        # part-staging-collision: the staging claim's lifetime is the .part's
        # lifetime, so purging the orphan purges its claim. Leaving it behind
        # would push a later download of the same name onto `_1` for no reason.
        from . import staging_claim as _sc
        # Row 492: release() now proves ownership. This is the ONE caller that
        # legitimately frees a claim it does not own -- delete_orphan runs on
        # explicit operator command against a .part the operator has chosen to
        # purge -- so it says force=True rather than inheriting the old
        # unconditional behaviour by omission.
        _sc.release(p, force=True)
        mark_decision(path, "deleted")
        return {"ok": True, "deleted_bytes": deleted_bytes}
    except OSError as e:
        return {"ok": False, "error": str(e)[:200]}


def clear_decisions(*, decision: Optional[str] = None) -> int:
    """Wipe past decisions. If `decision` is given, only that kind."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            if decision:
                cur = cx.execute(
                    "DELETE FROM crash_recovery_decisions WHERE decision=?",
                    (decision,))
            else:
                cur = cx.execute("DELETE FROM crash_recovery_decisions")
            return cur.rowcount or 0
    except Exception:
        return 0


def stats(*, s_cfg: dict, runners: dict) -> dict:
    """Summary numbers for the Review-tab card."""
    orphans = scan_for_orphans(s_cfg=s_cfg, runners=runners)
    total_bytes = sum(o.get("size_bytes", 0) for o in orphans)
    by_site = {}
    for o in orphans:
        sid = o.get("site_id", "?")
        by_site.setdefault(sid, {"count": 0, "bytes": 0,
                                 "site_name": o.get("site_name", sid)})
        by_site[sid]["count"] += 1
        by_site[sid]["bytes"] += o.get("size_bytes", 0)
    return {
        "orphan_count": len(orphans),
        "total_bytes": total_bytes,
        "by_site": by_site,
    }
