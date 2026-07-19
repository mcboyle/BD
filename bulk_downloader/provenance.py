"""Provenance ledger — append-only audit trail per download.

Phase 104, Block N (Archive & Forensics).

For each completed download, record an immutable provenance row:
  • Source URL (and resolved/redirected URL if different)
  • Site ID + account username used
  • VPN endpoint + IP at time of download (if VPN was active)
  • Final filename + size + SHA-256 hash
  • Timestamps: requested, started, finished
  • Mirror chain (which CDN host actually served bytes)

Append-only by policy: there is no update or delete API in this module
(only insert + query). For tamper-evident chaining, each row's
content hash is concatenated with the previous row's chain hash and
re-hashed, forming a Merkle-like chain. Verifying the chain is O(n)
and detects any mid-history modification.

Use cases:
  • "where did this file come from?" — query by filename or hash
  • "what did this account download in March?" — query by account + time
  • Bit-rot detection (Phase 105): rehash stored files, compare to
    ledger; mismatch flagged for repair
  • CDX archive lookups (Phase 106): for dead URLs, ledger preserves
    the original full URL so a Wayback fetch can target the exact path

Schema lives in db.py (added below as ledger_init / migrations). This
module is the public API; storage details are hidden.

Performance: at 100k downloads, ledger is ~50 MB. Indexed by url,
filename_hash, ts. Chain verification at that size takes ~2s; called
once a day or on-demand, not in the hot path.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Optional, Iterator


def _row_content_hash(row: dict) -> str:
    """Stable hash of a row's content (excluding the chain hash itself).
    Sort keys so dict ordering doesn't affect the hash."""
    payload = json.dumps({k: row[k] for k in sorted(row) if k != "chain_hash"},
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chain_hash(prev_chain: str, content_hash: str) -> str:
    """Chain hash = SHA256(prev_chain || content_hash). First row uses
    empty prev_chain so the chain is fully deterministic from row data."""
    return hashlib.sha256(
        ((prev_chain or "") + content_hash).encode("utf-8")
    ).hexdigest()


def _ensure_table():
    """Lazy table creation. Cheap when already exists. Centralized here
    rather than db.py so the ledger module is self-contained — if a
    user drops the ledger table, the next record() call recreates it."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS provenance(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                site_id TEXT NOT NULL,
                account TEXT DEFAULT '',
                source_url TEXT NOT NULL,
                resolved_url TEXT DEFAULT '',
                final_filename TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                sha256 TEXT DEFAULT '',
                vpn_endpoint TEXT DEFAULT '',
                external_ip TEXT DEFAULT '',
                mirror_host TEXT DEFAULT '',
                ts_requested REAL DEFAULT 0,
                ts_started REAL DEFAULT 0,
                ts_finished REAL DEFAULT 0,
                content_hash TEXT NOT NULL,
                chain_hash TEXT NOT NULL,
                extra_json TEXT DEFAULT ''
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_prov_url       ON provenance(source_url)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_prov_filename  ON provenance(final_filename)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_prov_sha256    ON provenance(sha256)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_prov_site_ts   ON provenance(site_id, ts DESC)")
    except Exception as e:
        import sys
        sys.stderr.write(f"[provenance] table init failed (will retry): {e}\n")


def _last_chain_hash() -> str:
    """Read the last row's chain_hash. Empty when the ledger is empty."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT chain_hash FROM provenance ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return ""
        return row["chain_hash"] if hasattr(row, "keys") else row[0]
    except Exception:
        return ""


def compute_sha256(path: str, *, chunk_size: int = 1 << 20) -> Optional[str]:
    """Stream a file through SHA-256. Returns hex digest or None on
    error. Caller decides when to call this — for big files this is
    several seconds; usually run from the post-download pipeline."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except OSError:
        return None


def record(
    *,
    site_id: str,
    source_url: str,
    final_filename: str,
    file_size: int = 0,
    sha256: str = "",
    resolved_url: str = "",
    account: str = "",
    vpn_endpoint: str = "",
    external_ip: str = "",
    mirror_host: str = "",
    ts_requested: float = 0,
    ts_started: float = 0,
    ts_finished: float = 0,
    extra: Optional[dict] = None,
) -> Optional[int]:
    """Append one provenance row. Returns the new row's id, or None
    on failure. Idempotency: the caller decides; we don't dedupe
    (the operator might legitimately re-download the same URL).

    Fail-open: a provenance write failure is logged but never raises.
    Losing audit data is bad; failing a download because audit broke
    is worse."""
    _ensure_table()
    now = time.time()
    row = {
        "ts": now,
        "site_id": site_id,
        "account": account,
        "source_url": source_url,
        "resolved_url": resolved_url,
        "final_filename": final_filename,
        "file_size": int(file_size or 0),
        "sha256": sha256,
        "vpn_endpoint": vpn_endpoint,
        "external_ip": external_ip,
        "mirror_host": mirror_host,
        "ts_requested": float(ts_requested or 0),
        "ts_started": float(ts_started or 0),
        "ts_finished": float(ts_finished or now),
        "extra_json": json.dumps(extra or {}, separators=(",", ":"), default=str)[:5000],
    }
    content_hash = _row_content_hash(row)
    prev = _last_chain_hash()
    chain_hash = _chain_hash(prev, content_hash)
    row["content_hash"] = content_hash
    row["chain_hash"] = chain_hash
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""INSERT INTO provenance(
                ts, site_id, account, source_url, resolved_url,
                final_filename, file_size, sha256, vpn_endpoint,
                external_ip, mirror_host, ts_requested, ts_started,
                ts_finished, content_hash, chain_hash, extra_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(row[k] for k in [
                    "ts", "site_id", "account", "source_url", "resolved_url",
                    "final_filename", "file_size", "sha256", "vpn_endpoint",
                    "external_ip", "mirror_host", "ts_requested", "ts_started",
                    "ts_finished", "content_hash", "chain_hash", "extra_json",
                ]))
            return cur.lastrowid
    except Exception as e:
        import sys
        sys.stderr.write(f"[provenance] record failed: {e}\n")
        return None


def query(
    *,
    url: Optional[str] = None,
    filename: Optional[str] = None,
    sha256: Optional[str] = None,
    site_id: Optional[str] = None,
    ts_from: Optional[float] = None,
    ts_to: Optional[float] = None,
    limit: int = 100,
) -> list:
    """Look up provenance rows by any combination of filters. Returns
    newest first. Empty list on miss or any error."""
    _ensure_table()
    sql = "SELECT * FROM provenance WHERE 1=1"
    params: list = []
    if url:
        sql += " AND source_url = ?"; params.append(url)
    if filename:
        sql += " AND final_filename = ?"; params.append(filename)
    if sha256:
        sql += " AND sha256 = ?"; params.append(sha256)
    if site_id:
        sql += " AND site_id = ?"; params.append(site_id)
    if ts_from:
        sql += " AND ts >= ?"; params.append(float(ts_from))
    if ts_to:
        sql += " AND ts <= ?"; params.append(float(ts_to))
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(int(limit))
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []


def verify_chain(*, batch_size: int = 1000) -> dict:
    """Walk the chain, recomputing each row's hashes from content.
    Returns {ok, checked, first_bad_id, message}. Detects: deleted
    rows (gap), modified content (content_hash mismatch), broken
    chain (chain_hash recompute mismatch).

    O(n) over rows. Streams in batches so memory stays bounded."""
    _ensure_table()
    try:
        from . import db as _db
    except Exception as e:
        return {"ok": False, "checked": 0, "first_bad_id": None,
                "message": f"db unavailable: {e}"}
    prev_chain = ""
    checked = 0
    last_id = 0
    while True:
        with _db.db_conn() as cx:
            rows = cx.execute(
                "SELECT * FROM provenance WHERE id > ? ORDER BY id ASC LIMIT ?",
                (last_id, batch_size)
            ).fetchall()
        if not rows:
            break
        for r in rows:
            row = dict(r)
            stored_content = row.pop("content_hash", "")
            stored_chain = row.pop("chain_hash", "")
            stored_id = row.pop("id")
            recomputed_content = _row_content_hash(row)
            if recomputed_content != stored_content:
                return {"ok": False, "checked": checked,
                        "first_bad_id": stored_id,
                        "message": f"row {stored_id}: content_hash mismatch"}
            recomputed_chain = _chain_hash(prev_chain, recomputed_content)
            if recomputed_chain != stored_chain:
                return {"ok": False, "checked": checked,
                        "first_bad_id": stored_id,
                        "message": f"row {stored_id}: chain_hash mismatch"}
            prev_chain = stored_chain
            checked += 1
            last_id = stored_id
    return {"ok": True, "checked": checked, "first_bad_id": None,
            "message": f"{checked} rows verified"}


def stats() -> dict:
    """Summary stats for /api/status: row count, total bytes, oldest/
    newest timestamps, distinct site count. Returns both
    `total_rows` (preferred — matches webhook/metrics naming) and
    `rows` (legacy alias for any older caller)."""
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) AS n,
                                       COALESCE(SUM(file_size), 0) AS total_bytes,
                                       MIN(ts) AS oldest,
                                       MAX(ts) AS newest,
                                       COUNT(DISTINCT site_id) AS sites
                                FROM provenance""").fetchone()
        if not row:
            return {"total_rows": 0, "rows": 0, "total_bytes": 0,
                    "oldest": None, "newest": None, "sites": 0}
        d = dict(row) if hasattr(row, "keys") else {
            "n": row[0], "total_bytes": row[1], "oldest": row[2],
            "newest": row[3], "sites": row[4]
        }
        n = d.get("n", 0)
        return {
            "total_rows": n,
            "rows": n,  # legacy alias
            "total_bytes": d.get("total_bytes", 0),
            "oldest": d.get("oldest"),
            "newest": d.get("newest"),
            "sites": d.get("sites", 0),
        }
    except Exception:
        return {"total_rows": 0, "rows": 0, "total_bytes": 0,
                "oldest": None, "newest": None, "sites": 0, "error": True}
