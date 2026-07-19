"""app_automation_status -- the HTTP surface for the AF5 readout (v3.66.723).

One endpoint:
  GET /api/automation/status -> the restore-rehearsal verdict (706) and the
                                pipeline halt verdict (708)

GET only, and deliberately so: a readout is not a lever. All policy lives in
automation_status; this module is a thin, boring shell.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

automation_status_bp = Blueprint("automation_status", __name__)


@automation_status_bp.route("/api/automation/status", methods=["GET"])
def api_automation_status():
    from . import automation_status as st

    return jsonify(st.status())


def register_routes(app) -> int:
    app.register_blueprint(automation_status_bp)
    return 1
