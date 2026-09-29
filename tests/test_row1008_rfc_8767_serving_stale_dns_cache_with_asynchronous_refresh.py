"""Unit tests for Row 1008: RFC 8767 Serving Stale DNS Cache with Asynchronous Refresh.

Guards:
- StaleDNSCache creation with valid/invalid parameters
- store/lookup lifecycle for fresh entries
- Stale entry served when TTL expired but within stale window
- Expired entry (past stale window) returns None
- Async refresh callback invoked for stale entries
- Refresh updates entry to fresh state
- Refresh failure leaves stale entry in place
- Only one refresh in-flight per key
- Invalidate and clear operations
- Capacity eviction (expired first, then oldest stale)
- Statistics tracking (hits_fresh, hits_stale, misses, refreshes)
- StaleEntry.state() transitions
- EntryState and RefreshResult enums
- Metadata introspection
"""
from __future__ import annotations

import threading
import time

import pytest

# H622 anti-orphan convention: scoped module test, collected by bd-test-shard
BD_GATE_SCOPE = "module"


def test_dns_resolver_has_stale_serve_wiring():
    """Row 1008 RED test: dns_resolver.py is wired to serve stale answers.

    At base (bc1544b7), dns_resolver._get_stale_cache does not exist and
    resolve_mesh_host does not call StaleDNSCache.  This test fails at base
    with AttributeError ('module has no attribute _get_stale_cache') -- the
    row's reason, not a generic ImportError.
    """
    from bulk_downloader import dns_resolver

    # The stale cache integration point must exist
    assert hasattr(dns_resolver, "_get_stale_cache"), (
        "Row 1008 capability missing: dns_resolver._get_stale_cache not wired"
    )
    cache = dns_resolver._get_stale_cache()
    assert hasattr(cache, "lookup"), (
        "Row 1008 capability missing: stale cache has no lookup method"
    )
    assert hasattr(cache, "store"), (
        "Row 1008 capability missing: stale cache has no store method"
    )


def test_dns_resolver_stale_window_configured():
    """Row 1008 behavioral RED: dns_resolver has a stale window constant.

    At base, _STALE_WINDOW_SECONDS does not exist in dns_resolver -- this
    fails with AssertionError for the row's reason.
    """
    from bulk_downloader import dns_resolver

    assert hasattr(dns_resolver, "_STALE_WINDOW_SECONDS"), (
        "Row 1008: dns_resolver missing _STALE_WINDOW_SECONDS constant"
    )
    assert dns_resolver._STALE_WINDOW_SECONDS > 0, (
        "Row 1008: stale window must be positive"
    )


def test_fresh_store_and_lookup():
    """Fresh entries are returned immediately."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=60.0, stale_window_seconds=300.0)
    cache.store("example.com", "93.184.216.34")
    result = cache.lookup("example.com")
    assert result == "93.184.216.34"
    s = cache.stats()
    assert s["hits_fresh"] == 1
    assert s["misses"] == 0


def test_miss_returns_none():
    """Lookup for absent key returns None."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=60.0, stale_window_seconds=300.0)
    assert cache.lookup("missing.com") is None
    assert cache.stats()["misses"] == 1


def test_stale_entry_served():
    """An entry past TTL but within stale window is served."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=0.01, stale_window_seconds=10.0)
    cache.store("stale.com", "1.2.3.4")
    time.sleep(0.02)  # TTL expires, still in stale window
    result = cache.lookup("stale.com")
    assert result == "1.2.3.4"
    assert cache.stats()["hits_stale"] >= 1


def test_expired_entry_returns_none():
    """An entry past both TTL and stale window returns None."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=0.01, stale_window_seconds=0.01)
    cache.store("expired.com", "1.2.3.4")
    time.sleep(0.03)  # Past both
    assert cache.lookup("expired.com") is None


def test_async_refresh_invoked_for_stale():
    """Stale lookup triggers async refresh callback."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    refreshed = threading.Event()
    refresh_key = []

    def refresh_cb(key):
        refresh_key.append(key)
        refreshed.set()
        return "5.6.7.8"

    cache = StaleDNSCache(
        ttl_seconds=0.01, stale_window_seconds=10.0,
        refresh_callback=refresh_cb)
    cache.store("refresh.com", "1.2.3.4")
    time.sleep(0.02)

    result = cache.lookup("refresh.com")
    assert result == "1.2.3.4"  # Stale served
    assert refreshed.wait(timeout=2.0), "Refresh not triggered"
    assert refresh_key[0] == "refresh.com"

    # Wait for refresh thread to complete and update
    time.sleep(0.1)
    result2 = cache.lookup("refresh.com")
    assert result2 == "5.6.7.8"  # Refreshed value
    assert cache.stats()["refreshes_completed"] >= 1


def test_refresh_failure_preserves_stale():
    """A failed refresh leaves the stale entry in place."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    def failing_cb(key):
        return None

    cache = StaleDNSCache(
        ttl_seconds=0.01, stale_window_seconds=10.0,
        refresh_callback=failing_cb)
    cache.store("fail.com", "1.2.3.4")
    time.sleep(0.02)

    result = cache.lookup("fail.com")
    assert result == "1.2.3.4"
    time.sleep(0.1)  # Let refresh thread run

    # Still stale but available
    result2 = cache.lookup("fail.com")
    assert result2 == "1.2.3.4"
    assert cache.stats()["refreshes_failed"] >= 1


