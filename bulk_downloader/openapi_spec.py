"""OpenAPI 3.1 spec generator (Phase 113, Block P).

Walks Flask's url_map and reflects every registered route into an
OpenAPI 3.1 document. Used to:

  • Expose /api/openapi.json so bdctl / external clients can
    auto-discover the API surface
  • Generate API docs (Swagger UI / Redoc / Stoplight Elements)
  • Audit REST/UI parity — what's reachable from the UI but missing
    from the API, or vice versa

Approach: zero-dependency reflection. We don't try to scrape JSON
schemas from Pydantic models (BD doesn't use them); we tag routes
with rich metadata via a side-table (`route_meta` dict below) and
fall back to introspecting the view function's docstring + signature
when not tagged.

For each route the generator records:
  • Path (Flask placeholders → OpenAPI {param} syntax)
  • Methods supported
  • Description (from docstring's first paragraph)
  • Path parameters (from the URL placeholders)
  • Response shape: always {} unless tagged
  • Security: 'CsrfToken' only where the production CSRF hook requires it

Limitations:
  • Request bodies not inferred — view functions accept arbitrary
    request.json. We tag the well-known ones in route_meta.
  • Response schemas not introspected; we mark all responses as
    application/json with a generic object schema.
"""
from __future__ import annotations

import inspect
import importlib
import re
from copy import deepcopy
from typing import Iterable, Optional


# Route-level metadata overrides. The generator merges this with the
# auto-introspected data. Add an entry here to give an endpoint a
# friendlier summary, request body shape, or response schema. Keys
# are (path, method) tuples; values are partial OpenAPI operation
# objects.
ROUTE_META: dict = {
    ("/api/status", "GET"): {
        "summary": "Get full multi-site status",
        "tags": ["status"],
    },
    ("/api/search", "GET"): {
        "summary": "Full-text search across history (FTS5)",
        "tags": ["search"],
    },
    ("/api/capacity", "GET"): {
        "summary": "Capacity forecasts (disk runway, queue ETA, bottleneck)",
        "tags": ["status"],
    },
    ("/api/pause_all", "POST"): {
        "summary": "Pause workers on every site",
        "tags": ["control"],
    },
    ("/api/saved_searches", "POST"): {
        "summary": "Add a saved search",
        "tags": ["search"],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {
                "type": "object",
                "required": ["name", "query"],
                "properties": {
                    "name": {"type": "string"},
                    "query": {"type": "string"},
                    "site_id": {"type": "string"},
                    "status": {"type": "string"},
                    "schedule": {"type": "string",
                                 "enum": ["hourly", "daily", "weekly", "manual"]},
                    "notify_via": {"type": "string"},
                },
            }}},
        },
    },
    ("/api/bitrot/scan", "POST"): {
        "summary": "Run a bit-rot scan",
        "tags": ["integrity"],
    },
    ("/api/provenance/verify", "POST"): {
        "summary": "Verify the provenance ledger Merkle chain",
        "tags": ["integrity"],
    },
    ("/api/wayback/snapshots", "GET"): {
        "summary": "List Wayback snapshots for a URL",
        "tags": ["archive"],
        "parameters": [{"name": "url", "in": "query", "required": True,
                       "schema": {"type": "string"}}],
    },
}

# Tag descriptions for the doc UI. Tags grouped by domain.
TAGS_META = [
    {"name": "status", "description": "Read-only status + metrics"},
    {"name": "control", "description": "State-mutating control endpoints"},
    {"name": "search", "description": "Full-text search + saved queries"},
    {"name": "integrity", "description": "Provenance ledger + bit-rot"},
    {"name": "archive", "description": "Wayback Machine + archive forensics"},
    {"name": "library", "description": "Library finalization + NFO"},
    {"name": "vpn", "description": "VPN tunnels + kill switch"},
    {"name": "ai", "description": "AI provider + login assist"},
    {"name": "templates", "description": "Site templates + teach"},
    {"name": "metadata", "description": "TPDB + MP4 atoms + subtitles"},
]


