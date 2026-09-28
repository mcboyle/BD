"""dl-f3 (findings/APP-DOWNLOAD-TEST-20260928.md#F3): direct-http fails on every dual-stack host when the box has
no IPv6 route.

PinnedTransport races the vetted IPv6 sibling first (row 867). The real backend is httpcore's SyncBackend, which
maps the socket OSError to httpcore.ConnectError -- NOT an OSError subclass. race_connect treated every
non-OSError as a transport bug and re-raised it, so the instant IPv6 failure ([Errno 101] on test5) aborted the
race before IPv4 was tried. tests/test_happy_eyeballs.py fakes connect() raising a plain OSError and never saw
the mapped type.

Hermetic: loopback only. The IPv6 candidate is ::1 on a port nothing listens on (refused or, with IPv6 off,
unavailable -- both reach the same mapped ConnectError the ENETUNREACH did); the IPv4 candidate is a local server.
"""
from __future__ import annotations

import http.server
import socket
import threading

import httpcore
import httpx
import pytest

from bulk_downloader import happy_eyeballs, multi_homed_egress, ssrf_transport

BD_GATE_SCOPE = "module"

BODY = b"\x00\x00\x00\x18ftypmp42" + b"B" * 4096


@pytest.fixture
def v4_server():
    """A 127.0.0.1-only HTTP server; its port on ::1 has no listener (checked by the test, not assumed)."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture(autouse=True)
def _single_homed(monkeypatch):
    # Row 1065's egress router would replace SyncBackend.connect_tcp; this test is about the SyncBackend path.
    router = multi_homed_egress.get_multi_homed_router()
    monkeypatch.setattr(router, "is_configured", lambda: False)


def _v6_fails_as_mapped_connect_error(port):
    """Positive control on the failure half: the real SyncBackend turns the IPv6 failure into httpcore.ConnectError."""
    with pytest.raises(httpcore.ConnectError) as ei:
        httpcore._backends.sync.SyncBackend().connect_tcp("::1", port, timeout=2.0)
    assert not isinstance(ei.value, OSError), "fixture: the mapped type is the whole defect -- it must not be an OSError"


class _Siblings:
    def __init__(self, siblings):
        self._vetted_siblings = siblings


def test_backend_falls_back_to_ipv4_when_ipv6_fails_through_the_real_syncbackend(v4_server):
    _v6_fails_as_mapped_connect_error(v4_server)
    backend = ssrf_transport._HappyEyeballsBackend(_Siblings({"127.0.0.1": ("::1", "127.0.0.1")}))

    stream = backend.connect_tcp("127.0.0.1", v4_server, timeout=5.0)
    try:
        assert stream.get_extra_info("server_addr")[0] == "127.0.0.1"
    finally:
        stream.close()


def test_pinned_transport_get_succeeds_on_a_dual_stack_host_without_ipv6(v4_server, monkeypatch):
    """The user-visible symptom: a direct-http GET of a dual-stack media URL. Before the fix: httpx.ConnectError."""
    _v6_fails_as_mapped_connect_error(v4_server)
    answers = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", v4_server, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", v4_server)),
    ]
    real_getaddrinfo = socket.getaddrinfo
    # ssrf_transport.socket IS the socket module: answer only for the test host, or create_connection("::1")
    # would resolve through this fake too and quietly fall back to 127.0.0.1 inside the IPv6 attempt.
    monkeypatch.setattr(ssrf_transport.socket, "getaddrinfo",
                        lambda host, *a, **k: answers if host == "dualstack.test" else real_getaddrinfo(host, *a, **k))
    # loopback is never admissible in production; the SSRF address policy is not under test here
    monkeypatch.setattr(ssrf_transport, "never_admissible", lambda addr: None)

    with httpx.Client(transport=ssrf_transport.PinnedTransport(), timeout=10.0) as client:
        r = client.get(f"http://dualstack.test:{v4_server}/file_example_MP4_480_1_5MG.mp4")

    assert r.status_code == 200
    assert r.content == BODY


def test_every_candidate_failing_still_raises_the_mapped_connect_error():
    """Negative control: a mapped ConnectError on every candidate is a failed download, never a stream."""
    calls = []

    def connect(ip, port, timeout):
        calls.append(ip)
        raise httpcore.ConnectError(f"[Errno 101] Network is unreachable ({ip})")

    with pytest.raises(httpcore.ConnectError, match="192.0.2.1"):
        happy_eyeballs.race_connect(("2001:db8::1", "192.0.2.1"), 443, connect=connect,
                                    lost=ssrf_transport._LOST_RACE, head_start=2.0)
    assert calls == ["2001:db8::1", "192.0.2.1"], "IPv4 must be tried after the IPv6 ConnectError"


def test_a_transport_bug_is_still_not_a_lost_race_through_the_backend(v4_server, monkeypatch):
    """The row 867 contract survives in the backend: only connect failures lose a race; a TypeError from the
    IPv6 attempt surfaces at once instead of being masked by an IPv4 stream that would connect."""
    backend = ssrf_transport._HappyEyeballsBackend(_Siblings({"127.0.0.1": ("::1", "127.0.0.1")}))
    real_connect = backend._base.connect_tcp
    calls = []

    def connect_tcp(host, port, timeout=None, local_address=None, socket_options=None):
        calls.append(host)
        if host == "::1":
            raise TypeError("transport bug")
        return real_connect(host, port, timeout, local_address, socket_options)

    monkeypatch.setattr(backend._base, "connect_tcp", connect_tcp)
    with pytest.raises(TypeError, match="transport bug"):
        backend.connect_tcp("127.0.0.1", v4_server, timeout=5.0)
    assert calls == ["::1"], "the bug was masked by racing the IPv4 candidate"
