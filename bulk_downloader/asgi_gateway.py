"""Asynchronous Gateway Migration & Starlette/FastAPI Modern ASGI Cutover.

Provides ASGI 3.0 gateway implementation, WSGI-to-ASGI bridge, lifespan
lifecycle management, and optional Starlette/FastAPI modern ASGI cutover.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import io
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError, AttributeError):
        return False


STARLETTE_AVAILABLE = _module_available("starlette")
FASTAPI_AVAILABLE = _module_available("fastapi")
UVICORN_AVAILABLE = _module_available("uvicorn")
HYPERCORN_AVAILABLE = _module_available("hypercorn")


def get_gateway_info() -> Dict[str, Any]:
    """Return runtime capability metadata for the ASGI gateway."""
    return {
        "asgi_version": "3.0",
        "spec_version": "2.0",
        "gateway_class": "ASGIGateway",
        "starlette_available": STARLETTE_AVAILABLE,
        "fastapi_available": FASTAPI_AVAILABLE,
        "uvicorn_available": UVICORN_AVAILABLE,
        "hypercorn_available": HYPERCORN_AVAILABLE,
    }


class ASGIGateway:
    """ASGI 3.0 compliant gateway adapter for WSGI applications."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._handle_lifespan(scope, receive, send)
        elif scope_type == "http":
            await self._handle_http(scope, receive, send)
        else:
            raise NotImplementedError(f"Scope type {scope_type!r} is not supported")

    async def _handle_lifespan(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        while True:
            message = await receive()
            msg_type = message.get("type")
            if msg_type == "lifespan.startup":
                try:
                    # Optional boot_once invocation if attached
                    boot_fn = getattr(self.app, "boot_once", None)
                    if boot_fn is None:
                        try:
                            from bulk_downloader.app import boot_once as boot_fn
                        except ImportError:
                            boot_fn = None
                    if callable(boot_fn):
                        await asyncio.to_thread(boot_fn)
                    await send({"type": "lifespan.startup.complete"})
                except Exception as exc:
                    logger.exception("ASGI lifespan startup failed")
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
            elif msg_type == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _handle_http(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        # Accumulate request body
        body_parts: List[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            body_parts.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(body_parts)

        # Build WSGI environ from ASGI scope
        environ = self._build_environ(scope, body)

        status_code = 200
        headers_to_send: List[Tuple[bytes, bytes]] = []

        def start_response(status: str, response_headers: List[Tuple[str, str]], exc_info=None):
            nonlocal status_code, headers_to_send
            status_code = int(status.split()[0])
            headers_to_send = [
                (k.encode("latin1"), v.encode("latin1"))
                for k, v in response_headers
                if k.lower() not in ("connection", "keep-alive", "transfer-encoding")
            ]
            return lambda _: None

        # Execute WSGI application in thread pool
        def run_wsgi() -> List[bytes]:
            iterable = self.app(environ, start_response)
            try:
                return list(iterable)
            finally:
                if hasattr(iterable, "close"):
                    iterable.close()

        response_chunks = await asyncio.to_thread(run_wsgi)

        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": headers_to_send,
        })

        if response_chunks:
            for i, chunk in enumerate(response_chunks):
                is_last = (i == len(response_chunks) - 1)
                await send({
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": not is_last,
                })
        else:
            await send({
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            })

    def _build_environ(self, scope: Dict[str, Any], body: bytes) -> Dict[str, Any]:
        path = scope.get("path", "/")
        raw_path = scope.get("raw_path", path.encode("ascii", "replace"))
        query_string = scope.get("query_string", b"").decode("latin1")

        server = scope.get("server") or ("127.0.0.1", 80)
        client = scope.get("client") or ("127.0.0.1", 0)

        environ: Dict[str, Any] = {
            "REQUEST_METHOD": scope.get("method", "GET"),
            "SCRIPT_NAME": scope.get("root_path", ""),
            "PATH_INFO": path,
            "RAW_URI": raw_path.decode("latin1", "replace"),
            "QUERY_STRING": query_string,
            "SERVER_NAME": str(server[0]),
            "SERVER_PORT": str(server[1]),
            "REMOTE_ADDR": str(client[0]),
            "REMOTE_PORT": int(client[1]),
            "SERVER_PROTOCOL": f"HTTP/{scope.get('http_version', '1.1')}",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": scope.get("scheme", "http"),
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }

        for header_name, header_value in scope.get("headers", []):
            name = header_name.decode("latin1").lower()
            value = header_value.decode("latin1")
            if name == "content-type":
                environ["CONTENT_TYPE"] = value
            elif name == "content-length":
                environ["CONTENT_LENGTH"] = value
            else:
                key = "HTTP_" + name.upper().replace("-", "_")
                if key in environ:
                    sep = "; " if name == "cookie" else ", "
                    environ[key] = f"{environ[key]}{sep}{value}"
                else:
                    environ[key] = value

        return environ


def get_asgi_app(flask_app: Any = None) -> ASGIGateway:
    """Return an ASGI application callable for the provided or global Flask app."""
    if flask_app is None:
        from bulk_downloader.app import app as flask_app
    if isinstance(flask_app, ASGIGateway):
        return flask_app
    return ASGIGateway(flask_app)


def create_modern_asgi_app(flask_app: Any = None) -> Any:
    """Create a modern ASGI cutover application.

    Utilizes Starlette/FastAPI if installed; otherwise falls back to
    native ASGI 3.0 gateway.
    """
    if flask_app is None:
        from bulk_downloader.app import app as flask_app

    if STARLETTE_AVAILABLE:
        try:
            starlette_apps = importlib.import_module("starlette.applications")
            starlette_wsgi = importlib.import_module("starlette.middleware.wsgi")
            starlette_app = starlette_apps.Starlette()
            starlette_app.mount("/", starlette_wsgi.WSGIMiddleware(flask_app))
            return starlette_app
        except Exception as err:
            logger.warning("Starlette ASGI bridge failed to mount: %s", err)

    return ASGIGateway(flask_app)


def serve_asgi(
    app: Any,
    host: str,
    port: int,
    debug: bool = False,
    *,
    uvicorn_run: Optional[Callable] = None,
    hypercorn_run: Optional[Callable] = None,
    fallback_wsgi: Optional[Callable] = None,
) -> str:
    """Run the ASGI application using an available ASGI server or fallback."""
    if uvicorn_run is not None:
        uvicorn_run(app, host=host, port=port, log_level="debug" if debug else "info")
        return "uvicorn"

    if UVICORN_AVAILABLE:
        try:
            uvicorn = importlib.import_module("uvicorn")
            uvicorn.run(app, host=host, port=port, log_level="debug" if debug else "info")
            return "uvicorn"
        except ImportError:
            pass

    if hypercorn_run is not None:
        hypercorn_run(app, host=host, port=port)
        return "hypercorn"

    if HYPERCORN_AVAILABLE:
        try:
            hypercorn_asyncio = importlib.import_module("hypercorn.asyncio")
            hypercorn_config = importlib.import_module("hypercorn.config")
            cfg = hypercorn_config.Config()
            cfg.bind = [f"{host}:{port}"]
            asyncio.run(hypercorn_asyncio.serve(app, cfg))
            return "hypercorn"
        except ImportError:
            pass

    if fallback_wsgi is not None:
        wsgi_app = getattr(app, "app", app)
        fallback_wsgi(wsgi_app, host, port, debug)
        return "fallback_wsgi"

    raise RuntimeError("No ASGI server available (install uvicorn or hypercorn)")
