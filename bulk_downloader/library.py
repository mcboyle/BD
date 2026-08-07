"""v3.50 (Phase 3) — library module.

The library is the operator's collection: one row per file-on-disk that
the app knows about. Rows arrive two ways:

  Forward path:
    runner.py completes a download → library.record_completion() with
    the final path + extracted metadata. Linked to history via
    library.history_id.

  Backward path:
    library.scan(root) walks a configured root directory, hashes/sizes
    every video file, and either reconciles with an existing library
    row (UPDATE last_scanned, file_size, file_mtime) or inserts a new
    one. Idempotent — running it twice doesn't duplicate rows.

The scanner reconciles with history too: if a file's path matches an
existing history row's filename, we link them (set library.history_id
+ history.library_id). This is what makes "I downloaded this two years
ago" show up correctly in the library tab after the v3.50 upgrade.

## Status / progress

A library scan can take minutes on a multi-TB collection. The
ScanState object tracks progress so the UI can show:
  - current root being walked
  - files seen so far
  - files added vs updated
  - files marked missing (existed last scan, now gone)
  - rough ETA based on disk-traversal rate

Scans run in a background thread; the UI polls /api/library/scan/status.

## Tags

`tags` is a simple lookup: id, name, color, description. `library_tags`
junctions library_id × tag_id. Both helpers below transact properly so
adding a tag is atomic (insert tag if missing → get id → upsert junction
row, all in one connection).
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .db import db_conn

# Lazy schema-ensure flag. The first library_* call triggers migrations
# so unit tests and direct-API access work without an app boot.
_SCHEMA_READY = False


def _ensure_schema():
    """Idempotent: ensure library + tags tables exist. Cheap when already
    initialized — just a flag check."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        from .db import db_init
        from . import migrations as _mig
        db_init()
        _mig.apply_pending()
        _SCHEMA_READY = True
    except Exception:
        # Don't trap — let the original call fail with a clearer error
        # than "no such table"
        pass


# Video file extensions the scanner picks up. Audio-only and image
# extensions are deliberately excluded — the app downloads video.
_VIDEO_EXTS = frozenset({
    ".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".flv",
    ".ts", ".m4s", ".mpg", ".mpeg", ".wmv", ".ogv",
})

# Sentinel files we skip. .part is an in-flight download, .bdseg.json is
# our own resume sidecar.
_SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
_SKIP_SUFFIXES = (".part", ".bdseg.json", ".tmp", ".bd-enrich.json")


def library_path_for_completion(dl_dir: str, name: str) -> Optional[str]:
    """15.11 (option b): resolve a backend-reported completion NAME to the
    absolute path a library row should record, or None for "record nothing".

    The qB/JD bridges report completion with a bare NAME -- qb_bridge.py
    poll() returns ``t.get("name")``, jd_bridge.py poll() returns
    ``row.get("name")``; never a path -- and for a multi-file torrent that
    name is a DIRECTORY. v3.66.837's contract is that db_log records a
    library row only for an absolute path naming ONE file, so:

      dl_dir/name is a FILE      -> that path, ONLY if its extension is in
                                    _VIDEO_EXTS. Without the predicate a
                                    single-file .rar mints a row the next
                                    scan flips to file_exists=0 while the
                                    file exists (the scanner only "sees"
                                    _VIDEO_EXTS files) -- the v3.66.837
                                    ghost, reintroduced.
      dl_dir/name is a DIRECTORY -> the LARGEST _VIDEO_EXTS file inside
                                    (operator decision, option b), walked
                                    with the scanner's own skip predicate so
                                    the forward row is exactly the row
                                    scan() would record and UNIQUE(file_path)
                                    collides instead of duplicating. Ties
                                    break to the lexicographically SMALLEST
                                    path so the answer cannot depend on
                                    os.walk order.
      neither                    -> None. A wrong row is worse than no row.

    The name comes from the remote side (torrent/package title), so a
    candidate that normalises outside dl_dir is refused. Never raises for
    filesystem reasons: unreadable entries are skipped and any OSError
    degrades to None -- but callers still wrap their call defensively,
    because a resolution failure must never cost the caller's history row.
    """
    try:
        if not dl_dir or not name:
            return None
        root = os.path.normpath(str(dl_dir))
        cand = os.path.normpath(os.path.join(root, str(name)))
        if not os.path.isabs(cand):
            return None
        if not cand.startswith(root + os.sep):
            return None
        if os.path.isfile(cand):
            if Path(cand).suffix.lower() in _VIDEO_EXTS:
                return cand
            return None
        if not os.path.isdir(cand):
            return None
        best: Optional[tuple[int, str]] = None  # (-size, path); min() wins
        for dirpath, _dirnames, filenames in os.walk(cand):
            for fn in filenames:
                if fn in _SKIP_FILES or fn.endswith(_SKIP_SUFFIXES):
                    continue
                if Path(fn).suffix.lower() not in _VIDEO_EXTS:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                key = (-size, full)
                if best is None or key < best:
                    best = key
        return best[1] if best is not None else None
    except OSError:
        return None


