"""flaresolverr API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/flaresolverr views moved onto a Flask Blueprint.
Endpoint labels gain a "flaresolverr." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_FLARE_AVAILABLE, _flare_client, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

flaresolverr_bp = Blueprint("flaresolverr", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__FLARE_AVAILABLE():
    """The live shared _FLARE_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_FLARE_AVAILABLE")

def _app__flare_client():
    """The live shared _flare_client from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_flare_client")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@flaresolverr_bp.route("/api/flaresolverr/status", methods=["GET"])
def api_flaresolverr_status():
    """Return stats + most recent ping check."""
    _FLARE_AVAILABLE = _app__FLARE_AVAILABLE()
    _flare_client = _app__flare_client()
    s_cfg = _app_s_cfg()
    if not (_FLARE_AVAILABLE and _flare_client is not None):
        return jsonify({"ok": False, "error": "flaresolverr module unavailable"})
    # Get the first site's endpoint (they typically share one)
    endpoint = ""
    for sid, cfg in s_cfg.items():
        if cfg.get("flaresolverr_endpoint"):
            endpoint = cfg["flaresolverr_endpoint"]
            break
    if not endpoint:
        endpoint = _flare_client.DEFAULT_ENDPOINT
    # Ping (cheap, ~50ms when up; fails fast when down)
    ping_result = _flare_client.update_ping_stats(endpoint)
    return jsonify({
        "ok": True,
        "endpoint": endpoint,
        "ping": ping_result,
        "stats": _flare_client.stats(),
    })


@flaresolverr_bp.route("/api/flaresolverr/test", methods=["POST"])
def api_flaresolverr_test():
    """Test a solve against a user-supplied URL. Used by the UI's
    'Test FlareSolverr' button. Does NOT touch any site queues."""
    _FLARE_AVAILABLE = _app__FLARE_AVAILABLE()
    _flare_client = _app__flare_client()
    _check_csrf()
    if not (_FLARE_AVAILABLE and _flare_client is not None):
        return jsonify({"ok": False, "error": "flaresolverr module unavailable"})
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing 'url'"})
    # SSRF guard (v3.66.781): a USER-SUPPLIED endpoint controls the fetch
    # destination and is only CSRF-gated, so validate its host against the
    # canonical _is_safe_public_host (rejects loopback/private/link-local/
    # CGNAT/reserved) BEFORE solve_cloudflare. Mirrors the multi_conn guard.
    # The hardcoded DEFAULT_ENDPOINT (localhost) is operator config, not
    # attacker-controlled, and stays exempt.
    raw_endpoint = (body.get("endpoint", "") or "").strip()
    if raw_endpoint:
        from urllib.parse import urlparse
        from .provider_resolve_impl._common import _is_safe_public_host
        _ep_host = urlparse(raw_endpoint).hostname or ""
        _ok, _why = _is_safe_public_host(_ep_host)
        if not _ok:
            return jsonify({"ok": False,
                            "error": f"endpoint host is not a public address: {_why}"})
    endpoint = raw_endpoint or _flare_client.DEFAULT_ENDPOINT
    timeout_s = float(body.get("timeout_s", 60.0) or 60.0)
    sr = _flare_client.solve_cloudflare(
        url, endpoint=endpoint, timeout_s=timeout_s,
    )
    return jsonify({
        "ok": sr.ok,
        "elapsed_s": round(sr.elapsed_s, 2),
        "cookie_count": len(sr.cookies),
        "user_agent": sr.user_agent,
        "status_code": sr.status_code,
        "html_length": len(sr.html or ""),
        "error": sr.error,
    })


@flaresolverr_bp.route("/api/flaresolverr/sessions", methods=["GET"])
def api_flaresolverr_sessions():
    """List active FlareSolverr sessions on the configured endpoint."""
    _FLARE_AVAILABLE = _app__FLARE_AVAILABLE()
    _flare_client = _app__flare_client()
    s_cfg = _app_s_cfg()
    if not (_FLARE_AVAILABLE and _flare_client is not None):
        return jsonify({"ok": False, "error": "flaresolverr module unavailable"})
    endpoint = ""
    for sid, cfg in s_cfg.items():
        if cfg.get("flaresolverr_endpoint"):
            endpoint = cfg["flaresolverr_endpoint"]
            break
    if not endpoint:
        endpoint = _flare_client.DEFAULT_ENDPOINT
    sessions = _flare_client.list_sessions(endpoint=endpoint)
    return jsonify({"ok": True, "endpoint": endpoint, "sessions": sessions})

def register_routes(app) -> int:
    app.register_blueprint(flaresolverr_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("flaresolverr."))

