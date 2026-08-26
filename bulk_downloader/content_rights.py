"""Content rights / takedown handling (Phase 194).

Single-user instances usually don't need this. Useful when BD ever
distributes to other users, or to honor explicit operator wishes
("don't download X regardless of how it gets into the queue").

Three primitives:

  1. Blocklist — URL patterns + content hashes that BD refuses to
     enqueue or download. Operator-managed.
  2. License-aware filter — flag content that's clearly paywalled +
     no account configured for the site. Soft warning; non-blocking.
  3. Audit log — append-only record of takedown events (added,
     removed, blocked-download-attempted, content-purged).

Schema (created lazily):

  content_blocklist(
    id INTEGER PRIMARY KEY,
    pattern TEXT,            -- URL regex or substring
    hash_hex TEXT,            -- file phash to block (from dedup)
    reason TEXT,
    added_at REAL,
    added_by TEXT             -- 'operator' / 'dmca_request' / etc
  )

  content_rights_audit(
    id INTEGER PRIMARY KEY,
    kind TEXT,                -- 'block_added', 'block_removed',
                              -- 'download_refused', 'file_purged'
    target TEXT,              -- URL or hash
    reason TEXT,
    ts REAL
  )

The URL admission check has three outcomes: a matching block, a measured
no-match, or an explicit unknown result when persistence cannot be read.  An
unreadable blocklist must never grant permission to enqueue a URL.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from . import db as _db


_table_ready = False

# v3.46.4 F9: SSRF / regex-DOS defense. Cap URL + pattern length before
# running re.search(). Without this, a malicious blocklist pattern paired
# with a long URL could trigger catastrophic backtracking and pin a CPU
# core. Source: cobalt-main/api/src/processing/service-patterns.js.
# 4096 chars is comfortably above any real URL (RFC 3986 doesn't set a
# limit but every browser caps at 2048-8192).
_MAX_URL_LEN_FOR_MATCH = 4096
_MAX_PATTERN_LEN = 512


def _ensure_tables():
    global _table_ready
    # Always run the idempotent DDL. Tests and maintenance paths can replace
    # the active database within one process; a process-global success bit
    # otherwise describes the previous database and turns a healthy new store
    # into a false UNKNOWN at the first policy read.
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS content_blocklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT DEFAULT '',
                    hash_hex TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    added_at REAL NOT NULL,
                    added_by TEXT DEFAULT 'operator'
                )
            """)
            cx.execute("""
                CREATE TABLE IF NOT EXISTS content_rights_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    target TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    ts REAL NOT NULL
                )
            """)
            cx.execute("""
                CREATE INDEX IF NOT EXISTS idx_blocklist_hash
                ON content_blocklist(hash_hex) WHERE hash_hex != ''
            """)
        _table_ready = True
    except Exception:
        pass


def _audit(kind: str, target: str = "", reason: str = ""):
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                INSERT INTO content_rights_audit (kind, target, reason, ts)
                VALUES (?, ?, ?, ?)
            """, (kind, (target or "")[:500], (reason or "")[:500],
                  time.time()))
    except Exception:
        pass


def block_url(pattern: str, *, reason: str = "",
             added_by: str = "operator") -> dict:
    """Add a URL pattern to the blocklist. Pattern is matched as a
    regex (use re.escape for literal). Returns {ok, id}."""
    if not pattern:
        return {"ok": False, "error": "pattern required"}
    # v3.46.4 F9: cap pattern length at insertion. The match-time cap
    # already skips over-long patterns, but rejecting at insertion
    # surfaces the problem to the operator instead of silently dropping.
    if len(pattern) > _MAX_PATTERN_LEN:
        return {"ok": False,
                "error": f"pattern too long ({len(pattern)} > "
                         f"{_MAX_PATTERN_LEN} chars). Split it into "
                         f"multiple narrower patterns."}
    _ensure_tables()
    # Validate pattern compiles
    try:
        re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"invalid regex: {e}"}
    try:
        with _db.db_conn() as cx:
            cur = cx.execute("""
                INSERT INTO content_blocklist
                (pattern, hash_hex, reason, added_at, added_by)
                VALUES (?, ?, ?, ?, ?)
            """, (pattern[:500], "", (reason or "")[:500],
                  time.time(), (added_by or "operator")[:50]))
        _audit("block_added", pattern, reason)
        return {"ok": True, "id": cur.lastrowid}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def block_hash(hash_hex: str, *, reason: str = "",
              added_by: str = "operator") -> dict:
    """Block by perceptual-hash. Matches anything within hamming
    distance 0 of the given hash. Use dedup.find_duplicates for
    softer matching."""
    if not hash_hex:
        return {"ok": False, "error": "hash_hex required"}
    _ensure_tables()
    try:
        with _db.db_conn() as cx:
            cur = cx.execute("""
                INSERT INTO content_blocklist
                (pattern, hash_hex, reason, added_at, added_by)
                VALUES (?, ?, ?, ?, ?)
            """, ("", hash_hex.lower()[:128], (reason or "")[:500],
                  time.time(), (added_by or "operator")[:50]))
        _audit("block_added", f"hash:{hash_hex}", reason)
        return {"ok": True, "id": cur.lastrowid}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def remove_block(block_id: int) -> dict:
    """Remove a blocklist entry by id."""
    _ensure_tables()
    try:
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT pattern, hash_hex FROM content_blocklist WHERE id = ?",
                (int(block_id),)).fetchone()
            cur = cx.execute(
                "DELETE FROM content_blocklist WHERE id = ?",
                (int(block_id),))
            removed = cur.rowcount > 0
        if removed and row:
            target = row["pattern"] or f"hash:{row['hash_hex']}"
            _audit("block_removed", target, "")
        return {"ok": True, "removed": removed}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def list_blocks() -> list:
    _ensure_tables()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT id, pattern, hash_hex, reason, added_at, added_by
                FROM content_blocklist
                ORDER BY added_at DESC
            """).fetchall()
        return [dict(r) for r in rs]
    except Exception:
        return []