# ── Library row read / write helpers ────────────────────────────────────

def library_record(file_path: str, *, history_id: Optional[int] = None,
                   site_id: str = "", file_size: int = 0,
                   performer: str = "", studio: str = "",
                   year: Optional[int] = None,
                   title: str = "", duration_s: Optional[float] = None,
                   resolution: str = "", codec: str = "",
                   cover_thumb: str = "") -> Optional[int]:
    """Insert (or update) a library row. Returns the row id.

    Called by:
      - runner.py on download completion (forward path)
      - scan() on each video file found (backward path)

    Idempotent: file_path is UNIQUE — second insert with the same path
    updates the row in place rather than creating a duplicate.
    """
    _ensure_schema()
    file_path = str(file_path)
    if not file_path:
        return None
    now = time.time()
    try:
        stat = os.stat(file_path)
        file_size = stat.st_size if file_size == 0 else file_size
        file_mtime = stat.st_mtime
        file_exists = 1
    except OSError:
        file_mtime = 0.0
        file_exists = 0
    try:
        with db_conn() as cx:
            # Try update first — the hot path is "scanner saw this file
            # last week and again today; just refresh last_scanned"
            cur = cx.execute(
                "UPDATE library SET "
                "  history_id = COALESCE(?, history_id), "
                "  site_id    = CASE WHEN ?<>'' THEN ? ELSE site_id END, "
                "  file_exists = ?, "
                "  file_size  = ?, "
                "  file_mtime = ?, "
                "  last_scanned = ?, "
                "  performer  = CASE WHEN ?<>'' THEN ? ELSE performer END, "
                "  studio     = CASE WHEN ?<>'' THEN ? ELSE studio END, "
                "  year       = COALESCE(?, year), "
                "  title      = CASE WHEN ?<>'' THEN ? ELSE title END, "
                "  duration_s = COALESCE(?, duration_s), "
                "  resolution = CASE WHEN ?<>'' THEN ? ELSE resolution END, "
                "  codec      = CASE WHEN ?<>'' THEN ? ELSE codec END, "
                "  cover_thumb = CASE WHEN ?<>'' THEN ? ELSE cover_thumb END "
                "WHERE file_path=?",
                (history_id,
                 site_id, site_id,
                 file_exists, file_size, file_mtime, now,
                 performer, performer,
                 studio, studio,
                 year,
                 title, title,
                 duration_s,
                 resolution, resolution,
                 codec, codec,
                 cover_thumb, cover_thumb,
                 file_path))
            if cur.rowcount > 0:
                # Row existed and was updated — find its id to return
                r = cx.execute(
                    "SELECT id FROM library WHERE file_path=?",
                    (file_path,)).fetchone()
                return r["id"] if r else None
            # Insert path
            cur = cx.execute(
                "INSERT INTO library("
                "  history_id, site_id, file_path, file_exists, file_size,"
                "  file_mtime, last_scanned, performer, studio, year,"
                "  title, duration_s, resolution, codec, cover_thumb,"
                "  added_at"
                ") VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?)",
                (history_id, site_id, file_path, file_exists, file_size,
                 file_mtime, now, performer, studio, year,
                 title, duration_s, resolution, codec, cover_thumb,
                 now))
            new_id = cur.lastrowid
            # Backfill history.library_id if we have a history_id
            if history_id and new_id:
                try:
                    cx.execute(
                        "UPDATE history SET library_id=? WHERE id=?",
                        (new_id, history_id))
                except Exception:
                    pass
            return new_id
    except Exception:
        return None


