"""Row 974: Asynchronous Gateway Migration & Starlette/FastAPI Modern ASGI Cutover.

Provides ASGI 3.0 gateway adapter, modern ASGI cutover interface, lifespan
protocol support, and server selection for asynchronous deployment.

RED on baseline: bulk_downloader.asgi_gateway and downloader_ui._serve_asgi do not exist.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import pathlib
import sys
from typing import Any, Dict, List

BD_GATE_SCOPE = "module"

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import downloader_ui  # noqa: E402

try:
    asgi_gateway = importlib.import_module("bulk_downloader.asgi_gateway")
    ASGIGateway = getattr(asgi_gateway, "ASGIGateway", None)
    create_modern_asgi_app = getattr(asgi_gateway, "create_modern_asgi_app", None)
    get_asgi_app = getattr(asgi_gateway, "get_asgi_app", None)
    get_gateway_info = getattr(asgi_gateway, "get_gateway_info", None)
    serve_asgi = getattr(asgi_gateway, "serve_asgi", None)
except ImportError:
    asgi_gateway = None
    ASGIGateway = None
    create_modern_asgi_app = None
    get_asgi_app = None
    get_gateway_info = None
    serve_asgi = None


def test_asgi_gateway_module_implemented():
    """Verify ASGI gateway module is present and exposes canonical interfaces."""
    assert asgi_gateway is not None, (
        "bulk_downloader.asgi_gateway module not implemented; "
        "ASGI 3.0 gateway adapter required"
    )
    for attr in ("ASGIGateway", "create_modern_asgi_app", "get_asgi_app", "get_gateway_info", "serve_asgi"):
        assert hasattr(asgi_gateway, attr), f"asgi_gateway missing expected attribute: {attr}"


def test_asgi_gateway_info():
    """Verify ASGI gateway metadata and version specification."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    info = get_gateway_info()
    assert isinstance(info, dict)
    assert info.get("asgi_version") == "3.0"
    assert "starlette_available" in info
    assert "fastapi_available" in info
    assert "uvicorn_available" in info


def test_asgi_gateway_creation():
    """Verify ASGIGateway wraps WSGI/Flask app correctly."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    from bulk_downloader.app import app as flask_app

    gateway = ASGIGateway(flask_app)
    assert callable(gateway)
    assert gateway.app is flask_app

    wrapped = get_asgi_app(flask_app)
    assert isinstance(wrapped, ASGIGateway)


def test_asgi_lifespan_protocol():
    """Exercise ASGI 3.0 lifespan events (startup & shutdown)."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    from bulk_downloader.app import app as flask_app

    gateway = ASGIGateway(flask_app)
    events_received: List[Dict[str, Any]] = []

    async def run_lifespan():
        messages = [
            {"type": "lifespan.startup"},
            {"type": "lifespan.shutdown"},
        ]

        async def receive():
            if messages:
                return messages.pop(0)
            return {"type": "lifespan.unknown"}

        async def send(message):
            events_received.append(message)

        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
        await gateway(scope, receive, send)

    asyncio.run(run_lifespan())
    types = [e["type"] for e in events_received]
    assert "lifespan.startup.complete" in types
    assert "lifespan.shutdown.complete" in types


def test_asgi_http_request():
    """Send an ASGI HTTP request scope through the gateway and verify response."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    from bulk_downloader.app import app as flask_app

    gateway = ASGIGateway(flask_app)
    responses: List[Dict[str, Any]] = []

    async def run_http():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            responses.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/manifest.json",
            "raw_path": b"/manifest.json",
            "query_string": b"",
            "headers": [(b"host", b"localhost:5555")],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 5555),
        }
        await gateway(scope, receive, send)

    asyncio.run(run_http())
    start = next((m for m in responses if m["type"] == "http.response.start"), None)
    assert start is not None, "Expected http.response.start"
    assert start["status"] == 200

    body_parts = [m.get("body", b"") for m in responses if m["type"] == "http.response.body"]
    full_body = b"".join(body_parts)
    payload = json.loads(full_body.decode("utf-8"))
    assert "name" in payload or "short_name" in payload


def test_serve_asgi_with_injected_uvicorn():
    """Verify ASGI runner selects uvicorn when provided."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    calls = []
    fake_asgi_app = object()

    name = serve_asgi(
        fake_asgi_app,
        "0.0.0.0",
        5555,
        debug=False,
        uvicorn_run=lambda app, **kw: calls.append(("uvicorn", app, kw)),
    )
    assert name == "uvicorn"
    assert len(calls) == 1
    tag, app, kw = calls[0]
    assert tag == "uvicorn"
    assert app is fake_asgi_app
    assert kw["host"] == "0.0.0.0"
    assert kw["port"] == 5555