def url_is_blocked(url: str) -> Optional[dict]:
    """Return a matching block, ``None`` for measured no-match, or UNKNOWN.

    UNKNOWN is a truthy result with ``blocked=None`` and ``unknown=True`` so a
    legacy truthiness caller refuses rather than admitting work.  Callers that
    render the result can distinguish the unavailable measurement from a real
    policy match.

    v3.46.4 F9: URL is truncated to _MAX_URL_LEN_FOR_MATCH before
    regex evaluation; patterns longer than _MAX_PATTERN_LEN are
    skipped. This caps the time complexity of any single match,
    preventing catastrophic-backtracking DoS via crafted patterns."""
    if not url:
        return None
    # Truncate the URL for matching purposes only — we don't modify
    # the URL going forward, just bound the work the regex engine does.
    url_for_match = url[:_MAX_URL_LEN_FOR_MATCH]
    try:
        _ensure_tables()
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT id, pattern, reason FROM content_blocklist
                WHERE pattern != ''
            """).fetchall()
        for r in rs:
            pat = r["pattern"] or ""
            if len(pat) > _MAX_PATTERN_LEN:
                continue  # pathologically long; skip
            try:
                if re.search(pat, url_for_match):
                    return {"id": r["id"], "pattern": pat,
                            "reason": r["reason"]}
            except re.error:
                continue  # corrupt pattern; skip
        return None
    except Exception as e:
        return {
            "blocked": None,
            "unknown": True,
            "error": f"content-rights blocklist unreadable: {e}"[:200],
        }


def hash_is_blocked(hash_hex: str) -> Optional[dict]:
    """Check if hash exactly matches any blocked hash."""
    if not hash_hex:
        return None
    _ensure_tables()
    try:
        with _db.db_conn() as cx:
            row = cx.execute("""
                SELECT id, reason FROM content_blocklist
                WHERE hash_hex = ?
            """, (hash_hex.lower(),)).fetchone()
        if row:
            return {"id": row["id"], "reason": row["reason"]}
        return None
    except Exception:
        return None


def record_refusal(target: str, reason: str = "blocklist match"):
    """Log that a download was refused. Public so the runner can
    call it when url_is_blocked() returns non-None."""
    _audit("download_refused", target, reason)


def record_purge(target: str, reason: str = ""):
    """Log a file deletion done in response to a takedown."""
    _audit("file_purged", target, reason)


def audit_log(*, limit: int = 200, kind: Optional[str] = None) -> list:
    """Recent audit entries."""
    _ensure_tables()
    try:
        with _db.db_conn() as cx:
            if kind:
                rs = cx.execute("""
                    SELECT id, kind, target, reason, ts
                    FROM content_rights_audit
                    WHERE kind = ?
                    ORDER BY id DESC LIMIT ?
                """, (kind, int(limit))).fetchall()
            else:
                rs = cx.execute("""
                    SELECT id, kind, target, reason, ts
                    FROM content_rights_audit
                    ORDER BY id DESC LIMIT ?
                """, (int(limit),)).fetchall()
        return [dict(r) for r in rs]
    except Exception:
        return []


def license_check(url: str, *, site_cfg: Optional[dict] = None) -> dict:
    """Soft check: is this URL on a paywalled site without configured
    account access? Returns {ok, warning?}. Non-blocking."""
    if not site_cfg:
        return {"ok": True}
    if not site_cfg.get("paywalled"):
        return {"ok": True}
    has_account = bool(site_cfg.get("username")
                      or (site_cfg.get("accounts") or [])
                      or site_cfg.get("cookie_file"))
    if not has_account:
        return {"ok": True,
                "warning": ("Site is marked paywalled but no account / "
                            "cookies / vault credential is configured. "
                            "Download may produce a low-res preview or "
                            "captcha challenge.")}
    return {"ok": True}