def library_get(library_id: int) -> Optional[dict]:
    """Single-row fetch with tags joined in."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            row = cx.execute(
                "SELECT * FROM library WHERE id=?",
                (int(library_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["tags"] = _tags_for_library(cx, int(library_id))
            return d
    except Exception:
        return None


def _tags_for_library(cx, library_id: int) -> list[dict]:
    return [dict(r) for r in cx.execute(
        "SELECT t.id, t.name, t.color FROM tags t "
        "JOIN library_tags lt ON lt.tag_id=t.id "
        "WHERE lt.library_id=? ORDER BY t.name",
        (library_id,)).fetchall()]


def library_browse(*, site_id: Optional[str] = None,
                   studio: Optional[str] = None,
                   performer: Optional[str] = None,
                   year: Optional[int] = None,
                   watched: Optional[bool] = None,
                   tag: Optional[str] = None,
                   query: Optional[str] = None,
                   missing_only: bool = False,
                   sort: str = "added_at_desc",
                   limit: int = 100,
                   after_id: Optional[int] = None) -> tuple[list[dict], Optional[int]]:
    """Browse the library. Returns (rows, next_cursor).

    Cursor-based pagination via after_id (same pattern as
    db_search_cursor in v3.48). Sort options:
      - added_at_desc / added_at_asc
      - file_size_desc / file_size_asc
      - title_asc
      - rating_desc / watched_at_desc
    """
    _ensure_schema()
    sql = "SELECT l.* FROM library l"
    params: list = []
    joins = ""
    wheres: list[str] = []
    if tag:
        joins += (" JOIN library_tags lt ON lt.library_id=l.id "
                  "JOIN tags t ON t.id=lt.tag_id")
        wheres.append("t.name=?")
        params.append(tag)
    if missing_only:
        wheres.append("l.file_exists=0")
    if site_id:
        wheres.append("l.site_id=?")
        params.append(site_id)
    if studio:
        wheres.append("l.studio=?")
        params.append(studio)
    if performer:
        wheres.append("l.performer=?")
        params.append(performer)
    if year:
        wheres.append("l.year=?")
        params.append(int(year))
    if watched is True:
        wheres.append("l.watched=1")
    elif watched is False:
        wheres.append("l.watched=0")
    if query:
        wheres.append(
            "(l.title LIKE ? OR l.file_path LIKE ? OR l.notes LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q, q])
    if after_id is not None:
        # Cursor: for descending sorts, "after" means id less than cursor
        # (assumes id is monotonic with added_at, which it is since both
        # auto-increment in insertion order).
        wheres.append("l.id < ?")
        params.append(int(after_id))
    sql += joins
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    # Whitelist sort keys — never f-string user input
    SORTS = {
        "added_at_desc":  "l.added_at DESC, l.id DESC",
        "added_at_asc":   "l.added_at ASC, l.id ASC",
        "file_size_desc": "l.file_size DESC, l.id DESC",
        "file_size_asc":  "l.file_size ASC, l.id DESC",
        "title_asc":      "l.title ASC, l.id DESC",
        "rating_desc":    "l.rating DESC, l.id DESC",
        "watched_at_desc": "l.watched_at DESC, l.id DESC",
    }
    sql += " ORDER BY " + SORTS.get(sort, SORTS["added_at_desc"])
    sql += " LIMIT ?"
    params.append(int(limit))
    try:
        with db_conn() as cx:
            rows = [dict(r) for r in cx.execute(sql, params).fetchall()]
            # Annotate each row with its tags (one extra query per row;
            # acceptable for page-sized result sets)
            for r in rows:
                r["tags"] = _tags_for_library(cx, r["id"])
            next_cursor = (rows[-1]["id"]
                           if len(rows) == int(limit) else None)
            return rows, next_cursor
    except Exception:
        return [], None


def library_set_watched(library_id: int, watched: bool) -> bool:
    """Flip the watched flag. Stamps watched_at when setting to true,
    clears it when setting to false."""
    _ensure_schema()
    now = time.time() if watched else None
    try:
        with db_conn() as cx:
            cur = cx.execute(
                "UPDATE library SET watched=?, watched_at=? WHERE id=?",
                (1 if watched else 0, now, int(library_id)))
            return (cur.rowcount or 0) > 0
    except Exception:
        return False


def library_set_rating(library_id: int,
                       rating: Optional[int]) -> bool:
    """Set rating 1–5 or None to clear. Out-of-range values are clamped."""
    _ensure_schema()
    if rating is not None:
        rating = max(1, min(5, int(rating)))
    try:
        with db_conn() as cx:
            cur = cx.execute(
                "UPDATE library SET rating=? WHERE id=?",
                (rating, int(library_id)))
            return (cur.rowcount or 0) > 0
    except Exception:
        return False


def library_set_notes(library_id: int, notes: str) -> bool:
    """Free-form notes field. 8KB cap defensively."""
    _ensure_schema()
    notes = str(notes)[:8192]
    try:
        with db_conn() as cx:
            cur = cx.execute(
                "UPDATE library SET notes=? WHERE id=?",
                (notes, int(library_id)))
            return (cur.rowcount or 0) > 0
    except Exception:
        return False


def library_delete(library_id: int, *, also_delete_file: bool = False) -> dict:
    """Soft-or-hard delete a library row.

    `also_delete_file=True` attempts to remove the underlying file. We
    do NOT remove it without explicit consent because the file on disk
    is the operator's actual data — losing it to a bad library-row
    delete would be a disaster.

    LIB-6: when also_delete_file is set we ALSO remove the item's thumbnail
    artifacts in the same operation (the sidecar `<base>.jpg` and any stored
    `cover_thumb`) so a hard delete doesn't orphan them on disk. The count is
    reported as `thumbs_removed`. The sidecar path is derived inline (no
    thumbnail_gen import — keeps the dependency graph edge-neutral); parallel-
    mode thumbs are covered by unlinking the stored cover_thumb.

    Returns {ok, deleted_row, file_removed?, thumbs_removed, error?}."""
    _ensure_schema()
    out = {"ok": False, "deleted_row": False, "file_removed": False,
           "thumbs_removed": 0}
    try:
        with db_conn() as cx:
            row = cx.execute(
                "SELECT file_path, history_id, cover_thumb FROM library WHERE id=?",
                (int(library_id),)).fetchone()
            if not row:
                out["error"] = "library row not found"
                return out
            file_path = row["file_path"]
            history_id = row["history_id"]
            cover_thumb = row["cover_thumb"] if "cover_thumb" in row.keys() else ""
            if also_delete_file and file_path:
                try:
                    Path(file_path).unlink(missing_ok=True)
                    out["file_removed"] = True
                except Exception as e:
                    out["error"] = f"file unlink failed: {e}"
                    # Continue — still drop the library row
                # LIB-6: cascade the item's thumbnail artifacts. Sidecar mode
                # is <media-base>.jpg next to the file (thumbnail_gen's default);
                # cover_thumb is the actually-stored path (covers parallel mode).
                _candidates = []
                try:
                    _base, _ = os.path.splitext(str(file_path))
                    _candidates.append(_base + ".jpg")
                except Exception:
                    pass
                if cover_thumb:
                    _candidates.append(str(cover_thumb))
                _seen = set()
                for _tp in _candidates:
                    if not _tp or _tp in _seen or _tp == file_path:
                        continue
                    _seen.add(_tp)
                    try:
                        _p = Path(_tp)
                        if _p.is_file():
                            _p.unlink()
                            out["thumbs_removed"] += 1
                    except Exception:
                        # Best-effort — a stuck thumb must never block the row delete.
                        pass
            # ON DELETE CASCADE on library_tags handles tag cleanup
            cx.execute("DELETE FROM library WHERE id=?",
                       (int(library_id),))
            # Null the history back-ref so the history row doesn't point
            # at a deleted library row
            if history_id:
                cx.execute(
                    "UPDATE history SET library_id=NULL WHERE id=?",
                    (history_id,))
            out["deleted_row"] = True
            out["ok"] = True
            return out
    except Exception as e:
        out["error"] = f"db error: {type(e).__name__}: {e}"
        return out


# ── Tags ────────────────────────────────────────────────────────────────

def tag_upsert(name: str, *, color: str = "",
               description: str = "") -> Optional[int]:
    """Get-or-create a tag by name. Returns the tag id. Tag name is
    case-sensitive and trimmed."""
    _ensure_schema()
    name = (name or "").strip()
    if not name or len(name) > 100:
        return None
    try:
        with db_conn() as cx:
            # Existing?
            r = cx.execute(
                "SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            if r:
                # Update color/description if provided non-empty
                if color or description:
                    cx.execute(
                        "UPDATE tags SET "
                        "  color = CASE WHEN ?<>'' THEN ? ELSE color END, "
                        "  description = CASE WHEN ?<>'' THEN ? ELSE description END "
                        "WHERE id=?",
                        (color, color, description, description, r["id"]))
                return r["id"]
            cur = cx.execute(
                "INSERT INTO tags(name, color, description, created_at) "
                "VALUES(?,?,?,?)",
                (name, color, description, time.time()))
            return cur.lastrowid
    except Exception:
        return None


def tag_list() -> list[dict]:
    """All tags + their usage count. Sorted by name."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            return [dict(r) for r in cx.execute(
                "SELECT t.*, "
                "  (SELECT COUNT(*) FROM library_tags WHERE tag_id=t.id) "
                "    AS usage_count "
                "FROM tags t ORDER BY t.name").fetchall()]
    except Exception:
        return []


