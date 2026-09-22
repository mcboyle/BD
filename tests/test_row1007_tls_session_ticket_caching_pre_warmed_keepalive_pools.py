"""Unit tests for Row 1007: TLS Session Ticket Caching & Pre-Warmed Keepalive Pools.

Guards:
- TLSSessionCache creation, store/lookup/invalidate/clear lifecycle
- LRU eviction when cache exceeds max_entries
- TTL expiry removes stale entries on lookup
- Thread-safety of concurrent store/lookup
- Hit/miss/eviction statistics tracking
- KeepalivePool register/checkout/checkin lifecycle
- Pool slot state transitions (IDLE -> CHECKED_OUT -> IDLE)
- Stale slot pruning past idle_timeout
- Pool capacity enforcement and idle-eviction
- Metadata introspection via get_tls_session_cache_info
"""
from __future__ import annotations

import threading
import time

import pytest

# H622 anti-orphan convention: scoped module test, collected by bd-test-shard
BD_GATE_SCOPE = "module"


def test_capability_exists():
    """Row 1007 capability presence: tls_session_cache is importable."""
    from bulk_downloader import tls_session_cache

    assert hasattr(
        tls_session_cache, "TLSSessionCache"
    ), "Row 1007 capability missing: TLSSessionCache not exposed"
    assert hasattr(
        tls_session_cache, "KeepalivePool"
    ), "Row 1007 capability missing: KeepalivePool not exposed"


def test_session_cache_store_and_lookup():
    """Store a session and retrieve it."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=10, ttl_seconds=60.0)
    cache.store("example.com", 443, b"ticket-data-1", tls_version="TLSv1.3")
    result = cache.lookup("example.com", 443)
    assert result is not None
    assert result.session_data == b"ticket-data-1"
    assert result.host == "example.com"
    assert result.port == 443
    assert result.tls_version == "TLSv1.3"


def test_session_cache_miss():
    """Lookup for absent key returns None and increments misses."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=10, ttl_seconds=60.0)
    assert cache.lookup("missing.com", 443) is None
    assert cache.stats()["misses"] == 1


def test_session_cache_lru_eviction():
    """Oldest entry is evicted when cache exceeds max_entries."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=2, ttl_seconds=60.0)
    cache.store("a.com", 443, b"a")
    cache.store("b.com", 443, b"b")
    cache.store("c.com", 443, b"c")  # Should evict a.com
    assert cache.lookup("a.com", 443) is None
    assert cache.lookup("b.com", 443) is not None
    assert cache.lookup("c.com", 443) is not None
    assert cache.stats()["evictions"] >= 1


def test_session_cache_ttl_expiry():
    """Expired entries are removed on lookup."""
    from bulk_downloader.tls_session_cache import CachedSession, TLSSessionCache

    cache = TLSSessionCache(max_entries=10, ttl_seconds=0.01)
    cache.store("expire.com", 443, b"ticket")
    time.sleep(0.02)  # Wait for TTL
    assert cache.lookup("expire.com", 443) is None


def test_session_cache_invalidate():
    """Explicit invalidation removes the entry."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=10, ttl_seconds=60.0)
    cache.store("remove.com", 443, b"ticket")
    assert cache.invalidate("remove.com", 443) is True
    assert cache.lookup("remove.com", 443) is None
    assert cache.invalidate("remove.com", 443) is False


def test_session_cache_clear():
    """clear() removes all entries and returns count."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=10, ttl_seconds=60.0)
    cache.store("a.com", 443, b"a")
    cache.store("b.com", 443, b"b")
    assert cache.clear() == 2
    assert cache.stats()["size"] == 0


def test_session_cache_stats_hit_rate():
    """Hit rate is correctly calculated."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=10, ttl_seconds=60.0)
    cache.store("hit.com", 443, b"ticket")
    cache.lookup("hit.com", 443)   # hit
    cache.lookup("hit.com", 443)   # hit
    cache.lookup("miss.com", 443)  # miss
    s = cache.stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert s["hit_rate"] == pytest.approx(2.0 / 3.0, abs=0.01)


def test_session_cache_empty_data_ignored():
    """Storing empty session_data is silently ignored."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=10, ttl_seconds=60.0)
    cache.store("empty.com", 443, b"")
    assert cache.lookup("empty.com", 443) is None


def test_session_cache_invalid_params():
    """Constructor rejects non-positive parameters."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    with pytest.raises(ValueError, match="positive"):
        TLSSessionCache(max_entries=0)
    with pytest.raises(ValueError, match="positive"):
        TLSSessionCache(ttl_seconds=-1.0)


