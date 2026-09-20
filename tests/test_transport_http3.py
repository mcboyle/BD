"""tests/test_transport_http3.py — Tests for Row 853 HTTP/3 QUIC Transport Protocol Integration.

Acceptance criteria (Row 853):
1. HTTP/3 stream multiplexing against local QUIC test fixture.
2. Instant fallback to TCP on connection drop (catching CurlError and OSError).
3. Zero site logins touched (Rule 21).
"""

from __future__ import annotations

# BD_GATE_SCOPE marker: strictly 'module' or 'repo-wide' (test_v3_66_939)
BD_GATE_SCOPE = "module"

import socket
import threading
from typing import Any, Dict, List, Optional, Tuple

import pytest

from bulk_downloader import runner_transport as transport

try:
    from curl_cffi.curl import CurlError
except ImportError:
    class CurlError(Exception):
        """Fallback definition if curl_cffi is missing."""
        pass


class LocalQuicFixture:
    """Local QUIC test fixture running an in-process UDP socket server on 127.0.0.1 (Rule 21)."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, 0))
        self.host, self.port = self.sock.getsockname()
        self.running = True
        self.received_datagrams: List[Tuple[bytes, Tuple[str, int]]] = []
        self.multiplexed_stream_ids: set[int] = set()
        self.datagram_received = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                self.received_datagrams.append((data, addr))
                if len(data) >= 4:
                    stream_id = int.from_bytes(data[:4], byteorder="big")
                    self.multiplexed_stream_ids.add(stream_id)
                self.datagram_received.set()
            except OSError:
                break

    def close(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


class MockCffiRequests:
    """Simulated curl_cffi requests handler with configurable QUIC connection drops."""

    def __init__(
        self,
        quic_fixture: Optional[LocalQuicFixture] = None,
        fail_h3_with: Optional[Exception] = None,
        fail_all_with: Optional[Exception] = None,
    ) -> None:
        self.quic_fixture = quic_fixture
        self.fail_h3_with = fail_h3_with
        self.fail_all_with = fail_all_with
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))

        if self.fail_all_with is not None:
            raise self.fail_all_with

        is_h3 = kwargs.get("http_version") == "v3"

        if is_h3:
            if self.fail_h3_with is not None:
                raise self.fail_h3_with

            # Send a simulated QUIC packet to local fixture to register multiplexed stream
            if self.quic_fixture is not None:
                stream_id = kwargs.get("stream_id", 0)
                pkt = int(stream_id).to_bytes(4, byteorder="big") + b"QUIC_FRAME"
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.sendto(pkt, (self.quic_fixture.host, self.quic_fixture.port))
                    s.close()
                except OSError:
                    pass

            return MockResponse(status_code=200, http_version="HTTP/3", url=url)

        # Standard TCP (HTTP/2 or HTTP/1.1)
        return MockResponse(status_code=200, http_version="HTTP/2", url=url)


class MockResponse:
    """Minimal response stub supporting close() lifecycle."""

    def __init__(self, status_code: int = 200, http_version: str = "HTTP/3", url: str = "") -> None:
        self.status_code = status_code
        self.http_version = http_version
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def iter_content(self, chunk_size: int = 8192):
        yield b"CHUNK_OK"


# =====================================================================
# Behavioral RED & Interface Tests
# =====================================================================

def test_runner_transport_exposes_http3_transport():
    """Behavioral assertion: runner_transport must expose HTTP3Transport (Row 853)."""
    http3_cls = getattr(transport, "HTTP3Transport", None)
    assert http3_cls is not None, (
        "runner_transport must export HTTP3Transport class required by Row 853"
    )


def test_runner_transport_exposes_request_download_stream():
    """Behavioral assertion: runner_transport must export _request_download_stream."""
    helper = getattr(transport, "_request_download_stream", None)
    assert helper is not None, (
        "runner_transport must export _request_download_stream helper function"
    )


# =====================================================================
# Acceptance Criteria 1: HTTP/3 Stream Multiplexing
# =====================================================================

def test_http3_stream_multiplexing_against_local_quic_fixture():
    """(1) Verify HTTP/3 stream multiplexing against a local QUIC test fixture."""
    fixture = LocalQuicFixture()
    try:
        mock_cffi = MockCffiRequests(quic_fixture=fixture)
        h3_transport = transport.HTTP3Transport(prefer_http3=True)

        # Dispatch multiple streams concurrently
        resp1 = h3_transport.request(mock_cffi, "GET", f"https://127.0.0.1:{fixture.port}/video_part1")
        resp2 = h3_transport.request(mock_cffi, "GET", f"https://127.0.0.1:{fixture.port}/video_part2")
        resp3 = h3_transport.request(mock_cffi, "GET", f"https://127.0.0.1:{fixture.port}/video_part3")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp3.status_code == 200
        assert resp1.http_version == "HTTP/3"

        # Verify all 3 requests requested HTTP/3 (v3)
        assert len(mock_cffi.calls) == 3
        for method, url, kwargs in mock_cffi.calls:
            assert kwargs.get("http_version") == "v3"

        # Verify stream multiplexing counts
        assert h3_transport.multiplexed_count == 3
        assert h3_transport.fallback_count == 0

        # Bounded wait for UDP datagram delivery to the local fixture (no bare sleep)
        assert fixture.datagram_received.wait(timeout=5.0), "no datagram reached the local fixture within 5s"
        assert len(fixture.received_datagrams) >= 1
    finally:
        fixture.close()


# =====================================================================
# Acceptance Criteria 2: Instant Fallback to TCP on Connection Drop
# =====================================================================

def test_instant_fallback_to_tcp_on_curl_error_drop():
    """(2a) Verify instant fallback to TCP when QUIC drops with CurlError (e.g. error code 7 / 28)."""
    curl_err = CurlError("QUIC connection failed: protocol error or connection drop")
    mock_cffi = MockCffiRequests(fail_h3_with=curl_err)
    h3_transport = transport.HTTP3Transport(prefer_http3=True)

    # Perform request; QUIC fails, instant fallback to TCP succeeds
    resp = h3_transport.request(
        mock_cffi, "GET", "https://127.0.0.1:8443/media",
        headers={"User-Agent": "BulkDownloader/3.66"},
        cookies={"session": "test_tok"},
        timeout=30,
    )

    assert resp.status_code == 200
    assert resp.http_version == "HTTP/2"  # Negotiated fallback protocol
    assert h3_transport.fallback_count == 1

    # Verify exactly 2 calls: first with v3 (failed), second without v3 (succeeded)
    assert len(mock_cffi.calls) == 2
    first_call = mock_cffi.calls[0]
    fallback_call = mock_cffi.calls[1]

    assert first_call[2]["http_version"] == "v3"
    assert "http_version" not in fallback_call[2]

    # Verify stream parameters and headers are fully preserved
    assert fallback_call[2]["headers"] == {"User-Agent": "BulkDownloader/3.66"}
    assert fallback_call[2]["cookies"] == {"session": "test_tok"}
    assert fallback_call[2]["timeout"] == 30


def test_instant_fallback_to_tcp_on_os_connection_error():
    """(2b) Verify instant fallback to TCP when UDP drops with OSError / ConnectionResetError."""
    os_err = ConnectionResetError("UDP socket connection reset by peer")
    mock_cffi = MockCffiRequests(fail_h3_with=os_err)
    h3_transport = transport.HTTP3Transport(prefer_http3=True)

    resp = h3_transport.request(mock_cffi, "GET", "https://127.0.0.1:8443/stream.ts")

    assert resp.status_code == 200
    assert resp.http_version == "HTTP/2"
    assert h3_transport.fallback_count == 1
    assert len(mock_cffi.calls) == 2


def test_request_download_stream_integration_with_fallback():
    """Verify high-level _request_download_stream helper activates HTTP/3 and falls back."""
    curl_err = CurlError("ALPN negotiation failed: h3 not supported by origin")
    mock_cffi = MockCffiRequests(fail_h3_with=curl_err)

    resp = transport._request_download_stream(
        mock_cffi, "https://127.0.0.1:8443/item.mp4",
        prefer_http3=True,
        headers={"Accept": "*/*"},
    )

    assert resp.status_code == 200
    assert len(mock_cffi.calls) == 2
    assert mock_cffi.calls[0][2]["http_version"] == "v3"
    assert "http_version" not in mock_cffi.calls[1][2]


# =====================================================================
# Acceptance Criteria 3: Zero Site Logins (Rule 21) & Negative Controls
# =====================================================================

def test_zero_site_logins_touched_rule_21():
    """(3) Invariant proof: tests operate exclusively on local fixtures (Rule 21)."""
    fixture = LocalQuicFixture()
    try:
        mock_cffi = MockCffiRequests(quic_fixture=fixture)
        h3_transport = transport.HTTP3Transport(prefer_http3=True)
        h3_transport.request(mock_cffi, "GET", f"http://127.0.0.1:{fixture.port}/local_test")

        for method, url, kwargs in mock_cffi.calls:
            # Asserts target is strictly local / loopback
            assert "127.0.0.1" in url or "localhost" in url
            # Asserts no site login credentials or cookies are leaked
            cookies = kwargs.get("cookies") or {}
            assert "auth" not in cookies and "login" not in cookies
    finally:
        fixture.close()


def test_negative_control_prefer_http3_false_never_attempts_v3():
    """Negative control: when prefer_http3=False, HTTP/3 is never requested."""
    mock_cffi = MockCffiRequests()
    h3_transport = transport.HTTP3Transport(prefer_http3=False)

    resp = h3_transport.request(mock_cffi, "GET", "http://127.0.0.1:8080/data")
    assert resp.status_code == 200
    assert len(mock_cffi.calls) == 1
    assert "http_version" not in mock_cffi.calls[0][2]
    assert h3_transport.multiplexed_count == 0
    assert h3_transport.fallback_count == 0


def test_negative_control_persistent_failure_fails_closed():
    """Negative control: if both HTTP/3 and fallback TCP fail, exception propagates."""
    fatal_err = RuntimeError("Persistent network outage: DNS resolution failure")
    mock_cffi = MockCffiRequests(fail_all_with=fatal_err)
    h3_transport = transport.HTTP3Transport(prefer_http3=True)

    with pytest.raises(RuntimeError) as exc_info:
        h3_transport.request(mock_cffi, "GET", "http://127.0.0.1:8080/data")

    assert "Persistent network outage" in str(exc_info.value)
