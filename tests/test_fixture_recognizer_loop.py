#!/usr/bin/env python3
"""test_fixture_recognizer_loop.py -- verify the fixture-site + seam-recognizer
loop works in-sandbox: a synthetic jwplayer embed served locally is loaded
headless and the real player_recognition seam detector fires on it.

This is the regression guard for the bd-fixture-serve + bd-recognizer-drift
capability (headless-browser recognizer testing, no live site). Uses a threaded
server (in-process, no port race) + a direct seam-detector call on the fetched
DOM, so it runs reliably under run_tests.py.

run_tests.py conventions: zero-arg test_* functions, plain asserts, no pytest
builtins, layout-flexible. Needs the staged chromium (skips cleanly if absent).
"""
import importlib.util
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_JWPLAYER_HTML = (
    '<!doctype html><html><head><title>jw</title></head><body>'
    '<div id="player"></div><script>'
    'jwplayer("player").setup({ playlist: [{ sources: ['
    '{ file: "https://cdn.example.invalid/master.m3u8" } ] }] });'
    '</script></body></html>'
)


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = _JWPLAYER_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve_once():
    """Start a threaded one-shot server on an ephemeral port; return (url, stop)."""
    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}/jwplayer", srv.shutdown


def _load_player_recognition():
    p = _REPO_ROOT / "tools" / "player_recognition.py"
    spec = importlib.util.spec_from_file_location("player_recognition", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _have_chromium():
    import glob
    return bool(glob.glob("/home/claude/.cache/ms-playwright/chromium*"))


def test_seam_detector_on_raw_fixture_html():
    """The seam detector fires on the fixture HTML directly (no browser)."""
    pr = _load_player_recognition()
    res = pr.extract_config_seam(_JWPLAYER_HTML)
    assert isinstance(res, dict) and res.get("seam") == "jwplayer_playlist", \
        f"extract_config_seam did not detect jwplayer on fixture HTML: {res}"


def test_headless_fixture_recognizer_loop():
    """Full loop: serve the fixture, load it headless, seam detector fires on the
    browser-rendered DOM. Skips cleanly if chromium isn't staged."""
    if not _have_chromium():
        print("  SKIP: chromium not staged")
        return
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("  SKIP: playwright not importable")
        return
    url, stop = _serve_once()
    try:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                              "/home/claude/.cache/ms-playwright")
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            pg.goto(url, timeout=20000, wait_until="domcontentloaded")
            dom = pg.content()
            b.close()
    finally:
        stop()
    assert ".setup(" in dom or "jwplayer(" in dom, \
        "headless DOM lost the jwplayer seam"
    pr = _load_player_recognition()
    res = pr.extract_config_seam(dom)
    assert isinstance(res, dict) and res.get("seam") == "jwplayer_playlist", \
        f"seam detector did not fire on the headless-rendered DOM: {res}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}"); p += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}"); f += 1
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}"); f += 1
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