def test_single_refresh_in_flight():
    """Only one refresh is launched per key at a time."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    call_count = []
    barrier = threading.Event()

    def slow_cb(key):
        call_count.append(key)
        barrier.wait(timeout=2.0)
        return "9.9.9.9"

    cache = StaleDNSCache(
        ttl_seconds=0.01, stale_window_seconds=10.0,
        refresh_callback=slow_cb)
    cache.store("once.com", "1.1.1.1")
    time.sleep(0.02)

    # Multiple lookups while refresh is slow
    cache.lookup("once.com")
    cache.lookup("once.com")
    cache.lookup("once.com")
    barrier.set()
    time.sleep(0.1)

    # Only one refresh should have been launched
    assert cache.stats()["refreshes_started"] == 1


def test_invalidate():
    """Explicit invalidation removes the entry."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=60.0, stale_window_seconds=300.0)
    cache.store("rm.com", "1.2.3.4")
    assert cache.invalidate("rm.com") is True
    assert cache.lookup("rm.com") is None
    assert cache.invalidate("rm.com") is False


def test_clear():
    """clear() removes all entries."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=60.0, stale_window_seconds=300.0)
    cache.store("a.com", "1.1.1.1")
    cache.store("b.com", "2.2.2.2")
    assert cache.clear() == 2
    assert cache.stats()["size"] == 0


def test_entry_state_transitions():
    """StaleEntry.state() returns correct state for each phase."""
    from bulk_downloader.stale_dns_cache import EntryState, StaleEntry

    now = time.monotonic()
    entry = StaleEntry(
        key="test", value="1.2.3.4", created_at=now,
        ttl_deadline=now + 10, stale_deadline=now + 20)
    assert entry.state(now) == EntryState.FRESH
    assert entry.state(now + 11) == EntryState.STALE
    assert entry.state(now + 21) == EntryState.EXPIRED

    entry.refresh_in_flight = True
    assert entry.state(now + 11) == EntryState.REFRESHING


def test_entry_state_enum_complete():
    """EntryState has exactly the four expected members."""
    from bulk_downloader.stale_dns_cache import EntryState

    expected = {"fresh", "stale", "refreshing", "expired"}
    assert {s.value for s in EntryState} == expected


def test_refresh_result_enum_complete():
    """RefreshResult has exactly the four expected members."""
    from bulk_downloader.stale_dns_cache import RefreshResult

    expected = {"success", "failure", "timeout", "skipped"}
    assert {r.value for r in RefreshResult} == expected


def test_invalid_params():
    """Constructor rejects invalid parameters."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    with pytest.raises(ValueError, match="positive"):
        StaleDNSCache(ttl_seconds=0)
    with pytest.raises(ValueError, match="non-negative"):
        StaleDNSCache(stale_window_seconds=-1)
    with pytest.raises(ValueError, match="positive"):
        StaleDNSCache(max_entries=0)


def test_capacity_eviction():
    """Entries are evicted when cache exceeds max_entries."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(
        ttl_seconds=60.0, stale_window_seconds=300.0, max_entries=2)
    cache.store("a.com", "1.1.1.1")
    cache.store("b.com", "2.2.2.2")
    cache.store("c.com", "3.3.3.3")
    assert cache.stats()["size"] <= 2


def test_ttl_override():
    """store() respects ttl_override."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=60.0, stale_window_seconds=10.0)
    cache.store("short.com", "1.2.3.4", ttl_override=0.01)
    time.sleep(0.02)
    # Should be stale (TTL expired, within stale window)
    result = cache.lookup("short.com")
    assert result == "1.2.3.4"
    assert cache.stats()["hits_stale"] >= 1


def test_stats():
    """Stats include entry_states breakdown."""
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    cache = StaleDNSCache(ttl_seconds=60.0, stale_window_seconds=300.0)
    cache.store("a.com", "1.1.1.1")
    s = cache.stats()
    assert "entry_states" in s
    assert s["entry_states"].get("fresh", 0) == 1