def tag_delete(tag_id: int) -> bool:
    """Delete a tag. Explicitly cascades to library_tags junction rows
    because SQLite doesn't enforce FK constraints unless
    `PRAGMA foreign_keys=ON` is set, which we don't enable globally
    (it has performance side effects on other tables). Manual cascade
    is fine here — only one junction table references tags."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            # Drop junction rows FIRST (otherwise they'd dangle if we
            # crash between the two deletes). The DELETE order doesn't
            # matter for correctness (FKs are off), but it matters for
            # debug-tooling that might re-enable FKs later.
            cx.execute("DELETE FROM library_tags WHERE tag_id=?",
                       (int(tag_id),))
            cur = cx.execute("DELETE FROM tags WHERE id=?",
                             (int(tag_id),))
            return (cur.rowcount or 0) > 0
    except Exception:
        return False


def library_add_tag(library_id: int, tag_name: str) -> bool:
    """Attach a tag (creating it if needed) to a library row."""
    _ensure_schema()
    tag_id = tag_upsert(tag_name)
    if not tag_id:
        return False
    try:
        with db_conn() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO library_tags("
                "  library_id, tag_id, added_at) VALUES(?,?,?)",
                (int(library_id), tag_id, time.time()))
        return True
    except Exception:
        return False


def library_remove_tag(library_id: int, tag_name: str) -> bool:
    """Detach a tag from a library row. Returns False only if the join
    row didn't exist (not an error — idempotent removal)."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            cur = cx.execute(
                "DELETE FROM library_tags WHERE library_id=? AND tag_id="
                "(SELECT id FROM tags WHERE name=?)",
                (int(library_id), tag_name))
            return (cur.rowcount or 0) > 0
    except Exception:
        return False


