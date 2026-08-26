#!/usr/bin/env python3
"""test_fixture_recognizer_loop.py -- verify the fixture-site + seam-recognizer
loop works in-sandbox: a synthetic jwplayer embed served locally is loaded
headless and the real player_recognition seam detector fires on it.

This is the regression guard for the bd-fixture-serve + bd-recognizer-drift
capability (headless-browser recognizer testing, no live site). Uses a threaded
server (in-process, no port race) + a direct seam-detector call on the fetched
DOM, so it runs reliably under run_tests.py.

run_tests.py conventions: zero-arg test_* functions, plain asserts, no pytest
builtins, layout-flexible. A missing Playwright runtime or browser is UNKNOWN.
"""
import importlib.util
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


def _chromium_executable(browser_type):
    """Resolve the executable from Playwright's configured/default pool."""
    executable = Path(browser_type.executable_path)
    assert executable.is_file(), (
        "UNKNOWN: Playwright Chromium is unavailable at its resolved path "
        f"{executable}"
    )
    return executable


def test_seam_detector_on_raw_fixture_html():
    """The seam detector fires on the fixture HTML directly (no browser)."""
    pr = _load_player_recognition()
    res = pr.extract_config_seam(_JWPLAYER_HTML)
    assert isinstance(res, dict) and res.get("seam") == "jwplayer_playlist", \
        f"extract_config_seam did not detect jwplayer on fixture HTML: {res}"


def test_headless_fixture_recognizer_loop():
    """Full loop: serve the fixture, load it headless, seam detector fires on the
    browser-rendered DOM. Missing browser capability is a loud UNKNOWN."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise AssertionError(
            f"UNKNOWN: Playwright is not importable, so no DOM was rendered: {exc}"
        ) from exc
    url, stop = _serve_once()
    try:
        try:
            with sync_playwright() as p:
                executable = _chromium_executable(p.chromium)
                assert executable.is_file()
                b = p.chromium.launch(headless=True)
                try:
                    pg = b.new_page()
                    pg.goto(url, timeout=20000, wait_until="domcontentloaded")
                    dom = pg.content()
                finally:
                    b.close()
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(
                "UNKNOWN: Playwright Chromium could not render the fixture: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    finally:
        stop()
    assert ".setup(" in dom or "jwplayer(" in dom, \
        "headless DOM lost the jwplayer seam"
    pr = _load_player_recognition()
    res = pr.extract_config_seam(dom)
    assert isinstance(res, dict) and res.get("seam") == "jwplayer_playlist", \
        f"seam detector did not fire on the headless-rendered DOM: {res}"


def test_chromium_resolution_distinguishes_present_from_missing():
    """The capability resolver reaches both its healthy and UNKNOWN states."""
    import tempfile
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory(prefix="bd_chromium_resolver_") as raw:
        present = Path(raw) / "chrome"
        present.write_bytes(b"fixture executable identity\n")
        assert _chromium_executable(
            SimpleNamespace(executable_path=str(present))
        ) == present

        missing = Path(raw) / "missing-chrome"
        raised = None
        try:
            _chromium_executable(SimpleNamespace(executable_path=str(missing)))
        except AssertionError as exc:
            raised = str(exc)
        assert raised is not None and "UNKNOWN" in raised, (
            "a missing browser executable was reported as healthy"
        )


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
