"""Batch B (auth-gate, keep default-ON):

F-APP04-01 -- the /api/dev/* surface is default-ON (is_dev_mode() opt-out) and
was reachable UNAUTHENTICATED from the network on a box with no global auth
token configured (_check_token returns early when no tokens are set). _dev_mode_
guard now also requires an authorized request: loopback, a same-origin browser
request (the SPA), a valid redeemed session, or the master bearer/X-BD-Token.

F-COCKPIT03-01 -- the arbitrary-command cockpit web-shell (/cockpit/api/shell/*)
had no self-contained origin/bind guard. cockpit_console now refuses shell
requests that are neither loopback nor same-origin.

Trust model that keeps the SPA + local/standalone use working while blocking a
remote unauthenticated cross-origin caller:
  loopback (local / standalone / test-client) OR same-origin OR session OR token.
"""
import os
import tempfile

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_DEV_ROUTE = "/api/dev/leak_scan"
_REMOTE = {"REMOTE_ADDR": "10.0.0.99"}   # a non-loopback network client


# ---- F-APP04-01: dev-route auth gate ----
def test_dev_route_blocks_remote_unauthenticated():
    from bulk_downloader.app import app
    c = app.test_client()
    r = c.get(_DEV_ROUTE, environ_base=_REMOTE)  # no Referer, no session, no token
    assert r.status_code == 403, f"remote unauth dev request must be 403, got {r.status_code}"


def test_dev_route_allows_same_origin_spa():
    from bulk_downloader.app import app
    c = app.test_client()
    # same-origin browser request: Referer host:port == Host
    r = c.get(_DEV_ROUTE, environ_base=_REMOTE,
              headers={"Host": "bd.local:5555", "Referer": "http://bd.local:5555/"})
    assert r.status_code != 403, f"same-origin SPA dev request must NOT be 403, got {r.status_code}"


def test_dev_route_allows_loopback():
    from bulk_downloader.app import app
    c = app.test_client()  # test-client default remote_addr is 127.0.0.1
    r = c.get(_DEV_ROUTE)
    assert r.status_code != 403, f"loopback dev request must NOT be 403, got {r.status_code}"


# ---- F-COCKPIT03-01: cockpit web-shell origin/bind guard ----
def _shell_client():
    from flask import Flask
    from tools.cockpit_console import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def test_shell_blocks_remote_unauthenticated():
    c = _shell_client()
    r = c.get("/cockpit/api/shell/status", environ_base=_REMOTE)
    assert r.status_code == 403, f"remote unauth shell request must be 403, got {r.status_code}"


def test_shell_allows_same_origin():
    c = _shell_client()
    r = c.get("/cockpit/api/shell/status", environ_base=_REMOTE,
              headers={"Host": "bd.local:5555", "Referer": "http://bd.local:5555/cockpit"})
    assert r.status_code != 403, f"same-origin shell request must NOT be 403, got {r.status_code}"


def test_shell_allows_loopback():
    c = _shell_client()
    r = c.get("/cockpit/api/shell/status")  # loopback
    assert r.status_code != 403, f"loopback shell request must NOT be 403, got {r.status_code}"


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
