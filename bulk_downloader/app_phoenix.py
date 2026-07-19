"""phoenix API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/phoenix views moved onto a Flask Blueprint.
Endpoint labels gain a "phoenix." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_PHOENIX_AVAILABLE, _phoenix_cat, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

phoenix_bp = Blueprint("phoenix", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__PHOENIX_AVAILABLE():
    """The live shared _PHOENIX_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_PHOENIX_AVAILABLE")

def _app__phoenix_cat():
    """The live shared _phoenix_cat from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_phoenix_cat")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@phoenix_bp.route("/api/phoenix/lookup", methods=["POST"])
def api_phoenix_lookup():
    """Look up a URL in the catalog. Returns the best match + confidence."""
    _PHOENIX_AVAILABLE = _app__PHOENIX_AVAILABLE()
    _phoenix_cat = _app__phoenix_cat()
    _check_csrf()
    if not _PHOENIX_AVAILABLE:
        return jsonify({"ok": False, "error": "phoenix_catalog unavailable"})
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing 'url'"})
    match = _phoenix_cat.lookup_url(url)
    if match is None:
        return jsonify({"ok": True, "url": url, "match": None})
    return jsonify({
        "ok": True,
        "url": url,
        "match": {
            "brand_id": match.brand_id,
            "name": match.name,
            "network": match.network,
            "network_display": match.network_display,
            "confidence": round(match.confidence, 3),
            "matched_pattern": match.matched_pattern,
        },
    })


@phoenix_bp.route("/api/phoenix/suggest", methods=["POST"])
def api_phoenix_suggest():
    """Get a routing suggestion: route to existing site or create new."""
    _PHOENIX_AVAILABLE = _app__PHOENIX_AVAILABLE()
    _phoenix_cat = _app__phoenix_cat()
    s_cfg = _app_s_cfg()
    _check_csrf()
    if not _PHOENIX_AVAILABLE:
        return jsonify({"ok": False, "error": "phoenix_catalog unavailable"})
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing 'url'"})
    suggestion = _phoenix_cat.suggest_site_for_url(url, s_cfg)
    if suggestion is None:
        return jsonify({"ok": True, "url": url, "suggestion": None})
    out: dict = {"action": suggestion.action}
    if suggestion.catalog_match:
        out["match"] = {
            "brand_id": suggestion.catalog_match.brand_id,
            "name": suggestion.catalog_match.name,
            "network_display": suggestion.catalog_match.network_display,
            "confidence": round(suggestion.catalog_match.confidence, 3),
        }
    if suggestion.action == "route_to":
        out["target_site_id"] = suggestion.target_site_id
        out["target_site_name"] = suggestion.target_site_name
    elif suggestion.action == "create_new":
        out["suggested_site_id"] = suggestion.suggested_site_id
        out["suggested_site_name"] = suggestion.suggested_site_name
    return jsonify({"ok": True, "url": url, "suggestion": out})


@phoenix_bp.route("/api/phoenix/networks", methods=["GET"])
def api_phoenix_networks():
    """List all known networks with entry counts."""
    _PHOENIX_AVAILABLE = _app__PHOENIX_AVAILABLE()
    _phoenix_cat = _app__phoenix_cat()
    if not _PHOENIX_AVAILABLE:
        return jsonify({"ok": False, "error": "phoenix_catalog unavailable"})
    return jsonify({
        "ok": True,
        "stats": _phoenix_cat.catalog_stats(),
        "networks": _phoenix_cat.list_networks(),
    })


@phoenix_bp.route("/api/phoenix/brands/<network>", methods=["GET"])
def api_phoenix_brands(network):
    """List all brands in a network."""
    _PHOENIX_AVAILABLE = _app__PHOENIX_AVAILABLE()
    _phoenix_cat = _app__phoenix_cat()
    if not _PHOENIX_AVAILABLE:
        return jsonify({"ok": False, "error": "phoenix_catalog unavailable"})
    brands = _phoenix_cat.list_brands_in_network(network)
    return jsonify({"ok": True, "network": network, "brands": brands})

def register_routes(app) -> int:
    app.register_blueprint(phoenix_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("phoenix."))