# ── Stats / aggregates ──────────────────────────────────────────────────

def library_stats() -> dict:
    """Disk usage + counts broken down by dimension. Cheap — one query
    per axis, indexed columns."""
    _ensure_schema()
    out: dict = {
        "total_files": 0, "total_size": 0,
        "missing_files": 0, "watched_files": 0, "unrated_files": 0,
        "by_site": [], "by_studio": [], "by_performer": [],
        "by_year": [], "by_resolution": [],
    }
    try:
        with db_conn() as cx:
            r = cx.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(file_size),0) AS s,"
                "       SUM(CASE WHEN file_exists=0 THEN 1 ELSE 0 END) AS m,"
                "       SUM(watched) AS w,"
                "       SUM(CASE WHEN rating IS NULL THEN 1 ELSE 0 END) AS u"
                "  FROM library").fetchone()
            if r:
                out["total_files"]   = r["n"] or 0
                out["total_size"]    = r["s"] or 0
                out["missing_files"] = r["m"] or 0
                out["watched_files"] = r["w"] or 0
                out["unrated_files"] = r["u"] or 0
            # Top 20 per dimension; aggregates are cheap because each
            # column has its own index
            for axis, col in [("by_site", "site_id"),
                              ("by_studio", "studio"),
                              ("by_performer", "performer"),
                              ("by_year", "year"),
                              ("by_resolution", "resolution")]:
                rows = cx.execute(
                    f"SELECT {col} AS k, COUNT(*) AS n, "
                    f"  COALESCE(SUM(file_size),0) AS s "
                    f"FROM library "
                    f"WHERE {col} IS NOT NULL AND {col} <> '' "
                    f"GROUP BY {col} "
                    f"ORDER BY n DESC LIMIT 20"
                ).fetchall()
                out[axis] = [dict(r) for r in rows]
    except Exception as e:
        out["error"] = str(e)
    return out