def _path_to_openapi(flask_rule: str) -> tuple:
    """Convert Flask rule '/api/sites/<sid>/pause' → ('/api/sites/{sid}/pause', [params])."""
    params = []
    # Flask placeholder pattern: <type:name> or <name>
    def repl(m):
        spec = m.group(1)
        if ":" in spec:
            type_, name = spec.split(":", 1)
        else:
            type_, name = "string", spec
        schema = {"int": {"type": "integer"},
                  "float": {"type": "number"},
                  "path": {"type": "string"},
                  "uuid": {"type": "string", "format": "uuid"},
                  "string": {"type": "string"}}.get(type_, {"type": "string"})
        params.append({"name": name, "in": "path", "required": True,
                       "schema": schema})
        return "{" + name + "}"
    out = re.sub(r"<([^>]+)>", repl, flask_rule)
    return out, params


def _docstring_summary(view_fn) -> str:
    """First non-empty line of the view function's docstring as summary."""
    if not view_fn:
        return ""
    doc = inspect.getdoc(view_fn) or ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def _csrf_required(method: str, path: str) -> bool:
    """Ask the production hook predicate; never maintain a second policy."""
    app_module = importlib.import_module("bulk_downloader.app")
    return bool(app_module.csrf_fires_for(method, path))


def generate(app, *, title: str = "BulkDownloader API",
            version: Optional[str] = None,
            base_url: str = "/") -> dict:
    """Produce an OpenAPI 3.1 doc reflecting `app`'s registered routes."""
    if version is None:
        from . import __version__
        version = __version__
    spec: dict = {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version,
            "description": ("Auto-generated from Flask url_map. Routes "
                            "tagged with metadata appear with richer "
                            "summaries; the rest fall back to the view "
                            "function's docstring."),
        },
        "servers": [{"url": base_url}],
        "tags": TAGS_META,
        "components": {
            "securitySchemes": {
                "CsrfToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-CSRF-Token",
                    "description": ("Required only where the production "
                                     "csrf_fires_for policy marks an operation. "
                                     "Consult each operation's "
                                     "x-csrf-required value."),
                }
            },
            "schemas": {
                "Empty": {"type": "object"},
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                },
            },
        },
        "paths": {},
    }
    for rule in app.url_map.iter_rules():
        # Skip Flask internals
        if rule.endpoint == "static" or rule.rule.startswith("/static"):
            continue
        path_str, path_params = _path_to_openapi(rule.rule)
        view_fn = app.view_functions.get(rule.endpoint)
        summary = _docstring_summary(view_fn) or rule.endpoint
        spec["paths"].setdefault(path_str, {})
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        for method in sorted(methods):
            method_lower = method.lower()
            op = {
                "summary": summary,
                "operationId": f"{method_lower}_{rule.endpoint}",
                "responses": {
                    "200": {"description": "Success",
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/Empty"}}}},
                    "400": {"description": "Bad request",
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}}}},
                    "500": {"description": "Server error",
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}}}},
                },
                "x-csrf-required": _csrf_required(method, rule.rule),
            }
            if path_params:
                op["parameters"] = list(path_params)
            if op["x-csrf-required"]:
                op["security"] = [{"CsrfToken": []}]
                op.setdefault("parameters", []).append({
                    "name": "X-CSRF-Token",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                })
                op["responses"]["403"] = {
                    "description": "CSRF token missing/invalid",
                }
            # Merge in tagged metadata if present
            override = deepcopy(ROUTE_META.get((rule.rule, method)))
            if override:
                # Merge parameters carefully so override extends path params
                if "parameters" in override:
                    op.setdefault("parameters", []).extend(override.pop("parameters"))
                op.update(override)
            spec["paths"][path_str][method_lower] = op
    spec["paths"] = dict(sorted(spec["paths"].items()))
    return spec


def audit_parity(app, *, ui_routes: Optional[Iterable[str]] = None) -> dict:
    """REST/UI parity audit. Compares registered API routes to a
    list of paths the UI is known to call (from grep of app.js).
    Returns {api_only: [...], ui_only: [...], common: N}.

    `ui_routes` is operator-supplied — Phase 113 UI work would
    populate it automatically. Default empty list means we just
    report the API surface."""
    api_paths = set()
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/api/"):
            api_paths.add(rule.rule)
    ui_paths = set(ui_routes or [])
    return {
        "api_routes": sorted(api_paths),
        "api_only": sorted(api_paths - ui_paths),
        "ui_only": sorted(ui_paths - api_paths),
        "common": sorted(api_paths & ui_paths),
        "api_count": len(api_paths),
        "ui_count": len(ui_paths),
    }
