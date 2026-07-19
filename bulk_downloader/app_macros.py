"""macros API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/macros views moved verbatim onto a Flask
Blueprint. Endpoint labels gain a "macros." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant
diffs empty). App-level helpers are reached lazily at call time.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

macros_bp = Blueprint("macros", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@macros_bp.route("/api/macros/list")
def api_macros_list():
    """List all stored macros. ?site_id=X to filter."""
    try:
        from . import macro_recorder as _mr
        return jsonify({"macros": _mr.list_macros(
            site_id=request.args.get("site_id") or None)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@macros_bp.route("/api/macros/get/<sid>/<name>")
def api_macros_get(sid, name):
    """Get one macro by site_id + name."""
    try:
        from . import macro_recorder as _mr
        m = _mr.get_macro(sid, name)
        if m is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(m)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@macros_bp.route("/api/macros/save", methods=["POST"])
def api_macros_save():
    """Record/overwrite a macro. Body: {site_id, name, actions,
    description?, tags?}."""
    _check_csrf()
    body = request.json or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be a JSON object"}), 400
    try:
        from . import macro_recorder as _mr
        return jsonify(_mr.record_macro(
            body.get("site_id", ""),
            body.get("name", ""),
            body.get("actions") or [],
            description=body.get("description", ""),
            tags=body.get("tags") or []))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@macros_bp.route("/api/macros/delete/<sid>/<name>", methods=["POST"])
def api_macros_delete(sid, name):
    """Delete a macro."""
    _check_csrf()
    try:
        from . import macro_recorder as _mr
        return jsonify({"ok": _mr.delete_macro(sid, name)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@macros_bp.route("/api/macros/validate", methods=["POST"])
def api_macros_validate():
    """Static-validate a macro without saving. Body: {actions: [...]}
    or a full macro bundle. Returns {ok, error?}. Useful when the UI
    wants to lint a hand-authored macro before persisting it."""
    body = request.json or {}
    try:
        from . import macro_recorder as _mr
        macro = body if "actions" in body else {"actions": []}
        ok, err = _mr.validate_macro(macro)
        return jsonify({"ok": ok, "error": err})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ── v3.45.8 Phase 186 (replay half): macro replay engine ──────────────
@macros_bp.route("/api/macros/replay_status")
def api_macros_replay_status():
    """Cheap probe: is Playwright installed? What action kinds are
    supported? Used by the UI to decide whether to show the replay
    button or grey it out."""
    try:
        from . import macro_replay as _mp
        return jsonify(_mp.status_dict())
    except Exception as e:
        return jsonify({"playwright_available": False,
                        "error": str(e)[:200]}), 500


@macros_bp.route("/api/macros/replay/<sid>/<name>", methods=["POST"])
def api_macros_replay(sid, name):
    """Execute a stored macro in a fresh standalone browser context.

    Body (all optional):
      start_url: URL to navigate to before the first action
      headless: bool (default True; set False for ops debugging)

    IMPORTANT: this opens a nested Playwright context. If the site
    has running SiteRunner workers, they should be paused first
    (INV-001). We do NOT pause them automatically because callers
    may have legitimate reasons not to (e.g. testing against a site
    that isn't a configured runner). UI is expected to warn the
    operator before invoking.

    Returns the replay result shape — see macro_replay.replay_on_page
    for the keys."""
    _check_csrf()
    body = request.json or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be a JSON object"}), 400
    try:
        from . import macro_replay as _mp
        result = _mp.replay_standalone(
            sid, name,
            start_url=(body.get("start_url") or None),
            headless=bool(body.get("headless", True)),
            persist_result=bool(body.get("persist_result", True)),
        )
        # Map "macro not found" → 404 so the UI doesn't show it as a
        # success-with-error
        if result.get("error") == "macro not found":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500

def register_routes(app) -> int:
    app.register_blueprint(macros_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("macros."))

