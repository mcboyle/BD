"""dl95-hoopladigital-2 (harness-work/UIUX-20260928/download-95/B9-B/p1/hoopladigital/RESULT.md, LOW).

Measured on test2 (hoopladigital 954f2e41, /movie/the-odyssey-llana-barron/20022407, logged in): the title page's only
action is BORROW ("available for streaming and downloading (mobile devices only)"), and the job failed as
"[page_shape] No download button found", advancing the paused_no_button streak. No media exists until the account
borrows the title, which the app does not do: an operator decision, not a page-shape miss.

Contract: a VISIBLE borrow/hold control on a page with no download candidate holds the job needs_review with a named
reason and resets (does not advance) the no-button streak; prose about borrowing, or a hidden control, is not a gate.

Hermetic: a 127.0.0.1 server with pages shaped like the measured title page, headless Chromium, a recording runner.
"""
from __future__ import annotations

import ast
import http.server
import threading
from pathlib import Path

import pytest
from bulk_downloader import runner, runner_telemetry

BD_GATE_SCOPE = "module"

TITLE = """<header><a href="/browse">Browse</a> <a href="/my/hoopla">My Hoopla</a></header>
<h1>The Odyssey</h1>{action}
<h3>Borrow Details</h3><p>This title is available for 3 days after you borrow it. This title is available for streaming
and downloading (mobile devices only).</p>"""
PAGES = {
    # the measured shape: the label is styled upper-case
    "/movie/the-odyssey/20022407": TITLE.format(
        action='<button style="text-transform:uppercase" class="btn-primary">Borrow</button>'),
    "/movie/hold/1": TITLE.format(action='<a role="button" href="#hold">Place Hold</a>'),
    "/movie/prose-only/2": TITLE.format(action=""),
    "/movie/hidden/3": TITLE.format(action='<button style="display:none">Borrow</button>'),
    "/movie/borrowed/4": TITLE.format(action='<button>Play</button> <button>Return</button>'),
}


@pytest.fixture(scope="module")
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"<!doctype html><html><body>{PAGES.get(self.path, 'nope')}</body></html>".encode()
            self.send_response(200 if self.path in PAGES else 404)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = None
    try:
        b = pw.chromium.launch(headless=True)
        yield b
    finally:
        if b is not None:
            b.close()
        pw.stop()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    try:
        yield ctx.new_page()
    finally:
        ctx.close()


class _Runner:
    site_id = "dl95hd2"

    def __init__(self, streak=4):
        self.config = {"name": "hoopladigital"}
        self._consec_no_btn = streak
        self.updates, self.failures = [], []

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def _handle_failure(self, url, message, screenshot=""):
        self.failures.append(message)


@pytest.fixture
def logged(monkeypatch):
    rows = []
    monkeypatch.setattr(runner, "db_log", lambda *a, **k: rows.append(a))
    return rows


@pytest.mark.parametrize("path,label", [("/movie/the-odyssey/20022407", "BORROW"), ("/movie/hold/1", "Place Hold")])
def test_a_visible_borrow_control_is_named_and_held_for_the_operator(server, page, logged, path, label):
    url = server + path
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner(streak=4)
    assert runner._handle_lending_gate_page(r, page, url, "shot.png") is True
    [(status, msg)] = r.updates
    assert status == "needs_review", status
    assert msg.startswith(f"{runner.LENDING_GATE_MARKER}: the title page offers '{label}'"), msg
    assert "no download button found" not in msg.lower(), f"DL95_HD2_STILL_PAGE_SHAPE: {msg}"
    assert runner_telemetry.TelemetryMixin._classify_error(None, msg) != "page_shape", msg
    assert r._consec_no_btn == 0, "a recognised borrow gate must not walk the site toward paused_no_button"
    assert r.failures == [] and logged and logged[-1][3] == "needs_review", logged


@pytest.mark.parametrize("path", ["/movie/prose-only/2", "/movie/hidden/3", "/movie/borrowed/4"])
def test_controls_prose_hidden_or_borrowed_pages_are_not_a_gate(server, page, logged, path):
    url = server + path
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner(streak=4)
    assert runner._handle_lending_gate_page(r, page, url, "shot.png") is False
    assert r.updates == [] and r._consec_no_btn == 4 and logged == []


def test_an_unreadable_page_is_not_a_gate():
    class Broken:
        def evaluate(self, _js):
            raise RuntimeError("target closed")
    assert runner._lending_gate(Broken()) == ""


def test_the_no_candidate_branch_consults_the_gate_before_the_streak_and_the_page_shape_failure():
    src = Path(runner.__file__).read_text()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_process_one")
    gates = [n for n in ast.walk(fn) if isinstance(n, ast.If) and isinstance(n.test, ast.Call)
             and isinstance(n.test.func, ast.Name) and n.test.func.id == "_handle_lending_gate_page"]
    assert len(gates) == 1, "DL95_HD2_GATE_NOT_WIRED: _process_one must consult _handle_lending_gate_page once"
    g = gates[0]
    assert any(isinstance(s, ast.Return) for s in g.body)
    streak = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Attribute)
              and n.target.attr == "_consec_no_btn"]
    no_button = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value == "No download button found"]
    assert streak and no_button
    assert g.lineno < min(streak) and g.lineno < min(no_button), (g.lineno, streak, no_button)