def test_get_stale_dns_cache_info():
    """Metadata introspection returns complete schema."""
    from bulk_downloader.stale_dns_cache import get_stale_dns_cache_info

    info = get_stale_dns_cache_info()
    assert isinstance(info, dict)
    assert info["version"] >= 1
    assert info["rfc"] == "RFC 8767"
    assert "StaleDNSCache" in info["components"]
    assert len(info["entry_states"]) == 4
    assert len(info["refresh_results"]) == 4


# -- bounce (N6-A E2-E5): behaviour through the resolver seam + outage limits --

class _Clock:
    """Injected monotonic clock: TTL windows move without real sleeps."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _fake_clock(monkeypatch, *modules):
    import types
    clock = _Clock()
    for module in modules:
        monkeypatch.setattr(module, "time", types.SimpleNamespace(monotonic=clock))
    return clock


def _wait_idle(cache, limit=2.0):
    """Synchronise with the background refresh thread (join, not a retry)."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        s = cache.stats()
        if s["refreshes_started"] == s["refreshes_completed"] + s["refreshes_failed"]:
            return
        time.sleep(0.005)
    raise AssertionError(f"background refresh never finished: {cache.stats()}")


def _mesh(monkeypatch, answer):
    """resolve_mesh_host with CoreDNS stubbed to ``answer[0]`` (an address or an exception)."""
    from bulk_downloader import dns_resolver, stale_dns_cache

    clock = _fake_clock(monkeypatch, dns_resolver, stale_dns_cache)
    calls = []

    def fake_query(name, *, nameserver, timeout):
        calls.append(name)
        if isinstance(answer[0], BaseException):
            raise answer[0]
        return answer[0]

    monkeypatch.setattr(dns_resolver, "_query_coredns", fake_query)
    monkeypatch.setattr(dns_resolver, "_STALE_CACHE", None)
    dns_resolver.clear_mesh_cache()
    return dns_resolver, clock, calls


def test_resolver_returns_fresh_answer_when_coredns_answers(monkeypatch):
    """E3: RFC 8767 serves stale on FAILURE -- a lapsed TTL with CoreDNS up returns the new answer."""
    answer = ["10.0.0.1"]
    dns, clock, _calls = _mesh(monkeypatch, answer)
    assert dns.resolve_mesh_host("svc.mesh.local", ttl_seconds=60) == "10.0.0.1"
    clock.t += 61
    answer[0] = "10.0.0.2"
    got = dns.resolve_mesh_host("svc.mesh.local", ttl_seconds=60)
    assert got == "10.0.0.2", f"TTL lapsed and CoreDNS answered 10.0.0.2 at once, resolver served stale {got}"


def test_resolver_serves_stale_when_coredns_fails(monkeypatch):
    """E2: the resolver's stale-store and stale-serve lines carry the answer through an outage."""
    answer = ["10.0.0.1"]
    dns, clock, calls = _mesh(monkeypatch, answer)
    monkeypatch.setattr(dns, "_hosts_address", lambda name: "10.9.9.9")
    assert dns.resolve_mesh_host("svc.mesh.local", ttl_seconds=60) == "10.0.0.1"
    clock.t += 61
    answer[0] = OSError("coredns restarting")
    got = dns.resolve_mesh_host("svc.mesh.local", ttl_seconds=60)
    assert got == "10.0.0.1", f"CoreDNS down inside the stale window: expected stale 10.0.0.1, got {got}"
    assert len(calls) == 2, f"stale served without first asking CoreDNS: {len(calls)} queries"
    clock.t += 300  # past ttl + stale window
    assert dns.resolve_mesh_host("svc.mesh.local", ttl_seconds=60) == "10.9.9.9"


def test_resolver_never_stores_hosts_fallback_as_stale(monkeypatch):
    """A /etc/hosts guess is cached only for the short negative TTL, never in the stale tier."""
    dns, _clock, _calls = _mesh(monkeypatch, [OSError("down")])
    monkeypatch.setattr(dns, "_hosts_address", lambda name: "10.9.9.9")
    assert dns.resolve_mesh_host("svc.mesh.local") == "10.9.9.9"
    assert dns._get_stale_cache().stats()["size"] == 0


