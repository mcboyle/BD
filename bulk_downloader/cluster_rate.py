"""Cluster-wide rate limits (Phase 144, Block Q, Row 828).

When multiple BD instances are federated (Phase 120), each instance
has local rate limits — but a site might still see N×rate_limit total
because the instances don't coordinate. This module provides a
shared rate-limit ledger usable across federated instances.

Three coordination modes:

  • soft_local — fast path, local-only counter (current BD default).
    No coordination, lowest latency, doesn't scale across instances.

  • shared_db — multi-instance lease through the shared DB. Each
    request reserves a token slot; the limit is enforced across all
    instances reading the same DB. Requires shared DB access (federated
    instances pointing at the same SQLite over NFS, or a Postgres
    setup down the line).

  • redis — distributed sliding-window lease across federated instances
    utilizing atomic sorted sets (ZREMRANGEBYSCORE/ZCARD/ZADD). Zero disk I/O,
    sub-millisecond command latency (<0.5ms), atomic rollback on denial, with
    fail-soft degradation to soft_local on Redis connection loss.

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

import os
import socket
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple, Union
import uuid

_INSTANCE_ID = str(uuid.uuid4())[:8]

# Thread-safe in-memory storage for soft_local coordination mode
_LOCAL_LOCK = threading.Lock()
_LOCAL_LEASES: Dict[str, Dict[str, float]] = {}

# Thread-local storage for persistent Redis socket connections
_THREAD_LOCAL = threading.local()

_ACQUIRE_LUA_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires_at = tonumber(ARGV[2])
local max_concurrent = tonumber(ARGV[3])
local lease_id = ARGV[4]

-- 1. Sweep expired leases outside sliding window
redis.call('ZREMRANGEBYSCORE', key, '-inf', '(' .. now)

-- 2. Count active leases
local active = tonumber(redis.call('ZCARD', key) or 0)
if active >= max_concurrent then
    -- Atomic rollback: zero changes made
    return {0, active}
end

-- 3. Add lease with expiration timestamp as score
redis.call('ZADD', key, expires_at, lease_id)
return {1, active + 1}
"""


def _read_resp(rfile: Any) -> Any:
    """Parse RESP wire protocol line from Redis server."""
    line = rfile.readline()
    if not line:
        raise ConnectionResetError("Empty response from Redis server")
    prefix = line[:1]
    if prefix == b"+":
        return line[1:-2].decode("utf-8", errors="replace")
    if prefix == b":":
        return int(line[1:-2])
    if prefix == b"-":
        raise RuntimeError(line[1:-2].decode("utf-8", errors="replace"))
    if prefix == b"$":
        length = int(line[1:-2])
        if length == -1:
            return None
        data = rfile.read(length + 2)
        return data[:-2].decode("utf-8", errors="replace")
    if prefix == b"*":
        count = int(line[1:-2])
        if count == -1:
            return None
        return [_read_resp(rfile) for _ in range(count)]
    raise ValueError(f"Unknown RESP prefix: {prefix!r}")