def test_serve_asgi_fallback_when_server_missing():
    """When no ASGI server is available, serve_asgi falls back gracefully."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    calls = []
    fake_app = object()

    name = serve_asgi(
        fake_app,
        "127.0.0.1",
        5555,
        debug=False,
        uvicorn_run=None,
        hypercorn_run=None,
        fallback_wsgi=lambda app, host, port, debug: calls.append((app, host, port, debug)),
    )
    assert name == "fallback_wsgi"
    assert len(calls) == 1
    assert calls[0] == (fake_app, "127.0.0.1", 5555, False)


def test_downloader_ui_has_serve_asgi():
    """Verify downloader_ui exposes _serve_asgi."""
    assert hasattr(downloader_ui, "_serve_asgi"), (
        "downloader_ui missing _serve_asgi cutover function"
    )
    calls = []
    downloader_ui._serve_asgi(
        object(),
        "127.0.0.1",
        5555,
        debug=False,
        uvicorn_run=lambda app, **kw: calls.append(kw),
    )
    assert len(calls) == 1


def test_create_modern_asgi_app():
    """Verify create_modern_asgi_app creates a valid ASGI callable."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    from bulk_downloader.app import app as flask_app

    asgi_app = create_modern_asgi_app(flask_app)
    assert callable(asgi_app)


def test_serve_asgi_fallback_unwraps_app_for_wsgi():
    """R1: Verify fallback_wsgi receives an unwrapped WSGI callable, not ASGIGateway.

    If ASGIGateway is passed to fallback_wsgi, calling it with (environ, start_response)
    fails with: TypeError: ASGIGateway.__call__() missing 1 required positional argument: 'send'
    """
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    calls = []

    def mock_wsgi_app(environ, start_response):
        start_response("200 OK", [("content-type", "text/plain")])
        return [b"ok"]

    def fake_fallback(app, host, port, debug):
        resp = app({"REQUEST_METHOD": "GET"}, lambda s, h: None)
        calls.append((app, list(resp)))

    downloader_ui._serve_asgi(
        mock_wsgi_app,
        "127.0.0.1",
        5555,
        debug=False,
        uvicorn_run=None,
        hypercorn_run=None,
        fallback_wsgi=fake_fallback,
    )
    assert len(calls) == 1
    passed_app, body = calls[0]
    assert body == [b"ok"], "WSGI app must execute and return response body"


def test_asgi_path_info_preserves_encoded_segments():
    """R2: Verify PATH_INFO is not double-decoded.

    scope['path'] is already percent-decoded per ASGI spec.
    Secondary unquote() corrupts paths like '/files/a%20b' into '/files/a b'
    and unescapes encoded dot-segments like '/files/%2e%2e/x' into '/files/../x'.
    """
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    recorded_env: Dict[str, Any] = {}

    def capture_app(environ, start_response):
        recorded_env.update(environ)
        start_response("200 OK", [])
        return [b""]

    gateway = ASGIGateway(capture_app)

    async def send_path(path_val: str):
        recorded_env.clear()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "GET",
            "path": path_val,
            "raw_path": path_val.encode("ascii"),
            "query_string": b"",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            pass

        await gateway(scope, receive, send)

    asyncio.run(send_path("/files/a%20b"))
    assert recorded_env.get("PATH_INFO") == "/files/a%20b", (
        f"PATH_INFO must not double-decode %20; got {recorded_env.get('PATH_INFO')!r}"
    )

    asyncio.run(send_path("/files/%2e%2e/x"))
    assert recorded_env.get("PATH_INFO") == "/files/%2e%2e/x", (
        f"PATH_INFO must not double-decode %2e%2e; got {recorded_env.get('PATH_INFO')!r}"
    )

    asyncio.run(send_path("/files/100%.txt"))
    assert recorded_env.get("PATH_INFO") == "/files/100%.txt", "Positive control path preserved"


