"""scrapling API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/scrapling views moved onto a Flask Blueprint.
Endpoint labels gain a "scrapling." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_SCRAP_AVAILABLE, _scrap_adapter) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

scrapling_bp = Blueprint("scrapling", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__SCRAP_AVAILABLE():
    """The live shared _SCRAP_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_SCRAP_AVAILABLE")

def _app__scrap_adapter():
    """The live shared _scrap_adapter from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_scrap_adapter")


@scrapling_bp.route("/api/scrapling/status", methods=["GET"])
def api_scrapling_status():
    """Module availability + runtime counters."""
    _SCRAP_AVAILABLE = _app__SCRAP_AVAILABLE()
    _scrap_adapter = _app__scrap_adapter()
    if not (_SCRAP_AVAILABLE and _scrap_adapter is not None):
        return jsonify({
            "ok": False, "available": False,
            "error": "scrapling_adapter module unavailable",
        })
    return jsonify({
        "ok": True,
        "available": _scrap_adapter.is_available(),
        "stealthy_fetcher": _scrap_adapter.is_stealthy_fetcher_available(),
        "stats": _scrap_adapter.stats(),
    })


@scrapling_bp.route("/api/scrapling/fingerprint", methods=["POST"])
def api_scrapling_fingerprint():
    """Build a fingerprint from a snippet of HTML + selector. Used by
    the teach pipeline to capture fingerprints; also useful via curl
    for testing."""
    _SCRAP_AVAILABLE = _app__SCRAP_AVAILABLE()
    _scrap_adapter = _app__scrap_adapter()
    _check_csrf()
    if not (_SCRAP_AVAILABLE and _scrap_adapter is not None):
        return jsonify({"ok": False, "error": "scrapling_adapter unavailable"})
    body = request.get_json(silent=True) or {}
    html = body.get("html", "") or ""
    selector = body.get("selector", "") or ""
    if not html or not selector:
        return jsonify({
            "ok": False,
            "error": "both 'html' and 'selector' are required",
        })
    fp = _scrap_adapter.build_fingerprint(html, selector)
    if fp is None:
        return jsonify({
            "ok": False,
            "error": ("fingerprint build failed — selector matched no "
                      "element, or scrapling not installed"),
        })
    return jsonify({"ok": True, "fingerprint": fp})


@scrapling_bp.route("/api/scrapling/recover", methods=["POST"])
def api_scrapling_recover():
    """Test selector recovery against a snippet of HTML + saved
    fingerprint. Returns the new selector + confidence score, or an
    error explanation."""
    _SCRAP_AVAILABLE = _app__SCRAP_AVAILABLE()
    _scrap_adapter = _app__scrap_adapter()
    _check_csrf()
    if not (_SCRAP_AVAILABLE and _scrap_adapter is not None):
        return jsonify({"ok": False, "error": "scrapling_adapter unavailable"})
    body = request.get_json(silent=True) or {}
    html = body.get("html", "") or ""
    fingerprint = body.get("fingerprint")
    min_score = float(body.get("min_score", 0.6))
    if not html or not isinstance(fingerprint, dict):
        return jsonify({
            "ok": False,
            "error": "'html' and 'fingerprint' (dict) required",
        })
    result = _scrap_adapter.recover_selector(
        html, fingerprint, min_score=min_score)
    return jsonify({
        "ok": result.ok,
        "selector": result.selector,
        "score": result.score,
        "candidates_considered": result.candidates_considered,
        "error": result.error,
    })


@scrapling_bp.route("/api/scrapling/turnstile_check", methods=["POST"])
def api_scrapling_turnstile_check():
    """Pure detection — does the supplied HTML look like a Turnstile
    challenge?"""
    _SCRAP_AVAILABLE = _app__SCRAP_AVAILABLE()
    _scrap_adapter = _app__scrap_adapter()
    _check_csrf()
    if not (_SCRAP_AVAILABLE and _scrap_adapter is not None):
        return jsonify({"ok": False, "error": "scrapling_adapter unavailable"})
    body = request.get_json(silent=True) or {}
    html = body.get("html", "") or ""
    return jsonify({
        "ok": True,
        "is_turnstile": _scrap_adapter.is_turnstile_page(html),
    })

def register_routes(app) -> int:
    app.register_blueprint(scrapling_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("scrapling."))

