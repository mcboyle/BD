"""diagnostics_bundle API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/diagnostics_bundle views moved onto a Flask Blueprint.
Endpoint labels gain a "diagnostics_bundle." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

diagnostics_bundle_bp = Blueprint("diagnostics_bundle", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@diagnostics_bundle_bp.route("/api/diagnostics_bundle/preview")
def api_diag_bundle_preview():
    """Return the bundle as JSON for inline preview (no zip)."""
    s_cfg = _app_s_cfg()
    try:
        from . import diagnostics_bundle as _db
        return jsonify(_db.bundle(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@diagnostics_bundle_bp.route("/api/diagnostics_bundle/download")
def api_diag_bundle_download():
    """Stream a zipped bundle as a download."""
    s_cfg = _app_s_cfg()
    try:
        from . import diagnostics_bundle as _db
        import io
        import tempfile
        from flask import send_file
        from datetime import datetime
        # Write to a temp file (send_file wants a path/buffer)
        with tempfile.NamedTemporaryFile(
                suffix=".zip", delete=False, prefix="bd-diag-") as tf:
            _db.bundle_as_zip(tf.name, s_cfg=s_cfg)
            fname = ("bd-diagnostics-"
                     + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip")
            return send_file(tf.name, as_attachment=True,
                             download_name=fname,
                             mimetype="application/zip")
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(diagnostics_bundle_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("diagnostics_bundle."))

