"""storage API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/storage views moved onto a Flask Blueprint.
Endpoint labels gain a "storage." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

storage_bp = Blueprint("storage", __name__)


@storage_bp.route("/api/storage/validate", methods=["GET"])
def api_storage_validate():
    """Read-only FS diagnosis of a path (Cut 4 operator intelligence).

    Reports exists / is_dir / writable / free space, a plain-language problem
    list, and a suggested fix. NEVER creates, moves, or deletes anything — a
    missing directory is reported, not made (repair-write is deferred to Cut 8).
    Query: ?path=<path>.
    """
    import os as _os
    import shutil as _shutil
    path = request.args.get("path")
    if not path:
        return jsonify({"ok": False, "error": "?path= is required"}), 400
    exists = _os.path.exists(path)
    is_dir = _os.path.isdir(path)
    problems = []
    suggested_fix = None
    free_bytes = None
    writable = False
    if not exists:
        problems.append("path does not exist")
        parent = path
        while parent and not _os.path.exists(parent):
            parent = _os.path.dirname(parent)
        suggested_fix = (f"create the directory {path!r} "
                         f"(nearest existing parent: {parent or '/'})")
    else:
        if not is_dir:
            problems.append("path is not a directory")
            suggested_fix = "point this at a directory, not a file"
        else:
            # os.access only — no probe file is ever written (read-only).
            writable = _os.access(path, _os.W_OK)
            if not writable:
                problems.append("directory is not writable")
                suggested_fix = ("grant write permission (chmod/chown) or "
                                 "choose another directory")
        try:
            base = path if is_dir else (_os.path.dirname(path) or "/")
            free_bytes = int(_shutil.disk_usage(base).free)
            if free_bytes < 100 * 1024 * 1024:
                problems.append("low free space (<100 MB)")
        except Exception:
            free_bytes = None
    return jsonify({
        "ok": True,
        "path": path,
        "exists": exists,
        "is_dir": is_dir,
        "writable": writable,
        "free_bytes": free_bytes,
        "problems": problems,
        "suggested_fix": suggested_fix,
    })

def register_routes(app) -> int:
    app.register_blueprint(storage_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("storage."))

