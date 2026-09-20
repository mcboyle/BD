"""Row 904 -- ASYNCHRONOUS-PROTOCOL-MESSAGE-EVENT-DISPATCHING.

Modern sites deliver media authorization/completion signals over a
persistent WebSocket rather than plain XHR. ``session_capture.py`` already
listens for ``Network.webSocketFrameReceived`` (CDP) for its raw capture
log, but that log is metadata for later inspection, not something a live
runner can await. ``BrowserMixin._watch_websocket_json`` (row904, in
``runner_browser.py``) attaches a second, independent CDP listener on the
same already-enabled ``Network`` domain and routes every JSON-parseable
frame into a plain ``queue.Queue`` (a ``WebSocketFrameDispatcher``), so a
runner can poll for an async state update / media token without blocking
on Playwright's own event loop.

Following the project's own convention for this exact CDP seam
(``tests/test_v3_66_50_at1_session_capture.py::TestCdpLiveWiring``, whose
docstring says live CDP wiring is driven by a fake CDP session double, not a
real browser -- ``capture_via_cdp`` is "exercised against a real browser in
the live-tests framework, not the unit suite"), this file uses the same
``_FakeCDPClient``/``_FakePage`` shape to stand in for the mock WebSocket
endpoint's CDP delivery.

Acceptance (verbatim from the register row):
  1. capture of JSON messages over mock WebSocket endpoint;
  2. event routing to queue dispatcher;
  3. clean socket teardown.
"""
from __future__ import annotations

import queue

import pytest

from bulk_downloader.runner_browser import BrowserMixin, WebSocketFrameDispatcher

BD_GATE_SCOPE = "module"


class _FakeCDPClient:
    """Stand-in for a Playwright CDP session (same shape as the existing
    ``_FakeCDPClient`` in test_v3_66_50_at1_session_capture.py, plus
    ``detach()`` for the teardown assertion this row adds)."""

    def __init__(self):
        self.handlers = {}
        self.sent = []
        self.detached = 0

    def send(self, method, *a, **k):
        self.sent.append(method)

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, params):
        for h in self.handlers.get(event, []):
            h(params)

    def detach(self):
        self.detached += 1


class _FakePage:
    def __init__(self, client):
        self.context = type("Ctx", (), {
            "new_cdp_session": staticmethod(lambda page: client)})()


class _Runner(BrowserMixin):
    pass


def _frame(payload: str) -> dict:
    return {"requestId": "1", "response": {"opcode": 1, "payloadData": payload}}


def test_json_message_over_mock_websocket_endpoint_is_captured():
    """Acceptance 1: a JSON frame delivered over the mock CDP endpoint parses."""
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))
    assert "Network.enable" in client.sent
    assert _CDP_EVENT_NAME() in client.handlers

    client.emit(_CDP_EVENT_NAME(), _frame('{"type": "media_token", "token": "abc123"}'))

    message = dispatcher.queue.get_nowait()
    assert message == {"type": "media_token", "token": "abc123"}


def test_event_routes_to_the_queue_dispatcher_in_order():
    """Acceptance 2: multiple frames land on the dispatcher's queue, in order."""
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))

    client.emit(_CDP_EVENT_NAME(), _frame('{"seq": 1}'))
    client.emit(_CDP_EVENT_NAME(), _frame('{"seq": 2}'))

    assert dispatcher.queue.get_nowait() == {"seq": 1}
    assert dispatcher.queue.get_nowait() == {"seq": 2}
    with pytest.raises(queue.Empty):
        dispatcher.queue.get_nowait()


def test_non_json_frame_is_dropped_not_queued():
    """A negative control: text frames that are not JSON must not silently
    become a queue item a consumer would mis-parse."""
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))

    client.emit(_CDP_EVENT_NAME(), _frame("ping"))

    with pytest.raises(queue.Empty):
        dispatcher.queue.get_nowait()


def test_close_detaches_the_cdp_session_exactly_once():
    """Acceptance 3 (part 1): clean teardown detaches the CDP client."""
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))

    dispatcher.close()
    assert client.detached == 1

    dispatcher.close()  # idempotent: a second close() must not detach again
    assert client.detached == 1