def _get_redis_conn(host: str, port: int, timeout: float = 1.0) -> Tuple[socket.socket, Any]:
    """Retrieve or establish a thread-local TCP connection to Redis."""
    conns = getattr(_THREAD_LOCAL, "conns", None)
    if conns is None:
        conns = _THREAD_LOCAL.conns = {}
    conn_key = f"{host}:{port}"
    if conn_key in conns:
        s, rfile = conns[conn_key]
        return s, rfile

    s = socket.create_connection((host, port), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    rfile = s.makefile("rb")
    conns[conn_key] = (s, rfile)
    return s, rfile


def _close_redis_conn(host: str, port: int) -> None:
    """Close and discard thread-local connection on error."""
    conns = getattr(_THREAD_LOCAL, "conns", None)
    if conns:
        conn_key = f"{host}:{port}"
        sock_tuple = conns.pop(conn_key, None)
        if sock_tuple:
            s, rfile = sock_tuple
            try:
                rfile.close()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass


def _redis_cmd(
    *args: Any,
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = 1.0,
) -> Any:
    """Execute Redis command over thread-local socket using zero-dependency RESP protocol."""
    h = host or os.environ.get("BD_REDIS_HOST", "127.0.0.1")
    p = int(port or os.environ.get("BD_REDIS_PORT", 6379))
    try:
        s, rfile = _get_redis_conn(h, p, timeout=timeout)
        buf = [f"*{len(args)}\r\n".encode("utf-8")]
        for a in args:
            if isinstance(a, str):
                b = a.encode("utf-8")
            elif isinstance(a, bytes):
                b = a
            else:
                b = str(a).encode("utf-8")
            buf.append(f"${len(b)}\r\n".encode("utf-8"))
            buf.append(b + b"\r\n")
        s.sendall(b"".join(buf))
        return _read_resp(rfile)
    except Exception:
        _close_redis_conn(h, p)
        raise


def _acquire_soft_local(
    site_id: str,
    *,
    max_concurrent: int,
    lease_seconds: int = 300,
    degraded: bool = False,
) -> dict:
    """In-memory rate limit lease acquisition for soft_local mode."""
    now = time.time()
    with _LOCAL_LOCK:
        site_dict = _LOCAL_LEASES.setdefault(site_id, {})
        # Sweep expired leases
        expired = [lid for lid, exp in site_dict.items() if exp < now]
        for lid in expired:
            del site_dict[lid]

        active = len(site_dict)
        if active >= max_concurrent:
            res: dict = {
                "ok": False,
                "active_count": active,
                "max_concurrent": max_concurrent,
                "mode": "soft_local",
            }
            if degraded:
                res["degraded"] = True
            return res

        lease_id = f"local:{site_id}:{_INSTANCE_ID}:{uuid.uuid4().hex[:8]}"
        site_dict[lease_id] = now + lease_seconds
        res = {
            "ok": True,
            "lease_id": lease_id,
            "active_count": active + 1,
            "max_concurrent": max_concurrent,
            "expires_at": now + lease_seconds,
            "mode": "soft_local",
        }
        if degraded:
            res["degraded"] = True
        return res


def _release_soft_local(site_id: Optional[str], lease_id: str) -> bool:
    """In-memory rate limit lease release."""
    with _LOCAL_LOCK:
        if site_id and site_id in _LOCAL_LEASES:
            return _LOCAL_LEASES[site_id].pop(lease_id, None) is not None
        for _, site_dict in _LOCAL_LEASES.items():
            if lease_id in site_dict:
                del site_dict[lease_id]
                return True
        return False


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


def acquire_lease(
    site_id: str,
    *,
    max_concurrent: int,
    lease_seconds: int = 300,
    coordination_mode: Optional[str] = None,
    redis_host: Optional[str] = None,
    redis_port: Optional[int] = None,
    redis_timeout: float = 1.0,
) -> dict:
    """Try to acquire a rate-limit lease. Returns
    {ok, lease_id, active_count, max_concurrent, ...}.

    Caller MUST release the lease (or let it expire) when done.

    `max_concurrent` is the cluster-wide cap. If `active_count` is
    already at the cap, this returns ok=False — caller should retry
    or queue.

    Coordination modes:
      - 'redis': Atomic sorted-set sliding window in Redis (ZREMRANGEBYSCORE/ZCARD/ZADD).
      - 'soft_local': In-memory process-local dictionary.
      - 'shared_db': Multi-instance lease through shared SQLite DB (default).
    """
    if not site_id or max_concurrent <= 0:
        return {"ok": False, "error": "site_id and max_concurrent required"}

    mode = coordination_mode or os.environ.get("BD_CLUSTER_RATE_MODE", "shared_db")

    if mode == "redis":
        key = f"bd:cluster_rate:{site_id}"
        now = time.time()
        expires_at = now + lease_seconds
        lease_id = f"redis:{site_id}:{_INSTANCE_ID}:{uuid.uuid4().hex[:8]}"

        try:
            # Atomic evaluation in Redis via EVAL (ZREMRANGEBYSCORE/ZCARD/ZADD)
            res = _redis_cmd(
                "EVAL",
                _ACQUIRE_LUA_SCRIPT,
                1,
                key,
                str(now),
                str(expires_at),
                str(max_concurrent),
                lease_id,
                host=redis_host,
                port=redis_port,
                timeout=redis_timeout,
            )
            granted, active_count = int(res[0]), int(res[1])
            if granted == 1:
                return {
                    "ok": True,
                    "lease_id": lease_id,
                    "active_count": active_count,
                    "max_concurrent": max_concurrent,
                    "expires_at": expires_at,
                    "mode": "redis",
                }
            return {
                "ok": False,
                "active_count": active_count,
                "max_concurrent": max_concurrent,
                "mode": "redis",
            }
        except Exception as e:
            # Fail-soft degradation to soft_local on Redis connection loss
            sys.stderr.write(f"[cluster_rate] Redis unavailable ({e}), degrading to soft_local\n")
            return _acquire_soft_local(
                site_id,
                max_concurrent=max_concurrent,
                lease_seconds=lease_seconds,
                degraded=True,
            )

    elif mode == "soft_local":
        return _acquire_soft_local(
            site_id,
            max_concurrent=max_concurrent,
            lease_seconds=lease_seconds,
            degraded=False,
        )

    # Default: shared_db (SQLite table)
    _ensure_table()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            # Sweep expired leases (cheap)
            cx.execute("DELETE FROM cluster_rate_leases WHERE expires_at < ?", (now,))
            # Count active
            r = cx.execute(
                """SELECT COUNT(*) FROM cluster_rate_leases
                   WHERE site_id = ? AND expires_at >= ?""",
                (site_id, now),
            ).fetchone()
            active = int(r[0] or 0)
            if active >= max_concurrent:
                return {
                    "ok": False,
                    "active_count": active,
                    "max_concurrent": max_concurrent,
                    "mode": "shared_db",
                }
            cur = cx.execute(
                """INSERT INTO cluster_rate_leases(
                site_id, instance_id, leased_at, expires_at
            ) VALUES (?,?,?,?)""",
                (site_id, _INSTANCE_ID, now, now + lease_seconds),
            )
            return {
                "ok": True,
                "lease_id": cur.lastrowid,
                "active_count": active + 1,
                "max_concurrent": max_concurrent,
                "expires_at": now + lease_seconds,
                "mode": "shared_db",
            }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "mode": "shared_db"}


