"""template_manager API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/template_manager views moved onto a Flask Blueprint.
Endpoint labels gain a "template_manager." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

import json
from flask import Blueprint, jsonify, request

template_manager_bp = Blueprint("template_manager", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@template_manager_bp.route("/api/template_manager")
def api_template_manager_list():
    """#10 — Template Manager: list reviewed + draft templates with status,
    host, selector groups, resolutions, redacted network patterns, and lint
    warnings. Read-only.
    """
    try:
        from .template_manager import list_templates
        return jsonify(list_templates())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@template_manager_bp.route("/api/template_manager/promote", methods=["POST"])
def api_template_manager_promote():
    """#10 — Promote a draft to a reviewed template (explicit operator action).
    Body: {"file": "<host>.template-draft.json", "enable"?: bool,
    "accept_api"?: bool}. Drafts are never auto-enabled; promotion of a draft
    with unsafe selectors is refused. ``accept_api`` (A6-1, default false)
    materializes the draft's validated review-only ``api_candidate`` into a
    runtime ``api`` block (ungating build_api_url — gated v3.66.155/157); an
    unverified candidate refuses the whole promote.
    """
    _check_csrf()
    # C7 11.1a: when multi-user is enabled, only a reviewer/admin may promote.
    # Default-no-op: with multi-user off (the single-operator default) this is
    # byte-identical to before. Best-effort — never 500s the route on a store error.
    try:
        from . import user_accounts as _ua
        if _ua.multi_user_enabled():
            _who = _ua.current_user_from_cookie(request.cookies.get("bd_user", ""))
            if not (_who and _ua._can_promote_role(_who.get("role", ""))):
                return jsonify({"ok": False,
                                "error": "reviewer role required to promote"}), 403
    except Exception:
        pass
    body = request.json or {}
    try:
        from .template_manager import promote_draft
        res = promote_draft(body.get("file") or "",
                            enable=bool(body.get("enable", True)),
                            accept_api=bool(body.get("accept_api", False)))
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@template_manager_bp.route("/api/template_manager/promote_check", methods=["POST"])
def api_template_manager_promote_check():
    """GCW guided-mode READ-ONLY promote preflight.
    Body: {"file": "<host>.template-draft.json"}.

    Returns the SAME structured verdict ``promote`` returns on refusal --
    ``{ok, gate_errors?, lint_warnings?}`` -- WITHOUT writing ``reviewed/`` or
    enabling anything. Runs the identical single-source predicates the real
    promote runs (the selector-lint blocking check + ``promote_gate_errors``:
    shape, resolutions, a media/API pattern, the download-selector shape, and the
    BAD_TERMS denylist), with the same conditional normalize-first for a raw
    builder draft. Lets the guided Promote step render a green "safe to promote"
    or a precise blocked field/term BEFORE the operator clicks, instead of a raw
    refusal after. Read-only: adds NO authority -- it only reports what the
    write-side promote already enforces.
    """
    body = request.json or {}
    fname = body.get("file") or ""
    try:
        from . import template_manager as tm
        from . import selector_lint as sl
        safe = tm._safe_name(fname, tm._DRAFT_SUFFIX)
        if not safe:
            return jsonify({"ok": False, "error": "invalid draft filename"}), 400
        src = tm.DRAFTS_DIR / safe
        if not src.is_file():
            return jsonify({"ok": False, "error": "draft not found"}), 404
        t = json.loads(src.read_text("utf-8"))
        # Same conditional normalize as promote_draft: a RAW builder draft is
        # normalized; an already-normalized candidate passes through untouched.
        _schema = str(t.get("schema_version") or t.get("schema") or "")
        if ("network_discovery" in t or "template_draft" in _schema
                or not isinstance(t.get("network_patterns"), list)):
            from .template_normalize import normalize_draft
            t = normalize_draft(t)
        issues = sl.lint_template(t)
        if sl.has_blocking_issues(issues):
            return jsonify({"ok": False,
                            "error": "draft has unsafe selectors; "
                                     "fix before promoting",
                            "lint_warnings": [i.to_dict() for i in issues]})
        gate = tm.promote_gate_errors(t)
        if gate:
            return jsonify({"ok": False, "error": gate[0], "gate_errors": gate})
        # 2c-guard: optional non-blocking live-trigger interlock. The FE may
        # pass the trigger's live match count (from /api/template/sandbox);
        # 0 -> a soft "stale trigger" warning that does NOT flip ok.
        gate_warnings = tm.promote_gate_warnings(
            t, trigger_match_count=body.get("trigger_match_count"))
        if gate_warnings:
            return jsonify({"ok": True, "gate_warnings": gate_warnings})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@template_manager_bp.route("/api/template_manager/disable", methods=["POST"])
def api_template_manager_disable():
    """#10 — Disable a reviewed template (status -> disabled, so it is no longer
    matched). Body: {"file": "<host>.template.json"}.
    """
    _check_csrf()
    body = request.json or {}
    try:
        from .template_manager import disable_reviewed
        res = disable_reviewed(body.get("file") or "")
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

# ── Item 4: F3.2 drift-repair sweep -- status + control surface ──────────────
@template_manager_bp.route("/api/automation/drift_repair")
def api_drift_repair_status():
    """Status of the daily drift->AI-repair sweep: whether the toggle is on, the
    last persisted sweep result (or None), and how many review-only drafts are
    pending. Read-only."""
    from . import drift_repair as _dr
    from . import global_config as _gc
    enabled = bool(_gc.get(_dr.ENABLE_KEY, False))
    last_run = _dr.read_last_run()
    drafts_pending = 0
    try:
        from . import template_manager as _tm
        drafts_pending = len(list(_tm.DRAFTS_DIR.glob("*.template-draft.json")))
    except Exception:
        drafts_pending = 0
    return jsonify({"ok": True, "enabled": enabled,
                    "last_run": last_run, "drafts_pending": drafts_pending})


@template_manager_bp.route("/api/automation/drift_repair/run", methods=["POST"])
def api_drift_repair_run():
    """Run the drift-repair sweep ON DEMAND. Body: ``{force?: bool}`` -- force
    runs it even when the daily toggle is off (so an operator can test without
    flipping the automation on). Lands the same REVIEW-ONLY drafts the scheduled
    sweep does; never enables a template. Returns the fresh summary."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))
    from . import drift_repair as _dr
    summary = _dr.scheduled_drift_repair(force=force)
    return jsonify({"ok": True, "summary": summary})


@template_manager_bp.route("/api/automation/drift_repair/toggle", methods=["POST"])
def api_drift_repair_toggle():
    """Set ``automation.drift_repair_enabled``. Body: ``{enabled: bool}``. The
    sweep is review-only/safe -- it only ever lands review-required drafts."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", False))
    from . import drift_repair as _dr
    from . import global_config as _gc
    _gc.set_config({_dr.ENABLE_KEY: enabled})
    return jsonify({"ok": True, "enabled": enabled})


def register_routes(app) -> int:
    app.register_blueprint(template_manager_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("template_manager."))

