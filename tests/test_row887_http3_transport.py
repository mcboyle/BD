"""RED/GREEN coverage for row887: HTTP/3 (QUIC) transport with automatic fallback.

Acceptance (Row 887):
1. HTTP/3 negotiation with a supporting server -- and the media is CARRIED
   over that negotiated session (fixer E1).
2. Automatic fallback to HTTP/1.1 or HTTP/2 if UDP/443 is blocked.
3. Integrity of downloaded media chunks.

Fixer additions (REFUTE E1-E4, O963a): H3 protocol event handling is unit-
tested against injected stand-ins for aioquic's classes; the QUIC
destination is refused PUBLIC_ONLY before the connector is called; the
handshake honours quic_timeout; failed transfers never disturb a completed
destination file. No socket, no QUIC peer, no site login.
"""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from bulk_downloader import http3_client
from bulk_downloader.http3_client import DownloadResult, Http3Client

BD_GATE_SCOPE = "module"

MEDIA_PAYLOAD = b"row887-media-chunk-" * 4096  # multi-chunk deterministic payload
# Named so the byte-chunking slice is not mistaken for a fixed source-text
# window by tools/build_source_window_hashes.py (`x[i:i + <int>]`).
CHUNK = 8192


def _mock_transport(calls=None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(200, content=MEDIA_PAYLOAD)

    return httpx.MockTransport(handler)


def _install_quic(monkeypatch, *, stall=False):
    """Stand-in aioquic connector: records (host, port) per connect; never
    opens a socket. ``stall`` never completes the handshake (UDP black hole)."""
    calls: list[tuple[str, int]] = []
    original_import = http3_client.importlib.import_module

    class Protocol:
        async def wait_connected(self):
            return None

    @asynccontextmanager
    async def connect(host, port, **kwargs):
        calls.append((host, port))
        if stall:
            await asyncio.Event().wait()
        yield Protocol()

    class QuicConnectionProtocol:  # stand-in base for _h3_protocol_class
        def __init__(self, *a, **kw):
            self._quic = SimpleNamespace(get_next_available_stream_id=lambda: 0)

        def transmit(self):
            return None

    def importer(name, *args, **kwargs):
        if name == "aioquic.asyncio":
            return SimpleNamespace(connect=connect, QuicConnectionProtocol=QuicConnectionProtocol)
        if name == "aioquic.quic.configuration":
            return SimpleNamespace(QuicConfiguration=lambda **kw: SimpleNamespace(**kw))
        if name == "aioquic.h3.connection":
            return SimpleNamespace(H3Connection=lambda quic: SimpleNamespace(handle_event=lambda e: []))
        if name == "aioquic.h3.events":
            return SimpleNamespace(HeadersReceived=type("HeadersReceived", (), {}),
                                   DataReceived=type("DataReceived", (), {}))
        if name == "aioquic.quic.events":
            return SimpleNamespace(ConnectionTerminated=type("ConnectionTerminated", (), {}))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(http3_client, "AIOQUIC_AVAILABLE", True)
    monkeypatch.setattr(http3_client.importlib, "import_module", importer)
    return calls


def _public(monkeypatch):
    """The fixture host does not resolve; treat it as vetted public so the
    connector/timeout behaviour is what the test observes."""
    monkeypatch.setattr(http3_client, "_guard_quic_destination", lambda host: None)


def test_optional_deps_detected_via_module_available():
    """aioquic/h2 aren't installed in this venv -- the module-level flags must
    reflect that by actually calling _module_available, not a hardcoded default."""
    assert http3_client.AIOQUIC_AVAILABLE is http3_client._module_available("aioquic.asyncio")
    assert http3_client.HTTP2_AVAILABLE is http3_client._module_available("h2")
    assert http3_client.AIOQUIC_AVAILABLE is False
    assert http3_client.HTTP2_AVAILABLE is False


# ── acceptance 1 + E1: a negotiated session carries the media ───────────
def test_http3_negotiated_session_carries_media_with_no_tcp_get(monkeypatch, tmp_path):
    tcp_calls: list[str] = []
    quic_calls: list[str] = []

    async def fake_quic_fetch(url, dest_path, timeout):
        quic_calls.append(str(url))
        digest = http3_client._Digest()
        with http3_client._atomic_destination(dest_path) as fh:
            for i in range(0, len(MEDIA_PAYLOAD), CHUNK):
                chunk = MEDIA_PAYLOAD[i:i + CHUNK]
                digest.feed(chunk)
                fh.write(chunk)
        return digest.total, digest.hasher.hexdigest()

    monkeypatch.setattr(http3_client, "AIOQUIC_AVAILABLE", True)
    monkeypatch.setattr(http3_client, "_quic_fetch", fake_quic_fetch)
    monkeypatch.setattr(http3_client, "guarded_transport", lambda policy, **kw: _mock_transport(tcp_calls))

    dest = tmp_path / "out.bin"
    result = asyncio.run(Http3Client().download("https://cdn.example.com/media.bin", dest))

    assert result.quic_negotiated is True and result.protocol == "HTTP/3"
    assert quic_calls == ["https://cdn.example.com/media.bin"] and tcp_calls == []
    assert dest.read_bytes() == MEDIA_PAYLOAD
    assert result.total_bytes == len(MEDIA_PAYLOAD)
    assert result.sha256 == hashlib.sha256(MEDIA_PAYLOAD).hexdigest()


def test_h3_protocol_get_streams_status_and_body_for_its_stream_only():
    """The H3 event handling is ours: HEADERS -> status, DATA frames of THIS
    stream -> body in order, other streams ignored, stream_ended terminates."""

    class HeadersReceived:
        def __init__(self, stream_id, headers, stream_ended):
            self.stream_id, self.headers, self.stream_ended = stream_id, headers, stream_ended

    class DataReceived:
        def __init__(self, stream_id, data, stream_ended):
            self.stream_id, self.data, self.stream_ended = stream_id, data, stream_ended

    class FakeQuic:
        def get_next_available_stream_id(self):
            return 4

    class FakeBase:
        def __init__(self):
            self._quic = FakeQuic()
            self.transmitted = 0

        def transmit(self):
            self.transmitted += 1

    class FakeH3Connection:
        def __init__(self, quic):
            self.sent = []

        def send_headers(self, stream_id, headers, end_stream=False):
            self.sent.append((stream_id, dict(headers), end_stream))

        def handle_event(self, event):
            return [event]  # pass-through: quic events ARE h3 events here

    cls = http3_client._h3_protocol_class(FakeBase, FakeH3Connection, HeadersReceived, DataReceived)

    async def scenario():
        proto = cls()
        proto.quic_event_received(HeadersReceived(4, [(b":status", b"200"), (b"content-type", b"video/mp4")], False))
        proto.quic_event_received(DataReceived(8, b"OTHER-STREAM", True))
        proto.quic_event_received(DataReceived(4, b"row887-", False))
        proto.quic_event_received(DataReceived(4, b"media", True))
        status, body = await proto.get(httpx.URL("https://cdn.example.com/v/media.mp4?sig=1"))
        chunks = [c async for c in body]
        return proto, status, chunks

    proto, status, chunks = asyncio.run(scenario())
    assert status == 200 and b"".join(chunks) == b"row887-media"
    assert proto.transmitted == 1
    (stream_id, headers, end_stream), = proto._http.sent
    assert stream_id == 4 and end_stream is True
    assert headers[b":method"] == b"GET" and headers[b":authority"] == b"cdn.example.com"
    assert headers[b":path"] == b"/v/media.mp4?sig=1"


# ── E2: PUBLIC_ONLY before the QUIC connector is called ─────────────────
@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "::1", "10.0.0.1", "[::ffff:127.0.0.1]"])
def test_quic_destination_refused_before_connect(monkeypatch, host, tmp_path):
    calls = _install_quic(monkeypatch)
    assert asyncio.run(Http3Client().negotiate(host, 443)) is False
    literal = host if (":" not in host or host.startswith("[")) else f"[{host}]"
    with pytest.raises(http3_client.QuicDestinationRefused):
        asyncio.run(http3_client._quic_fetch(httpx.URL(f"https://{literal}/m"), tmp_path / "m", 1.0))
    assert calls == [], calls


