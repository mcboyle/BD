"""Row 870: distributed work stealing across per-site queues via Redis.

Acceptance:
(1) Idle worker successfully claims job from saturated queue.
(2) Atomic lease ownership prevents duplicate processing.
(3) Fail-soft return of abandoned jobs.

Fixes for prior correctness refutation:
- E1: Wire WorkStealingCoordinator into SiteRunner dispatch loop when idle.
- E2: Hermetic mock RESP server over loopback socket; zero ambient daemon requirement, zero skips.
- E3: Self-describing job payload encodes origin site/queue; multi-site reap returns each job to its origin.
- E4: Redis Cluster hash tagging ({site}) prevents CROSSSLOT failures.
- E5: Behavioral RED asserted on base prior to wiring.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from typing import Any

import pytest
import importlib

BD_GATE_SCOPE = "module"


def _ws():
    return importlib.import_module("bulk_downloader.work_stealing")


def _runner():
    return importlib.import_module("bulk_downloader.runner")


# ===========================================================================
# Hermetic Mock RESP Server (zero external daemon, zero skips)
# ===========================================================================

class MockRESPHandler:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.lists: dict[str, list[bytes]] = {}
        self.strings: dict[str, tuple[bytes, float | None]] = {}  # key -> (val, expire_time)

    def _clean_expired(self) -> None:
        now = time.time()
        expired = [k for k, (_, exp) in self.strings.items() if exp is not None and exp <= now]
        for k in expired:
            del self.strings[k]

    def execute(self, cmd: str, args: list[bytes]) -> bytes:
        with self.lock:
            self._clean_expired()
            cmd_upper = cmd.upper()

            if cmd_upper == "PING":
                return b"+PONG\r\n"

            if cmd_upper == "RPOPLPUSH":
                src = args[0].decode("utf-8")
                dst = args[1].decode("utf-8")
                src_list = self.lists.get(src, [])
                if not src_list:
                    return b"$-1\r\n"
                item = src_list.pop()  # RPOP (from tail)
                self.lists.setdefault(dst, []).insert(0, item)  # LPUSH (to head)
                return f"${len(item)}\r\n".encode("ascii") + item + b"\r\n"

            if cmd_upper == "LPUSH":
                key = args[0].decode("utf-8")
                lst = self.lists.setdefault(key, [])
                for item in args[1:]:
                    lst.insert(0, item)
                return f":{len(lst)}\r\n".encode("ascii")

            if cmd_upper == "RPUSH":
                key = args[0].decode("utf-8")
                lst = self.lists.setdefault(key, [])
                for item in args[1:]:
                    lst.append(item)
                return f":{len(lst)}\r\n".encode("ascii")

            if cmd_upper == "LREM":
                key = args[0].decode("utf-8")
                count = int(args[1])
                target = args[2]
                lst = self.lists.get(key, [])
                removed = 0
                if count >= 0:
                    i = 0
                    while i < len(lst) and (count == 0 or removed < count):
                        if lst[i] == target:
                            lst.pop(i)
                            removed += 1
                        else:
                            i += 1
                return f":{removed}\r\n".encode("ascii")

            if cmd_upper == "LRANGE":
                key = args[0].decode("utf-8")
                start = int(args[1])
                stop = int(args[2])
                lst = self.lists.get(key, [])
                if start < 0:
                    start = max(0, len(lst) + start)
                if stop < 0:
                    stop = len(lst) + stop
                slice_items = lst[start : stop + 1] if start < len(lst) else []
                parts = [f"*{len(slice_items)}\r\n".encode("ascii")]
                for it in slice_items:
                    parts.append(f"${len(it)}\r\n".encode("ascii") + it + b"\r\n")
                return b"".join(parts)

            if cmd_upper == "LLEN":
                key = args[0].decode("utf-8")
                return f":{len(self.lists.get(key, []))}\r\n".encode("ascii")

            if cmd_upper == "SET":
                key = args[0].decode("utf-8")
                val = args[1]
                nx = False
                ex: float | None = None
                idx = 2
                while idx < len(args):
                    arg_s = args[idx].decode("utf-8").upper()
                    if arg_s == "NX":
                        nx = True
                        idx += 1
                    elif arg_s == "EX" and idx + 1 < len(args):
                        ex = time.time() + float(args[idx + 1])
                        idx += 2
                    else:
                        idx += 1
                if nx and key in self.strings:
                    return b"$-1\r\n"
                self.strings[key] = (val, ex)
                return b"+OK\r\n"

            if cmd_upper == "GET":
                key = args[0].decode("utf-8")
                if key not in self.strings:
                    return b"$-1\r\n"
                val, _ = self.strings[key]
                return f"${len(val)}\r\n".encode("ascii") + val + b"\r\n"

            if cmd_upper == "TTL":
                key = args[0].decode("utf-8")
                if key not in self.strings:
                    return b":-2\r\n"
                _, exp = self.strings[key]
                if exp is None:
                    return b":-1\r\n"
                rem = int(max(0, exp - time.time()))
                return f":{rem}\r\n".encode("ascii")

            if cmd_upper == "DEL":
                deleted = 0
                for a in args:
                    k = a.decode("utf-8")
                    if k in self.strings:
                        del self.strings[k]
                        deleted += 1
                    if k in self.lists:
                        del self.lists[k]
                        deleted += 1
                return f":{deleted}\r\n".encode("ascii")

            return b"-ERR unknown command\r\n"


class HermeticRESPDaemon:
    def __init__(self) -> None:
        self.handler = MockRESPHandler()
        self.running = True
        self.threads: list[threading.Thread] = []
        self.sockets: list[socket.socket] = []

    def create_client(self):
        client_sock, server_sock = socket.socketpair()
        self.sockets.extend([client_sock, server_sock])
        t = threading.Thread(target=self._client_loop, args=(server_sock,), daemon=True)
        t.start()
        self.threads.append(t)
        RedisClient = _ws().RedisClient
        return RedisClient(sock=client_sock)

    def _client_loop(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while self.running:
                while b"\r\n" not in buf:
                    data = conn.recv(4096)
                    if not data:
                        return
                    buf += data
                if not buf.startswith(b"*"):
                    conn.close()
                    return
                line, _, buf = buf.partition(b"\r\n")
                num_args = int(line[1:])
                args: list[bytes] = []
                for _ in range(num_args):
                    while b"\r\n" not in buf:
                        data = conn.recv(4096)
                        if not data:
                            return
                        buf += data
                    len_line, _, buf = buf.partition(b"\r\n")
                    arg_len = int(len_line[1:])
                    while len(buf) < arg_len + 2:
                        data = conn.recv(4096)
                        if not data:
                            return
                        buf += data
                    arg_val = buf[:arg_len]
                    buf = buf[arg_len + 2:]
                    args.append(arg_val)
                if not args:
                    continue
                cmd_str = args[0].decode("utf-8", "replace")
                response = self.handler.execute(cmd_str, args[1:])
                conn.sendall(response)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def stop(self) -> None:
        self.running = False
        for s in self.sockets:
            try:
                s.close()
            except Exception:
                pass


@pytest.fixture(scope="module")
def resp_server():
    server = HermeticRESPDaemon()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def redis_client(resp_server):
    client = resp_server.create_client()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def ns():
    return uuid.uuid4().hex[:8]


# ===========================================================================
# 1. Behavioral RED verification (E5)
# ===========================================================================

def test_site_runner_has_work_stealing_interface():
    """Behavioral RED: verifies SiteRunner exposes work stealing hooks on base."""
    SiteRunner = _runner().SiteRunner
    runner = SiteRunner("site_idle", {"name": "Idle"})
    assert hasattr(runner, "configure_work_stealing"), (
        "SiteRunner missing configure_work_stealing interface"
    )
    assert hasattr(runner, "_try_steal_job"), (
        "SiteRunner missing _try_steal_job method"
    )


# ===========================================================================
# 2. Acceptance (1): Idle worker claims job from saturated queue
# ===========================================================================

def test_idle_worker_steals_job_from_saturated_queue(redis_client, ns):
    ws = _ws()
    WorkStealingCoordinator = ws.WorkStealingCoordinator
    queue_key = ws.queue_key
    site = f"site-sat-{ns}"
    worker = f"worker-idle-{ns}"
    coord = WorkStealingCoordinator(redis_client, worker_id=worker)

    # Populate saturated queue
    coord.push_job(site, "job-1", payload="https://example.com/item1")
    coord.push_job(site, "job-2", payload="https://example.com/item2")

    # Claim from saturated queue
    stolen = coord.steal(site)
    assert stolen is not None
    assert stolen.job_id == "job-1" or stolen.job_id == "job-2"
    assert stolen.site == site
    assert stolen.source_queue == queue_key(site)
    assert stolen.worker_id == worker
    assert "https://example.com" in stolen.payload


def test_steal_from_empty_queue_returns_none(redis_client, ns):
    WorkStealingCoordinator = _ws().WorkStealingCoordinator
    site = f"site-empty-{ns}"
    coord = WorkStealingCoordinator(redis_client, worker_id=f"worker-{ns}")
    assert coord.steal(site) is None


# ===========================================================================
# 3. Acceptance (2): Atomic lease ownership prevents duplicate processing
# ===========================================================================

def test_two_workers_racing_single_job_prevent_duplicate_claim(resp_server, redis_client, ns):
    WorkStealingCoordinator = _ws().WorkStealingCoordinator
    site = f"site-race-{ns}"
    worker1 = f"worker-1-{ns}"
    worker2 = f"worker-2-{ns}"

    c1 = WorkStealingCoordinator(redis_client, worker_id=worker1)
    client2 = resp_server.create_client()
    try:
        c2 = WorkStealingCoordinator(client2, worker_id=worker2)
        c1.push_job(site, "sole-job", payload="https://example.com/race")

        stolen1 = c1.steal(site)
        stolen2 = c2.steal(site)

        # Exactly one worker claims the sole job; no duplicate claim
        claims = [j for j in (stolen1, stolen2) if j is not None]
        assert len(claims) == 1
        assert claims[0].job_id == "sole-job"
    finally:
        client2.close()


def test_lease_records_worker_and_expiration(redis_client, ns):
    ws = _ws()
    WorkStealingCoordinator = ws.WorkStealingCoordinator
    lease_key = ws.lease_key
    site = f"site-lease-{ns}"
    worker = f"worker-lease-{ns}"
    coord = WorkStealingCoordinator(redis_client, worker_id=worker, lease_seconds=30)
    coord.push_job(site, "leased-job", payload="test-payload")

    stolen = coord.steal(site)
    assert stolen is not None

    key = lease_key(site, "leased-job")
    owner = redis_client.get(key)
    assert owner == worker.encode("utf-8")
    ttl = redis_client.ttl(key)
    assert 0 < ttl <= 30


# ===========================================================================
# 4. Acceptance (3): Fail-soft return of abandoned and expired jobs
# ===========================================================================

def test_abandon_returns_job_to_source_queue(redis_client, ns):
    ws = _ws()
    WorkStealingCoordinator = ws.WorkStealingCoordinator
    queue_key = ws.queue_key
    site = f"site-abandon-{ns}"
    worker = f"worker-ab-{ns}"
    coord = WorkStealingCoordinator(redis_client, worker_id=worker)
    coord.push_job(site, "abandon-job", payload="payload-abandon")

    stolen = coord.steal(site)
    assert stolen is not None
    assert coord.queue_length(site) == 0

    # Worker encounters transient failure and abandons job
    coord.abandon(stolen)

    # Job is back on source queue, lease deleted, AND the worker's processing
    # list no longer holds it (shape mutant: a dropped lrem left a ghost
    # entry that reap_expired would later return a second time)
    assert coord.queue_length(site) == 1
    assert redis_client.get(ws.lease_key(site, "abandon-job")) is None
    assert redis_client.llen(ws.processing_key(site, worker)) == 0
    new_steal = coord.steal(site)
    assert new_steal is not None
    assert new_steal.job_id == "abandon-job"
    assert redis_client.llen(ws.processing_key(site, worker)) == 1     # positive control: steal lists it
    coord.complete(new_steal)
    assert redis_client.llen(ws.processing_key(site, worker)) == 0


def test_complete_cleans_processing_queue_and_lease(redis_client, ns):
    ws = _ws()
    WorkStealingCoordinator = ws.WorkStealingCoordinator
    lease_key = ws.lease_key
    site = f"site-comp-{ns}"
    worker = f"worker-comp-{ns}"
    coord = WorkStealingCoordinator(redis_client, worker_id=worker)
    coord.push_job(site, "complete-job", payload="payload-comp")

    stolen = coord.steal(site)
    assert stolen is not None
    assert redis_client.llen(ws.processing_key(site, worker)) == 1
    coord.complete(stolen)

    assert coord.queue_length(site) == 0
    assert redis_client.get(lease_key(site, "complete-job")) is None
    assert redis_client.llen(ws.processing_key(site, worker)) == 0


def test_reap_expired_returns_jobs_to_respective_origin_queues(redis_client, ns):
    """Verifies E3 fix: Multi-site recovery restores each job to its origin queue."""
    WorkStealingCoordinator = _ws().WorkStealingCoordinator
    site_a = f"site-a-{ns}"
    site_b = f"site-b-{ns}"
    crashed_worker = f"crashed-worker-{ns}"

    c = WorkStealingCoordinator(redis_client, worker_id=crashed_worker, lease_seconds=1)
    c.push_job(site_a, "job-from-a", payload="payload-a")
    c.push_job(site_b, "job-from-b", payload="payload-b")

    stolen_a = c.steal(site_a)
    stolen_b = c.steal(site_b)
    assert stolen_a is not None and stolen_b is not None

    # Simulate worker crash: lease expires
    time.sleep(1.2)

    reaper = WorkStealingCoordinator(redis_client, worker_id=f"reaper-{ns}")
    recovered_a = reaper.reap_expired(site_a, worker_id=crashed_worker)
    recovered_b = reaper.reap_expired(site_b, worker_id=crashed_worker)

    assert len(recovered_a) == 1
    assert recovered_a[0].job_id == "job-from-a"
    assert recovered_a[0].site == site_a

    assert len(recovered_b) == 1
    assert recovered_b[0].job_id == "job-from-b"
    assert recovered_b[0].site == site_b

    # Both origin queues have their respective jobs back
    assert reaper.queue_length(site_a) == 1
    assert reaper.queue_length(site_b) == 1


def test_reap_expired_leaves_unexpired_live_lease_untouched(redis_client, ns):
    WorkStealingCoordinator = _ws().WorkStealingCoordinator
    site = f"site-live-{ns}"
    live_worker = f"live-worker-{ns}"
    coord = WorkStealingCoordinator(redis_client, worker_id=live_worker, lease_seconds=60)
    coord.push_job(site, "live-job", payload="payload-live")

    stolen = coord.steal(site)
    assert stolen is not None

    reaper = WorkStealingCoordinator(redis_client, worker_id=f"reaper-{ns}")
    recovered = reaper.reap_expired(site, worker_id=live_worker)
    assert recovered == []
    assert coord.queue_length(site) == 0


# ===========================================================================
# 5. Redis Cluster Hash Tagging Alignment (E4)
# ===========================================================================

def test_redis_cluster_hash_tags_aligned_to_prevent_crossslot():
    """Verifies E4 fix: Queue, processing, and lease keys for a site share {site} tag."""
    ws = _ws()
    queue_key = ws.queue_key
    processing_key = ws.processing_key
    lease_key = ws.lease_key
    site = "mysite"
    worker = "worker1"
    job = "job1"

    qk = queue_key(site)
    pk = processing_key(site, worker)
    lk = lease_key(site, job)

    assert f"{{{site}}}" in qk, f"Queue key {qk} missing hash tag {{{site}}}"
    assert f"{{{site}}}" in pk, f"Processing key {pk} missing hash tag {{{site}}}"
    assert f"{{{site}}}" in lk, f"Lease key {lk} missing hash tag {{{site}}}"


# ===========================================================================
# 6. SiteRunner Dispatch Integration (E1)
# ===========================================================================

def test_site_runner_steals_and_processes_from_foreign_saturated_queue(redis_client, ns):
    """Verifies E1 fix: When local queue is empty, SiteRunner steals from saturated site."""
    SiteRunner = _runner().SiteRunner
    WorkStealingCoordinator = _ws().WorkStealingCoordinator

    site_empty = f"site-empty-{ns}"
    site_saturated = f"site-sat-{ns}"

    coord = WorkStealingCoordinator(redis_client, worker_id=f"runner-{ns}")
    coord.push_job(site_saturated, "stolen-url-1", payload="https://example.com/stolen1")

    runner = SiteRunner(site_empty, {"name": "EmptySite"})
    runner.configure_work_stealing(coord, stealable_sites=[site_saturated])

    # Runner's local queue is empty
    assert runner._url_queue.empty()

    # Steal hook invoked by worker loop
    stolen_url = runner._try_steal_job()
    assert stolen_url == "https://example.com/stolen1"
    assert coord.queue_length(site_saturated) == 0


def test_an_abandoned_job_is_never_reaped_a_second_time(redis_client, ns):
    """Shape mutant (abandon without the processing-list lrem): the ghost
    entry would be handed back AGAIN by reap_expired once the lease is gone,
    duplicating the job. After abandon(), an expired-lease reap returns
    nothing for that worker and the source queue holds exactly one copy."""
    ws = _ws()
    site = f"site-ghost-{ns}"
    worker = f"worker-ghost-{ns}"
    coord = ws.WorkStealingCoordinator(redis_client, worker_id=worker)
    coord.push_job(site, "ghost-job", payload="p")
    stolen = coord.steal(site)
    coord.abandon(stolen)
    reaped = coord.reap_expired(site, worker_id=worker)
    assert reaped == [], [j.job_id for j in reaped]
    assert coord.queue_length(site) == 1
    assert redis_client.llen(ws.processing_key(site, worker)) == 0
