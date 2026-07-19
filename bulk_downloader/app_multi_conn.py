"""multi_conn API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/multi_conn views moved onto a Flask Blueprint.
Endpoint labels gain a "multi_conn." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_MULTI_CONN_AVAILABLE, _multi_conn, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

multi_conn_bp = Blueprint("multi_conn", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__MULTI_CONN_AVAILABLE():
    """The live shared _MULTI_CONN_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_MULTI_CONN_AVAILABLE")

def _app__multi_conn():
    """The live shared _multi_conn from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_multi_conn")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@multi_conn_bp.route("/api/multi_conn/status", methods=["GET"])
def api_multi_conn_status():
    """Multi-conn module availability + per-site enablement summary."""
    _MULTI_CONN_AVAILABLE = _app__MULTI_CONN_AVAILABLE()
    _multi_conn = _app__multi_conn()
    s_cfg = _app_s_cfg()
    if not (_MULTI_CONN_AVAILABLE and _multi_conn is not None):
        return jsonify({"ok": False, "error": "multi_conn module unavailable"})
    sites_enabled = []
    for sid, cfg in s_cfg.items():
        if cfg.get("use_multi_conn", False):
            sites_enabled.append({
                "site_id": sid,
                "name": cfg.get("name", sid),
                "chunk_count": cfg.get("multi_conn_count", 4),
                "min_size_mb": cfg.get("multi_conn_min_size_mb", 100),
            })
    return jsonify({
        "ok": True,
        "available": _multi_conn.is_available(),
        "default_chunk_count": _multi_conn.DEFAULT_CHUNK_COUNT,
        "default_min_size_mb": _multi_conn.DEFAULT_MIN_SIZE_BYTES // (1024 * 1024),
        "sites_enabled": sites_enabled,
    })


@multi_conn_bp.route("/api/multi_conn/probe", methods=["POST"])
def api_multi_conn_probe():
    """Probe whether a URL supports multi-connection. UI 'Test' button.
    Doesn't actually download."""
    _MULTI_CONN_AVAILABLE = _app__MULTI_CONN_AVAILABLE()
    _multi_conn = _app__multi_conn()
    _check_csrf()
    if not (_MULTI_CONN_AVAILABLE and _multi_conn is not None):
        return jsonify({"ok": False, "error": "multi_conn module unavailable"})
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing 'url'"})
    headers = {}
    if body.get("user_agent"):
        headers["User-Agent"] = body["user_agent"]
    if body.get("referer"):
        headers["Referer"] = body["referer"]
    try:
        pr = _multi_conn.probe(url, headers=headers, timeout_s=10.0)
    except Exception as e:
        return jsonify({"ok": False, "error": f"probe raised: {e}"})
    min_mb = int(body.get("min_size_mb", 100) or 100)
    viable = _multi_conn.should_use_multi_conn(
        pr.content_length, pr.accept_ranges,
        min_size_bytes=min_mb * 1024 * 1024,
    )
    return jsonify({
        "ok": pr.ok,
        "viable": viable,
        "content_length": pr.content_length,
        "content_length_human": (f"{pr.content_length / (1024*1024):.1f} MB"
                                  if pr.content_length else "?"),
        "accept_ranges": pr.accept_ranges,
        "final_url": pr.final_url,
        "server": pr.server,
        "error": pr.error,
    })

def register_routes(app) -> int:
    app.register_blueprint(multi_conn_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("multi_conn."))