def test_failed_refresh_is_rate_limited_during_outage(monkeypatch):
    """E4: one refresh per failure-recheck window, not one refresh thread per stale lookup."""
    from bulk_downloader import stale_dns_cache
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    clock = _fake_clock(monkeypatch, stale_dns_cache)
    calls = []
    cache = StaleDNSCache(ttl_seconds=60, stale_window_seconds=300,
                          refresh_callback=lambda key: calls.append(key))
    cache.store("down.mesh.local", "10.0.0.1")
    clock.t += 61
    for _ in range(50):
        assert cache.lookup("down.mesh.local") == "10.0.0.1"
        _wait_idle(cache)
    assert len(calls) == 1, f"outage: {len(calls)} refreshes for 50 stale lookups (want 1 per recheck window)"
    clock.t += 31  # the 30 s failure-recheck window has passed
    cache.lookup("down.mesh.local")
    _wait_idle(cache)
    assert len(calls) == 2


def test_refresh_in_flight_does_not_outlive_stale_window(monkeypatch):
    """E5: an unanswered refresh never extends serve-stale past stale_deadline."""
    from bulk_downloader import stale_dns_cache
    from bulk_downloader.stale_dns_cache import EntryState, StaleDNSCache

    clock = _fake_clock(monkeypatch, stale_dns_cache)
    started, release = threading.Event(), threading.Event()

    def hung(key):
        started.set()
        release.wait(5)
        return None

    cache = StaleDNSCache(ttl_seconds=60, stale_window_seconds=300, refresh_callback=hung)
    cache.store("hung.mesh.local", "10.0.0.1")
    clock.t += 61
    try:
        assert cache.lookup("hung.mesh.local") == "10.0.0.1"
        assert started.wait(2)
        clock.t += 300
        got = cache.lookup("hung.mesh.local")
        assert got is None, f"refresh still in flight past stale_deadline, lookup served {got!r}"
    finally:
        release.set()
    _wait_idle(cache)
    e = stale_dns_cache.StaleEntry(key="k", value="v", ttl_deadline=1.0, stale_deadline=2.0, refresh_in_flight=True)
    assert e.state(2.0) == EntryState.EXPIRED and e.state(1.5) == EntryState.REFRESHING


def test_slow_refresh_serves_stale_then_lands(monkeypatch):
    """Async half: a refresh slower than client_response_seconds answers stale, then updates the cache."""
    from bulk_downloader import stale_dns_cache
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    clock = _fake_clock(monkeypatch, stale_dns_cache)
    release = threading.Event()

    def slow(key):
        release.wait(5)
        return "10.0.0.2"

    cache = StaleDNSCache(ttl_seconds=60, stale_window_seconds=300,
                          client_response_seconds=0.05, refresh_callback=slow)
    cache.store("slow.mesh.local", "10.0.0.1")
    clock.t += 61
    assert cache.lookup("slow.mesh.local") == "10.0.0.1"
    release.set()
    _wait_idle(cache)
    assert cache.lookup("slow.mesh.local") == "10.0.0.2"


def test_refresh_keeps_the_answer_ttl(monkeypatch):
    """A refreshed entry keeps the TTL it was stored with, not the cache default."""
    from bulk_downloader import stale_dns_cache
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    clock = _fake_clock(monkeypatch, stale_dns_cache)
    cache = StaleDNSCache(ttl_seconds=60, stale_window_seconds=300,
                          refresh_callback=lambda key: "10.0.0.2")
    cache.store("short.mesh.local", "10.0.0.1", ttl_override=5)
    clock.t += 6
    cache.lookup("short.mesh.local")
    _wait_idle(cache)
    clock.t += 6
    states = cache.stats()["entry_states"]
    assert states == {"stale": 1}, f"refresh reset a 5 s answer to the 60 s default: {states}"


def test_eviction_order_expired_then_stale_then_oldest_fresh(monkeypatch):
    """E5 (m3): over capacity, expired goes first, then stale, and a fresh entry survives both."""
    from bulk_downloader import stale_dns_cache
    from bulk_downloader.stale_dns_cache import StaleDNSCache

    clock = _fake_clock(monkeypatch, stale_dns_cache)
    cache = StaleDNSCache(ttl_seconds=60, stale_window_seconds=10, max_entries=2)
    cache.store("old-fresh", "1", ttl_override=1000)
    cache.store("stale", "2", ttl_override=5)
    clock.t += 6  # "stale" is STALE; "old-fresh" is the oldest entry and still FRESH
    cache.store("new", "3")
    assert (cache.lookup("old-fresh"), cache.lookup("stale"), cache.lookup("new")) == ("1", None, "3")
    tier = StaleDNSCache(ttl_seconds=60, stale_window_seconds=10, max_entries=2)
    tier.store("gone", "4", ttl_override=1)
    tier.store("stale", "5", ttl_override=10)
    clock.t += 15  # "gone" is past its stale window; "stale" is inside it
    tier.store("newest", "6")
    assert (tier.lookup("stale"), tier.lookup("newest")) == ("5", "6")