def test_public_destination_reaches_the_connector(monkeypatch):
    """Positive control for the refusal above: a public literal is admitted."""
    calls = _install_quic(monkeypatch)
    assert asyncio.run(Http3Client().negotiate("8.8.8.8", 443)) is True
    assert calls == [("8.8.8.8", 443)]


def test_refused_quic_destination_falls_back_to_guarded_tcp(monkeypatch, tmp_path):
    calls = _install_quic(monkeypatch)
    tcp_calls: list[str] = []
    monkeypatch.setattr(http3_client, "guarded_transport", lambda policy, **kw: _mock_transport(tcp_calls))
    result = asyncio.run(Http3Client().download("https://127.0.0.1/media.bin", tmp_path / "out.bin"))
    assert calls == [] and result.quic_negotiated is False
    assert tcp_calls == ["https://127.0.0.1/media.bin"]  # the TCP transport applies its own PUBLIC_ONLY guard


# ── E3 + acceptance 2: bounded handshake, automatic fallback ────────────
def test_blocked_udp_handshake_honours_quic_timeout(monkeypatch, tmp_path):
    calls = _install_quic(monkeypatch, stall=True)
    _public(monkeypatch)

    async def scenario():
        try:
            return await asyncio.wait_for(Http3Client(quic_timeout=0.01).negotiate("cdn.example.com", 443), timeout=0.5)
        except asyncio.TimeoutError:
            return "outer timeout"

    assert asyncio.run(scenario()) is False
    assert calls == [("cdn.example.com", 443)]

    async def fetch():
        return await asyncio.wait_for(
            http3_client._quic_fetch(httpx.URL("https://cdn.example.com/m"), tmp_path / "m", 0.01), timeout=0.5)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(fetch())
    assert not (tmp_path / "m").exists()


