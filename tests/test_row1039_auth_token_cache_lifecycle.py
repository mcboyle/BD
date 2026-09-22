"""RED/GREEN test for row1039: AuthTokenCache lifecycle -- expired entries are evicted.

Row 894 delivered the cache; its lifecycle leaked. On 5f74ea9c21ee an entry that
expired read back as None but stayed stored forever, and every invalidate()
left a generation counter behind for good. In a long-running process that
sees many (egress_ip, domain) pairs, both maps only grow.

ACCEPTANCE (RULING-2138, OPTION A): (1) an expired entry is evicted when read;
(2) expired entries nobody reads again are pruned on the next write;
(3) the generation map holds a key only while a fetch for it is in flight;
(4) unexpired entries survive the prune (negative control);
(5) an invalidate during an in-flight fetch still discards that fetch's result.
"""

import threading

from bulk_downloader.login_impl.token_manager import AuthTokenCache

BD_GATE_SCOPE = "module"

N = 1000


def _pairs(n, prefix):
    return [(f"10.0.{i // 250}.{i % 250}", f"{prefix}{i}.test") for i in range(n)]


def _clocked():
    now = {"t": 0.0}
    return AuthTokenCache(clock=lambda: now["t"]), now


def test_expired_clearance_is_evicted_on_read():
    cache, now = _clocked()
    pairs = _pairs(N, "d")
    for ip, domain in pairs:
        cache.set_clearance(ip, domain, "ck", ttl_seconds=10)
    assert len(cache._clearance) == N  # the fixture built the shape

    now["t"] = 100.0
    misses = sum(1 for ip, domain in pairs if cache.get_clearance(ip, domain) is None)

    assert misses == N
    assert len(cache._clearance) == 0


def test_expired_token_is_evicted_on_read():
    cache, now = _clocked()
    pairs = _pairs(N, "t")
    for ip, domain in pairs:
        cache.set(ip, domain, "tok", ttl_seconds=10)
    assert len(cache._entries) == N

    now["t"] = 100.0
    misses = sum(1 for ip, domain in pairs if cache.get(ip, domain) is None)

    assert misses == N
    assert len(cache._entries) == 0


def test_unread_expired_entries_pruned_on_next_write_unexpired_survive():
    cache, now = _clocked()
    short = _pairs(N, "s")
    long_ = _pairs(N // 2, "l")
    for ip, domain in short:
        cache.set_clearance(ip, domain, "ck", ttl_seconds=10)
        cache.set(ip, domain, "tok", ttl_seconds=10)
    for ip, domain in long_:
        cache.set_clearance(ip, domain, "ck-long", ttl_seconds=1000)
        cache.set(ip, domain, "tok-long", ttl_seconds=1000)
    assert len(cache._clearance) == N + N // 2
    assert len(cache._entries) == N + N // 2

    now["t"] = 100.0
    cache.set_clearance("192.0.2.1", "fresh.test", "ck-new", ttl_seconds=10)
    cache.set("192.0.2.1", "fresh.test", "tok-new", ttl_seconds=10)

    # the N short-lived entries are gone; the N/2 long-lived ones plus the new one remain
    assert len(cache._clearance) == N // 2 + 1
    assert len(cache._entries) == N // 2 + 1
    assert all(cache.get_clearance(ip, d) == "ck-long" for ip, d in long_)
    assert all(cache.get(ip, d) == "tok-long" for ip, d in long_)


def test_generation_map_is_bounded_without_inflight_fetch():
    cache, _ = _clocked()
    for ip, domain in _pairs(N, "g"):
        cache.invalidate_clearance(ip, domain)
        cache.invalidate(ip, domain)

    assert len(cache._generation) == 0


def test_invalidate_during_inflight_fetch_still_discards_stale_result():
    cache, _ = _clocked()
    started = threading.Event()
    release = threading.Event()

    def slow_fetch():
        started.set()
        assert release.wait(5)
        return "stale", 60.0

    worker = threading.Thread(target=lambda: cache.get("198.51.100.9", "race.test", slow_fetch))
    worker.start()
    assert started.wait(5)
    assert len(cache._generation) == 0  # nothing bumped yet: the key stays at the implicit 0
    cache.invalidate("198.51.100.9", "race.test")
    assert len(cache._generation) == 1  # held while the fetch is in flight
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert cache.get("198.51.100.9", "race.test") is None
    assert len(cache._generation) == 0


def test_failed_fetch_releases_its_inflight_slot():
    cache, _ = _clocked()

    def boom():
        raise RuntimeError("fetch failed")

    for _ in range(3):
        try:
            cache.get("198.51.100.10", "err.test", boom)
        except RuntimeError:
            pass
    # a leaked in-flight slot would keep the key's generation alive after this
    cache.invalidate("198.51.100.10", "err.test")

    assert len(cache._generation) == 0
