"""knowledge API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION with the one sanctioned stateful transform: api_knowledge_runbook
reads the shared ``s_cfg`` dict, which stays owned by app.py. It is reached via the
``_app_s_cfg()`` accessor (getattr on the app module, FRESH per call) -- the same
convention app_widgets_api uses -- so the handler sees the same live dict object by
reference, tracking any rebinding, with no import cycle. Endpoint labels gain a
``knowledge.`` prefix; the (rule, methods, bare-name) routing surface is byte-identical
(test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, request, jsonify

knowledge_bp = Blueprint("knowledge", __name__)


def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


def _app_s_cfg():
    """The live shared s_cfg dict, owned by app.py (fetched fresh per call)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg", {})


def _turnstile_bypass_state() -> dict:
    """Measure recipe advice; probe failures stay UNKNOWN."""
    try:
        from . import scrapling_adapter
        state = scrapling_adapter.capability_status()["turnstile_bypass"]
    except Exception as exc:
        return {
            "available": False,
            "status": "unknown",
            "reason": f"capability_probe_failed:{type(exc).__name__}",
        }
    if not isinstance(state, dict):
        return {
            "available": False,
            "status": "unknown",
            "reason": "capability_probe_returned_invalid_state",
        }
    return dict(state)


@knowledge_bp.route("/api/knowledge/runbook/<sid>")
def api_knowledge_runbook(sid):
    try:
        from . import knowledge as _kn
        s_cfg = _app_s_cfg()
        return jsonify(_kn.runbook(sid, s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@knowledge_bp.route("/api/knowledge/notes", methods=["GET"])
def api_knowledge_notes_list():
    try:
        from . import knowledge as _kn
        site_id = request.args.get("site_id")
        kind = request.args.get("kind")
        return jsonify({"notes": _kn.list_notes(site_id=site_id, kind=kind)})
    except Exception as e:
        return jsonify({"notes": [], "error": str(e)[:200]}), 500


@knowledge_bp.route("/api/knowledge/notes", methods=["POST"])
def api_knowledge_notes_add():
    _check_csrf()
    body = request.json or {}
    try:
        from . import knowledge as _kn
        nid = _kn.add_note(
            site_id=body.get("site_id", "") or "",
            kind=body.get("kind", "failure") or "failure",
            pattern=body.get("pattern", "") or "",
            resolution=body.get("resolution", "") or "",
        )
        if nid is None:
            return jsonify({"ok": False,
                            "error": "pattern and resolution required"}), 400
        return jsonify({"ok": True, "id": nid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@knowledge_bp.route("/api/knowledge/notes/<int:nid>", methods=["DELETE"])
def api_knowledge_notes_remove(nid):
    _check_csrf()
    try:
        from . import knowledge as _kn
        return jsonify({"ok": _kn.remove_note(nid)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@knowledge_bp.route("/api/knowledge/recipes")
def api_knowledge_recipes():
    """Diagnostic recipes catalog. Filter by ?message=<text> to get
    only recipes matching a given failure message."""
    try:
        from . import knowledge as _kn
        msg = request.args.get("message", "")
        turnstile_bypass = _turnstile_bypass_state()
        if msg:
            return jsonify({"recipes": _kn.diagnostic_recipes_for(
                msg, turnstile_bypass=turnstile_bypass)})
        return jsonify({"recipes": _kn.all_recipes(
            turnstile_bypass=turnstile_bypass)})
    except Exception as e:
        return jsonify({"recipes": [], "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(knowledge_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("knowledge."))
