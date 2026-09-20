"""Row 842: local CoreDNS mesh resolution and cache behaviour."""

import importlib
import importlib.util
import socket
import struct
import threading
import time

import pytest

BD_GATE_SCOPE = "module"


def _answer(packet: bytes, address: str) -> bytes:
    """Return one A-record answer for the packet's first question."""
    question_end = 12
    while packet[question_end] != 0:
        question_end += packet[question_end] + 1
    question_end += 5
    header = packet[:2] + b"\x81\x80" + struct.pack("!HHHH", 1, 1, 0, 0)
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4)
    return header + packet[12:question_end] + answer + socket.inet_aton(address)


@pytest.fixture
def local_dns_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(0.1)
    calls = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                packet, peer = server.recvfrom(512)
            except socket.timeout:
                continue
            calls.append(packet)
            server.sendto(_answer(packet, "10.0.70.44"), peer)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield ("127.0.0.1", server.getsockname()[1]), calls
    finally:
        stop.set()
        thread.join(timeout=1)
        server.close()


def _resolver_module():
    # row842 fixer (4): behavioural RED lives in the tests below (they fail
    # against the reviewer-judged module, see DONE.md); module presence is
    # not asserted as a test in itself.
    return importlib.import_module("bulk_downloader.dns_resolver")


def test_mesh_name_uses_local_dns_and_caches_answer(local_dns_server):
    resolver = _resolver_module()
    resolver.clear_mesh_cache()
    nameserver, calls = local_dns_server

    assert resolver.resolve_mesh_host("worker.mesh.local", nameserver=nameserver) == "10.0.70.44"
    started = time.perf_counter()
    assert resolver.resolve_mesh_host("worker.mesh.local", nameserver=nameserver) == "10.0.70.44"
    assert time.perf_counter() - started < 0.0002
    assert len(calls) == 1


def test_unreachable_coredns_falls_back_to_hosts(monkeypatch):
    resolver = _resolver_module()
    resolver.clear_mesh_cache()
    monkeypatch.setattr(resolver, "_hosts_address", lambda host: "127.0.0.77")

    assert resolver.resolve_mesh_host(
        "offline.mesh.local", nameserver=("127.0.0.1", 1), timeout=0.001
    ) == "127.0.0.77"


def test_only_mesh_names_use_the_coredns_transport(monkeypatch):
    resolver = _resolver_module()
    resolver.clear_mesh_cache()
    monkeypatch.setattr(resolver, "_query_coredns", lambda *_args, **_kwargs: "10.0.70.44")
    monkeypatch.setattr(resolver, "_hosts_address", lambda host: "192.0.2.8")

    assert resolver.resolve_mesh_host("example.test") == "192.0.2.8"


def test_non_loopback_nameserver_is_refused_before_network_io(monkeypatch):
    """The loopback guard runs BEFORE any socket exists: a recorder on
    socket.socket sees zero connect/sendto attempts and the call returns far
    inside the query timeout (a guard that merely let the query time out
    would show one connect to 192.0.2.1 and ~timeout elapsed)."""
    resolver = _resolver_module()
    resolver.clear_mesh_cache()
    monkeypatch.setattr(resolver, "_hosts_address", lambda host: "127.0.0.77")
    attempts = []
    real_socket = socket.socket

    class RecordingSocket(real_socket):
        def connect(self, address):
            attempts.append(("connect", address))
            return super().connect(address)

        def sendto(self, *args):
            attempts.append(("sendto", args[-1]))
            return super().sendto(*args)

        def send(self, *args):
            attempts.append(("send", None))
            return super().send(*args)

    monkeypatch.setattr(socket, "socket", RecordingSocket)
    t0 = time.perf_counter()
    assert resolver.resolve_mesh_host(
        "safe.mesh.local", nameserver=("192.0.2.1", 53), timeout=0.25
    ) == "127.0.0.77"
    assert attempts == [], attempts
    assert time.perf_counter() - t0 < 0.1


# ── FIXER (row842 REFUTE items 1-4) ──────────────────────────────────────