def test_close_stops_routing_a_frame_that_arrives_after_teardown():
    """Acceptance 3 (part 2): a frame racing the teardown is dropped, not
    queued into an already-abandoned dispatcher."""
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))
    dispatcher.close()

    client.emit(_CDP_EVENT_NAME(), _frame('{"late": true}'))

    with pytest.raises(queue.Empty):
        dispatcher.queue.get_nowait()


def _CDP_EVENT_NAME() -> str:
    return "Network.webSocketFrameReceived"


def test_frame_with_no_payload_data_is_dropped_not_queued():
    """Another negative control: a binary/opcode-only frame (no payloadData
    key at all, e.g. a ping/pong control frame) must not reach the queue."""
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))

    client.emit(_CDP_EVENT_NAME(), {"requestId": "1", "response": {"opcode": 9}})

    with pytest.raises(queue.Empty):
        dispatcher.queue.get_nowait()


def test_close_swallows_a_client_that_raises_on_detach():
    """A CDP session already gone (page/context closed first) must not make
    teardown itself raise."""
    client = _FakeCDPClient()

    def _boom():
        raise RuntimeError("session already closed")

    client.detach = _boom
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))

    dispatcher.close()  # must not raise


def test_close_on_a_dispatcher_with_no_client_attached_is_a_no_op():
    dispatcher = WebSocketFrameDispatcher()
    dispatcher.close()  # must not raise even though _client was never set
    assert dispatcher._closed is True


def test_dispatcher_is_the_documented_class():
    assert isinstance(_Runner()._watch_websocket_json(_FakePage(_FakeCDPClient())),
                       WebSocketFrameDispatcher)


# ---- fixer (O928): correctness REFUTE E1-E4 ----------------------------------

import ast
import inspect
import textwrap
import threading


def test_e2_binary_and_control_frames_and_bare_scalars_are_dropped():
    """E2: only a TEXT frame (opcode 1) carrying a JSON object/array is a
    message; binary (opcode 2, base64 payload) and control frames (8/9/10)
    and bare JSON scalars are dropped."""
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))
    ev = _CDP_EVENT_NAME()
    for opcode, payload in ((2, "1234"), (2, "eyJhIjoxfQ=="), (9, "true"), (10, '{"pong": 1}'), (8, "1000")):
        client.emit(ev, {"requestId": "1", "response": {"opcode": opcode, "payloadData": payload}})
    for scalar in ("1234", "true", '"text"', "null"):
        client.emit(ev, _frame(scalar))
    with pytest.raises(queue.Empty):
        dispatcher.queue.get_nowait()
    client.emit(ev, _frame('{"kind": "ok"}'))            # positive control
    client.emit(ev, _frame('[1, 2]'))
    assert dispatcher.drain() == [{"kind": "ok"}, [1, 2]]
    dispatcher.close()


def test_e3_a_parse_in_flight_when_close_runs_never_enqueues_after_close(monkeypatch):
    """E3: pause a frame after its closed-check, close/detach, release it:
    the queue stays empty (the closed-check and enqueue are one locked step)."""
    from bulk_downloader import runner_browser
    client = _FakeCDPClient()
    dispatcher = _Runner()._watch_websocket_json(_FakePage(client))
    parsed, release = threading.Event(), threading.Event()
    real_loads = runner_browser.json.loads

    def slow_loads(payload):
        message = real_loads(payload)
        parsed.set()
        assert release.wait(5)
        return message
    monkeypatch.setattr(runner_browser.json, "loads", slow_loads)
    t = threading.Thread(target=lambda: client.emit(_CDP_EVENT_NAME(), _frame('{"late": true}')))
    t.start()
    assert parsed.wait(5)
    dispatcher.close()
    assert client.detached == 1
    release.set()
    t.join(5)
    assert not t.is_alive()
    assert dispatcher.queue.qsize() == 0


