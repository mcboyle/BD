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

import sys
import threading

# Row 1068: the partition shards are an index over THIS ledger. Only an
# absent module turns sharding off, and it says why; a module that is present
# but broken raises here rather than being mistaken for "not installed".
_SHARDING_IMPORT_ERROR: Optional[str] = None
try:
    from . import ledger_sharding as _ledger_sharding
    _SHARDING_AVAILABLE = True
except ImportError as _e:
    _ledger_sharding = None
    _SHARDING_AVAILABLE = False
    _SHARDING_IMPORT_ERROR = repr(_e)[:200]
    sys.stderr.write(f"[provenance] ledger sharding unavailable: {_SHARDING_IMPORT_ERROR}\n")

# The shard mirror is rebuilt from the durable table the first time it is
# needed in a process, then kept current by record(). _SHARD_MIRROR_THROUGH is
# the highest provenance.id the mirror holds (None = not built yet), so a row
# is never mirrored twice and never skipped.
_SHARD_LOCK = threading.RLock()
_SHARD_MIRROR: dict = {"router": None, "through": None}
_SHARD_WRITE_FAILURES = 0
_RECORD_LOCK = threading.Lock()


class ShardingUnavailable(RuntimeError):
    """The partition shards could not be consulted (rule 6: not "found none")."""


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
    # One writer at a time: the chain link reads the previous row's hash,
    # so two unserialized writers would both link to the same parent and
    # fork the durable chain (row1068: verify now checks that chain).
    with _RECORD_LOCK:
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
                row_id = cur.lastrowid
                _mirror_row(row_id, row)
                return row_id
        except Exception as e:
            import sys
            sys.stderr.write(f"[provenance] record failed: {e}\n")
            return None


def shard_write_failures() -> int:
    """Rows that reached the durable ledger but not its shard mirror."""
    return _SHARD_WRITE_FAILURES


def _mirror_row(row_id, row: dict) -> None:
    """Append a just-recorded row to the shard mirror, if the mirror exists.
    A failure is counted and logged: the durable row stands, and the next
    verify_sharded_chains() reports the partition that diverged."""
    global _SHARD_WRITE_FAILURES
    if not _SHARDING_AVAILABLE or _ledger_sharding is None:
        return
    with _SHARD_LOCK:
        through = _SHARD_MIRROR["through"]
        if (_SHARD_MIRROR["router"] is not _ledger_sharding.get_ledger_router()
                or through is None or row_id is None or row_id <= through):
            return  # mirror not built yet: the rebuild will read this row
        try:
            _ledger_sharding.record_sharded_ledger_entry(
                tenant_id=row["site_id"], url=row["source_url"], data=row,
                timestamp=row["ts_finished"])
        except Exception as e:
            _SHARD_WRITE_FAILURES += 1
            sys.stderr.write(f"[provenance] shard write failed for row {row_id}: {e}\n")
        _SHARD_MIRROR["through"] = row_id


def _iter_ledger_rows(*, batch_size: int = 1000):
    """Durable rows in id order, as record() built them (id split off).
    Every row: a tenant is selected by the router's own key (chain_heads),
    never by SQL, whose lower() folds a site id differently."""
    _ensure_table()
    from . import db as _db
    last_id = 0
    while True:
        with _db.db_conn() as cx:
            rows = cx.execute(
                "SELECT * FROM provenance WHERE id > ? ORDER BY id ASC LIMIT ?",
                (last_id, batch_size)).fetchall()
        if not rows:
            return
        for r in rows:
            row = dict(r)
            last_id = row.pop("id")
            yield last_id, row


def _replay(router, rows) -> None:
    for _row_id, row in rows:
        router.record_entry(row["site_id"], row["source_url"], row,
                            timestamp=row["ts_finished"])


def _sharded_router():
    """The live shard router, rebuilt from the durable ledger on first use in
    this process. Raises ShardingUnavailable when it cannot be consulted."""
    if not _SHARDING_AVAILABLE or _ledger_sharding is None:
        raise ShardingUnavailable(
            f"ledger sharding unavailable: {_SHARDING_IMPORT_ERROR}")
    with _SHARD_LOCK:
        router = _ledger_sharding.get_ledger_router()
        if _SHARD_MIRROR["router"] is router and _SHARD_MIRROR["through"] is not None:
            return router
        try:
            router.reset()
            through = 0
            for row_id, row in _iter_ledger_rows():
                _replay(router, [(row_id, row)])
                through = row_id
        except Exception as e:
            router.reset()
            _SHARD_MIRROR.update(router=None, through=None)
            raise ShardingUnavailable(f"shard rebuild from ledger failed: {e}"[:200])
        _SHARD_MIRROR.update(router=router, through=through)
        return router


