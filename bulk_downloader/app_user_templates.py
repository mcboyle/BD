"""user_templates API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/user_templates views moved onto a Flask Blueprint.
Endpoint labels gain a "user_templates." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

import json
from flask import Blueprint, jsonify, request

user_templates_bp = Blueprint("user_templates", __name__)


@user_templates_bp.route("/api/user_templates", methods=["GET"])
def api_user_templates_list():
    """List all user-saved templates with full data. The combined picker
    list (built-in + user) is at /api/templates — this endpoint is for
    the Settings -> Templates panel where the user manages their saved
    set."""
    from . import user_templates as _ut
    return jsonify({"ok": True, "templates": _ut.list_user_templates()})


@user_templates_bp.route("/api/user_templates", methods=["POST"])
def api_user_templates_create():
    """Save a new user template OR update an existing one.

    Body:
      {
        name: "My favorite site",
        description: "...",
        patterns: ["mysite\\.com"],         # array of regex patterns
        learned: {download: {row_selectors:[...], url_attribute:..., ...}},
        config_defaults: {quality_preference: "..."},  # optional
        id: "user_my_site_..."               # optional, for updates
      }

    Returns {ok: true, template: {...}} on success with the assigned id
    so the frontend can reflect it in the UI immediately.

    Validation:
      - name and description must be non-empty strings
      - patterns is an array (may be empty — template won't auto-suggest)
      - every pattern must compile as a valid regex
      - learned.download.row_selectors must be a non-empty list
      - if url_attribute is a list, length must match row_selectors

    On validation failure returns 400 with {ok: false, error: "..."}.
    """
    from . import user_templates as _ut
    data = request.json or {}
    ok, result = _ut.save_user_template(
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        patterns=list(data.get("patterns") or []),
        learned=data.get("learned") or {},
        config_defaults=data.get("config_defaults") or None,
        source=str(data.get("source", "user_teach")),
        tid=data.get("id") or None,
    )
    if not ok:
        return jsonify({"ok": False, "error": result}), 400
    return jsonify({"ok": True, "template": result})


@user_templates_bp.route("/api/user_templates/<tid>", methods=["DELETE"])
def api_user_templates_delete(tid):
    """Remove a user template by id. 404 if not found."""
    from . import user_templates as _ut
    if _ut.delete_user_template(tid):
        return jsonify({"ok": True, "deleted": tid})
    return jsonify({"ok": False, "error": "not found"}), 404


@user_templates_bp.route("/api/user_templates/export", methods=["GET"])
def api_user_templates_export():
    """Stream the user templates payload as a download. JSON file
    compatible with /api/user_templates/import — supports the
    "share my templates with another install" workflow."""
    from . import user_templates as _ut
    from flask import Response
    payload = _ut.export_user_templates()
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="user_templates.json"',
        },
    )


@user_templates_bp.route("/api/user_templates/import", methods=["POST"])
def api_user_templates_import():
    """Accept a previously-exported templates payload.

    Query param: ?merge=1 (default — keep existing, add new on no
    id conflict) or ?merge=0 (DESTRUCTIVE — replace all user templates
    with the imported set).

    Body: the parsed JSON from a prior /export call.

    Returns:
      {ok: true, added: N, skipped: M, errors: [...]}
    """
    from . import user_templates as _ut
    payload = request.json or {}
    merge = request.args.get("merge", "1") != "0"
    added, skipped, errors = _ut.import_user_templates(payload, merge=merge)
    return jsonify({
        "ok": True,
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "mode": "merge" if merge else "replace",
    })


@user_templates_bp.route("/api/user_templates/import/preview", methods=["POST"])
def api_user_templates_import_preview():
    """Read-only preview of /api/user_templates/import (Cut 3).

    Classifies what the import WOULD do — new / changed / conflict /
    destructive / secrets-omitted — and writes nothing. Query param
    ?merge=1 (default) or ?merge=0 (replace). Body: a prior /export payload.
    No CSRF gate: this endpoint never mutates state.
    """
    from . import user_templates as _ut
    payload = request.json or {}
    merge = request.args.get("merge", "1") != "0"
    return jsonify(_ut.preview_user_templates_import(payload, merge=merge))

def register_routes(app) -> int:
    app.register_blueprint(user_templates_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("user_templates."))