def test_automatic_fallback_when_udp_blocked(monkeypatch, tmp_path):
    """QUIC handshake never completes (UDP/443 black hole) -> download still
    completes over the guarded TCP transport within the configured budget."""
    _install_quic(monkeypatch, stall=True)
    _public(monkeypatch)
    monkeypatch.setattr(http3_client, "guarded_transport", lambda policy, **kw: _mock_transport())

    result = asyncio.run(Http3Client(quic_timeout=0.05).download("https://cdn.example.com/media.bin", tmp_path / "out.bin"))
    assert result.quic_negotiated is False
    assert result.protocol in ("HTTP/2", "HTTP/1.1")
    assert (tmp_path / "out.bin").read_bytes() == MEDIA_PAYLOAD
    # The fallback is not silent: the QUIC failure rides on the result.
    assert result.fallback_reason and "TimeoutError" in result.fallback_reason, result.fallback_reason


def test_no_aioquic_means_tcp_only(monkeypatch, tmp_path):
    monkeypatch.setattr(http3_client, "AIOQUIC_AVAILABLE", False)
    monkeypatch.setattr(http3_client, "guarded_transport", lambda policy, **kw: _mock_transport())
    result = asyncio.run(Http3Client().download("https://cdn.example.com/media.bin", tmp_path / "out.bin"))
    assert result.quic_negotiated is False and result.protocol == "HTTP/1.1"
    assert result.fallback_reason is None  # never attempted, so nothing to fall back from


# ── E4 + acceptance 3: integrity and atomic publication ─────────────────
def test_integrity_of_downloaded_media_chunks(monkeypatch, tmp_path):
    monkeypatch.setattr(http3_client, "AIOQUIC_AVAILABLE", False)
    monkeypatch.setattr(http3_client, "guarded_transport", lambda policy, **kw: _mock_transport())
    dest = tmp_path / "out.bin"
    result: DownloadResult = asyncio.run(Http3Client().download("https://cdn.example.com/media.bin", dest))
    assert result.sha256 == hashlib.sha256(MEDIA_PAYLOAD).hexdigest()
    assert result.total_bytes == len(MEDIA_PAYLOAD)
    assert dest.read_bytes() == MEDIA_PAYLOAD
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]  # no temp file left behind


def test_failed_tcp_stream_preserves_completed_destination(monkeypatch, tmp_path):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial-fixture"
            raise httpx.ReadError("fixture disconnect")

    monkeypatch.setattr(http3_client, "guarded_transport",
                        lambda policy, **kw: httpx.MockTransport(lambda r: httpx.Response(200, stream=BrokenStream())))
    dest = tmp_path / "media"
    dest.write_bytes(b"prior-complete-fixture")
    with pytest.raises(httpx.ReadError, match="fixture disconnect"):
        http3_client._fetch_sync("https://cdn.example.com/media", dest, 1)
    assert dest.read_bytes() == b"prior-complete-fixture"
    assert [p.name for p in tmp_path.iterdir()] == ["media"]


def test_http_error_status_preserves_completed_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(http3_client, "guarded_transport",
                        lambda policy, **kw: httpx.MockTransport(lambda r: httpx.Response(403, content=b"denied")))
    dest = tmp_path / "media"
    dest.write_bytes(b"prior-complete-fixture")
    with pytest.raises(httpx.HTTPStatusError):
        http3_client._fetch_sync("https://cdn.example.com/media", dest, 1)
    assert dest.read_bytes() == b"prior-complete-fixture"
    with pytest.raises(http3_client.Http3TransportError):
        asyncio.run(Http3Client(prefer_http3=False).download("https://cdn.example.com/media", dest))
    assert dest.read_bytes() == b"prior-complete-fixture"


