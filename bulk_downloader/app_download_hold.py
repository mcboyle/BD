"""download_hold API -- the DURABLE operator hold on downloading (row 390).

/api/pause_all is a RUNTIME call: it reaches runner.pause() and lives only in
process memory, so a crash, a reboot, systemd RestartSec, or scripts/deploy.sh
(which restarts the app on every deployment) silently re-arms downloading. These
routes record the same intent in app_config.json, where it survives a restart
and is re-applied by runner.start()/resume().

Shaped like app_pause_all.py: a Blueprint with lazy app delegates so the module
never imports app.py at import time (import-cycle safety).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import download_hold as _dh

download_hold_bp = Blueprint("download_hold", __name__)


def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


def _runners():
    """The live shared runners dict (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


def _pause_every_runner() -> int:
    """Best-effort runtime pause so an ALREADY RUNNING pool stops now.

    The durable record is what survives the restart; this is what makes the
    hold take effect in THIS process without waiting for one. Per-runner
    failures are swallowed -- the durable record is the contract, and it is
    written before this runs.
    """
    paused = 0
    try:
        items = list(_runners().items())
    except Exception:
        return 0
    for _sid, runner in items:
        try:
            runner.pause()
            paused += 1
        except Exception:
            continue
    return paused


def _body() -> dict:
    try:
        data = request.get_json(silent=True)
    except Exception:
        data = None
    return data if isinstance(data, dict) else {}


@download_hold_bp.route("/api/download_hold", methods=["GET"])
def api_download_hold_get():
    """Report HELD / CLEAR / UNKNOWN. Read-only; never mutates the store."""
    state = _dh.hold_state()
    return jsonify({"ok": state["state"] != _dh.UNKNOWN,
                    **_dh.health_block(state)})


@download_hold_bp.route("/api/download_hold", methods=["POST"])
def api_download_hold_set():
    """Record a durable hold, then pause any running pool."""
    _check_csrf()
    body = _body()
    reason = str(body.get("reason") or _dh.DEFAULT_REASON)[:200]
    note = str(body.get("note") or "")[:1000]
    by = str(body.get("by") or "")[:200]
    written = _dh.hold(reason, note, by=by)
    if not written:
        return jsonify({"ok": False, "error": "hold not persisted",
                        "state": _dh.hold_state()["state"]}), 500
    paused = _pause_every_runner()
    state = _dh.hold_state()
    return jsonify({"ok": True, "paused_runners": paused,
                    **_dh.health_block(state)})


@download_hold_bp.route("/api/download_hold/lift", methods=["POST"])
def api_download_hold_lift():
    """Explicitly lift the hold. The lift is itself durable (held: false)."""
    _check_csrf()
    body = _body()
    note = str(body.get("note") or "")[:1000]
    by = str(body.get("by") or "")[:200]
    written = _dh.lift(note, by=by)
    if not written:
        return jsonify({"ok": False, "error": "lift not persisted",
                        "state": _dh.hold_state()["state"]}), 500
    state = _dh.hold_state()
    return jsonify({"ok": True, **_dh.health_block(state)})


def register_routes(app) -> int:
    app.register_blueprint(download_hold_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("download_hold."))