def release_lease(
    lease_id: Union[int, str],
    *,
    site_id: Optional[str] = None,
    coordination_mode: Optional[str] = None,
    redis_host: Optional[str] = None,
    redis_port: Optional[int] = None,
    redis_timeout: float = 1.0,
) -> bool:
    """Release a lease early (before its TTL). Idempotent."""
    if not lease_id:
        return False

    str_lease = str(lease_id)
    if str_lease.startswith("redis:") or coordination_mode == "redis":
        target_site = site_id
        if not target_site and str_lease.startswith("redis:"):
            parts = str_lease.split(":")
            if len(parts) >= 2:
                target_site = parts[1]
        if not target_site:
            return False
        key = f"bd:cluster_rate:{target_site}"
        try:
            res = _redis_cmd("ZREM", key, str_lease, host=redis_host, port=redis_port, timeout=redis_timeout)
            return int(res or 0) > 0
        except Exception:
            return False

    if str_lease.startswith("local:") or coordination_mode == "soft_local":
        return _release_soft_local(site_id, str_lease)

    # Default: shared_db (SQLite table)
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute(
                """DELETE FROM cluster_rate_leases
                   WHERE id = ?""",
                (int(lease_id),),
            )
            return cur.rowcount > 0
    except Exception:
        return False


