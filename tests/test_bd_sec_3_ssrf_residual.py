"""BD-SEC-3 residual (r3 OWED (a)-(c)): three browser paths whose own route guard never sees a redirect hop.

Measured on Playwright 1.62: a ``page.route`` handler is handed the first URL of a navigation only; the 302 hop
Chromium follows is never routed, so a guard that lives only in a route admits a public page that redirects to
the metadata address.  ``outbound_guard.install_page_guard`` adds the browser-level CDP ``Fetch`` backstop that
does see the hop.  The three paths:

  (a) selector_playground._fetch_playwright  (/api/playground/test use_playwright)  -> PUBLIC_ONLY page guard
  (b) app_template browser mode               (/api/template/sandbox mode=browser)   -> a Fetch backstop judged by
      the sandbox's own predicate (public + the deliberate loopback exemption; no outbound_guard policy is that)
  (c) session_keeper keepalive browser        (navigate heartbeat)                   -> LAN_OK page guard (the
      keeper's check URL was never judged at all: not on the first hop either)

Real Chromium, loopback only: Python's resolver (faked) says what a name IS; Chromium's resolver is pinned with
``--host-resolver-rules`` so every name lands on the loopback sink.  A refused hop that is followed shows up as a
sink hit on ``/secret`` -- the measurement is the server's own log, not the browser's error text.
"""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

from bulk_downloader import outbound_guard as og

BD_GATE_SCOPE = "module"

NAMES = {
    "public.example": "93.184.216.34",
    "public2.example": "93.184.216.35",
    "metadata.example": "169.254.169.254",
    "lan.example": "192.168.1.20",
}
_CHROMIUM_RULES = "--host-resolver-rules=" + ", ".join(f"MAP {n} 127.0.0.1" for n in NAMES)
_REAL_GETADDRINFO = socket.getaddrinfo
BACKENDS = ["cloakbrowser", "playwright"]


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    """Python's view: NAMES answer from the table, a literal passes through, anything else does not resolve."""

    def fake(host, port, *args, **kwargs):
        if host in NAMES:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (NAMES[host], port or 0))]
        try:
            socket.inet_pton(socket.AF_INET6 if ":" in str(host) else socket.AF_INET, str(host))
        except OSError:
            raise socket.gaierror(socket.EAI_NONAME, f"fake resolver: no such name {host!r}") from None
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)

    monkeypatch.setattr(og, "_resolve", lambda host, port: fake(host, port))
    monkeypatch.setattr(socket, "getaddrinfo", fake)


