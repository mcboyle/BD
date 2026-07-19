"""User-applied tags on history rows (Phase 140, Block Q).

Lets operators tag downloaded scenes for organization. Tags are simple
strings (no hierarchy). Per-row, tags are deduplicated.

Schema (created lazily):

  history_tags(history_id INTEGER, tag TEXT, ts_assigned REAL,
               UNIQUE(history_id, tag))

Public surface:
  • add_tag(history_ids, tag)        — bulk add one tag to many rows
  • remove_tag(history_ids, tag)     — bulk remove
  • tags_for(history_id)             — list tags on one row
  • tags_for_many(history_ids)       — batch fetch {hid: [tags]}
  • all_tags(*, with_counts)         — list distinct tags + use counts
  • rows_with_tag(tag, limit)        — find rows tagged X
  • rename_tag(old, new)             — bulk rename across all rows
  • untag_all(tag)                   — remove a tag from every row

The implementation uses _ensure_table() so callers don't need to manage
schema creation explicitly. Index on `tag` for fast filter queries.
"""
from __future__ import annotations

import re
import time
from typing import Iterable, Optional

from . import db as _db


_tags_table_ready = False
_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def _ensure_table():
    """Idempotent schema creation."""
    global _tags_table_ready
    if _tags_table_ready:
        return
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS history_tags (
                    history_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    ts_assigned REAL NOT NULL,
                    UNIQUE(history_id, tag)
                )
            """)
            cx.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_tags_tag
                ON history_tags(tag)
            """)
            cx.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_tags_hid
                ON history_tags(history_id)
            """)
        _tags_table_ready = True
    except Exception:
        # Don't propagate — caller's operation should fail-open
        pass


def normalize_tag(tag: str) -> str:
    """Lowercase, strip whitespace. Returns '' if invalid (used so
    callers can validate cheaply)."""
    if not tag:
        return ""
    t = tag.strip().lower()
    # Replace whitespace with hyphens
    t = re.sub(r"\s+", "-", t)
    if not _TAG_PATTERN.match(t):
        return ""
    return t


def add_tag(history_ids: Iterable[int], tag: str) -> dict:
    """Add `tag` to every row in history_ids. Returns
    {ok, tag, added, already_had, invalid_tag}."""
    norm = normalize_tag(tag)
    if not norm:
        return {"ok": False, "invalid_tag": True,
                "error": "invalid tag format (lowercase letters/digits/_/-)"}
    _ensure_table()
    ids = [int(x) for x in history_ids if x]
    if not ids:
        return {"ok": True, "tag": norm, "added": 0, "already_had": 0}
    added = 0
    already = 0
    now = time.time()
    try:
        with _db.db_conn() as cx:
            for hid in ids:
                try:
                    cx.execute("""
                        INSERT INTO history_tags (history_id, tag, ts_assigned)
                        VALUES (?, ?, ?)
                    """, (hid, norm, now))
                    added += 1
                except Exception:
                    # UNIQUE violation = already had this tag
                    already += 1
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "tag": norm, "added": added,
            "already_had": already}


def remove_tag(history_ids: Iterable[int], tag: str) -> dict:
    """Remove `tag` from every row in history_ids. Returns
    {ok, removed}."""
    norm = normalize_tag(tag)
    if not norm:
        return {"ok": False, "invalid_tag": True, "error": "invalid tag"}
    _ensure_table()
    ids = [int(x) for x in history_ids if x]
    if not ids:
        return {"ok": True, "tag": norm, "removed": 0}
    try:
        placeholders = ",".join("?" * len(ids))
        with _db.db_conn() as cx:
            cx.execute(f"""
                DELETE FROM history_tags
                WHERE tag = ? AND history_id IN ({placeholders})
            """, [norm] + ids)
            removed = cx.execute("SELECT changes()").fetchone()[0]
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "tag": norm, "removed": int(removed or 0)}


def tags_for(history_id: int) -> list:
    """All tags assigned to one history row, in alphabetical order."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT tag FROM history_tags
                WHERE history_id = ? ORDER BY tag
            """, (int(history_id),)).fetchall()
        return [r[0] for r in rs]
    except Exception:
        return []


def tags_for_many(history_ids: Iterable[int]) -> dict:
    """Batch fetch tags for multiple rows. Returns {hid: [tags]}.
    Rows with no tags are present with empty lists."""
    _ensure_table()
    ids = [int(x) for x in history_ids if x]
    out = {i: [] for i in ids}
    if not ids:
        return out
    try:
        placeholders = ",".join("?" * len(ids))
        with _db.db_conn() as cx:
            rs = cx.execute(f"""
                SELECT history_id, tag FROM history_tags
                WHERE history_id IN ({placeholders})
                ORDER BY history_id, tag
            """, ids).fetchall()
        for hid, tag in rs:
            out[int(hid)].append(tag)
    except Exception:
        pass
    return out


def all_tags(*, with_counts: bool = True,
            min_count: int = 1) -> list:
    """List distinct tags. With counts → [{tag, count}], sorted
    by count desc; without → [tag, ...] alphabetical."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            if with_counts:
                rs = cx.execute("""
                    SELECT tag, COUNT(*) FROM history_tags
                    GROUP BY tag HAVING COUNT(*) >= ?
                    ORDER BY COUNT(*) DESC, tag ASC
                """, (int(min_count),)).fetchall()
                return [{"tag": r[0], "count": int(r[1])} for r in rs]
            else:
                rs = cx.execute("""
                    SELECT DISTINCT tag FROM history_tags
                    ORDER BY tag
                """).fetchall()
                return [r[0] for r in rs]
    except Exception:
        return []


