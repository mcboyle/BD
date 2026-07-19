"""v3.66.794 -- F0.4 waitress swap.

The werkzeug dev server (`app.run`) is single-process and crashes under
concurrent load (Task-E fuzz recon: ffuf `Errors: 64`, nuclei hangs it). In
production (debug off) the app should serve under waitress -- a real threaded
WSGI server -- while debug mode keeps werkzeug for its interactive reloader and
traceback page.

The launch used to be an inline `app.run(...)` in the `__main__` block, which is
unreachable to a unit test (module-level + blocking). This suite pins the pure
*selection* logic of the extracted `_serve_wsgi` helper: which server is chosen,
with which arguments, and that a missing waitress falls back to werkzeug rather
than failing to boot. The serve callables block forever, so they are injected --
the helper returns the NAME of the server it used so the choice is assertable
without ever binding a port.

RED before the cut: `downloader_ui._serve_wsgi` does not exist.
"""
import sys

import pytest

import downloader_ui

SENTINEL_APP = object()

# PEP 3333: a WSGI application MUST NOT set hop-by-hop headers -- they are the
# server's responsibility. waitress ENFORCES this (start_response raises
# AssertionError -> 500); werkzeug's dev server silently tolerated them, which
# is how `Connection: keep-alive` on the SSE responses shipped unnoticed.
_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade",
               "te", "trailer", "proxy-authenticate", "proxy-authorization"}


def test_dashboard_sse_has_no_hop_by_hop_headers():
    """The real waitress reproduction. Under waitress `/api/stream` returned 500
    (`Connection is a "hop-by-hop" header`) -- SSE, and thus the whole live
    dashboard, was broken. The Response must not carry any hop-by-hop header.
    The generator is lazy, so building the Response sets headers without
    consuming the (infinite) body."""
    from bulk_downloader.app import app
    from bulk_downloader.app_stream import api_stream
    with app.test_request_context("/api/stream"):
        resp = api_stream()
        bad = [h for h in resp.headers.keys() if h.lower() in _HOP_BY_HOP]
    assert not bad, "/api/stream sets hop-by-hop header(s): %s" % bad


def test_no_sse_endpoint_sets_hop_by_hop_headers():
    """Regression net across EVERY SSE endpoint -- including the auth/session-
    gated captcha-relay stream that cannot be exercised without a live takeover.
    No module emitting text/event-stream may set a hop-by-hop header literal on
    its Response. Denominator = all such modules, so a future SSE endpoint that
    reintroduces the bug fails here, not on stash."""
    import glob
    import os
    import re
    root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bulk_downloader")
    hop_re = re.compile(
        r'headers\[\s*["\'](connection|keep-alive|transfer-encoding|upgrade|'
        r'te|trailer|proxy-authenticate|proxy-authorization)["\']\s*\]\s*=',
        re.I)
    offenders = []
    for p in sorted(glob.glob(os.path.join(root, "*.py"))):
        body = open(p, encoding="utf-8", errors="replace").read()
        if "text/event-stream" not in body:
            continue
        for m in hop_re.finditer(body):
            offenders.append("%s: %s" % (os.path.basename(p), m.group(1)))
    assert not offenders, "SSE endpoints set hop-by-hop headers: %s" % offenders


def test_production_prefers_waitress():
    """debug off + waitress available -> waitress, with app/host/port/threads;
    werkzeug never touched."""
    calls = []
    name = downloader_ui._serve_wsgi(
        SENTINEL_APP, "0.0.0.0", 5555, debug=False,
        waitress_serve=lambda app, **kw: calls.append(("waitress", app, kw)),
        werkzeug_run=lambda **kw: calls.append(("werkzeug", kw)),
    )
    assert name == "waitress"
    assert len(calls) == 1, "exactly one server should be started"
    tag, app, kw = calls[0]
    assert tag == "waitress"
    assert app is SENTINEL_APP
    assert kw["host"] == "0.0.0.0"
    assert kw["port"] == 5555
    assert kw["threads"] == 30         # BD_WSGI_THREADS default
    # the _quiet=True in the @783 design proposal is REJECTED by waitress
    # 3.0.2 (ValueError: Unknown adjustment) -- it must not be passed.
    assert "_quiet" not in kw


def test_debug_keeps_werkzeug():
    """debug on -> werkzeug (reloader/traceback), even when waitress is present;
    use_reloader stays False (the app manages its own restart)."""
    calls = {}
    name = downloader_ui._serve_wsgi(
        SENTINEL_APP, "127.0.0.1", 5555, debug=True,
        waitress_serve=lambda *a, **k: calls.setdefault("waitress", True),
        werkzeug_run=lambda **kw: calls.setdefault("werkzeug", kw),
    )
    assert name == "werkzeug"
    assert "waitress" not in calls, "debug must not use waitress"
    assert calls["werkzeug"]["use_reloader"] is False
    assert calls["werkzeug"]["debug"] is True


def test_missing_waitress_falls_back_to_werkzeug(monkeypatch):
    """debug off but waitress unimportable -> werkzeug fallback, never a crash.
    Forcing ImportError on the real `from waitress import serve` proves the boot
    survives a missing dependency (the whole point of the soft import)."""
    monkeypatch.setitem(sys.modules, "waitress", None)  # -> ImportError
    calls = []
    name = downloader_ui._serve_wsgi(
        SENTINEL_APP, "127.0.0.1", 5555, debug=False,
        werkzeug_run=lambda **kw: calls.append(kw),
    )
    assert name == "werkzeug"
    assert calls and calls[0]["use_reloader"] is False


def test_thread_count_from_env(monkeypatch):
    """BD_WSGI_THREADS sizes the waitress pool. Sizing matters because each
    long-lived /api/stream SSE connection pins one worker thread for its whole
    life -- too few threads and concurrent dashboards starve normal requests."""
    monkeypatch.setenv("BD_WSGI_THREADS", "16")
    seen = {}
    name = downloader_ui._serve_wsgi(
        SENTINEL_APP, "127.0.0.1", 5555, debug=False,
        waitress_serve=lambda app, **kw: seen.update(kw),
    )
    assert name == "waitress"
    assert seen["threads"] == 16


def test_bad_thread_env_defaults_to_30(monkeypatch):
    """A non-integer BD_WSGI_THREADS must not crash boot -- fall back to 30."""
    monkeypatch.setenv("BD_WSGI_THREADS", "not-a-number")
    seen = {}
    downloader_ui._serve_wsgi(
        SENTINEL_APP, "127.0.0.1", 5555, debug=False,
        waitress_serve=lambda app, **kw: seen.update(kw),
    )
    assert seen["threads"] == 30
