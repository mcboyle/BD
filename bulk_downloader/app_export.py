"""export API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/export views moved onto a Flask Blueprint.
Endpoint labels gain a "export." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

export_bp = Blueprint("export", __name__)


def _limit_arg():
    """?limit= as an int, or None when it is not one (q-import-export L8: a bad
    limit was a 500 whose body carried the ValueError text)."""
    try:
        return int(request.args.get("limit", 10000))
    except (TypeError, ValueError):
        return None


def _bad_limit():
    return jsonify({"error": "limit must be an integer"}), 400


def _export_rows(fdict):
    """BH1-67: (rows, refused). The rows a client is shown (exports.display_rows),
    raw only under the history export's include_tokens gate (a client on this
    machine; anyone else asking gets its 403)."""
    from . import exports as _exp
    from .app_sites_queue import _include_tokens
    raw, refused = _include_tokens()
    if refused:
        return None, refused
    rows = _exp._rows_from_filter(fdict)
    return (rows if raw else _exp.display_rows(rows)), None


@export_bp.route("/api/export/csv")
def api_export_csv():
    try:
        from . import exports as _exp
        # Accept simple filter via query params
        limit = _limit_arg()
        if limit is None:
            return _bad_limit()
        fdict = {"limit": limit}
        for k in ("site_id", "status", "message_contains"):
            v = request.args.get(k)
            if v:
                fdict[k] = v
        rows, refused = _export_rows(fdict)
        if refused:
            return refused
        body = _exp.to_csv(rows=rows)
        return Response(body, mimetype="text/csv",
                        headers={"Content-Disposition":
                                 'attachment; filename="bd-history.csv"'})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@export_bp.route("/api/export/json")
def api_export_json():
    try:
        from . import exports as _exp
        limit = _limit_arg()
        if limit is None:
            return _bad_limit()
        fdict = {"limit": limit}
        for k in ("site_id", "status"):
            v = request.args.get(k)
            if v:
                fdict[k] = v
        rows, refused = _export_rows(fdict)
        if refused:
            return refused
        return Response(_exp.to_json(rows=rows),
                        mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@export_bp.route("/api/export/m3u")
def api_export_m3u():
    try:
        from . import exports as _exp
        limit = _limit_arg()
        if limit is None:
            return _bad_limit()
        fdict = {"limit": limit}
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

