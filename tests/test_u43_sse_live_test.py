"""U43 — L35 (sse-live-update), a single live test, the last unit in
Tier 2.

L35 connects to /api/stream, confirms SSE events arrive, drops the
connection, and confirms a reconnect re-establishes the channel. It
needs the deployment to truly run; here it is exercised for real
against a minimal local SSE server, so the PASS path — not just
graceful degradation — is verified.
"""
import http.server
import threading
import time

import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h


_LEVELS = (h.PASS, h.WARN, h.FAIL)


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── a minimal local SSE server, for the PASS-path test ────────────

def _make_sse_server(body=b"event: dashboard\ndata: {\"x\":1}\n\n"
                            b"event: status\ndata: {\"y\":2}\n\n",
                     content_type="text/event-stream"):
    """Start a throwaway HTTP server that answers any GET with `body`
    under `content_type`. Returns (base_url, shutdown_callable)."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
                time.sleep(1.5)
            except Exception:
                pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.15)
    return f"http://127.0.0.1:{port}", srv.shutdown


# ── registration ───────────────────────────────────────────────────

def test_l35_is_registered():
    assert _get_test("L35") is not None


def test_l35_is_not_disruptive():
    assert _get_test("L35").disruptive is False


def test_tier2_live_suite_is_complete():
    # U43 is the last Tier 2 unit — the live suite should now carry
    # the full set of Tier 0-2 checks
    ids = {t.id for t in h.registry()}
    assert "L35" in ids
    assert len(ids) >= 28


# ── graceful degradation ───────────────────────────────────────────

def test_l35_unreachable_is_warn():
    ctx = h.Context("http://localhost:1", "/tmp")
    level, detail = _get_test("L35").fn(ctx)
    assert level == h.WARN
    assert "could not open" in detail or "unreachable" in detail


def test_l35_returns_a_valid_tuple():
    ctx = h.Context("http://localhost:1", "/tmp")
    res = _get_test("L35").fn(ctx)
    assert isinstance(res, tuple) and len(res) == 2
    assert res[0] in _LEVELS


# ── the _read_sse_events helper ────────────────────────────────────

def test_read_sse_events_parses_event_blocks():
    base, stop = _make_sse_server()
    try:
        events, err = checks._read_sse_events(base, max_events=2,
                                              budget_s=8)
    finally:
        stop()
    assert err is None
    assert len(events) == 2
    assert events[0].startswith("event: dashboard")
    assert events[1].startswith("event: status")


def test_read_sse_events_rejects_a_non_sse_endpoint():
    # a plain JSON endpoint must be reported, not parsed as a stream
    base, stop = _make_sse_server(body=b'{"ok":true}',
                                  content_type="application/json")
    try:
        events, err = checks._read_sse_events(base, budget_s=6)
    finally:
        stop()
    assert events == []
    assert err is not None
    assert "event-stream" in err or "SSE" in err


def test_read_sse_events_handles_an_unreachable_host():
    events, err = checks._read_sse_events("http://localhost:1",
                                          budget_s=3)
    assert events == []
    assert err is not None


# ── L35 PASS path against a real SSE stream ────────────────────────

def test_l35_passes_against_a_real_sse_stream():
    base, stop = _make_sse_server()
    try:
        ctx = h.Context(base, "/tmp")
        level, detail = _get_test("L35").fn(ctx)
    finally:
        stop()
    # events arrive on connect AND on reconnect -> PASS
    assert level == h.PASS
    assert "reconnect" in detail


# ── harness integration ────────────────────────────────────────────

def test_l35_runs_via_harness(tmp_path):
    rdir = tmp_path / "results"
    # against a dead target L35 WARNs -> exit 0
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L35"], results_dir=rdir)
    assert code == 0
    assert (rdir / "L35.log").is_file()