def current_load(
    site_id: Optional[str] = None,
    *,
    coordination_mode: Optional[str] = None,
    redis_host: Optional[str] = None,
    redis_port: Optional[int] = None,
    redis_timeout: float = 1.0,
) -> dict:
    """Snapshot active leases by site."""
    mode = coordination_mode or os.environ.get("BD_CLUSTER_RATE_MODE", "shared_db")

    if mode == "redis":
        now = time.time()
        out = {}
        if site_id:
            key = f"bd:cluster_rate:{site_id}"
            try:
                _redis_cmd("ZREMRANGEBYSCORE", key, "-inf", f"({now}", host=redis_host, port=redis_port, timeout=redis_timeout)
                cnt = int(_redis_cmd("ZCARD", key, host=redis_host, port=redis_port, timeout=redis_timeout) or 0)
                out[site_id] = {
                    "active_leases": cnt,
                    "mode": "redis",
                }
            except Exception:
                pass
        return out

    if mode == "soft_local":
        now = time.time()
        out = {}
        with _LOCAL_LOCK:
            sites = [site_id] if site_id else list(_LOCAL_LEASES.keys())
            for s in sites:
                if s in _LOCAL_LEASES:
                    active = [lid for lid, exp in _LOCAL_LEASES[s].items() if exp >= now]
                    out[s] = {"active_leases": len(active), "mode": "soft_local"}
        return out

    # Default: shared_db (SQLite table)
    _ensure_table()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("DELETE FROM cluster_rate_leases WHERE expires_at < ?", (now,))
            if site_id:
                rows = cx.execute(
                    """SELECT site_id, COUNT(*) AS n,
                              COUNT(DISTINCT instance_id) AS instances
                       FROM cluster_rate_leases
                       WHERE expires_at >= ? AND site_id = ?
                       GROUP BY site_id""",
                    (now, site_id),
                ).fetchall()
            else:
                rows = cx.execute(
                    """SELECT site_id, COUNT(*) AS n,
                              COUNT(DISTINCT instance_id) AS instances
                       FROM cluster_rate_leases
                       WHERE expires_at >= ?
                       GROUP BY site_id""",
                    (now,),
                ).fetchall()
        out = {}
        for r in rows:
            d = dict(r)
            out[d["site_id"]] = {
                "active_leases": int(d["n"]),
                "instances": int(d["instances"]),
                "mode": "shared_db",
            }
        return out
    except Exception:
        return {}


class lease_context:
    """Context manager: acquire on enter, release on exit.

    Usage:
        with lease_context('vixen', max_concurrent=3, coordination_mode='redis') as lease:
            if not lease['ok']:
                return  # over capacity, try later
            do_work()
    """

    def __init__(
        self,
        site_id: str,
        *,
        max_concurrent: int,
        lease_seconds: int = 300,
        coordination_mode: Optional[str] = None,
        **kwargs: Any,
    ):
        self.site_id = site_id
        self.max_concurrent = max_concurrent
        self.lease_seconds = lease_seconds
        self.coordination_mode = coordination_mode
        self.extra_kwargs = kwargs
        self.lease: Optional[dict] = None

    def __enter__(self) -> Optional[dict]:
        self.lease = acquire_lease(
            self.site_id,
            max_concurrent=self.max_concurrent,
            lease_seconds=self.lease_seconds,
            coordination_mode=self.coordination_mode,
            **self.extra_kwargs,
        )
        return self.lease

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if self.lease and self.lease.get("ok") and self.lease.get("lease_id"):
            release_lease(
                self.lease["lease_id"],
                site_id=self.site_id,
                coordination_mode=self.lease.get("mode") or self.coordination_mode,
                **self.extra_kwargs,
            )
        return False
