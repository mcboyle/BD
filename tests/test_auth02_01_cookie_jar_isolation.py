"""F-AUTH02-01 -- cookie_health jar isolation.

``check_site`` used to flatten the whole Netscape jar into a domain-blind
``{name: value}`` dict and hand it to ``httpx.get(check_url, cookies=...)``,
so every cookie in a multi-domain ``cookies.txt`` was sent to check_url's
host -- and, because httpx carries a cookie dict verbatim across redirects,
to any redirect target too. The fix sends cookies through the jar so httpx
enforces per-host domain matching on every hop.

Self-contained (no pytest fixtures / builtins) so it runs under both the
sandbox minimal runner and on-stash pytest. Uses loopback: 127.0.0.1 and
'localhost' are distinct cookie hosts that both resolve to the loopback iface,
which lets us exercise a genuine cross-host redirect without touching DNS.
"""
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def _make_server(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _write_jar(host):
    path = os.path.join(tempfile.mkdtemp(), "cookies.txt")
    exp = str(int(time.time()) + 3600)
    with open(path, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write(f"{host}\tFALSE\t/\tFALSE\t{exp}\tgood\tGOODVAL\n")
        f.write(f".evil.example.com\tTRUE\t/\tFALSE\t{exp}\tevil\tEVILVAL\n")
    return path


def test_cross_domain_cookie_not_leaked_first_hop():
    from bulk_downloader import cookie_health
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["cookie"] = self.headers.get("Cookie", "")
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *a): pass

    srv, port = _make_server(H)
    try:
        cfg = {"auth_check_url": f"http://127.0.0.1:{port}/",
               "cookies_file": _write_jar("127.0.0.1")}
        cookie_health.check_site("auth02-first-hop", cfg)
    finally:
        srv.shutdown()

    sent = seen.get("cookie", "")
    assert "GOODVAL" in sent, f"same-host cookie should be sent; got {sent!r}"
    assert "EVILVAL" not in sent, f"cross-domain cookie leaked: {sent!r}"


def test_cookie_not_carried_across_cross_host_redirect():
    from bulk_downloader import cookie_health
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/start"):
                seen["start"] = self.headers.get("Cookie", "")
                # redirect to a DIFFERENT host string (still loopback)
                self.send_response(302)
                self.send_header("Location",
                                 f"http://localhost:{self.server.server_address[1]}/land")
                self.end_headers()
                return
            seen["land"] = self.headers.get("Cookie", "")
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *a): pass

    srv, port = _make_server(H)
    try:
        cfg = {"auth_check_url": f"http://127.0.0.1:{port}/start",
               "cookies_file": _write_jar("127.0.0.1")}
        cookie_health.check_site("auth02-redirect", cfg)
    finally:
        srv.shutdown()

    # cookie was scoped to 127.0.0.1: sent on the first hop, NOT on the
    # localhost redirect target.
    assert "GOODVAL" in seen.get("start", ""), \
        f"cookie should ride the same-host first hop; got {seen.get('start')!r}"
    assert "GOODVAL" not in seen.get("land", ""), \
        f"cookie leaked across cross-host redirect: {seen.get('land')!r}"


if __name__ == "__main__":
    import traceback
    for name in ("test_cross_domain_cookie_not_leaked_first_hop",
                 "test_cookie_not_carried_across_cross_host_redirect"):
        try:
            globals()[name](); print(f"PASS  {name}")
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception:
            print(f"ERROR {name}"); traceback.print_exc()
