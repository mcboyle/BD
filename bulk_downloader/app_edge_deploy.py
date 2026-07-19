"""edge_deploy API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/edge_deploy views moved onto a Flask Blueprint.
Endpoint labels gain a "edge_deploy." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

edge_deploy_bp = Blueprint("edge_deploy", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@edge_deploy_bp.route("/api/edge_deploy/compose", methods=["POST"])
def api_edge_compose():
    """Generate docker-compose.yml for current install."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import edge_deploy as _ed
        yaml = _ed.compose_yaml(
            bd_image=body.get("image", "bulkdownloader:latest"),
            bd_port=int(body.get("port", 7777) or 7777),
            install_path=body.get("install_dir") or "/data/bd",
            downloads_path=body.get("downloads_dir") or "/data/downloads",
            timezone=body.get("tz", "UTC"),
            include_qbittorrent=bool(body.get("with_qbittorrent", False)),
            include_flaresolverr=bool(body.get("with_flaresolverr", False)),
            include_vpn_sidecar=bool(body.get("with_vpn", False)),
        )
        return jsonify({"ok": True, "yaml": yaml})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@edge_deploy_bp.route("/api/edge_deploy/all", methods=["POST"])
def api_edge_all():
    """Return all deploy artifacts (compose + systemd + k8s)."""
    _check_csrf()
    body = request.json or {}
    # Map UI-friendly keys to the module's actual param names
    kwargs = {
        "bd_image": body.get("image", "bulkdownloader:latest"),
        "bd_port": int(body.get("port", 7777) or 7777),
        "install_path": body.get("install_dir") or "/data/bd",
        "downloads_path": body.get("downloads_dir") or "/data/downloads",
        "timezone": body.get("tz", "UTC"),
        "include_qbittorrent": bool(body.get("with_qbittorrent", False)),
        "include_flaresolverr": bool(body.get("with_flaresolverr", False)),
        "include_vpn_sidecar": bool(body.get("with_vpn", False)),
    }
    try:
        from . import edge_deploy as _ed
        return jsonify({"ok": True,
                        "artifacts": _ed.all_artifacts(**kwargs)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(edge_deploy_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("edge_deploy."))