def library_snapshot(library_id: int) -> Optional[dict]:
    """Full reversible snapshot of one library row: the row (all columns) plus its
    library_tags junction rows. Used by `autonomy_library_reconcile` so a governed
    removal can be exactly reversed (the row's autoincrement `id` is an FK target for
    library_tags and the history back-ref, so a plain re-insert would not suffice)."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            row = cx.execute("SELECT * FROM library WHERE id=?", (int(library_id),)).fetchone()
            if not row:
                return None
            tags = cx.execute(
                "SELECT tag_id, added_at FROM library_tags WHERE library_id=?",
                (int(library_id),)).fetchall()
            return {"row": dict(row), "tags": [dict(t) for t in tags]}
    except Exception:
        return None


def library_restore(snapshot: dict) -> dict:
    """Inverse of a `library_delete(..., also_delete_file=False)`: re-create the row with its
    ORIGINAL id (so tag FKs and the history back-ref line up again), re-insert its tags, and
    re-point history.library_id. Generic over columns so it survives schema additions.
    Never touches files. Returns {ok, restored_id, error?}."""
    _ensure_schema()
    out = {"ok": False, "restored_id": None}
    try:
        row = dict((snapshot or {}).get("row") or {})
        if not row.get("id"):
            out["error"] = "snapshot missing row id"
            return out
        rid = int(row["id"])
        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        with db_conn() as cx:
            cx.execute(
                f"INSERT OR REPLACE INTO library({','.join(cols)}) VALUES ({placeholders})",
                [row[c] for c in cols])
            for t in (snapshot.get("tags") or []):
                cx.execute(
                    "INSERT OR IGNORE INTO library_tags(library_id, tag_id, added_at) VALUES (?,?,?)",
                    (rid, t.get("tag_id"), t.get("added_at", 0)))
            hist_id = row.get("history_id")
            if hist_id:
                cx.execute("UPDATE history SET library_id=? WHERE id=?", (rid, int(hist_id)))
        out["ok"] = True
        out["restored_id"] = rid
    except Exception as e:
        out["error"] = str(e)
    return out


def library_missing_for_site(site_id: str) -> list[dict]:
    """All `file_exists=0` rows for one site, every column (incl. last_scanned). The
    reconcile kind applies the missing-age debounce in Python on `last_scanned`."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            return [dict(r) for r in cx.execute(
                "SELECT * FROM library WHERE file_exists=0 AND site_id=?",
                (site_id,)).fetchall()]
    except Exception:
        return []


def library_orphans(root: str) -> list[str]:
    """Files under `root` that aren't in the library table.

    For "I have files on disk the app doesn't know about" — typically
    after a manual download outside the app, or after restoring from
    backup. The scanner subsumes this when run on the same root, but
    the orphans list is useful when the operator wants a peek first
    without persisting anything.
    """
    _ensure_schema()
    out: list[str] = []
    try:
        with db_conn() as cx:
            known = {r["file_path"] for r in cx.execute(
                "SELECT file_path FROM library").fetchall()}
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn in _SKIP_FILES:
                    continue
                if fn.endswith(_SKIP_SUFFIXES):
                    continue
                if Path(fn).suffix.lower() not in _VIDEO_EXTS:
                    continue
                full = os.path.join(dirpath, fn)
                if full not in known:
                    out.append(full)
                    if len(out) >= 1000:
                        return out  # safety cap on huge collections
    except Exception:
        pass
    return out


