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

BD_GATE_SCOPE = "repo-wide"

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