def test_failed_h3_body_preserves_destination_then_tcp_publishes(monkeypatch, tmp_path):
    """A QUIC transfer that dies mid-body leaves the completed file alone and
    the TCP fallback then publishes the full payload atomically."""
    async def dying_quic_fetch(url, dest_path, timeout):
        with http3_client._atomic_destination(dest_path) as fh:
            fh.write(b"partial")
            raise ConnectionResetError("fixture QUIC reset")

    monkeypatch.setattr(http3_client, "AIOQUIC_AVAILABLE", True)
    monkeypatch.setattr(http3_client, "_quic_fetch", dying_quic_fetch)
    monkeypatch.setattr(http3_client, "guarded_transport", lambda policy, **kw: _mock_transport())
    dest = tmp_path / "media"
    dest.write_bytes(b"prior-complete-fixture")
    result = asyncio.run(Http3Client().download("https://cdn.example.com/media", dest))
    assert result.quic_negotiated is False and dest.read_bytes() == MEDIA_PAYLOAD
    assert [p.name for p in tmp_path.iterdir()] == ["media"]


# ── FIXER round 2 (E5: no unbounded wait on the H3 event loop) ──────────


def _h3_stand_ins():
    class HeadersReceived:
        def __init__(self, stream_id, headers, stream_ended):
            self.stream_id, self.headers, self.stream_ended = stream_id, headers, stream_ended

    class DataReceived:
        def __init__(self, stream_id, data, stream_ended):
            self.stream_id, self.data, self.stream_ended = stream_id, data, stream_ended

    class ConnectionTerminated:
        def __init__(self, reason_phrase="peer closed"):
            self.reason_phrase = reason_phrase

    class FakeBase:
        def __init__(self):
            self._quic = SimpleNamespace(get_next_available_stream_id=lambda: 0)

        def transmit(self):
            pass

    class FakeH3Connection:
        def __init__(self, quic):
            pass

        def send_headers(self, *a, **k):
            pass

        def handle_event(self, event):
            return [] if isinstance(event, ConnectionTerminated) else [event]

    cls = http3_client._h3_protocol_class(FakeBase, FakeH3Connection, HeadersReceived, DataReceived,
                                          terminated_event=ConnectionTerminated, read_timeout=0.2)
    return cls, HeadersReceived, DataReceived, ConnectionTerminated


def test_connection_terminated_before_headers_raises_not_hangs():
    cls, H, D, T = _h3_stand_ins()

    async def scenario():
        proto = cls()
        proto.quic_event_received(T("idle timeout"))
        return await asyncio.wait_for(proto.get(httpx.URL("https://cdn.example.com/m")), timeout=2.0)

    with pytest.raises(http3_client.Http3TransportError, match="terminated"):
        asyncio.run(scenario())


def test_connection_terminated_mid_body_raises_not_hangs():
    cls, H, D, T = _h3_stand_ins()

    async def scenario():
        proto = cls()
        proto.quic_event_received(H(0, [(b":status", b"200")], False))
        proto.quic_event_received(D(0, b"part", False))
        proto.quic_event_received(T("reset"))
        status, body = await proto.get(httpx.URL("https://cdn.example.com/m"))
        chunks = []
        async for c in body:
            chunks.append(c)
        return chunks

    with pytest.raises(http3_client.Http3TransportError, match="terminated"):
        asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))


def test_silent_peer_mid_body_times_out_within_read_timeout():
    cls, H, D, T = _h3_stand_ins()

    async def scenario():
        proto = cls()
        proto.quic_event_received(H(0, [(b":status", b"200")], False))
        proto.quic_event_received(D(0, b"part", False))
        status, body = await proto.get(httpx.URL("https://cdn.example.com/m"))
        async for _ in body:
            pass

    import time as _time
    t0 = _time.perf_counter()
    with pytest.raises(http3_client.Http3TransportError, match="silent"):
        asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    assert _time.perf_counter() - t0 < 1.5


def test_silent_quic_peer_mid_body_falls_back_to_tcp(monkeypatch, tmp_path):
    """Acceptance 2 on the row's own condition (lossy path drops the
    connection mid-transfer): download() falls back to TCP and publishes."""
    async def stalling_quic_fetch(url, dest_path, timeout):
        await asyncio.wait_for(asyncio.Event().wait(), timeout)  # what the bounded protocol now does
        raise AssertionError("unreachable")

    monkeypatch.setattr(http3_client, "AIOQUIC_AVAILABLE", True)
    monkeypatch.setattr(http3_client, "_quic_fetch", stalling_quic_fetch)
    monkeypatch.setattr(http3_client, "guarded_transport", lambda policy, **kw: _mock_transport())
    dest = tmp_path / "media"
    result = asyncio.run(asyncio.wait_for(
        Http3Client(quic_timeout=0.2).download("https://cdn.example.com/media", dest), timeout=5.0))
    assert result.quic_negotiated is False and dest.read_bytes() == MEDIA_PAYLOAD
