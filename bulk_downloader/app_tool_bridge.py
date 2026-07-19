"""app_tool_bridge -- the HTTP surface for the exec bridge (v3.66.717, Cut 7).

Two endpoints:
  GET  /api/tools/available  -> the allowlist, so a control surface can render it
  POST /api/tools/run        -> validate + execute an allowlisted (tool, flags) request

The POST is CSRF-gated like every mutating endpoint. All policy lives in tool_bridge
(the allowlist is data); this module is a thin, boring shell.
"""
from __future__ import annotations

import importlib

from flask import Blueprint, jsonify, request

tool_bridge_bp = Blueprint("tool_bridge", __name__)


def _check_csrf(*_a, **_k):
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@tool_bridge_bp.route("/api/tools/available", methods=["GET"])
def api_tools_available():
    from . import tool_bridge as tb

    return jsonify({"ok": True, "tools": tb.available()})


@tool_bridge_bp.route("/api/tools/run", methods=["POST"])
def api_tools_run():
    from . import tool_bridge as tb

    csrf = _check_csrf()
    if csrf is not None:
        return csrf  # the app's CSRF failure response (403/400)

    data = request.get_json(silent=True) or {}
    tool = data.get("tool")
    flags = data.get("flags") or {}
    if not isinstance(tool, str) or not isinstance(flags, dict):
        return jsonify({"ok": False, "error": "tool (str) and flags (object) required"}), 400

    try:
        result = tb.run(tool, flags)
    except tb.BridgeError as e:
        # a policy violation: unlisted tool, unlisted flag, bad value, path escape.
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, **result})


def register_routes(app) -> int:
    app.register_blueprint(tool_bridge_bp)
    return 2