def test_keepalive_pool_register_and_checkout():
    """Register a slot and check it out."""
    from bulk_downloader.tls_session_cache import KeepalivePool, PoolSlotState

    pool = KeepalivePool(max_slots=4, idle_timeout=60.0)
    slot = pool.register("example.com", 443)
    assert slot is not None
    assert slot.state == PoolSlotState.IDLE

    checkout = pool.checkout("example.com", 443)
    assert checkout is not None
    assert checkout.state == PoolSlotState.CHECKED_OUT
    assert checkout.use_count == 1


def test_keepalive_pool_checkin():
    """Checkin returns a slot to IDLE."""
    from bulk_downloader.tls_session_cache import KeepalivePool, PoolSlotState

    pool = KeepalivePool(max_slots=4, idle_timeout=60.0)
    pool.register("example.com", 443)
    slot = pool.checkout("example.com", 443)
    assert slot is not None
    pool.checkin(slot)
    assert slot.state == PoolSlotState.IDLE
    s = pool.stats()
    assert s["total_checkouts"] == 1
    assert s["total_returns"] == 1


def test_keepalive_pool_stale_checkout_refused():
    """A stale idle slot is refused on checkout."""
    from bulk_downloader.tls_session_cache import KeepalivePool

    pool = KeepalivePool(max_slots=4, idle_timeout=0.01)
    pool.register("stale.com", 443)
    time.sleep(0.02)
    assert pool.checkout("stale.com", 443) is None


def test_keepalive_pool_capacity():
    """Pool refuses registration when full with checked-out slots."""
    from bulk_downloader.tls_session_cache import KeepalivePool

    pool = KeepalivePool(max_slots=2, idle_timeout=60.0)
    pool.register("a.com", 443)
    pool.register("b.com", 443)
    pool.checkout("a.com", 443)
    pool.checkout("b.com", 443)
    # Both checked out, no idle to evict
    slot = pool.register("c.com", 443)
    assert slot is None


def test_keepalive_pool_idle_eviction():
    """When pool is full, registering evicts the oldest idle slot."""
    from bulk_downloader.tls_session_cache import KeepalivePool

    pool = KeepalivePool(max_slots=2, idle_timeout=60.0)
    pool.register("a.com", 443)
    time.sleep(0.01)
    pool.register("b.com", 443)
    # a.com is oldest idle
    slot = pool.register("c.com", 443)
    assert slot is not None
    assert pool.checkout("a.com", 443) is None  # evicted


def test_keepalive_pool_prune_stale():
    """prune_stale removes idle slots past timeout."""
    from bulk_downloader.tls_session_cache import KeepalivePool

    pool = KeepalivePool(max_slots=4, idle_timeout=0.01)
    pool.register("a.com", 443)
    pool.register("b.com", 443)
    time.sleep(0.02)
    pruned = pool.prune_stale()
    assert pruned == 2
    assert pool.stats()["size"] == 0


def test_keepalive_pool_remove():
    """remove() deletes a specific slot."""
    from bulk_downloader.tls_session_cache import KeepalivePool

    pool = KeepalivePool(max_slots=4, idle_timeout=60.0)
    pool.register("rm.com", 443)
    assert pool.remove("rm.com", 443) is True
    assert pool.remove("rm.com", 443) is False


def test_keepalive_pool_stats():
    """stats() reports correct counts."""
    from bulk_downloader.tls_session_cache import KeepalivePool

    pool = KeepalivePool(max_slots=4, idle_timeout=60.0)
    pool.register("a.com", 443)
    pool.register("b.com", 443)
    pool.checkout("a.com", 443)
    s = pool.stats()
    assert s["size"] == 2
    assert s["idle"] == 1
    assert s["checked_out"] == 1


def test_keepalive_pool_invalid_params():
    """Constructor rejects non-positive parameters."""
    from bulk_downloader.tls_session_cache import KeepalivePool

    with pytest.raises(ValueError, match="positive"):
        KeepalivePool(max_slots=0)
    with pytest.raises(ValueError, match="positive"):
        KeepalivePool(idle_timeout=-1.0)


def test_get_tls_session_cache_info():
    """Metadata introspection returns complete schema."""
    from bulk_downloader.tls_session_cache import get_tls_session_cache_info

    info = get_tls_session_cache_info()
    assert isinstance(info, dict)
    assert info["version"] >= 1
    assert "TLSSessionCache" in info["components"]
    assert "KeepalivePool" in info["components"]
    assert len(info["eviction_strategies"]) == 3
    assert len(info["pool_slot_states"]) == 3


