"""import API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/import views moved onto a Flask Blueprint.
Endpoint labels gain a "import." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import_bp = Blueprint("import", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


@import_bp.route("/api/import/start/<sid>", methods=["POST"])
def api_import_start(sid):
    """Start a background mass-import job. Body accepts either:
      • {"text": "url1\nurl2\n..."} — newline-separated URLs
      • multipart file upload as form field 'file'
      • Optional ?folder_scan=1 to pre-mark URLs already on disk

    Returns 202 + {job_id, total} on success."""
    runners = _app_runners()
    _check_csrf()
    if sid not in runners:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    content = ""
    if "file" in request.files:
        content = request.files["file"].read().decode("utf-8", "ignore")
    elif request.json:
        content = (request.json or {}).get("text", "")
    elif request.form:
        content = request.form.get("text", "")
    urls = [u.strip() for u in content.splitlines()
            if u.strip().startswith("http")]
    if not urls:
        return jsonify({"ok": False, "error": "no valid URLs"}), 400
    folder_scan = (request.args.get("folder_scan") == "1"
                   or (request.form.get("folder_scan") if request.form
                       else "") == "1")
    try:
        from . import mass_import as _mi
        runner = runners[sid]
        res = _mi.start_import(site_id=sid, urls=urls,
                               load_urls_fn=runner.load_urls,
                               folder_scan=folder_scan)
        if not res.get("ok"):
            return jsonify(res), 400
        return jsonify(res), 202
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@import_bp.route("/api/import/status/<job_id>")
def api_import_status(job_id):
    """Polling status endpoint. Returns the job state dict or 404."""
    try:
        from . import mass_import as _mi
        s = _mi.status(job_id)
        if s is None:
            return jsonify({"error": "unknown job"}), 404
        return jsonify(s)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@import_bp.route("/api/import/cancel/<job_id>", methods=["POST"])
def api_import_cancel(job_id):
    _check_csrf()
    try:
        from . import mass_import as _mi
        return jsonify({"ok": _mi.cancel(job_id)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@import_bp.route("/api/import/recent")
def api_import_recent():
    """Recent imports across this run + persisted history."""
    try:
        from . import mass_import as _mi
        limit = min(100, max(1, int(request.args.get("limit", 20))))
        return jsonify({"jobs": _mi.list_recent(limit=limit)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@import_bp.route("/api/import/stream/<job_id>")
def api_import_stream(job_id):
    """SSE stream: pushes status updates every 500ms until the job
    is done/cancelled/errored. UI uses EventSource(...) to render
    live progress without polling overhead."""
    from flask import Response, stream_with_context
    from . import mass_import as _mi
    import json as _json

    @stream_with_context
    def gen():
        last_processed = -1
        for _ in range(2 * 60 * 60 * 2):  # 2h ceiling at 500ms
            s = _mi.status(job_id)
            if s is None:
                yield f"data: {_json.dumps({'error':'unknown job'})}\n\n"
                return
            # Only push when something changed (or every 5s as heartbeat)
            if s["processed"] != last_processed:
                yield f"data: {_json.dumps(s)}\n\n"
                last_processed = s["processed"]
            if s["state"] in ("done", "cancelled", "error", "crashed"):
                return
            import time as _t
            _t.sleep(0.5)
    return Response(gen(), mimetype="text/event-stream")

def register_routes(app) -> int:
    app.register_blueprint(import_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("import."))

