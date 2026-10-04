"""O1826 C14: work-stealing lease ownership (M188, M189).

M188: steal() ignored the SET NX result, so a job whose lease another worker
already held was handed out a second time.
M189: reap_expired() requeued a job even when LREM removed nothing (the owner
completed it after the LRANGE snapshot), and with no renew API every job
running past the lease was reaped while still in flight.

The fake below keeps Redis list/string semantics on a manual clock (no sleeps).
"""
from __future__ import annotations

import importlib
import math

BD_GATE_SCOPE = "module"

SITE = "siteA"


def _ws():
    return importlib.import_module("bulk_downloader.work_stealing")


class FakeRedis:
    """In-memory stand-in for the RedisClient surface the coordinator uses."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.lists: dict[str, list[bytes]] = {}
        self.strings: dict[str, tuple[bytes, float | None]] = {}
        self.after_lrange = None

    @staticmethod
    def _b(value) -> bytes:
        return value if isinstance(value, bytes) else str(value).encode("utf-8")

    def _live(self, key: str):
        item = self.strings.get(key)
        if item is not None and item[1] is not None and item[1] <= self.now:
            del self.strings[key]
            return None
        return item

    def lpush(self, key, *values):
        lst = self.lists.setdefault(key, [])
        for v in values:
            lst.insert(0, self._b(v))
        return len(lst)

    def rpush(self, key, *values):
        lst = self.lists.setdefault(key, [])
        lst.extend(self._b(v) for v in values)
        return len(lst)

    def rpoplpush(self, src, dst):
        lst = self.lists.get(src) or []
        if not lst:
            return None
        raw = lst.pop()
        self.lists.setdefault(dst, []).insert(0, raw)
        return raw

    def lrem(self, key, count, value):
        lst = self.lists.get(key) or []
        value = self._b(value)
        if value in lst:
            lst.remove(value)
            return 1
        return 0

    def lrange(self, key, start, stop):
        snapshot = list(self.lists.get(key) or [])
        if self.after_lrange is not None:
            hook, self.after_lrange = self.after_lrange, None
            hook()
        return snapshot

    def llen(self, key):
        return len(self.lists.get(key) or [])

    def set_nx_ex(self, key, value, ex_seconds):
        if self._live(key) is not None:
            return False
        self.strings[key] = (self._b(value), self.now + int(ex_seconds))
        return True

    def expire_if_value(self, key, value, ex_seconds):
        item = self._live(key)
        if item is None or item[0] != self._b(value):
            return False
        self.strings[key] = (item[0], self.now + int(ex_seconds))
        return True

    def get(self, key):
        item = self._live(key)
        return None if item is None else item[0]

    def ttl(self, key):
        item = self._live(key)
        if item is None:
            return -2
        if item[1] is None:
            return -1
        return math.ceil(item[1] - self.now)

    def delete(self, *keys):
        n = 0
        for k in keys:
            if self.strings.pop(k, None) is not None:
                n += 1
            if self.lists.pop(k, None) is not None:
                n += 1
        return n


def _pair(lease_seconds=60):
    ws = _ws()
    r = FakeRedis()
    a = ws.WorkStealingCoordinator(r, worker_id="A", lease_seconds=lease_seconds)
    b = ws.WorkStealingCoordinator(r, worker_id="B", lease_seconds=lease_seconds)
    return ws, r, a, b


def test_steal_skips_a_job_whose_lease_another_worker_holds():
    ws, r, a, b = _pair()
    a.push_job(SITE, "j1", "https://example.test/1")
    held = a.steal(SITE)
    assert held is not None and held.job_id == "j1"
    # A duplicate entry for j1 reaches the queue while A still holds the lease.
    a.push_job(SITE, "j1", "https://example.test/1")

    assert b.steal(SITE) is None
    assert r.get(ws.lease_key(SITE, "j1")) == b"A"
    assert r.lrange(ws.processing_key(SITE, "B"), 0, -1) == []
    assert r.lrange(ws.processing_key(SITE, "A"), 0, -1) == [held.raw_bytes]


def test_steal_moves_past_a_held_duplicate_to_the_next_free_job():
    ws, r, a, b = _pair()
    a.push_job(SITE, "j1")
    assert a.steal(SITE).job_id == "j1"
    a.push_job(SITE, "j1")  # duplicate, at the steal end
    a.push_job(SITE, "j2")

    got = b.steal(SITE)
    assert got is not None and got.job_id == "j2"
    assert r.get(ws.lease_key(SITE, "j2")) == b"B"
    assert r.lrange(ws.processing_key(SITE, "B"), 0, -1) == [got.raw_bytes]
    assert r.llen(ws.queue_key(SITE)) == 0


def test_free_job_is_stolen_exactly_once():
    ws, r, a, b = _pair()
    a.push_job(SITE, "j1")
    got = a.steal(SITE)
    assert got is not None and got.job_id == "j1" and got.worker_id == "A"
    assert b.steal(SITE) is None
    assert r.get(ws.lease_key(SITE, "j1")) == b"A"


def test_reap_does_not_requeue_a_job_completed_after_the_snapshot():
    ws, r, a, b = _pair()
    a.push_job(SITE, "j1")
    job = a.steal(SITE)
    r.now += 61  # lease lapses; A is slow but still finishes the job
    r.after_lrange = lambda: a.complete(job)

    assert b.reap_expired(SITE, worker_id="A") == []
    assert r.llen(ws.queue_key(SITE)) == 0


def test_reap_still_requeues_a_genuinely_expired_job():
    ws, r, a, b = _pair()
    a.push_job(SITE, "j1")
    job = a.steal(SITE)
    r.now += 61

    recovered = b.reap_expired(SITE, worker_id="A")
    assert [j.job_id for j in recovered] == ["j1"]
    assert r.lrange(ws.queue_key(SITE), 0, -1) == [job.raw_bytes]
    assert r.llen(ws.processing_key(SITE, "A")) == 0


def test_renewed_lease_keeps_a_long_job_from_being_reaped():
    ws, r, a, b = _pair()
    a.push_job(SITE, "j1")
    job = a.steal(SITE)
    for _ in range(4):  # 200s of work, renewing every 50s on a 60s lease
        r.now += 50
        assert a.renew(job) is True

    assert b.reap_expired(SITE, worker_id="A") == []
    assert r.llen(ws.queue_key(SITE)) == 0
    assert 0 < r.ttl(ws.lease_key(SITE, "j1")) <= 60


def test_renew_refuses_a_lease_held_by_another_worker():
    ws, r, a, b = _pair()
    a.push_job(SITE, "j1")
    job = a.steal(SITE)
    r.now += 61
    b.reap_expired(SITE, worker_id="A")
    taken = b.steal(SITE)
    assert taken is not None and taken.job_id == "j1"

    assert a.renew(job) is False
    assert r.get(ws.lease_key(SITE, "j1")) == b"B"


class _WireSocket:
    def __init__(self, reply: bytes) -> None:
        self.sent = b""
        self._reply = reply

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, n: int) -> bytes:
        out, self._reply = self._reply[:n], self._reply[n:]
        return out

    def close(self) -> None:
        pass


def test_expire_if_value_is_one_atomic_eval_on_the_wire():
    ws = _ws()
    sock = _WireSocket(b":1\r\n")
    client = ws.RedisClient(sock=sock)
    assert client.expire_if_value("k", "A", 30) is True
    sent = sock.sent
    assert sent.startswith(b"*6\r\n$4\r\nEVAL\r\n")
    assert b"redis.call('GET', KEYS[1]) == ARGV[1]" in sent
    assert b"redis.call('EXPIRE', KEYS[1], ARGV[2])" in sent
    assert sent.endswith(b"$1\r\n1\r\n$1\r\nk\r\n$1\r\nA\r\n$2\r\n30\r\n")

    assert ws.RedisClient(sock=_WireSocket(b":0\r\n")).expire_if_value("k", "A", 30) is False
