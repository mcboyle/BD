"""queue_templates API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/queue_templates views moved onto a Flask Blueprint.
Endpoint labels gain a "queue_templates." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

queue_templates_bp = Blueprint("queue_templates", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


@queue_templates_bp.route("/api/queue_templates", methods=["GET", "POST"])
def api_queue_templates():
    from . import queue_templates as _qt
    if request.method == "GET":
        return jsonify({"ok": True, "templates": _qt.list_all()})
    body = request.json or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    origin = body.get("origin_site_id") or ""
    urls = body.get("urls") or []
    if not isinstance(urls, list):
        return jsonify({"ok": False,
                        "error": "urls must be a list"}), 400
    tid = _qt.create(
        name=name, origin_site_id=origin, urls=urls,
        priority_map=body.get("priority_map") or {},
        force_set=body.get("force_set") or [],
        note=body.get("note") or "")
    return jsonify({"ok": True, "id": tid})


@queue_templates_bp.route("/api/queue_templates/<int:tid>",
           methods=["GET", "PUT", "DELETE"])
def api_queue_template_one(tid):
    from . import queue_templates as _qt
    if request.method == "GET":
        t = _qt.get(tid)
        if not t: return jsonify({"ok": False,
                                  "error": "not found"}), 404
        return jsonify({"ok": True, "template": t})
    if request.method == "DELETE":
        ok = _qt.delete(tid)
        return jsonify({"ok": ok}), (200 if ok else 404)
    # PUT
    body = request.json or {}
    ok = _qt.update(
        tid,
        name=body.get("name"),
        urls=body.get("urls"),
        priority_map=body.get("priority_map"),
        force_set=body.get("force_set"),
        note=body.get("note"))
    return jsonify({"ok": ok})


@queue_templates_bp.route("/api/queue_templates/<int:tid>/apply/<sid>",
           methods=["POST"])
def api_queue_template_apply(tid, sid):
    """Import a template into a site's queue. Mode `append` (default)
    skips URLs already in the target queue; `replace` clears first."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}), 404
    from . import queue_templates as _qt
    t = _qt.get(tid)
    if not t: return jsonify({"ok": False, "error": "not found"}), 404
    mode = (request.args.get("mode") or "append").lower()
    if mode not in ("append", "replace"):
        return jsonify({"ok": False, "error":
                        f"unknown mode: {mode}"}), 400
    runner = runners[sid]
    if mode == "replace":
        with runner._lock:
            from .db import queue_delete_site
            queue_delete_site(sid)
            runner.jobs.clear()
            runner.urls.clear()
    # Insert URLs
    new_urls = [u for u in t["urls"] if u not in runner.jobs]
    added = 0
    if new_urls:
        result = runner.load_urls(new_urls, folder_scan=False)
        added = result[0] if isinstance(result, tuple) else len(new_urls)
    # Apply priority + force flags from the template
    pmap = t.get("priority_map") or {}
    fset = set(t.get("force_set") or [])
    with runner._lock:
        for url in t["urls"]:
            if url not in runner.jobs: continue
            if url in pmap:
                runner.jobs[url]["priority"] = pmap[url]
            if url in fset:
                runner.jobs[url]["force_download"] = True
    _qt.record_use(tid)
    return jsonify({"ok": True, "added": added, "applied": tid,
                    "site_id": sid, "mode": mode})

def register_routes(app) -> int:
    app.register_blueprint(queue_templates_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("queue_templates."))

