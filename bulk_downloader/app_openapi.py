"""openapi API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/openapi.json and /api/openapi/parity views moved onto a
single Flask Blueprint("openapi"). Endpoint labels gain an "openapi." prefix; the
(rule, methods, bare-name) routing surface is byte-identical
(test_route_map_invariant diffs empty). BOTH routes live in this ONE module by
hand -- combining them sidesteps the dotted-module-name footgun of deriving a
module per route from a path like "/api/openapi.json".

The Flask `app` object (passed to openapi_spec.generate/audit_parity) is owned by
app.py and reached via a reference-identical _app_app() accessor; openapi_spec is
a sibling-package module imported directly.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

openapi_bp = Blueprint("openapi", __name__)

def _app_app():
    """The live Flask app object from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "app")


@openapi_bp.route("/api/openapi.json")
def api_openapi():
    """Return the OpenAPI 3.1 spec for this BD instance. Reflects the
    current registered routes — endpoint is dynamic, not pre-built."""
    app = _app_app()
    try:
        from . import openapi_spec as _oa
        return jsonify(_oa.generate(app))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@openapi_bp.route("/api/openapi/parity")
def api_openapi_parity():
    """REST/UI parity audit. Reports which API routes exist."""
    app = _app_app()
    try:
        from . import openapi_spec as _oa
        return jsonify(_oa.audit_parity(app))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(openapi_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("openapi."))