def test_asgi_repeated_headers_joined():
    """R3: Verify repeated request headers are joined per PEP 3333 / RFC 6265.

    Multiple headers must be joined with ', ', except Cookie which joins with '; '.
    """
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    recorded_env: Dict[str, Any] = {}

    def capture_app(environ, start_response):
        recorded_env.update(environ)
        start_response("200 OK", [])
        return [b""]

    gateway = ASGIGateway(capture_app)

    async def run():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "GET",
            "path": "/",
            "headers": [
                (b"x-forwarded-for", b"1.1.1.1"),
                (b"x-forwarded-for", b"2.2.2.2"),
                (b"cookie", b"a=1"),
                (b"cookie", b"b=2"),
                (b"accept", b"text/html"),
                (b"accept", b"application/xhtml+xml"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            pass

        await gateway(scope, receive, send)

    asyncio.run(run())
    assert recorded_env.get("HTTP_X_FORWARDED_FOR") == "1.1.1.1, 2.2.2.2", (
        f"Repeated x-forwarded-for must join with comma, got: {recorded_env.get('HTTP_X_FORWARDED_FOR')!r}"
    )
    assert recorded_env.get("HTTP_COOKIE") == "a=1; b=2", (
        f"Repeated cookie must join with semicolon, got: {recorded_env.get('HTTP_COOKIE')!r}"
    )
    assert recorded_env.get("HTTP_ACCEPT") == "text/html, application/xhtml+xml", (
        f"Repeated accept must join with comma, got: {recorded_env.get('HTTP_ACCEPT')!r}"
    )


def test_serve_asgi_fallback_hermetic_when_server_missing(monkeypatch):
    """R4: Fallback must be hermetic and not attempt to run uvicorn or bind sockets.

    Monkeypatching UVICORN_AVAILABLE = False and HYPERCORN_AVAILABLE = False
    guarantees fallback without environment-dependent socket operations.
    """
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    monkeypatch.setattr(asgi_gateway, "UVICORN_AVAILABLE", False)
    monkeypatch.setattr(asgi_gateway, "HYPERCORN_AVAILABLE", False)

    calls = []
    fake_app = object()

    name = serve_asgi(
        fake_app,
        "127.0.0.1",
        5555,
        debug=False,
        fallback_wsgi=lambda app, host, port, debug: calls.append((app, host, port, debug)),
    )
    assert name == "fallback_wsgi"
    assert len(calls) == 1
    assert calls[0] == (fake_app, "127.0.0.1", 5555, False)


def test_asgi_http_non_200_status_and_body_and_query():
    """Mutation coverage M1, M3, M4, M5: Assert non-200 status, query string, request body, headers."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    observed = {}

    def custom_app(environ, start_response):
        observed["method"] = environ.get("REQUEST_METHOD")
        observed["query_string"] = environ.get("QUERY_STRING")
        observed["body"] = environ["wsgi.input"].read()
        observed["content_type"] = environ.get("CONTENT_TYPE")
        observed["content_length"] = environ.get("CONTENT_LENGTH")
        observed["custom_hdr"] = environ.get("HTTP_X_TRACE_ID")
        start_response("201 Created", [("Content-Type", "application/json"), ("X-Test", "Echo")])
        return [b'{"created": true}']

    gateway = ASGIGateway(custom_app)
    responses = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b'{"key": "value"}', "more_body": False}

        async def send(msg):
            responses.append(msg)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": "/api/items",
            "query_string": b"filter=active&limit=10",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"16"),
                (b"x-trace-id", b"trace-12345"),
            ],
        }
        await gateway(scope, receive, send)

    asyncio.run(run())

    # M1: status code must be 201, not 200
    start = next((m for m in responses if m["type"] == "http.response.start"), None)
    assert start is not None
    assert start["status"] == 201, "M1: Status code must be extracted correctly from WSGI start_response"

    # M3: query string must be preserved
    assert observed["query_string"] == "filter=active&limit=10", "M3: QUERY_STRING must be preserved"

    # M4: request body must be readable from wsgi.input
    assert observed["body"] == b'{"key": "value"}', "M4: Request body must be passed via wsgi.input"

    # M5: headers must be passed into environ
    assert observed["content_type"] == "application/json", "M5: CONTENT_TYPE must be set"
    assert observed["content_length"] == "16", "M5: CONTENT_LENGTH must be set"
    assert observed["custom_hdr"] == "trace-12345", "M5: Custom HTTP headers must be set"

    body = b"".join(m.get("body", b"") for m in responses if m["type"] == "http.response.body")
    assert body == b'{"created": true}'


def test_serve_asgi_with_injected_hypercorn():
    """M6: Verify hypercorn_run is invoked with app, host, and port."""
    assert asgi_gateway is not None, "bulk_downloader.asgi_gateway not implemented"
    calls = []
    fake_asgi_app = object()

    name = serve_asgi(
        fake_asgi_app,
        "0.0.0.0",
        8000,
        debug=False,
        uvicorn_run=None,
        hypercorn_run=lambda app, host, port: calls.append((app, host, port)),
    )
    assert name == "hypercorn"
    assert len(calls) == 1
    assert calls[0] == (fake_asgi_app, "0.0.0.0", 8000)

