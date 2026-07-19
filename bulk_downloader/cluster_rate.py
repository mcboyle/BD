"""Cluster-wide rate limits (Phase 144, Block Q).

When multiple BD instances are federated (Phase 120), each instance
has local rate limits — but a site might still see N×rate_limit total
because the instances don't coordinate. This module provides a
shared rate-limit ledger usable across federated instances.

Two coordination modes:

  • soft_local — fast path, local-only counter (current BD default).
    No coordination, lowest latency, doesn't scale across instances.

  • shared_db — multi-instance lease through the shared DB. Each
    request reserves a token slot; the limit is enforced across all
    instances reading the same DB. Requires shared DB access (federated
    instances pointing at the same SQLite over NFS, or a Postgres
    setup down the line).

The shared_db mode uses an explicit lease table:
  cluster_rate_leases(
    site_id TEXT,
    leased_at REAL,
    expires_at REAL,
    instance_id TEXT
  )

When checking the budget, count valid leases for the site in the
last N seconds. If under the limit, insert a lease. Otherwise wait
or reject.
"""
from __future__ import annotations

import sys
import time
import uuid
from typing import Optional


_INSTANCE_ID = str(uuid.uuid4())[:8]


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS cluster_rate_leases(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                leased_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )""")
            cx.execute("""CREATE INDEX IF NOT EXISTS idx_cluster_lease
                          ON cluster_rate_leases(site_id, expires_at)""")
    except Exception as e:
        sys.stderr.write(f"[cluster_rate] schema init: {e}\n")


def acquire_lease(site_id: str, *,
                 max_concurrent: int,
                 lease_seconds: int = 300) -> dict:
    """Try to acquire a rate-limit lease. Returns
    {ok, lease_id, active_count, max_concurrent}.

    Caller MUST release the lease (or let it expire) when done.

    `max_concurrent` is the cluster-wide cap. If `active_count` is
    already at the cap, this returns ok=False — caller should retry
    or queue."""
    if not site_id or max_concurrent <= 0:
        return {"ok": False, "error": "site_id and max_concurrent required"}
    _ensure_table()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            # Sweep expired leases (cheap)
            cx.execute("DELETE FROM cluster_rate_leases WHERE expires_at < ?",
                       (now,))
            # Count active
            r = cx.execute("""SELECT COUNT(*) FROM cluster_rate_leases
                              WHERE site_id = ? AND expires_at >= ?""",
                          (site_id, now)).fetchone()
            active = int(r[0] or 0)
            if active >= max_concurrent:
                return {"ok": False, "active_count": active,
                        "max_concurrent": max_concurrent}
            cur = cx.execute("""INSERT INTO cluster_rate_leases(
                site_id, instance_id, leased_at, expires_at
            ) VALUES (?,?,?,?)""",
                (site_id, _INSTANCE_ID, now, now + lease_seconds))
            return {
                "ok": True,
                "lease_id": cur.lastrowid,
                "active_count": active + 1,
                "max_concurrent": max_concurrent,
                "expires_at": now + lease_seconds,
            }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def release_lease(lease_id: int) -> bool:
    """Release a lease early (before its TTL). Idempotent."""
    if not lease_id:
        return False
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""DELETE FROM cluster_rate_leases
                                WHERE id = ?""", (int(lease_id),))
            return cur.rowcount > 0
    except Exception:
        return False


def current_load(site_id: Optional[str] = None) -> dict:
    """Snapshot active leases by site."""
    _ensure_table()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("DELETE FROM cluster_rate_leases WHERE expires_at < ?",
                       (now,))
            if site_id:
                rows = cx.execute("""SELECT site_id, COUNT(*) AS n,
                                            COUNT(DISTINCT instance_id) AS instances
                                      FROM cluster_rate_leases
                                      WHERE expires_at >= ? AND site_id = ?
                                      GROUP BY site_id""",
                                  (now, site_id)).fetchall()
            else:
                rows = cx.execute("""SELECT site_id, COUNT(*) AS n,
                                            COUNT(DISTINCT instance_id) AS instances
                                      FROM cluster_rate_leases
                                      WHERE expires_at >= ?
                                      GROUP BY site_id""",
                                  (now,)).fetchall()
        out = {}
        for r in rows:
            d = dict(r)
            out[d["site_id"]] = {
                "active_leases": int(d["n"]),
                "instances": int(d["instances"]),
            }
        return out
    except Exception:
        return {}


class lease_context:
    """Context manager: acquire on enter, release on exit.

    Usage:
        with lease_context('vixen', max_concurrent=3) as lease:
            if not lease['ok']:
                return  # over capacity, try later
            do_work()
    """
    def __init__(self, site_id: str, *, max_concurrent: int,
                 lease_seconds: int = 300):
        self.site_id = site_id
        self.max_concurrent = max_concurrent
        self.lease_seconds = lease_seconds
        self.lease = None

    def __enter__(self):
        self.lease = acquire_lease(
            self.site_id,
            max_concurrent=self.max_concurrent,
            lease_seconds=self.lease_seconds,
        )
        return self.lease

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lease and self.lease.get("ok") and self.lease.get("lease_id"):
            release_lease(self.lease["lease_id"])
        return False
