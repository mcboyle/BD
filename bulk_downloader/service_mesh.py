"""Opt-in service gateway routes backed by Traefik with direct fallback."""
from __future__ import annotations

import http.client
import json
import re
from collections.abc import Callable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from flask import Blueprint, current_app, jsonify, request


service_mesh_bp = Blueprint("service_mesh", __name__)


# A backend that ANSWERED is healthy, whatever it answered: only these gateway-side statuses mean
# "the route could not reach the service" and justify trying the next route (REFUTE E1: HTTPError
# is an OSError, so a healthy node's own 4xx/5xx was replayed to every route and became a 503).
FAILOVER_STATUSES = frozenset({502, 503, 504})


class RouteUnavailable(OSError):
    """The route did not deliver the request to a healthy service (transport error or 502/503/504)."""


def _decode(response_body: str):
    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        return response_body


def _error_body(err: HTTPError):
    """The body the service sent with its error status ("" when urllib attached no stream)."""
    stream = getattr(err, "fp", None)
    raw = stream.read() if stream is not None else b""
    return _decode(raw.decode("utf-8", "replace"))


def _default_transport(url: str, *, headers: dict[str, str], payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    outbound = Request(url, data=body, headers={**headers, "Content-Type": "application/json"})
    try:
        with urlopen(outbound, timeout=2) as response:  # nosec B310: configured service endpoints only
            # "replace", as _error_body does: a non-UTF-8 answer is still the service's answer and must
            # not surface as a UnicodeDecodeError blamed on the (valid) path
            return {"status": response.status, "body": _decode(response.read().decode("utf-8", "replace"))}
    except HTTPError as err:
        if err.code in FAILOVER_STATUSES:
            raise RouteUnavailable(f"{url}: HTTP {err.code}") from err
        # the service answered: its status and body are the answer, not a dead route
        return {"status": err.code, "body": _error_body(err)}


def _route_config(service: str) -> Mapping[str, object] | None:
    routes = current_app.config.get("SERVICE_MESH_ROUTES", {})
    candidate = routes.get(service) if isinstance(routes, Mapping) else None
    return candidate if isinstance(candidate, Mapping) else None


# a relative URL path: "/" then RFC 3986 pchar/"/" characters only -- no whitespace or control
# characters (http.client.InvalidURL is not an OSError and used to surface as a 500; REFUTE E2),
# no "?" query or "#" fragment, no "//" (scheme-relative) prefix
_PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")


def _path_from_request() -> str | None:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    path = body.pop("path", "/")
    if not isinstance(path, str) or not _PATH_RE.match(path):
        return None
    request.environ["service_mesh.payload"] = body
    return path


@service_mesh_bp.post("/api/service-mesh/<service>")
def route_service(service: str):
    config = _route_config(service)
    path = _path_from_request()
    if config is None or path is None:
        return jsonify({"error": "unknown service or invalid path"}), 400

    payload = request.environ["service_mesh.payload"]
    host = config.get("host")
    traefik_url = config.get("traefik_url")
    direct_urls = config.get("direct_urls", ())
    if not isinstance(host, str) or not isinstance(traefik_url, str) or not isinstance(direct_urls, (list, tuple)):
        return jsonify({"error": "invalid service configuration"}), 500

    transport: Callable[..., dict] = current_app.config.get("SERVICE_MESH_TRANSPORT", _default_transport)
    candidates = [("traefik", traefik_url, {"Host": host})]
    candidates.extend((f"direct:{index}", url, {}) for index, url in enumerate(direct_urls))
    for route, base_url, headers in candidates:
        if not isinstance(base_url, str) or not base_url.startswith("http://"):
            continue
        try:
            result = transport(f"{base_url.rstrip('/')}{path}", headers=headers, payload=payload)
        except HTTPError as err:
            # a custom transport that lets urllib's error through: same rule as _default_transport
            if err.code in FAILOVER_STATUSES:
                continue
            return jsonify({"route": route, "body": _error_body(err)}), int(err.code)
        except http.client.InvalidURL:
            # only urllib's own URL rejection maps to 400 (a bare ValueError from a transport is its bug)
            return jsonify({"error": "unknown service or invalid path"}), 400
        except OSError:
            continue  # URLError / socket errors / RouteUnavailable: the route is down, try the next
        status = int(result.get("status", 200))
        if status in FAILOVER_STATUSES:
            continue
        return jsonify({"route": route, "body": result.get("body")}), status
    return jsonify({"error": "all service routes are unavailable"}), 503


def register_service_mesh(app) -> int:
    """Register the gateway only when the owning app explicitly enables it."""
    if not app.config.get("SERVICE_MESH_ENABLED", False):
        return 0
    app.register_blueprint(service_mesh_bp)
    return 1