def test_session_cache_concurrent_access():
    """Multiple threads can store/lookup without errors."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=100, ttl_seconds=60.0)
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(50):
                host = f"t{tid}-{i}.com"
                cache.store(host, 443, f"data-{tid}-{i}".encode())
                cache.lookup(host, 443)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors, f"Concurrent errors: {errors}"
    assert cache.stats()["size"] > 0


# =============================================================================================
# RE-EMIT after REFUTE (bd-review-correctness-B3, 2026-09-22T09:25Z). Three findings, three
# sections. F1: the module had NO non-test caller, so no handshake was ever abbreviated. F2: all
# 21 tests failed at base with ModuleNotFoundError, which is the test failing to CALL the
# subject, not the subject failing. F3: the cache is called LRU and nothing held it to recency.
# =============================================================================================

import datetime
import os
import socket
import ssl
import tempfile


def _self_signed(tmpdir):
    """A localhost cert, generated here so the test needs no fixture on disk."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_path = os.path.join(tmpdir, "cert.pem")
    key_path = os.path.join(tmpdir, "key.pem")
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM,
                                   serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption()))
    return cert_path, key_path


class _TLSEchoServer:
    """A real TLS endpoint on loopback. TLS 1.2 on purpose: its session resumption is the
    deterministic one -- a 1.3 ticket arrives after the handshake, which would make the test
    depend on timing rather than on whether the cache offered a session."""

    def __init__(self, cert_path, key_path, connections=4):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert_path, key_path)
        self._ctx = ctx
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(10.0)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, args=(connections,), daemon=True)
        self._thread.start()

    def _serve(self, connections):
        for _ in range(connections):
            try:
                raw, _addr = self._listener.accept()
            except OSError:
                return
            try:
                conn = self._ctx.wrap_socket(raw, server_side=True)
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
                conn.close()
            except OSError:
                pass

    def close(self):
        try:
            self._listener.close()
        except OSError:
            pass


def _client_context(cert_path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_verify_locations(cert_path)
    return ctx


def _fetch_once(port, ctx):
    """One request through the PRODUCT's pinned HTTPS connection; returns session_reused."""
    from bulk_downloader.urllib_ssrf import _PinnedHTTPSConnection

    conn = _PinnedHTTPSConnection("127.0.0.1", server_hostname="localhost",
                                  port=port, context=ctx)
    conn.connect()
    reused = conn.sock.session_reused
    conn.request("GET", "/")
    conn.getresponse().read()
    conn.close()
    return reused


def _clear_session_cache():
    """Empty the process cache if the module exists. Written so the resumption test below can
    run AT BASE, where the module does not exist yet: its failure must be the handshake not
    resuming, not an ImportError about the subject (F2)."""
    try:
        from bulk_downloader import tls_session_cache
    except ImportError:
        return
    tls_session_cache.get_session_cache().clear()


# -- F1: the feature, exercised THROUGH its caller on a real handshake -------------------------

def test_the_pinned_https_seam_resumes_its_second_handshake():
    """Row 1007's reason, measured end to end: BD's own TLS seam (urllib_ssrf, row 728, reached
    from hooks.py / doh_resolver.py / app_template.py / dev_suite/capture_diag.py) pays a full
    handshake for every connection because nothing keeps the session ticket the peer issued.

    This test asserts on `session_reused`, which OpenSSL sets -- not on a cache counter, which
    could say 'hit' while the handshake was still full.
    """
    _clear_session_cache()
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path)
        try:
            ctx = _client_context(cert_path)
            first = _fetch_once(server.port, ctx)
            second = _fetch_once(server.port, ctx)
        finally:
            server.close()
    assert first is False, "the first connection cannot resume anything"
    assert second is True, (
        "the second handshake to the same host was full: the pinned HTTPS seam does not offer "
        "the session ticket from the previous connection, so every request pays a fresh "
        "key exchange")


def test_clearing_the_cache_makes_the_next_handshake_full_again():
    """NEGATIVE CONTROL for the test above: proves `session_reused` tracks THIS cache and is not
    simply OpenSSL's own client-side reuse."""
    _clear_session_cache()
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path)
        try:
            ctx = _client_context(cert_path)
            _fetch_once(server.port, ctx)
            _clear_session_cache()
            after_clear = _fetch_once(server.port, ctx)
        finally:
            server.close()
    assert after_clear is False, (
        "a handshake resumed from an empty cache, so the reuse under test is coming from "
        "somewhere other than this module")