def test_e4_a_session_that_fails_to_wire_is_detached_not_leaked():
    """E4: Network.enable (or listener registration) raising after the CDP
    session exists must detach that session; the error propagates."""
    class Failing(_FakeCDPClient):
        def send(self, method, *a, **k):
            raise RuntimeError("Network.enable refused")
    client = Failing()
    with pytest.raises(RuntimeError, match="Network.enable refused"):
        _Runner()._watch_websocket_json(_FakePage(client))
    assert client.detached == 1

    class FailingOn(_FakeCDPClient):
        def on(self, event, handler):
            raise RuntimeError("no listeners")
    client = FailingOn()
    with pytest.raises(RuntimeError, match="no listeners"):
        _Runner()._watch_websocket_json(_FakePage(client))
    assert client.sent == ["Network.enable"] and client.detached == 1
    # positive control: a healthy client is wired and not detached
    client = _FakeCDPClient()
    _Runner()._watch_websocket_json(_FakePage(client))
    assert client.detached == 0 and _CDP_EVENT_NAME() in client.handlers


def test_e1_media_urls_are_extracted_from_captured_messages_and_consumed():
    """E1: JSON event parsing AND URL extraction: the messages an app sends
    over its socket yield discovered targets through the consumer."""
    from bulk_downloader.runner_browser import extract_media_urls
    msg = {"type": "media.ready", "data": {"url": "https://cdn.example/v/1.m3u8", "poster": "https://cdn.example/p.jpg",
                                          "token": "abc", "alts": [{"src": "https://cdn.example/v/1.mp4"}, "ws://x"]}}
    assert extract_media_urls(msg) == ["https://cdn.example/v/1.m3u8", "https://cdn.example/v/1.mp4", "https://cdn.example/p.jpg"]
    assert extract_media_urls({"a": 1, "b": ["nope", None]}) == []
    runner = _Runner()
    client = _FakeCDPClient()
    ctx = type("Ctx", (), {"pages": [_FakePage(client)], "on": lambda self, e, h: None})()
    ctx.pages[0].context = ctx
    ctx.new_cdp_session = lambda page: client
    assert runner._maybe_install_websocket_json_capture(ctx) is not None
    client.emit(_CDP_EVENT_NAME(), _frame('{"data": {"url": "https://cdn.example/v/2.m3u8"}}'))
    client.emit(_CDP_EVENT_NAME(), _frame('{"data": {"url": "https://cdn.example/v/2.m3u8"}}'))   # duplicate
    assert runner.drain_websocket_media_urls() == ["https://cdn.example/v/2.m3u8"]
    assert runner.drain_websocket_media_urls() == []
    runner._close_websocket_capture()
    assert client.detached == 1
    off = type("R", (BrowserMixin,), {"config": {"websocket_capture": False}})()
    assert off._maybe_install_websocket_json_capture(ctx) is None


def test_rb9_dispatcher_is_bounded_and_a_media_announcement_survives_chatty_traffic():
    """rb9 correctness lens (measured on real Chromium): the dispatcher is
    installed on every page of a persistent context and nothing in the runner
    lifecycle must drain it, so 200k presence-style frames grew the process by
    ~106 MB. Bounded ring: oldest messages evicted and counted; the media URL a
    message carried is lifted at arrival so later chatter cannot evict it."""
    from bulk_downloader import runner_browser
    dispatcher = WebSocketFrameDispatcher(max_messages=3)   # the bound, small enough to trace
    dispatcher._on_frame(_frame('{"type": "media.ready", "url": "https://cdn.example/v/9.m3u8"}'))
    for seq in range(5):
        dispatcher._on_frame(_frame('{"type": "presence", "seq": %d}' % seq))

    assert dispatcher.queue.qsize() == 3
    assert dispatcher.dropped == 3
    assert dispatcher.drain() == [{"type": "presence", "seq": 2}, {"type": "presence", "seq": 3},
                                  {"type": "presence", "seq": 4}]
    # the announcement's message was evicted, the URL was not
    assert dispatcher.media_urls() == ["https://cdn.example/v/9.m3u8"]
    assert dispatcher.media_urls() == []
    # the URL store is bounded too (oldest distinct URL evicted, counted)
    small = WebSocketFrameDispatcher(max_messages=10, max_urls=2)
    for i in range(3):
        small._on_frame(_frame('{"url": "https://cdn.example/u/%d.mp4"}' % i))
    assert small.dropped == 1
    assert small.media_urls() == ["https://cdn.example/u/1.mp4", "https://cdn.example/u/2.mp4"]
    # negative control: below the bound nothing is dropped
    assert WebSocketFrameDispatcher(max_messages=10).dropped == 0
    assert runner_browser._WS_MAX_QUEUED_MESSAGES == 1000   # the production bound
    dispatcher.close()