def test_answer_from_a_spoofing_sender_is_ignored(local_dns_server, monkeypatch):
    """(1) The query socket is CONNECTED to the nameserver: a datagram from
    any other local sender never reaches the parser. The genuine server here
    is silenced; a spoofer that learned the client port answers first."""
    resolver = _resolver_module()
    resolver.clear_mesh_cache()
    nameserver, calls = local_dns_server

    spoofer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    spoofer.bind(("127.0.0.1", 0))
    real_socket = socket.socket

    class Observed(real_socket):
        def connect(self, addr):  # record the peer the client pinned itself to
            observed.append(addr)
            return super().connect(addr)

        def send(self, data, *a):
            # a spoofed answer to OUR query id, from the wrong sender
            spoofer.sendto(_answer(data, "6.6.6.6"), self.getsockname())
            time.sleep(0.02)
            return super().send(data, *a)

    observed = []
    monkeypatch.setattr(resolver.socket, "socket", Observed)
    try:
        assert resolver.resolve_mesh_host("pin.mesh.local", nameserver=nameserver) == "10.0.70.44"
    finally:
        spoofer.close()
    assert observed == [nameserver]
    assert len(calls) == 1


def test_transient_coredns_failure_is_retried_within_seconds_not_the_ttl(monkeypatch):
    """(2, round 2) After a failed query the /etc/hosts fallback is served
    from cache for FALLBACK_TTL_SECONDS (a lookup never pays the CoreDNS
    timeout twice in a row), then CoreDNS is retried; a recovered answer is
    cached for the full TTL."""
    resolver = _resolver_module()
    resolver.clear_mesh_cache()
    monkeypatch.setattr(resolver, "_hosts_address", lambda host: "127.0.0.77")
    clock = [1000.0]
    monkeypatch.setattr(resolver.time, "monotonic", lambda: clock[0])
    attempts = []

    def flaky(name, *, nameserver, timeout):
        attempts.append(clock[0])
        if len(attempts) == 1:
            raise OSError("CoreDNS restarting")
        return "10.0.70.44"

    monkeypatch.setattr(resolver, "_query_coredns", flaky)
    assert resolver.resolve_mesh_host("flaky.mesh.local", ttl_seconds=60) == "127.0.0.77"
    clock[0] += 1.0
    assert resolver.resolve_mesh_host("flaky.mesh.local", ttl_seconds=60) == "127.0.0.77"  # negative-cached: no 2nd timeout
    assert len(attempts) == 1
    clock[0] += resolver.FALLBACK_TTL_SECONDS
    assert resolver.resolve_mesh_host("flaky.mesh.local", ttl_seconds=60) == "10.0.70.44"  # retried, recovered
    clock[0] += 30.0
    assert resolver.resolve_mesh_host("flaky.mesh.local", ttl_seconds=60) == "10.0.70.44"
    assert len(attempts) == 2  # recovered answer cached for the full TTL
    assert resolver.FALLBACK_TTL_SECONDS < 60


def test_name_coredns_never_answers_costs_the_timeout_once_per_window(monkeypatch):
    """Round 2 (measured on the named CoreDNS: a third of the catalog gets no
    answer): repeated lookups of such a name are cache hits, not 250ms each."""
    resolver = _resolver_module()
    resolver.clear_mesh_cache()
    monkeypatch.setattr(resolver, "_hosts_address", lambda host: "10.0.70.90")
    started = time.perf_counter()
    first = resolver.resolve_mesh_host("dead.mesh.local", nameserver=("127.0.0.1", 1), timeout=0.05)
    first_ms = (time.perf_counter() - started) * 1000
    assert first == "10.0.70.90"
    laps = []
    for _ in range(5):
        t0 = time.perf_counter()
        assert resolver.resolve_mesh_host("dead.mesh.local", nameserver=("127.0.0.1", 1), timeout=0.05) == "10.0.70.90"
        laps.append((time.perf_counter() - t0) * 1000)
    assert max(laps) < 1.0, (first_ms, laps)


def _query_packet(transaction_id: int, name: str) -> bytes:
    question = b"".join(bytes((len(l),)) + l.encode() for l in name.split(".")) + b"\0"
    return struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0) + question + struct.pack("!HH", 1, 1)


def test_rdata_length_beyond_packet_is_refused():
    """(3) A malformed answer whose RDATA length overshoots the packet is an
    error, not a silently truncated record."""
    resolver = _resolver_module()
    good = _answer(_query_packet(0x1234, "x.mesh.local"), "10.0.70.44")
    assert resolver._first_a_record(good, 0x1234) == "10.0.70.44"
    # overshoot: declare 4 bytes of RDATA but cut the packet after 2
    truncated = good[:-2]
    with pytest.raises(ValueError, match="truncated"):
        resolver._first_a_record(truncated, 0x1234)
    # positive control for the length field itself: declare 400 bytes
    bloated = good[:-6] + struct.pack("!H", 400) + good[-4:]
    with pytest.raises(ValueError, match="truncated"):
        resolver._first_a_record(bloated, 0x1234)