def test_the_module_has_a_non_test_caller():
    """F1 was found by grep, so it is closed by grep. The row's own deliverable is the wiring."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "bulk_downloader"
    callers = sorted(
        p.name for p in root.rglob("*.py")
        if p.name != "tls_session_cache.py"
        and re.search(r"tls_session_cache", p.read_text(encoding="utf-8")))
    assert callers, "tls_session_cache is imported by nothing in bulk_downloader/"
    assert "urllib_ssrf.py" in callers, callers


# -- F2: the first assertion is a spec probe, not an import ------------------------------------

def test_the_subject_is_present_before_anything_imports_it():
    """O895: `find_spec` answers 'is the module there' without an ImportError standing in for a
    behavioural failure. At base this fails as a plain False, naming the missing module."""
    import importlib.util

    assert importlib.util.find_spec("bulk_downloader.tls_session_cache") is not None, (
        "row 1007's subject module bulk_downloader/tls_session_cache.py does not exist")


# -- F3: the word 'LRU' now has an assertion behind it -----------------------------------------

def test_eviction_is_least_RECENTLY_used_not_first_in():
    """MUTANT M2 (`move_to_end` -> `pass`) survived the old suite: eviction was tested for
    capacity only, so a FIFO passed as an LRU. For a session cache that is the whole point --
    the host you just talked to is the one worth keeping.
    """
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=3, ttl_seconds=3600)
    for host in ("a.example", "b.example", "c.example"):
        cache.store(host, 443, b"ticket-" + host.encode())

    # Touch the OLDEST entry: under LRU that promotes it, under FIFO it stays first out.
    assert cache.lookup("a.example", 443) is not None
    cache.store("d.example", 443, b"ticket-d")

    assert cache.lookup("a.example", 443) is not None, (
        "the entry used most recently was evicted: eviction is first-in-first-out, not LRU")
    assert cache.lookup("b.example", 443) is None, (
        "b.example was the least recently used entry and should have been the one evicted")
    assert cache.lookup("c.example", 443) is not None
    assert cache.lookup("d.example", 443) is not None


def test_a_store_of_an_existing_key_also_counts_as_use():
    """The second `move_to_end` site: re-storing a key must refresh its recency too."""
    from bulk_downloader.tls_session_cache import TLSSessionCache

    cache = TLSSessionCache(max_entries=3, ttl_seconds=3600)
    for host in ("a.example", "b.example", "c.example"):
        cache.store(host, 443, b"t")
    cache.store("a.example", 443, b"t2")          # use by writing, not by reading
    cache.store("d.example", 443, b"t")
    assert cache.lookup("a.example", 443) is not None
    assert cache.lookup("b.example", 443) is None


def test_a_second_context_does_not_inherit_the_first_contexts_session():
    """REFUTE F1 (bd-review-correctness-B6-B, 2026-09-22T10:0xZ): OpenSSL binds a session to the
    SSLContext that negotiated it, so offering a cached session to a DIFFERENT context raises
    `ValueError: Session refers to a different SSLContext`. The cache was keyed on (host, port)
    alone, so the second caller to reach a warmed host crashed instead of connecting.

    Measured through the real caller, with the working case beside it as the control: the same
    context still resumes, and a fresh one still connects.
    """
    _clear_session_cache()
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=4)
        try:
            ctx_a = _client_context(cert_path)
            ctx_b = _client_context(cert_path)
            first_a = _fetch_once(server.port, ctx_a)
            second_a = _fetch_once(server.port, ctx_a)      # CONTROL: resumption still works
            first_b = _fetch_once(server.port, ctx_b)       # the case that used to raise
            second_b = _fetch_once(server.port, ctx_b)
        finally:
            server.close()
    assert (first_a, second_a) == (False, True), "same-context resumption regressed"
    assert first_b is False, (
        "a connection on a second SSLContext resumed a session the first context negotiated; "
        "OpenSSL rejects that outright")
    assert second_b is True, "the second context must build its own resumable session"


def test_the_seam_survives_a_session_that_belongs_to_another_context():
    """Belt beside the braces: even if a foreign session reaches wrap_socket, the request must
    fall back to a full handshake rather than raise. Forced by seeding the cache under the key
    the connection will look up, with a session negotiated on a different context."""
    from bulk_downloader import tls_session_cache

    _clear_session_cache()
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=3)
        try:
            donor_ctx = _client_context(cert_path)
            _fetch_once(server.port, donor_ctx)
            donor = tls_session_cache.resume_session_for("localhost", server.port, donor_ctx)
            assert donor is not None, "the donor context produced no session to misuse"

            victim_ctx = _client_context(cert_path)
            # Seed the victim's own key with the foreign session, bypassing remember_session.
            tls_session_cache.get_session_cache().store(
                tls_session_cache._scoped_host("localhost", victim_ctx), server.port, donor)
            before = tls_session_cache.REUSE_FAILURES.get("offer:ValueError", 0)
            reused = _fetch_once(server.port, victim_ctx)
        finally:
            server.close()
    assert reused is False, "a foreign session cannot produce a resumed handshake"
    assert tls_session_cache.REUSE_FAILURES.get("offer:ValueError", 0) == before + 1, (
        "the refused offer was not counted; a cache that silently stops resuming looks exactly "
        "like one that is working")
