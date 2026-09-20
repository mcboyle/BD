"""Tests for Row 828: REDIS-SLIDING-WINDOW-DISTRIBUTED-CLUSTER-RATE-LIMITER.

Verifies:
1. Sliding-window concurrency enforcement across concurrent simulated instances.
2. Atomic rollback on lease denial.
3. Fail-soft degradation to soft_local on Redis connection loss.
"""
import concurrent.futures
import os
import socket
import socketserver
import threading
import time
import uuid
import pytest
from bulk_downloader import cluster_rate

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Redis endpoint for the redis-mode tests. The row declares DEPENDENCY: none,
# so the file must be green on a host with no Redis (the O1016 band hosts have
# none). A real server is used when one is reachable (BD_REDIS_HOST, or
# 127.0.0.1:6379); otherwise _MiniRedis -- an in-process RESP server that
# implements exactly the sorted-set commands cluster_rate issues, and whose
# EVAL accepts ONLY the product's own acquire script byte-for-byte, so a
# change to the Lua fails loudly here instead of being silently faked.
# ---------------------------------------------------------------------------
def _resp_read(rfile):
    line = rfile.readline()
    if not line:
        return None
    prefix, body = line[:1], line[1:-2]
    if prefix == b"*":
        return [_resp_read(rfile) for _ in range(int(body))]
    if prefix == b"$":
        n = int(body)
        if n < 0:
            return None
        data = rfile.read(n)
        rfile.read(2)
        return data
    if prefix in (b":", b"+"):
        return body
    raise ValueError(f"unexpected RESP prefix {prefix!r}")


def _resp_write(value):
    if value is None:
        return b"$-1\r\n"
    if isinstance(value, bool):
        return f":{int(value)}\r\n".encode()
    if isinstance(value, int):
        return f":{value}\r\n".encode()
    if isinstance(value, (list, tuple)):
        return f"*{len(value)}\r\n".encode() + b"".join(_resp_write(v) for v in value)
    if isinstance(value, Exception):
        return f"-ERR {value}\r\n".encode()
    raise TypeError(type(value))


