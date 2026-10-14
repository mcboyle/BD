"""Row ssrf-public-literal: PUBLIC_ONLY refuses non-public IP-literal hosts.

R205-D A3: ``httpx.Client(transport=guarded_transport(PUBLIC_ONLY))`` reached a
literal ``http://127.0.0.1:<port>/`` with HTTP 200 -- the established guard
delegates IP literals unjudged, so a redirect hop to a private literal skipped
every check.  The transport now judges a literal with the same classifier as a
resolved address (embedded IPv4 included) and refuses before any socket opens.
"""
from __future__ import annotations

import http.server
import socket
import threading

import httpx
import pytest

from bulk_downloader import ssrf_transport
from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
from bulk_downloader.ssrf_transport import (
    PINNED,
    PUBLIC_ONLY,
    GuardedTransportRefused,
    guarded_transport,
)

BD_GATE_SCOPE = "module"

_PUBLIC_V4 = "93.184.216.34"


class _Hits(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):
        self.hits.append(self.path)
        body = b"reached"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a, **k):
        pass


@pytest.fixture
def loopback_server():
    hits: list = []
    handler = type("H", (_Hits,), {"hits": hits})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1], hits
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def recorder(monkeypatch):
    """The socket-opening base transport, replaced: every request that would
    have left the process is recorded and answered 200 (or 302 from the
    public hop when a redirect target is set)."""
    seen: list = []
    redirect_to: list = []

    def handle(self, request):
        seen.append(str(request.url))
        if redirect_to and request.url.host == _PUBLIC_V4:
            return httpx.Response(302, headers={"Location": redirect_to[0]})
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    return seen, redirect_to


def test_a3_loopback_literal_is_refused_before_the_socket(loopback_server):
    base, hits = loopback_server
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY)) as cl:
        try:
            r = cl.get(base + "/a3")
        except GuardedTransportRefused as ex:
            assert ex.reason == "loopback" and ex.host == "127.0.0.1", (ex.reason, ex.host)
        else:
            pytest.fail(f"SPL-A3-LOOPBACK-REACHED status={r.status_code} hits={hits}")
    assert hits == [], f"SPL-A3-LOOPBACK-REACHED hits={hits}"


def test_a3_control_the_rig_can_reach_the_server(loopback_server):
    """Rule 7: the same server answers a PINNED client, so the zero hits above
    is the guard refusing, not a dead rig."""
    base, hits = loopback_server
    with httpx.Client(transport=guarded_transport(PINNED)) as cl:
        assert cl.get(base + "/control").status_code == 200
    assert hits == ["/control"]


@pytest.mark.parametrize(
    "host,reason",
    [
        ("127.0.0.1", "loopback"),
        ("10.0.0.1", "private"),
        ("[::1]", "loopback"),
        ("[::ffff:127.0.0.1]", "private"),
        ("100.64.0.1", "cgnat"),
        ("169.254.169.254", "link_local"),
    ],
)
def test_non_public_literal_is_refused(recorder, host, reason):
    seen, _ = recorder
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY)) as cl:
        try:
            cl.get(f"http://{host}:8080/x")
        except GuardedTransportRefused as ex:
            assert ex.reason == reason, f"SPL-LITERAL-REASON {host} {ex.reason}"
        else:
            pytest.fail(f"SPL-LITERAL-DELEGATED {host} {seen}")
    assert seen == [], f"SPL-LITERAL-DELEGATED {host} {seen}"


def test_integer_literal_is_refused_without_a_socket(recorder):
    """2130706433 is a hostname to httpx; the resolution path classifies the
    address glibc gives it (127.0.0.1).  Refused on the base as well."""
    seen, _ = recorder
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY)) as cl:
        with pytest.raises((GuardedTransportRefused, SSRFBlocked)):
            cl.get("http://2130706433/x")
    assert seen == [], f"SPL-INTEGER-DELEGATED {seen}"


def test_redirect_from_public_host_to_loopback_is_refused_at_the_hop(recorder, monkeypatch):
    seen, redirect_to = recorder
    redirect_to.append("http://127.0.0.1:9/x")
    real = socket.getaddrinfo

    def resolve(host, *a, **k):
        if host == "media.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_V4, 80))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY), follow_redirects=True) as cl:
        try:
            cl.get("http://media.example/start")
        except GuardedTransportRefused as ex:
            assert ex.host == "127.0.0.1", f"SPL-REDIRECT-WRONG-HOST {ex.host}"
        else:
            pytest.fail(f"SPL-REDIRECT-HOP-REACHED {seen}")
    assert seen == [f"http://{_PUBLIC_V4}/start"], f"SPL-REDIRECT-HOP-REACHED {seen}"


@pytest.mark.parametrize("host", [_PUBLIC_V4, "[2001:4860:4860::8888]"])
def test_public_literal_is_admitted(recorder, host):
    seen, _ = recorder
    with httpx.Client(transport=guarded_transport(PUBLIC_ONLY)) as cl:
        assert cl.get(f"http://{host}/ok").status_code == 200
    assert seen == [f"http://{host}/ok"]


def test_pinned_policy_still_admits_private_literals(recorder):
    """The row changes PUBLIC_ONLY only: PINNED sites keep LAN peers."""
    seen, _ = recorder
    with httpx.Client(transport=guarded_transport(PINNED)) as cl:
        assert cl.get("http://10.0.0.1:8080/lan").status_code == 200
    assert seen == ["http://10.0.0.1:8080/lan"]
    assert ssrf_transport.PUBLIC_ONLY != ssrf_transport.PINNED