def rows_with_tag(tag: str, *, limit: int = 100,
                  site_id: Optional[str] = None) -> list:
    """History rows that have the given tag. Joins to history for
    filename/url/status. Latest-first."""
    norm = normalize_tag(tag)
    if not norm:
        return []
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            if site_id:
                rs = cx.execute("""
                    SELECT h.id, h.site_id, h.url, h.filename, h.file_size,
                           h.status, h.ts
                    FROM history h
                    JOIN history_tags t ON t.history_id = h.id
                    WHERE t.tag = ? AND h.site_id = ?
                    ORDER BY h.id DESC LIMIT ?
                """, (norm, site_id, int(limit))).fetchall()
            else:
                rs = cx.execute("""
                    SELECT h.id, h.site_id, h.url, h.filename, h.file_size,
                           h.status, h.ts
                    FROM history h
                    JOIN history_tags t ON t.history_id = h.id
                    WHERE t.tag = ?
                    ORDER BY h.id DESC LIMIT ?
                """, (norm, int(limit))).fetchall()
        return [dict(r) for r in rs]
    except Exception:
        return []


def rename_tag(old: str, new: str) -> dict:
    """Bulk-rename `old` → `new` across all rows. UNIQUE constraint
    means rows already having `new` get the `old` removed silently
    (no duplicate).  Returns {ok, renamed, merged}."""
    old_norm = normalize_tag(old)
    new_norm = normalize_tag(new)
    if not old_norm or not new_norm:
        return {"ok": False, "error": "invalid tag(s)"}
    if old_norm == new_norm:
        return {"ok": True, "renamed": 0, "merged": 0}
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            # Step 1: rows that have ONLY the old tag → update
            # Step 2: rows that have BOTH → delete the old one (the new
            #         one already exists, UNIQUE blocks update)
            cx.execute("""
                DELETE FROM history_tags
                WHERE tag = ? AND history_id IN (
                    SELECT history_id FROM history_tags WHERE tag = ?
                )
            """, (old_norm, new_norm))
            merged = cx.execute("SELECT changes()").fetchone()[0]
            cx.execute("""
                UPDATE history_tags SET tag = ? WHERE tag = ?
            """, (new_norm, old_norm))
            renamed = cx.execute("SELECT changes()").fetchone()[0]
        return {"ok": True, "renamed": int(renamed or 0),
                "merged": int(merged or 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def untag_all(tag: str) -> dict:
    """Remove a tag from every row in history_tags."""
    norm = normalize_tag(tag)
    if not norm:
        return {"ok": False, "error": "invalid tag"}
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            cx.execute("DELETE FROM history_tags WHERE tag = ?", (norm,))
            removed = cx.execute("SELECT changes()").fetchone()[0]
        return {"ok": True, "removed": int(removed or 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def suggest_tags(history_id: int) -> list:
    """Suggest tags for a row by running tag_inference over its
    filename. Returns inferred tags NOT already applied."""
    _ensure_table()
    try:
        from . import tag_inference as _ti
        with _db.db_conn() as cx:
            row = cx.execute("""
                SELECT filename, url FROM history WHERE id = ?
            """, (int(history_id),)).fetchone()
        if not row:
            return []
        text = (row[0] or "") + " " + (row[1] or "")
        inferred = _ti.infer_tags(text, max_tags=12)
        existing = set(tags_for(history_id))
        return [t for t in inferred if t not in existing]
    except Exception:
        return []