def query_sharded(
    *,
    tenant_id: Optional[str] = None,
    domain: Optional[str] = None,
    epoch_from: Optional[int] = None,
    epoch_to: Optional[int] = None,
    sha256: Optional[str] = None,
    filename: Optional[str] = None,
    url: Optional[str] = None,
    limit: int = 100,
) -> list:
    """Look up partitioned provenance rows by tenant, domain, or epoch range.
    [] means the shards were consulted and nothing matched; a lookup that
    could not be made raises ShardingUnavailable."""
    return _sharded_router().query_entries(
        tenant_id=tenant_id,
        domain=domain,
        epoch_from=epoch_from,
        epoch_to=epoch_to,
        sha256=sha256,
        filename=filename,
        url=url,
        limit=limit,
    )


def get_sharded_partitions(tenant_id: Optional[str] = None) -> list[dict]:
    """Metadata for the ledger partition shards (raises ShardingUnavailable)."""
    return _sharded_router().list_partitions_metadata(tenant_id=tenant_id)


def verify_sharded_chains(tenant_id: Optional[str] = None) -> dict:
    """Verify every partition's chain AND that each partition still matches
    the durable ledger it indexes.

    status: "verified" (valid True), "tampered" (valid False), "empty"
    (nothing to verify, valid None) or "unavailable" (could not look, valid
    None). A partition is tampered when its own chain breaks, when the
    durable rows it was built from changed, were deleted or were reordered
    since (a fresh rebuild differs), or when verify_chain() fails on a row in
    it -- the last one also catches edits made before this process started.
    verify_chain() stops at its first bad row, so nothing after that row was
    checked: a broken durable chain is never "verified". A break not pinned
    to a partition in scope is "tampered" for the whole ledger and
    "unavailable" for one tenant, whose later rows went unchecked.
    """
    try:
        live = _sharded_router()
        report = live.verify_all_partitions(tenant_id=tenant_id)
        rebuilt = _ledger_sharding.MultiTenantLedgerRouter(
            epoch_duration_seconds=live.epoch_duration_seconds)
        _replay(rebuilt, _iter_ledger_rows())
        durable = verify_chain()
    except ShardingUnavailable as e:
        return {"valid": None, "status": "unavailable", "verified_count": 0,
                "tampered_partitions": [], "error": str(e)}
    except Exception as e:
        return {"valid": None, "status": "unavailable", "verified_count": 0,
                "tampered_partitions": [], "error": f"{e}"[:200]}
    tampered = set(report["tampered_partitions"])
    heads_live = live.chain_heads(tenant_id=tenant_id)
    heads_durable = rebuilt.chain_heads(tenant_id=tenant_id)
    tampered |= {pid for pid in set(heads_live) | set(heads_durable)
                 if heads_live.get(pid) != heads_durable.get(pid)}
    if not durable.get("ok") and durable.get("first_bad_id") is not None:
        pid = _partition_of_row(live, durable["first_bad_id"])
        if pid and (tenant_id is None or pid in heads_live or pid in heads_durable):
            tampered.add(pid)
    tampered_list = sorted(tampered)
    unpinned = {}
    if tampered_list:
        status, valid = "tampered", False
    elif not durable.get("ok"):
        whole_ledger = not (tenant_id or "").strip()  # the router's "no tenant filter"
        if whole_ledger and durable.get("first_bad_id") is not None:
            status, valid = "tampered", False
        else:
            status, valid = "unavailable", None
        unpinned["error"] = f"durable chain not verified: {durable.get('message')}"[:200]
    elif report["verified_count"] == 0:
        status, valid = "empty", None
    else:
        status, valid = "verified", True
    return {"valid": valid, "status": status,
            "verified_count": report["verified_count"],
            "tampered_partitions": tampered_list,
            "durable_chain": durable, **unpinned}


def _partition_of_row(router, row_id) -> Optional[str]:
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("SELECT site_id, source_url, ts_finished FROM "
                           "provenance WHERE id = ?", (row_id,)).fetchone()
        if r is None:
            return None
        r = dict(r)
        return router.resolve_shard_key(r["site_id"], r["source_url"],
                                        r["ts_finished"]).partition_id
    except Exception:
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
