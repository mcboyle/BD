"""deploy API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/deploy views moved onto a Flask Blueprint.
Endpoint labels gain a "deploy." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

deploy_bp = Blueprint("deploy", __name__)


@deploy_bp.route("/api/deploy/compose")
def api_deploy_compose():
    try:
        from . import edge_deploy as _ed
        return Response(_ed.compose_yaml(
            include_qbittorrent=request.args.get("qbittorrent", "true") == "true",
            include_flaresolverr=request.args.get("flaresolverr", "true") == "true",
            include_vpn_sidecar=request.args.get("vpn", "false") == "true",
        ), mimetype="text/yaml")
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@deploy_bp.route("/api/deploy/systemd")
def api_deploy_systemd():
    try:
        from . import edge_deploy as _ed
        return Response(_ed.systemd_unit(), mimetype="text/plain")
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@deploy_bp.route("/api/deploy/k8s")
def api_deploy_k8s():
    try:
        from . import edge_deploy as _ed
        return Response(_ed.k8s_manifests(), mimetype="text/yaml")
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(deploy_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("deploy."))

