"""Cleanup helpers (Phase 130, Block Q).

Sweep the library for cleanup candidates: tiny files (likely failed),
orphans (file with no history row), stale partials (.part files older
than 24h), broken symlinks, empty directories.

Read-only analysis — never deletes. Returns lists the operator can
review and then pass to batch_ops.bulk_delete.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional


_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv",
               ".webm", ".flv", ".ts", ".mpg", ".mpeg"}
_PARTIAL_EXTS = {".part", ".ytdl", ".download", ".crdownload", ".tmp"}


def find_tinies(*, threshold_mb: int = 5,
               site_id: Optional[str] = None,
               limit: int = 1000) -> list:
    """History rows marked 'done' whose file is suspiciously small.
    A 5GB scene that finished at 2MB is almost certainly a captured
    error page, paywall, or login redirect."""
    try:
        from . import db as _db
        sql = """SELECT id, site_id, url, filename, file_size, ts
                  FROM history
                  WHERE status='done' AND file_size > 0
                    AND file_size < ?"""
        params: list = [threshold_mb * 1024 * 1024]
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []


def find_stale_partials(*, dirs: list, age_hours: int = 24) -> list:
    """Find .part/.tmp/.crdownload files older than N hours. These
    are stale downloads — likely interrupted and never resumed."""
    out = []
    cutoff = time.time() - (age_hours * 3600)
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                ext = Path(f).suffix.lower()
                if ext not in _PARTIAL_EXTS:
                    continue
                full = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(full)
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if mtime < cutoff:
                    out.append({
                        "path": full,
                        "size_bytes": size,
                        "mtime": mtime,
                        "age_hours": round((time.time() - mtime) / 3600, 1),
                    })
    return sorted(out, key=lambda x: x["age_hours"], reverse=True)


def find_broken_symlinks(dirs: list, *, limit: int = 1000) -> list:
    """Find symlinks whose target doesn't exist. Comes up after
    moving libraries between disks."""
    out = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                full = os.path.join(root, f)
                if not os.path.islink(full):
                    continue
                if not os.path.exists(full):  # broken symlink
                    out.append({
                        "path": full,
                        "target": os.readlink(full),
                    })
                    if len(out) >= limit:
                        return out
    return out


def find_empty_dirs(dirs: list, *, exclude_names: Optional[set] = None) -> list:
    """Find empty directories that could be removed. Walks bottom-up
    so nested empties are caught. Excludes well-known dirs like
    .git, .cache, etc."""
    skip = exclude_names or {".git", ".cache", "__pycache__",
                              "node_modules", ".tmp"}
    out = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, sub_dirs, files in os.walk(d, topdown=False):
            base = os.path.basename(root)
            if base in skip:
                continue
            if not sub_dirs and not files:
                out.append({"path": root})
    return out


def find_orphans_for(dirs: list, *, limit: int = 1000) -> list:
    """Video files in `dirs` with no matching history row. Wraps
    library_final.list_orphans for backward compat + multi-dir
    support."""
    try:
        from .library_final import list_orphans as _list_orph
        out = []
        for d in dirs:
            out.extend(_list_orph(d))
            if len(out) >= limit:
                break
        return out[:limit]
    except Exception:
        return []


def find_missing_metadata(*, site_id: Optional[str] = None,
                         limit: int = 500) -> list:
    """History rows marked 'done' with no NFO sidecar. Useful before
    triggering library_final.regen_nfos_from_history."""
    try:
        from . import db as _db
        sql = """SELECT id, site_id, filename FROM history
                  WHERE status='done' AND filename != ''"""
        params: list = []
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit) * 2)  # over-fetch since we filter
        with _db.db_conn() as cx:
            rows = cx.execute(sql, params).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        fn = r[2] if not hasattr(r, "keys") else r["filename"]
        if not fn:
            continue
        nfo = Path(fn).with_suffix(".nfo")
        if not nfo.exists():
            out.append({"id": r[0] if not hasattr(r, "keys") else r["id"],
                        "site_id": r[1] if not hasattr(r, "keys") else r["site_id"],
                        "filename": fn})
            if len(out) >= limit:
                break
    return out


# v3.66.1012 -- SIX FILESYSTEM WALKS ARE NOT A PER-REQUEST COST.
#
# MEASURED ON THE BOX, v3.66.1011 capture: `/api/cleanup/summary` was one of two
# routes L34 adjudicated as EXCEEDED -- over 8 seconds SERIAL, on a quiet app,
# with 1959.6 GB on the volume. `summary()` runs find_tinies,
# find_stale_partials, find_broken_symlinks, find_empty_dirs, find_orphans_for
# and find_missing_metadata on every call, and its only caller is a plain GET.
#
# A SHORT TTL RATHER THAN A LONG ONE. This is a review screen: the operator
# looks at it, deletes something, and reloads expecting the numbers to move.
# A cache measured in minutes would show them their own pre-cleanup state and
# read as a bug. 60s absorbs a page load and its refresh, and nothing more.
#
# KEYED ON THE DIRECTORIES, because that is what the answer is about -- keyed on
# nothing, a second site's summary would be served the first's numbers.
SUMMARY_TTL_S = 60.0
_summary_cache: dict = {}


def summary_cache_clear() -> None:
    """Drop the cached snapshot. For tests and for any caller that has just
    mutated the filesystem and needs the next read to be true."""
    _summary_cache.clear()


def summary(s_cfg: Optional[dict] = None, *, force: bool = False) -> dict:
    """Aggregate one-call cleanup snapshot. Lists candidates by
    category with sample + total recoverable bytes.

    Cached for SUMMARY_TTL_S against the set of directories being summarised;
    `force=True` bypasses it, mirroring the `force=1` convention
    /api/community_scrapers/index already uses.
    """
    if not s_cfg:
        s_cfg = {}
    dirs = list({(cfg or {}).get("download_dir", "")
                 for cfg in s_cfg.values()
                 if (cfg or {}).get("download_dir")})
    key = tuple(sorted(dirs))
    if not force:
        hit = _summary_cache.get(key)
        if hit and (time.monotonic() - hit[0]) < SUMMARY_TTL_S:
            return hit[1]
    out = {}
    tinies = find_tinies(threshold_mb=5)
    out["tinies"] = {
        "count": len(tinies),
        "total_size_mb": round(sum(int(t["file_size"]) for t in tinies) / (1024**2), 1),
        "sample": tinies[:5],
    }
    partials = find_stale_partials(dirs=dirs, age_hours=24)
    out["stale_partials"] = {
        "count": len(partials),
        "total_size_gb": round(sum(p["size_bytes"] for p in partials) / (1024**3), 2),
        "sample": partials[:5],
    }
    broken = find_broken_symlinks(dirs=dirs)
    out["broken_symlinks"] = {"count": len(broken), "sample": broken[:5]}
    empty = find_empty_dirs(dirs=dirs)
    out["empty_dirs"] = {"count": len(empty), "sample": empty[:5]}
    orphans = find_orphans_for(dirs=dirs)
    out["orphans"] = {
        "count": len(orphans),
        "total_size_gb": round(sum(o["size_bytes"] for o in orphans) / (1024**3), 2),
        "sample": orphans[:5],
    }
    missing_nfo = find_missing_metadata()
    out["missing_nfo"] = {"count": len(missing_nfo),
                          "sample": missing_nfo[:5]}
    _summary_cache[key] = (time.monotonic(), out)
    return out
