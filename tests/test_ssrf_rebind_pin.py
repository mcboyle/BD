"""ssrf-rebind-pin (O1789, adjudicator-A probe 2151Z as a regression test).

DNS rebinding: an attacker name answers a PUBLIC address to the guard's lookup
and an internal one to the transport's connect-time lookup (TTL 0).  The fix
pins the connect to the address judged at connect time in
selector_playground.fetch_page, site_weather.probe_http, deep_http.guarded_open
and webhooks._deliver_one.  No network: name answers come from a fake
getaddrinfo, the internal target is a loopback-only server that counts hits,
and every non-loopback connect is recorded and refused.  Every assertion
message starts with SSRF-REBIND-PIN.
"""
import http.server
import os
import socket
import sys
import tempfile
import threading

os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd-rebind-home-"))
os.environ.setdefault("BD_DATA_DIR", tempfile.mkdtemp(prefix="bd-rebind-data-"))
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import pytest

from bulk_downloader import deep_http as dh
from bulk_downloader import selector_playground as sp
from bulk_downloader import site_weather as sw
from bulk_downloader import webhooks

BD_GATE_SCOPE = "module"

PUB = "93.184.216.34"
LOOPBACK = ("127.0.0.1", "::1")
_PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy")


class _Handler(http.server.BaseHTTPRequestHandler):
    hits = []

    def _ok(self):
        self.hits.append((self.command, self.path))
        body = b"INTERNAL-ONLY-LOOPBACK-BODY"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_HEAD = do_POST = _ok

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _transport_lookup():
    """True when getaddrinfo was called from the HTTP transport (connect time)
    rather than from a pre-send guard."""
    f = sys._getframe(2)
    while f is not None:
        m = f.f_globals.get("__name__", "")
        if m.startswith(("urllib3", "http.client")) or m == "socket":
            return True
        f = f.f_back
    return False


@pytest.fixture
def net(monkeypatch, server):
    """Rebinding DNS + hermetic connect.  ``net.answer`` maps the
    ``*.rebind.test`` connect-time lookup; the guard's lookup always gets PUB."""
    for k in _PROXY_ENV:
        monkeypatch.delenv(k, raising=False)
    real_gai = socket.getaddrinfo
    real_connect = socket.socket.connect

    class Net:
        answer = "127.0.0.1"
        connects = []

    def fake_gai(host, port, *a, **k):
        if isinstance(host, str) and host.endswith(".rebind.test"):
            ip = Net.answer if _transport_lookup() else PUB
            return real_gai(ip, port, *a, **k)
        return real_gai(host, port, *a, **k)

    def connect(self, address):
        Net.connects.append(address)
        if isinstance(address, tuple) and address[0] not in LOOPBACK:
            raise ConnectionRefusedError(f"hermetic test: no route to {address!r}")
        return real_connect(self, address)

    Net.connects = []
    monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
    monkeypatch.setattr(socket.socket, "connect", connect)
    _Handler.hits.clear()
    return Net


def _call(fn):
    try:
        return fn()
    except Exception as e:  # the outcome is judged by hits/connects
        return {"exc": f"{type(e).__name__}: {e}"}


# -- positive controls: the probe can say YES ------------------------------

def test_control_unguarded_urllib_follows_rebind(net, server):
    import urllib.request
    body = urllib.request.urlopen(f"http://a.rebind.test:{server}/x", timeout=5).read()
    assert body == b"INTERNAL-ONLY-LOOPBACK-BODY" and len(_Handler.hits) == 1, (
        f"SSRF-REBIND-PIN-CONTROL: fake DNS did not rebind the transport; hits={_Handler.hits}")


def test_control_unguarded_requests_follows_rebind(net, server):
    import requests
    net.answer = "169.254.169.254"
    _call(lambda: requests.post(f"http://hook.rebind.test:{server}/h", data=b"{}", timeout=5))
    assert ("169.254.169.254", server) in net.connects, (
        f"SSRF-REBIND-PIN-CONTROL: requests transport never got the rebound answer; "
        f"connects={net.connects}")


@pytest.mark.parametrize("site", ["selector_playground", "site_weather", "deep_http"])
def test_control_guard_refuses_loopback_literal(net, server, site):
    lit = f"http://127.0.0.1:{server}/x"
    out = _call({"selector_playground": lambda: sp.fetch_page(lit),
                 "site_weather": lambda: sw.probe_http("rebind", lit),
                 "deep_http": lambda: dh.guarded_open(lit, timeout=5)}[site])
    assert _Handler.hits == [], (
        f"SSRF-REBIND-PIN-CONTROL site={site}: guard let the loopback literal through; out={out}")


# -- the rebinding cases ---------------------------------------------------

def test_selector_playground_fetch_page_pins(net, server):
    out = _call(lambda: sp.fetch_page(f"http://a.rebind.test:{server}/x"))
    assert _Handler.hits == [], (
        f"SSRF-REBIND-PIN-MISSING site=selector_playground.fetch_page "
        f"loopback_hits={len(_Handler.hits)} out={str(out)[:160]}")


def test_site_weather_probe_http_pins(net, server):
    out = _call(lambda: sw.probe_http("rebind", f"http://a.rebind.test:{server}/x"))
    assert _Handler.hits == [], (
        f"SSRF-REBIND-PIN-MISSING site=site_weather.probe_http "
        f"loopback_hits={len(_Handler.hits)} out={str(out)[:160]}")


def test_deep_http_guarded_open_pins(net, server):
    out = _call(lambda: dh.guarded_open(f"http://a.rebind.test:{server}/x", timeout=5))
    assert _Handler.hits == [], (
        f"SSRF-REBIND-PIN-MISSING site=deep_http.guarded_open "
        f"loopback_hits={len(_Handler.hits)} out={str(out)[:160]}")
    assert ("127.0.0.1", server) not in net.connects, (
        f"SSRF-REBIND-PIN-MISSING site=deep_http.guarded_open connected to loopback; "
        f"connects={net.connects}")


@pytest.mark.parametrize("internal", ["169.254.169.254", "100.64.0.7"])
def test_webhook_delivery_refuses_rebound_internal(net, server, internal):
    webhooks._ensure_tables()
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("DELETE FROM webhook_subscriptions")
    url = f"http://hook.rebind.test:{server}/h"
    sid = webhooks.add_subscription(url=url, events=["download.done"], secret="")
    assert isinstance(sid, int), f"SSRF-REBIND-PIN setup: add_subscription({url!r}) refused"
    net.answer = internal
    out = webhooks._deliver_one({"id": 1, "subscription_id": sid,
                                 "event_name": "download.done", "payload": "{}"})
    reached = [a for a in net.connects if isinstance(a, tuple) and a[0] == internal]
    assert reached == [] and not out.get("ok"), (
        f"SSRF-REBIND-PIN-MISSING site=webhooks._deliver_one connected to {internal}; "
        f"connects={net.connects} out={out}")
    assert "rebind pin" in str(out.get("error", "")), (
        f"SSRF-REBIND-PIN-MISSING site=webhooks._deliver_one refusal not from the pin; out={out}")
