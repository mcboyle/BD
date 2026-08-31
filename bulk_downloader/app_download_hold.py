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


def _pause_every_runner() -> dict:
    """Pause every RUNNING pool now, and report EFFECTS rather than attempts.

    The durable record is what survives a restart; this is what makes the hold
    take effect in THIS process without waiting for one.

    Row 451: the old loop counted every runner whose ``pause()`` did not raise.
    ``pause()`` is state-gated -- it acts only when ``_state == "running"`` and
    otherwise returns ``None`` without raising -- so six idle runners and zero
    running reported ``paused_runners: 6``. A count of attempts presented as a
    count of effects, and precisely the number that hid row 433's mid-start
    runner. So each runner is classified by what MEASURABLY happened to it:

      paused              was running, and is no longer running afterwards
      already_not_running was not running before the call (nothing to stop)
      unknown             pause() raised, its state could not be read, or it
                          was running and STILL is -- an unestablished effect
                          is UNKNOWN, never counted as done (CLAUDE.md A7)

    Enumeration failure is UNKNOWN too (``enumerated: False``), not "0 paused,
    all clear": the old ``return 0`` reported a fleet it never looked at as a
    fleet with nothing to stop.
    """
    report = {"paused": 0, "already_not_running": 0, "unknown": 0,
              "total": 0, "enumerated": False}
    try:
        items = list(_runners().items())
    except Exception:
        return report
    report["enumerated"] = True
    report["total"] = len(items)
    for _sid, runner in items:
        try:
            before = getattr(runner, "_state", None)
        except Exception:
            report["unknown"] += 1
            continue
        if before != "running":
            report["already_not_running"] += 1
            continue
        try:
            runner.pause()
        except Exception:
            report["unknown"] += 1
            continue
        try:
            after = getattr(runner, "_state", None)
        except Exception:
            report["unknown"] += 1
            continue
        if after == "running":
            # pause() returned cleanly and changed nothing. Whatever that is,
            # it is not a stopped pool.
            report["unknown"] += 1
        else:
            report["paused"] += 1
    return report


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
    """Record a durable hold, then stop any running pool.

    Row 433: the record and the runtime walk happen inside ``_dh.barrier()``,
    which ``runner.start()`` and ``runner.resume()`` also take across their
    hold re-read and their transition to "running". That makes the hold a
    BARRIER rather than a snapshot: a runner mid-start either completes its
    transition before this walk (and is therefore seen as running and paused)
    or reaches its transition after the record is written (and refuses). It
    can no longer arm a pool in the gap between the two.
    """
    _check_csrf()
    body = _body()
    reason = str(body.get("reason") or _dh.DEFAULT_REASON)[:200]
    note = str(body.get("note") or "")[:1000]
    by = str(body.get("by") or "")[:200]
    with _dh.barrier():
        written = _dh.hold(reason, note, by=by)
        if not written:
            return jsonify({"ok": False, "error": "hold not persisted",
                            "state": _dh.hold_state()["state"]}), 500
        report = _pause_every_runner()
    state = _dh.hold_state()
    # The durable record IS written by this point -- that is the contract that
    # survives a restart, and it is reported either way. But an unestablished
    # runtime pause must not read as success: "ok" here means "the hold is
    # recorded AND every runner's disposition was measured".
    unresolved = report["unknown"] > 0 or not report["enumerated"]
    payload = {"ok": not unresolved,
               "paused_runners": report["paused"],
               "runners_total": report["total"],
               "runners_already_not_running": report["already_not_running"],
               "runners_pause_unknown": report["unknown"],
               "runners_enumerated": report["enumerated"],
               **_dh.health_block(state)}
    if unresolved:
        payload["error"] = "runner_pause_unknown"
    return jsonify(payload)


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