def library_missing() -> list[dict]:
    """Library rows whose file_exists=0. The scanner sets this when it
    walks a root and finds the path is gone."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            return [dict(r) for r in cx.execute(
                "SELECT * FROM library WHERE file_exists=0 "
                "ORDER BY id DESC LIMIT 500").fetchall()]
    except Exception:
        return []


# ── Scanner ─────────────────────────────────────────────────────────────

@dataclass
class ScanState:
    """Progress tracker for an in-flight library scan."""
    started_at: float = 0.0
    finished_at: Optional[float] = None
    current_root: str = ""
    roots: list[str] = field(default_factory=list)
    seen: int = 0           # files walked
    added: int = 0          # new library rows
    updated: int = 0        # existing rows refreshed
    missing_marked: int = 0 # rows flipped to file_exists=0
    errors: int = 0
    error_samples: list[str] = field(default_factory=list)
    cancelled: bool = False

    def to_dict(self) -> dict:
        now = time.time()
        elapsed = now - self.started_at if self.started_at else 0
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "running": self.finished_at is None and not self.cancelled,
            "cancelled": self.cancelled,
            "current_root": self.current_root,
            "roots": list(self.roots),
            "seen": self.seen, "added": self.added,
            "updated": self.updated, "missing_marked": self.missing_marked,
            "errors": self.errors,
            "error_samples": list(self.error_samples)[:5],
            "elapsed_s": round(elapsed, 1),
        }


_scan_state: Optional[ScanState] = None
_scan_lock = threading.Lock()
_scan_thread: Optional[threading.Thread] = None


def scan_status() -> dict:
    """UI poll target. Returns last/current scan state, or an idle stub."""
    with _scan_lock:
        if _scan_state is None:
            return {"running": False, "never_run": True}
        return _scan_state.to_dict()


def scan_cancel() -> bool:
    """Request that the running scan stop at the next file boundary.
    Returns True if a scan was in progress."""
    with _scan_lock:
        if _scan_state and _scan_state.finished_at is None:
            _scan_state.cancelled = True
            return True
        return False


def scan_start(roots: Iterable[str]) -> dict:
    """Kick off a scan over the given roots (typically the configured
    download_dir values from sites_config). Returns immediately; the
    scan runs in a background thread. Subsequent calls while a scan is
    already running return {error: 'already running'}."""
    _ensure_schema()
    global _scan_state, _scan_thread
    roots = [str(r) for r in roots if r and Path(str(r)).is_dir()]
    if not roots:
        return {"ok": False, "error": "no valid roots provided"}
    with _scan_lock:
        if _scan_state and _scan_state.finished_at is None and not _scan_state.cancelled:
            return {"ok": False, "error": "scan already running"}
        # @941: ...and refuse while a CANCELLED scan's thread is still alive.
        # scan_cancel() only sets a flag; the worker stops at the next file or
        # root boundary, which on a large tree is a long way off. The clause
        # above stops matching the moment `cancelled` flips, so without this a
        # second walk started immediately over the same database.
        #
        # Binding the worker to its own state (see _scan_worker) is what makes
        # the corruption impossible; this refusal is what stops two walks
        # running at once. Neither replaces the other -- a liveness check alone
        # races the thread's actual exit, and binding alone would still let two
        # workers hammer the same rows.
        if _scan_thread is not None and _scan_thread.is_alive():
            return {"ok": False,
                    "error": "previous scan is still stopping; try again"}
        _scan_state = ScanState(
            started_at=time.time(),
            roots=list(roots),
            current_root=roots[0],
        )
        # Daemon so the scanner doesn't keep the process alive on shutdown.
        # `_scan_state` is passed BY REFERENCE, so scan_cancel() -- which sets
        # the flag on the global -- still reaches this thread.
        _scan_thread = threading.Thread(
            target=_scan_worker, args=(roots, _scan_state), daemon=True,
            name="library-scanner")
        _scan_thread.start()
    return {"ok": True, "started": True}


def _fp_under_root(fp: str, root: str) -> bool:
    """True iff fp is root itself or a descendant of root, tested on a path
    boundary (not a raw string prefix). Prevents a sibling directory that merely
    shares a name prefix -- e.g. '/data/vids-backup/x' vs root '/data/vids' --
    from being swept into scope. roots come from os.path.join'd config dirs, so
    the trailing-separator form suffices (root is trailing-sep-normalised)."""
    root = root.rstrip(os.sep)
    return fp == root or fp.startswith(root + os.sep)


def _scan_worker(roots: list[str], state: ScanState):
    """The actual scan loop. Runs on a daemon thread."""
    # @941: BOUND to the ScanState this worker was started for, never the
    # module global. scan_cancel() clears the refusal in scan_start, so a
    # NEW scan could be accepted while this thread was still walking -- and
    # every write below resolved the global at CALL time, so this worker's
    # counters, and finally its finished_at, landed in the NEXT scan's
    # state. `state` IS the object scan_start published as the global, so
    # scan_cancel still reaches this thread; what changes is that a LATER
    # scan's state can no longer be the target.
    # Helper: lock-protected mutation of `state`. We hold the lock only for
    # the assignment itself; the file work happens outside.
    def _mut(**kw):
        with _scan_lock:
            for k, v in kw.items():
                setattr(state, k, v)
    def _bump(field_name):
        with _scan_lock:
            setattr(state, field_name,
                    getattr(state, field_name) + 1)
    def _record_error(msg):
        with _scan_lock:
            state.errors += 1
            if len(state.error_samples) < 5:
                state.error_samples.append(msg[:200])

    seen_paths: set[str] = set()
    try:
        for root in roots:
            with _scan_lock:
                if state.cancelled:
                    break
                state.current_root = root
            for dirpath, _dirnames, filenames in os.walk(root):
                with _scan_lock:
                    if state.cancelled:
                        break
                for fn in filenames:
                    if fn in _SKIP_FILES:
                        continue
                    if fn.endswith(_SKIP_SUFFIXES):
                        continue
                    if Path(fn).suffix.lower() not in _VIDEO_EXTS:
                        continue
                    full = os.path.join(dirpath, fn)
                    seen_paths.add(full)
                    _bump("seen")
                    # Did this row exist before?
                    existed = False
                    try:
                        with db_conn() as cx:
                            r = cx.execute(
                                "SELECT id FROM library WHERE file_path=?",
                                (full,)).fetchone()
                            existed = r is not None
                    except Exception as e:
                        _record_error(f"check {full}: {e}")
                        continue
                    # Try to link to a history row by filename match
                    history_id = None
                    try:
                        with db_conn() as cx:
                            r = cx.execute(
                                "SELECT id FROM history "
                                "WHERE filename LIKE ? "
                                "  AND status='done' "
                                "ORDER BY id DESC LIMIT 1",
                                ("%" + os.path.basename(full),)
                            ).fetchone()
                            if r:
                                history_id = r["id"]
                    except Exception:
                        pass
                    rid = library_record(full, history_id=history_id)
                    if rid is None:
                        _record_error(f"record_failed {full}")
                    elif existed:
                        _bump("updated")
                    else:
                        _bump("added")
        # Second pass: rows in DB whose file_path WASN'T seen this
        # scan. Mark them file_exists=0 (only if their path is under
        # one of our scan roots — paths outside the roots aren't
        # invalidated by a scan that didn't cover them).
        with _scan_lock:
            if state.cancelled:
                state.finished_at = time.time()
                return
        try:
            with db_conn() as cx:
                rows = cx.execute(
                    "SELECT id, file_path FROM library WHERE file_exists=1"
                ).fetchall()
                missing_count = 0
                for r in rows:
                    fp = r["file_path"]
                    # Only flip files under our scanned roots (path-boundary
                    # match, not a raw string prefix)
                    if not any(_fp_under_root(fp, root) for root in roots):
                        continue
                    if fp not in seen_paths:
                        cx.execute(
                            "UPDATE library SET file_exists=0 WHERE id=?",
                            (r["id"],))
                        missing_count += 1
                _mut(missing_marked=missing_count)
        except Exception as e:
            _record_error(f"missing-pass: {e}")
    finally:
        _mut(finished_at=time.time())
