"""export API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/export views moved onto a Flask Blueprint.
Endpoint labels gain a "export." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

export_bp = Blueprint("export", __name__)


@export_bp.route("/api/export/csv")
def api_export_csv():
    try:
        from . import exports as _exp
        # Accept simple filter via query params
        fdict = {"limit": int(request.args.get("limit", 10000))}
        for k in ("site_id", "status", "message_contains"):
            v = request.args.get(k)
            if v:
                fdict[k] = v
        body = _exp.to_csv(fdict)
        return Response(body, mimetype="text/csv",
                        headers={"Content-Disposition":
                                 'attachment; filename="bd-history.csv"'})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@export_bp.route("/api/export/json")
def api_export_json():
    try:
        from . import exports as _exp
        fdict = {"limit": int(request.args.get("limit", 10000))}
        for k in ("site_id", "status"):
            v = request.args.get(k)
            if v:
                fdict[k] = v
        return Response(_exp.to_json(fdict),
                        mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@export_bp.route("/api/export/m3u")
def api_export_m3u():
    try:
        from . import exports as _exp
        fdict = {"limit": int(request.args.get("limit", 10000))}
        v = request.args.get("site_id")
        if v:
            fdict["site_id"] = v
        base_url = request.args.get("base_url") or None
        return Response(_exp.to_m3u(fdict, base_url=base_url),
                        mimetype="audio/x-mpegurl",
                        headers={"Content-Disposition":
                                 'attachment; filename="bd-library.m3u"'})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(export_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("export."))