class _MiniRedis(socketserver.ThreadingTCPServer):
    """Sorted sets + the one EVAL script cluster_rate uses, atomic under a lock."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _MiniRedisHandler)
        self.zsets = {}
        self.lock = threading.Lock()

    def _zremrangebyscore(self, key, lo, hi):
        z = self.zsets.get(key, {})
        exclusive = hi.startswith("(")
        bound = float(hi.lstrip("("))
        assert lo == "-inf", lo
        gone = [m for m, s in z.items() if (s < bound if exclusive else s <= bound)]
        for m in gone:
            del z[m]
        return len(gone)

    def execute(self, argv):
        cmd = argv[0].decode().upper()
        args = [a.decode() for a in argv[1:]]
        with self.lock:
            if cmd == "ZADD":
                key, score, member = args[0], float(args[1]), args[2]
                z = self.zsets.setdefault(key, {})
                added = member not in z
                z[member] = score
                return int(added)
            if cmd == "ZREM":
                z = self.zsets.get(args[0], {})
                return int(z.pop(args[1], None) is not None)
            if cmd == "ZCARD":
                return len(self.zsets.get(args[0], {}))
            if cmd == "ZREMRANGEBYSCORE":
                return self._zremrangebyscore(*args[:3])
            if cmd == "EVAL":
                script, numkeys = args[0], int(args[1])
                if script != cluster_rate._ACQUIRE_LUA_SCRIPT:
                    return ValueError("unknown script -- _MiniRedis only runs cluster_rate._ACQUIRE_LUA_SCRIPT")
                key = args[2]
                now, expires_at, max_concurrent, lease_id = float(args[3]), float(args[4]), int(args[5]), args[6]
                self._zremrangebyscore(key, "-inf", f"({now}")
                active = len(self.zsets.get(key, {}))
                if active >= max_concurrent:
                    return [0, active]
                self.zsets.setdefault(key, {})[lease_id] = expires_at
                return [1, active + 1]
            return ValueError(f"unsupported command {cmd}")


class _MiniRedisHandler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            try:
                argv = _resp_read(self.rfile)
            except (ValueError, ConnectionError):
                return
            if argv is None:
                return
            self.wfile.write(_resp_write(self.server.execute(argv)))
            self.wfile.flush()


def _reachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def redis_endpoint(monkeypatch):
    """Point cluster_rate at a real Redis when one is reachable, else at _MiniRedis."""
    host = os.environ.get("BD_REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("BD_REDIS_PORT", 6379))
    if _reachable(host, port):
        yield ("real", host, port)
        return
    server = _MiniRedis()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BD_REDIS_HOST", server.server_address[0])
    monkeypatch.setenv("BD_REDIS_PORT", str(server.server_address[1]))
    # cluster_rate caches a thread-local socket per host:port; a fresh port per
    # fixture means no stale connection can be reused across servers.
    try:
        yield ("mini", *server.server_address)
    finally:
        server.shutdown()
        server.server_close()


def test_row828_sliding_window_concurrency_across_instances():
    """Verify sliding-window concurrency enforcement with coordination_mode='redis'."""
    site_id = f"test_site_{uuid.uuid4().hex[:8]}"
    max_concurrent = 3

    # Acquire up to cap
    l1 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
    assert l1["ok"] is True
    assert l1["active_count"] == 1
    assert "lease_id" in l1

    l2 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
    assert l2["ok"] is True
    assert l2["active_count"] == 2

    l3 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
    assert l3["ok"] is True
    assert l3["active_count"] == 3

    # 4th must be denied (at cap)
    l4 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
    assert l4["ok"] is False
    assert l4["active_count"] == 3
    assert l4["max_concurrent"] == 3

    # Release one lease
    released = cluster_rate.release_lease(l2["lease_id"], site_id=site_id, coordination_mode="redis")
    assert released is True

    # Now a new lease should succeed
    l5 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
    assert l5["ok"] is True
    assert l5["active_count"] == 3

    # Cleanup
    cluster_rate.release_lease(l1["lease_id"], site_id=site_id, coordination_mode="redis")
    cluster_rate.release_lease(l3["lease_id"], site_id=site_id, coordination_mode="redis")
    cluster_rate.release_lease(l5["lease_id"], site_id=site_id, coordination_mode="redis")


class _ShiftedClock:
    """cluster_rate's clock, advanced by a fixed offset: the sliding window is
    scored by the ``now`` the client passes to the Lua script, so moving the
    client clock past ``expires_at`` is exactly what a real TTL expiry looks
    like to Redis -- without waiting for wall time to pass (T4)."""

    def __init__(self, offset_seconds):
        self._offset = offset_seconds

    def time(self):
        return time.time() + self._offset

    def __getattr__(self, name):
        return getattr(time, name)


def test_row828_sliding_window_expiration_sweeps(monkeypatch):
    """Verify expired leases in sliding window are automatically swept by timestamp."""
    site_id = f"test_site_exp_{uuid.uuid4().hex[:8]}"
    max_concurrent = 1

    # Acquire lease with 1 second TTL
    l1 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=1, coordination_mode="redis")
    assert l1["ok"] is True

    # Immediate second attempt should be denied
    l2 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=1, coordination_mode="redis")
    assert l2["ok"] is False

    # Advance the client clock past the 1s TTL (no wall-clock wait)
    monkeypatch.setattr(cluster_rate, "time", _ShiftedClock(1.5))

    # Next attempt should succeed as expired lease was swept
    l3 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=5, coordination_mode="redis")
    assert l3["ok"] is True
    cluster_rate.release_lease(l3["lease_id"], site_id=site_id, coordination_mode="redis")


def test_row828_atomic_rollback_on_lease_denial():
    """Verify atomic state: denied lease does not leave ghost records or alter active count."""
    site_id = f"test_site_rb_{uuid.uuid4().hex[:8]}"
    max_concurrent = 2

    l1 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
    l2 = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
    assert l1["ok"] is True
    assert l2["ok"] is True

    # Attempt multiple denied acquisitions
    for _ in range(5):
        denied = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=10, coordination_mode="redis")
        assert denied["ok"] is False
        assert denied["active_count"] == 2

    # Verify load snapshot reflects exactly 2 active leases
    load = cluster_rate.current_load(site_id=site_id, coordination_mode="redis")
    assert site_id in load
    assert load[site_id]["active_leases"] == 2

    cluster_rate.release_lease(l1["lease_id"], site_id=site_id, coordination_mode="redis")
    cluster_rate.release_lease(l2["lease_id"], site_id=site_id, coordination_mode="redis")


def test_row828_concurrent_instances_thread_safety():
    """Verify concurrent simulated instances never exceed max_concurrent."""
    site_id = f"test_site_conc_{uuid.uuid4().hex[:8]}"
    max_concurrent = 4
    num_threads = 16

    active_gauge = 0
    max_observed = 0
    lock = threading.Lock()
    errors = []
    # Holders keep their lease until the cap has been observed once (or a 1s
    # safety bound), so the overlap the test measures is produced by the
    # leases themselves rather than by an arbitrary sleep.
    cap_reached = threading.Event()

    def worker_task():
        nonlocal active_gauge, max_observed
        for _ in range(10):
            res = cluster_rate.acquire_lease(site_id, max_concurrent=max_concurrent, lease_seconds=2, coordination_mode="redis")
            if res.get("ok"):
                with lock:
                    active_gauge += 1
                    if active_gauge > max_observed:
                        max_observed = active_gauge
                    if active_gauge > max_concurrent:
                        errors.append(f"Exceeded cap: active={active_gauge} > {max_concurrent}")
                    if active_gauge == max_concurrent:
                        cap_reached.set()
                cap_reached.wait(1.0)
                with lock:
                    active_gauge -= 1
                cluster_rate.release_lease(res["lease_id"], site_id=site_id, coordination_mode="redis")

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_task) for _ in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert not errors, f"Concurrency violations observed: {errors}"
    assert max_observed <= max_concurrent
    assert max_observed > 0


def test_row828_failsoft_degradation_to_soft_local_on_connection_loss():
    """Verify fail-soft fallback to soft_local when Redis is unreachable."""
    site_id = f"test_site_degrade_{uuid.uuid4().hex[:8]}"
    max_concurrent = 2

    # Use an invalid Redis port to simulate connection loss
    lease = cluster_rate.acquire_lease(
        site_id,
        max_concurrent=max_concurrent,
        lease_seconds=5,
        coordination_mode="redis",
        redis_port=65534,  # Dead port
    )

    # Must not raise an exception, must degrade fail-soft to soft_local
    assert lease["ok"] is True
    assert lease.get("degraded") is True
    assert lease.get("mode") == "soft_local"

    # Local cap enforcement still works in degraded mode
    lease2 = cluster_rate.acquire_lease(
        site_id,
        max_concurrent=max_concurrent,
        lease_seconds=5,
        coordination_mode="redis",
        redis_port=65534,
    )
    assert lease2["ok"] is True

    # 3rd should be denied locally
    lease3 = cluster_rate.acquire_lease(
        site_id,
        max_concurrent=max_concurrent,
        lease_seconds=5,
        coordination_mode="redis",
        redis_port=65534,
    )
    assert lease3["ok"] is False

    cluster_rate.release_lease(lease["lease_id"], site_id=site_id, coordination_mode="soft_local")
    cluster_rate.release_lease(lease2["lease_id"], site_id=site_id, coordination_mode="soft_local")


def test_row828_lease_context_manager_redis():
    """Verify lease_context manager supports coordination_mode='redis'."""
    site_id = f"test_site_ctx_{uuid.uuid4().hex[:8]}"

    with cluster_rate.lease_context(site_id, max_concurrent=1, lease_seconds=10, coordination_mode="redis") as lease:
        assert lease["ok"] is True
        # Second attempt while inside context must be blocked
        denied = cluster_rate.acquire_lease(site_id, max_concurrent=1, lease_seconds=10, coordination_mode="redis")
        assert denied["ok"] is False

    # Exited context: lease was auto-released, next acquire must succeed
    next_lease = cluster_rate.acquire_lease(site_id, max_concurrent=1, lease_seconds=10, coordination_mode="redis")
    assert next_lease["ok"] is True
    cluster_rate.release_lease(next_lease["lease_id"], site_id=site_id, coordination_mode="redis")


def test_row828_repeated_acquire_release_leaves_no_residue():
    """50 acquire/release round trips all succeed and leave the window empty
    (a functional check; a wall-clock latency bound is not an acceptance)."""
    site_id = f"test_site_perf_{uuid.uuid4().hex[:8]}"
    for _ in range(50):
        res = cluster_rate.acquire_lease(site_id, max_concurrent=100, lease_seconds=10, coordination_mode="redis")
        assert res["ok"] is True and res["active_count"] == 1, res
        assert cluster_rate.release_lease(res["lease_id"], site_id=site_id, coordination_mode="redis") is True
    load = cluster_rate.current_load(site_id=site_id, coordination_mode="redis")
    assert load[site_id]["active_leases"] == 0, load