class Sink:
    """Loopback HTTP server recording every request; /redir?to=<url> answers 302, /secret answers a marker."""

    def __init__(self):
        self.hits = hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append((self.path, self.headers.get("Host")))
                if self.path.startswith("/redir?to="):
                    self.send_response(302)
                    self.send_header("Location", self.path.split("to=", 1)[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = (b"SECRET-BODY" if self.path == "/secret" else b"SINK-BODY " + self.path.encode())
                body = b"<html><body><p id='m'>" + body + b"</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, host, path):
        return f"http://{host}:{self.port}{path}"

    def redirect(self, host, to_host, to_path):
        return self.url(host, "/redir?to=" + self.url(to_host, to_path))

    def paths(self):
        return [p for p, _ in self.hits]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sink():
    s = Sink()
    yield s
    s.close()


@pytest.fixture(params=BACKENDS)
def browser_backend(request, monkeypatch):
    """Both backends, real Chromium, with Chromium's resolver pinned to the sink for every NAMES entry."""
    from bulk_downloader import cloak

    monkeypatch.setenv("BD_BROWSER_BACKEND", request.param)
    for name in ("launch_browser", "open_persistent_context", "cloaked_page"):
        real = getattr(cloak, name)

        def wrapped(*a, _real=real, **kw):
            kw["args"] = list(kw.get("args") or []) + [_CHROMIUM_RULES, "--no-proxy-server"]
            return _real(*a, **kw)

        monkeypatch.setattr(cloak, name, wrapped)
    return request.param


# ---- (a) /api/playground/test use_playwright ---------------------------------------------------------------------


def test_a_playground_browser_redirect_hop_to_metadata_is_refused(sink, browser_backend):
    from bulk_downloader import selector_playground as sp

    out = sp.fetch_page(sink.redirect("public.example", "metadata.example", "/secret"), use_playwright=True, timeout=20)
    assert "/redir?to=" in "".join(sink.paths()), f"precondition: the browser never reached the sink: {out}"
    assert "/secret" not in sink.paths() and "SECRET-BODY" not in (out.get("html") or ""), (
        f"SSRF_RESIDUAL_PLAYGROUND_HOP_REACHED: the playwright playground followed a 302 to metadata.example "
        f"(169.254.169.254) and fetched it [{browser_backend}]: hits={sink.hits} out={str(out)[:300]}"
    )
    assert out["ok"] is False


def test_a_playground_browser_redirect_to_a_public_host_still_renders(sink, browser_backend):
    from bulk_downloader import selector_playground as sp

    out = sp.fetch_page(sink.redirect("public.example", "public2.example", "/ok"), use_playwright=True, timeout=20)
    assert out["ok"] is True and "SINK-BODY /ok" in out["html"], f"[{browser_backend}] {out}"
    assert sink.paths()[-1] == "/ok"


# ---- (b) /api/template/sandbox mode=browser ----------------------------------------------------------------------


def _sandbox(fresh_app, url):
    r = fresh_app.post("/api/template/sandbox", json={"url": url, "template": {}, "mode": "browser", "wait_ms": 1})
    return r.status_code, r.get_json()


def test_b_sandbox_browser_redirect_hop_to_metadata_is_refused(fresh_app, sink, browser_backend):
    code, body = _sandbox(fresh_app, sink.redirect("127.0.0.1", "metadata.example", "/secret"))
    assert "/redir?to=" in "".join(sink.paths()), f"precondition: the browser never reached the sink: {code} {body}"
    assert "/secret" not in sink.paths(), (
        f"SSRF_RESIDUAL_SANDBOX_HOP_REACHED: the template sandbox browser followed a 302 to metadata.example "
        f"(169.254.169.254) and fetched it [{browser_backend}]: hits={sink.hits} body={str(body)[:300]}"
    )
    assert body["ok"] is False


def test_b_sandbox_browser_keeps_its_loopback_exemption_and_public_hops(fresh_app, sink, browser_backend):
    code, body = _sandbox(fresh_app, sink.redirect("127.0.0.1", "public2.example", "/ok"))
    assert code == 200 and body["ok"] is True, f"[{browser_backend}] {code} {body}"
    assert sink.paths() == [sink.paths()[0], "/ok"], sink.hits


# ---- (c) session keeper navigate heartbeat -----------------------------------------------------------------------


def _keeper_navigate(monkeypatch, tmp_path, check_url):
    from bulk_downloader import session_keeper as sk

    monkeypatch.chdir(tmp_path)
    keeper = sk.SessionKeeper("ssrfresidual", 0, {"keep_alive_check_url": check_url}, lambda *a, **k: None)
    try:
        assert keeper._launch_browser(), f"keeper browser did not launch: {keeper._last_launch_failure}"
        return keeper._heartbeat_navigate()
    finally:
        keeper._teardown_browser()


@pytest.mark.parametrize("hop", ["direct", "redirect"])
def test_c_keeper_navigation_to_metadata_is_refused(monkeypatch, tmp_path, sink, browser_backend, hop):
    url = (sink.url("metadata.example", "/secret") if hop == "direct"
           else sink.redirect("public.example", "metadata.example", "/secret"))
    verdict = _keeper_navigate(monkeypatch, tmp_path, url)
    assert "/secret" not in sink.paths(), (
        f"SSRF_RESIDUAL_KEEPER_NAV_REACHED: the keepalive browser navigated to metadata.example (169.254.169.254) "
        f"[{browser_backend} {hop}]: hits={sink.hits} verdict={verdict}"
    )
    assert "navigate ok" not in verdict[1], verdict


def test_c_keeper_navigation_to_a_lan_site_still_works(monkeypatch, tmp_path, sink, browser_backend):
    """The keeper keeps LAN_OK (a LAN-hosted site is a legitimate keepalive target): a public page that redirects
    to a 192.168.x site is followed and the heartbeat is ALIVE."""
    verdict = _keeper_navigate(monkeypatch, tmp_path, sink.redirect("public.example", "lan.example", "/ok"))
    assert sink.paths()[-1] == "/ok", (sink.hits, verdict)
    assert "navigate ok" in verdict[1], verdict