def test_e1_the_persistent_launch_path_installs_the_capture():
    from bulk_downloader import runner_browser
    tree = ast.parse(textwrap.dedent(inspect.getsource(runner_browser.BrowserMixin._launch_browser)))
    persistent_returns = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)
                                 and isinstance(n.value.elts[0], ast.Constant) and n.value.elts[0].value is None),
                                key=lambda n: n.lineno)
    calls = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_maybe_install_websocket_json_capture"), key=lambda n: n.lineno)
    assert len(persistent_returns) == 2 == len(calls)
    assert all(c.lineno < r.lineno for c, r in zip(calls, persistent_returns))


class _MockWebSocketEndpoint:
    """A minimal RFC 6455 server on the standard library (no third-party
    websockets package): one client handshake, then the scripted frames."""

    def __init__(self, frames):
        import socket
        self.frames = frames
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0)); self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @staticmethod
    def _frame(opcode, payload):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([n])
        elif n < 65536:
            header += bytes([126]) + n.to_bytes(2, "big")
        else:
            header += bytes([127]) + n.to_bytes(8, "big")
        return header + payload

    def _serve(self):
        import base64, hashlib
        conn, _ = self.sock.accept()
        with conn:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            key = next(l.split(b":", 1)[1].strip() for l in request.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key"))
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            for opcode, payload in self.frames:
                conn.sendall(self._frame(opcode, payload))
            self.closed.wait(10)

    def close(self):
        self.closed.set()
        try:
            self.sock.close()
        except OSError:
            pass


def test_e1_real_chromium_json_over_a_mock_websocket_endpoint_reaches_the_consumer():
    """Acceptance 1-3 on real Chromium: a page opens a WebSocket to a loopback
    endpoint that sends one JSON media message, one binary frame and one
    non-JSON text frame; the consumer receives the media URL only, and
    teardown detaches cleanly. (FLEET_RULE 46: no skip -- the endpoint is
    stdlib.)"""
    import json as _json
    from playwright.sync_api import sync_playwright
    endpoint = _MockWebSocketEndpoint([
        (1, _json.dumps({"type": "media.ready", "data": {"url": "https://cdn.example/live/1.m3u8", "token": "t"}}).encode()),
        (2, b"\x00\x01binary"),
        (1, b"not json"),
    ])
    page_html = ("<html><body><script>window.__got = 0; const ws = new WebSocket('ws://127.0.0.1:%d/');"
                 "ws.onmessage = () => { window.__got += 1; };</script></body></html>" % endpoint.port).encode()
    # the page is served from loopback too: Chromium's local-network-access
    # check blocks a public-origin page from opening a loopback socket
    import http.server

    class Player(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(page_html))); self.end_headers(); self.wfile.write(page_html)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), Player)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    runner = type("R", (BrowserMixin,), {"config": {}, "site_id": "t"})()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            assert runner._maybe_install_websocket_json_capture(ctx)
            page.goto(f"http://127.0.0.1:{srv.server_port}/player")
            page.wait_for_function("window.__got >= 3", timeout=10000)
            page.wait_for_timeout(200)
            urls = runner.drain_websocket_media_urls()
            dispatchers = list(runner._websocket_dispatchers)
            runner._close_websocket_capture()
            assert dispatchers and all(d._closed for d in dispatchers) and runner._websocket_dispatchers == []
            browser.close()
    finally:
        endpoint.close()
        srv.shutdown(); srv.server_close()
    assert urls == ["https://cdn.example/live/1.m3u8"]
