"""Unit tests for Row 1007: TLS Session Ticket Caching & Pre-Warmed Keepalive Pools.

Guards:
- TLSSessionCache creation, store/lookup/invalidate/clear lifecycle
- LRU eviction when cache exceeds max_entries
- TTL expiry removes stale entries on lookup
- Thread-safety of concurrent store/lookup
- Hit/miss/eviction statistics tracking
- Resumption through PinnedUrlOpener.open with no context handed in (N6-A E1)
- TLS 1.3: the post-handshake ticket is cached (rc5 E1); a session that cannot resume never is --
  no "hit" from a server that issues no tickets (G1), no eviction of a resumable one (G2)
- The pinned opener works where http.client has no _create_https_context, CPython < 3.12 (G3)
- Context keys never reused after a context is freed (N6-A E2)
- No keepalive pool: urllib closes every connection (N6-A E3)
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
    assert not hasattr(tls_session_cache, "KeepalivePool"), (
        "N6-A E3: the socket-less, caller-less KeepalivePool is back; urllib closes every "
        "connection, so a keepalive pool on this seam has nothing to hold")


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
    from bulk_downloader.tls_session_cache import TLSSessionCache

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


def test_get_tls_session_cache_info():
    """Metadata introspection returns complete schema."""
    from bulk_downloader.tls_session_cache import get_tls_session_cache_info

    info = get_tls_session_cache_info()
    assert isinstance(info, dict)
    assert info["version"] >= 1
    assert "TLSSessionCache" in info["components"]
    assert info["components"] == ["TLSSessionCache"]
    assert len(info["eviction_strategies"]) == 3


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
    """A real TLS endpoint on loopback, pinned to ONE protocol version (TLS 1.2 by default).
    A 1.3 ticket arrives after the handshake; the rc5 tests below pin 1.3 to measure that path
    (the ticket precedes the response on the wire, so reading the response makes it deterministic).
    issue_tickets=False: no session tickets at all -- TLS 1.3 sends no NewSessionTicket, and TLS 1.2
    can then resume only by session id, from this server's own session cache. alpn=True: the server
    speaks ALPN http/1.1, and `self.alpn` records the protocol each connection negotiated."""

    def __init__(self, cert_path, key_path, connections=4, version=ssl.TLSVersion.TLSv1_2,
                 close_header=False, issue_tickets=True, alpn=False):
        self._reply = (b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                       + (b"Connection: close\r\n" if close_header else b"") + b"\r\nhi")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ctx.maximum_version = version
        ctx.load_cert_chain(cert_path, key_path)
        if not issue_tickets:
            ctx.num_tickets = 0
            ctx.options |= ssl.OP_NO_TICKET
        if alpn:
            ctx.set_alpn_protocols(["http/1.1"])
        self.alpn = []
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
                self.alpn.append(conn.selected_alpn_protocol())
                conn.recv(4096)
                conn.sendall(self._reply)
                conn.close()
            except OSError:
                pass

    def close(self):
        try:
            self._listener.close()
        except OSError:
            pass


def _client_context(cert_path, version=ssl.TLSVersion.TLSv1_2):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ctx.maximum_version = version
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
    """Empty the process cache if the module and its seam exist. Written so the resumption tests
    below can run on a tree where the module is absent (the old base) or has no seam yet (the
    first generation, which B3 refuted as unwired): their failure must be the handshake not
    resuming, not an ImportError/AttributeError about the subject (F2)."""
    try:
        from bulk_downloader import tls_session_cache
    except ImportError:
        return
    get_cache = getattr(tls_session_cache, "get_session_cache", None)
    if get_cache is not None:
        get_cache().clear()


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


# -- N6-A E1: resumption measured through PinnedUrlOpener.open, the way the product calls it ----

def _instrument_opener(monkeypatch, cert_path, port, version=ssl.TLSVersion.TLSv1_2, seen=None):
    """Route the PRODUCT opener at the loopback server and record `session_reused` per handshake.
    The stdlib default-context factory is pointed at the test CA, as a system CA store would be;
    no context is handed to the opener, so whatever SSLContext the product builds is the one
    measured. Returns the list the spy appends to. Call ONCE per test (it wraps `connect`).
    `seen`, if given, also gets (context used, session held) as connect() returns."""
    from bulk_downloader import urllib_ssrf

    monkeypatch.setattr(ssl, "_create_default_https_context",
                        lambda *a, **k: _client_context(cert_path, version))
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "localhost":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    reused = []
    real_connect = urllib_ssrf._PinnedHTTPSConnection.connect

    def spy_connect(conn):
        real_connect(conn)
        assert conn.sock.version() == {ssl.TLSVersion.TLSv1_2: "TLSv1.2",
                                       ssl.TLSVersion.TLSv1_3: "TLSv1.3"}[version]
        reused.append(conn.sock.session_reused)
        if seen is not None:
            seen.append((conn._context, conn.sock.session))

    monkeypatch.setattr(urllib_ssrf._PinnedHTTPSConnection, "connect", spy_connect)
    return reused


def _opener_fetch(port, host="localhost"):
    """One request through a NEW PinnedUrlOpener (the doh_resolver.py / hooks.py shape)."""
    import urllib.request

    from bulk_downloader import urllib_ssrf

    opener = urllib_ssrf.PinnedUrlOpener(lambda address, host: (True, ""))
    with opener.open(urllib.request.Request(f"https://{host}:{port}/"), timeout=10) as resp:
        assert resp.read() == b"hi"


def test_e1_repeat_opener_requests_resume_through_the_product_path(monkeypatch):
    """E1: every PinnedUrlOpener.open built its handler with no context, so http.client made a
    fresh SSLContext per connection and the (host, port, context) key never hit in production."""
    _clear_session_cache()
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=3)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port)
            for _ in range(3):
                _opener_fetch(server.port)
        finally:
            server.close()
    assert reused == [False, True, True], (
        f"session_reused per opener request = {reused}: the product's pinned opener builds a new "
        "SSLContext for every connection, so no cached session is ever offered")


def test_e1_negative_control_an_empty_cache_resumes_nothing(monkeypatch):
    """Control: same product path, cache cleared between the requests -> both handshakes full."""
    from bulk_downloader import tls_session_cache

    _clear_session_cache()
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=2)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port)
            _opener_fetch(server.port)
            tls_session_cache.get_session_cache().clear()
            _opener_fetch(server.port)
        finally:
            server.close()
    assert reused == [False, False], (
        f"session_reused = {reused} with the cache emptied between requests: the resumption "
        "measured above is not coming from this module's cache")


# -- rc5 E1: the TLS 1.3 ticket arrives AFTER the handshake -----------------------------------

def test_rc5_e1_tls13_repeat_opener_requests_resume_through_the_product_path(monkeypatch):
    """rc5 E1: a TLS 1.3 server sends its NewSessionTicket after the handshake, and the client
    only processes it when it reads application data. Caching `sock.session` straight after
    wrap_socket kept a session with no ticket, so every TLS 1.3 request paid a full handshake."""
    _clear_session_cache()
    v13 = ssl.TLSVersion.TLSv1_3
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=3, version=v13)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port, v13)
            for _ in range(3):
                _opener_fetch(server.port)
        finally:
            server.close()
    assert reused == [False, True, True], (
        f"rc5 E1: TLS 1.3 session_reused per opener request = {reused}: the session was cached "
        "before the post-handshake NewSessionTicket arrived, so nothing resumable was offered")


def test_rc5_e1_tls13_resumes_when_the_server_answers_connection_close(monkeypatch):
    """rc5 E1, the common server shape: urllib sends `Connection: close`, many servers echo it,
    and http.client then detaches the socket from the connection inside getresponse(). The
    post-ticket session must be read from the socket the response arrived on, not from
    `self.sock` after getresponse() has already set it to None."""
    _clear_session_cache()
    v13 = ssl.TLSVersion.TLSv1_3
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=3, version=v13,
                                close_header=True)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port, v13)
            for _ in range(3):
                _opener_fetch(server.port)
        finally:
            server.close()
    assert reused == [False, True, True], (
        f"rc5 E1: TLS 1.3 + 'Connection: close' session_reused = {reused}: the post-ticket "
        "session was read after http.client detached the socket, so nothing was stored")


def test_rc5_e1_negative_control_tls13_empty_cache_resumes_nothing(monkeypatch):
    """Control: TLS 1.3, cache cleared between the requests -> both handshakes full."""
    from bulk_downloader import tls_session_cache

    _clear_session_cache()
    v13 = ssl.TLSVersion.TLSv1_3
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=2, version=v13)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port, v13)
            _opener_fetch(server.port)
            tls_session_cache.get_session_cache().clear()
            _opener_fetch(server.port)
        finally:
            server.close()
    assert reused == [False, False], (
        f"TLS 1.3 session_reused = {reused} with the cache emptied between requests: the "
        "resumption measured above is not coming from this module's cache")


# -- G1/G2 (bd-fixer-B findings on the boarded r3 tree c6a6af0e, RULING-0063): a session that ----
# -- cannot resume is never cached -----------------------------------------------------------------

def test_g1_a_tls13_server_that_issues_no_tickets_leaves_nothing_cached(monkeypatch):
    """G1: connect() hands remember_session the session it holds straight after wrap_socket, and
    under TLS 1.3 that session has no ticket and no id -- it cannot resume. A server that issues
    no tickets makes this exact: nothing resumable ever exists, so nothing may be cached. Cached
    anyway, the next lookup counts a hit while the handshake is still full: the stats lie."""
    from bulk_downloader import tls_session_cache

    _clear_session_cache()
    cache = tls_session_cache.get_session_cache()
    hits0, misses0 = cache.stats()["hits"], cache.stats()["misses"]
    v13 = ssl.TLSVersion.TLSv1_3
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=2, version=v13,
                                issue_tickets=False)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port, v13)
            _opener_fetch(server.port)
            _opener_fetch(server.port)
        finally:
            server.close()
    stats = cache.stats()
    measured = (reused, stats["size"], stats["hits"] - hits0, stats["misses"] - misses0)
    assert measured == ([False, False], 0, 0, 2), (
        f"G1: TLS 1.3 server issuing no tickets: (session_reused, cache size, hits, misses) = "
        f"{measured}: a session that cannot resume was cached, so a lookup counted a hit while "
        "the handshake was still full")


def test_g1_control_a_tls12_session_resumable_by_id_alone_is_still_cached(monkeypatch):
    """Control for the refusal above: a TLS 1.2 server that issues no tickets still resumes by
    session id, so its session -- no ticket, but an id -- must still be cached and offered. The
    refusal is 'neither a ticket nor an id', never merely 'no ticket'."""
    _clear_session_cache()
    seen = []
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=2, issue_tickets=False)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port, seen=seen)
            _opener_fetch(server.port)
            _opener_fetch(server.port)
        finally:
            server.close()
    shape = [(session.has_ticket, len(session.id) > 0) for _ctx, session in seen]
    assert (reused, shape) == ([False, True], [(False, True), (False, True)]), (
        f"TLS 1.2 without tickets: session_reused = {reused}, (has_ticket, has id) = {shape}: a "
        "session resumable by its id alone was refused, so session-id resumption stopped")


def test_g2_a_concurrent_full_handshake_does_not_evict_a_resumable_session(monkeypatch):
    """G2: workers share one context. A worker whose TLS 1.3 handshake began before any ticket was
    cached does a full handshake, and its connect() stores its session -- still ticketless --
    AFTER another worker's getresponse() cached a resumable one. Stored, it replaces that ticket
    and the next connection pays a full handshake. Reproduced with no threads on two product
    connections: `early` finishes its request exactly between `late`'s cache lookup and `late`'s
    handshake (a hook on the lookup, which still returns the real answer)."""
    from bulk_downloader import tls_session_cache
    from bulk_downloader.urllib_ssrf import _PinnedHTTPSConnection

    _clear_session_cache()
    v13 = ssl.TLSVersion.TLSv1_3
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=3, version=v13)
        try:
            ctx = _client_context(cert_path, v13)

            def connection():
                return _PinnedHTTPSConnection("127.0.0.1", server_hostname="localhost",
                                              port=server.port, context=ctx, timeout=10)

            early, late = connection(), connection()
            early.connect()
            early_reused = early.sock.session_reused
            real_lookup = tls_session_cache.resume_session_for
            cached_before_late_stores = []

            def lookup_then_early_finishes(host, port, context=None):
                offered = real_lookup(host, port, context)
                early.request("GET", "/")
                early.getresponse().read()           # early's getresponse() caches its ticket
                early.close()
                cached = real_lookup(host, port, context)
                cached_before_late_stores.append(getattr(cached, "has_ticket", None))
                return offered

            monkeypatch.setattr(tls_session_cache, "resume_session_for",
                                lookup_then_early_finishes)
            late.connect()                           # full handshake, then connect()'s own store
            monkeypatch.setattr(tls_session_cache, "resume_session_for", real_lookup)
            late_reused, late_has_ticket = late.sock.session_reused, late.sock.session.has_ticket
            late.request("GET", "/")                 # late's response is left unread for now
            next_reused = _fetch_once(server.port, ctx)
            late.getresponse().read()
            late.close()
        finally:
            server.close()
    measured = (early_reused, cached_before_late_stores, late_reused, late_has_ticket, next_reused)
    assert measured == (False, [True], False, False, True), (
        f"G2: (early reused, ticket cached before late's store, late reused, late's session "
        f"has_ticket, next reused) = {measured}: the ticketless session of a concurrent full "
        "handshake replaced the resumable one, so the next connection paid a full handshake")


# -- G3: the shared context must not need a private helper CPython < 3.12 lacks -----------------

def test_g3_the_pinned_opener_works_where_http_client_has_no_context_helper(monkeypatch):
    """G3: the shared context came from http.client._create_https_context, a PRIVATE helper only
    CPython 3.12+ has (CPython 3.11.16 measured: absent). install_linux.sh, install_windows.bat
    and doctor.py accept 3.9+, and there every PinnedUrlOpener.open -- hooks, the DoH resolver,
    app_template, capture_diag -- raised before connecting. Without the helper the opener must
    build the context the stdlib would (same factory, ALPN http/1.1, post-handshake auth) and
    share it. Deleting the helper stands in for 3.11 only because the product hands urllib a
    context: 3.12's own HTTPSHandler() calls the helper when given none."""
    import http.client

    _clear_session_cache()
    monkeypatch.delattr(http.client, "_create_https_context", raising=False)
    outcomes, seen = [], []
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=2, alpn=True)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port, seen=seen)
            for _ in range(2):
                try:
                    _opener_fetch(server.port)
                    outcomes.append("200 hi")
                except Exception as exc:  # noqa: BLE001 -- what the request did IS the measurement
                    outcomes.append(f"{type(exc).__name__}: {exc}")
        finally:
            server.close()
    policy = [(c.verify_mode, c.check_hostname, c.post_handshake_auth) for c, _s in seen]
    shared = len({id(c) for c, _s in seen})
    measured = (outcomes, reused, server.alpn, policy, shared)
    assert measured == (["200 hi", "200 hi"], [False, True], ["http/1.1", "http/1.1"],
                        [(ssl.CERT_REQUIRED, True, True)] * 2, 1), (
        f"G3: with no http.client._create_https_context (CPython < 3.12) the pinned opener gave "
        f"(outcomes, session_reused, ALPN, (verify, check_hostname, PHA), contexts) = {measured}")


