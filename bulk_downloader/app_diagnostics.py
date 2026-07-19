"""diagnostics API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/diagnostics views moved onto a Flask Blueprint.
Endpoint labels gain a "diagnostics." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import time
from flask import Blueprint, jsonify, send_file

diagnostics_bp = Blueprint("diagnostics", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@diagnostics_bp.route("/api/diagnostics/bundle")
def api_diagnostics_bundle():
    """Return the JSON diagnostics snapshot. Safe to share — sensitive
    fields redacted."""
    s_cfg = _app_s_cfg()
    try:
        from . import diagnostics_bundle as _db
        return jsonify(_db.bundle(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@diagnostics_bp.route("/api/diagnostics/download")
def api_diagnostics_download():
    """Generate a zip of the diagnostics bundle for download."""
    s_cfg = _app_s_cfg()
    try:
        import tempfile
        from . import diagnostics_bundle as _db
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            r = _db.bundle_as_zip(tmp.name, s_cfg=s_cfg, include_logs=True)
        if not r.get("ok"):
            return jsonify({"error": r.get("error", "bundle failed")}), 500
        ts = time.strftime("%Y%m%d-%H%M%S")
        return send_file(tmp.name, mimetype="application/zip",
                         as_attachment=True,
                         download_name=f"bd-diagnostics-{ts}.zip")
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(diagnostics_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("diagnostics."))