# -- B3 gen-1 F2, measured on the product path --------------------------------------------------

def test_host_case_does_not_split_the_cache_on_the_product_path(monkeypatch):
    """B3 (gen 1) F2 said cache keys are case-sensitive. TLSSessionCache is (a plain mapping), but
    its only production caller keys by urlsplit(url).hostname, which the stdlib lowercases -- so
    'LocalHost' and 'localhost' are one peer, one entry, one resumable session."""
    from bulk_downloader import tls_session_cache

    _clear_session_cache()
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = _self_signed(tmp)
        server = _TLSEchoServer(cert_path, key_path, connections=2)
        try:
            reused = _instrument_opener(monkeypatch, cert_path, server.port)
            _opener_fetch(server.port, host="LocalHost")
            _opener_fetch(server.port, host="localhost")
        finally:
            server.close()
    size = tls_session_cache.get_session_cache().stats()["size"]
    assert (reused, size) == ([False, True], 1), (
        f"'LocalHost' then 'localhost': session_reused = {reused}, cache size = {size}: one peer "
        "was cached under two keys")


# -- N6-A E2: a dead context's key is never handed to a new one --------------------------------

def test_e2_a_freed_contexts_key_is_never_reused():
    """E2: the key was id(context). CPython reuses a freed object's address at once, so a new
    context inherited a dead one's cache entries and was offered a session it cannot resume
    (ValueError -> reconnect). Keys must be unique for the life of the process."""
    import gc

    from bulk_downloader import tls_session_cache

    keys = []
    for _ in range(64):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        keys.append(tls_session_cache._context_key(ctx))
        del ctx
        gc.collect()
    assert len(set(keys)) == len(keys), (
        f"{len(keys) - len(set(keys))} of {len(keys)} short-lived SSLContexts were given the key of "
        "an earlier, dead context: a new context would be offered a foreign session")


def test_e2_control_a_live_context_keeps_its_key():
    from bulk_downloader import tls_session_cache

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert tls_session_cache._context_key(ctx) == tls_session_cache._context_key(ctx)
    assert tls_session_cache._context_key(None) == 0


# -- N6-A escape m5: a falsy session is refused, not cached ------------------------------------

@pytest.mark.parametrize("session", [None, b"", 0])
def test_m5_remember_session_refuses_a_falsy_session(session):
    from bulk_downloader import tls_session_cache

    tls_session_cache.get_session_cache().clear()
    assert tls_session_cache.remember_session("h.example", 443, session) is False
    assert tls_session_cache.get_session_cache().stats()["size"] == 0, (
        "a falsy session was stored: every later lookup would be a hit that resumes nothing")
    assert tls_session_cache.remember_session("h.example", 443, object()) is True
