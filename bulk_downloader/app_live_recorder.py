"""v3.43.62: Flask routes for the live-stream recording runtime.

Wires `live_recorder` into the existing Flask app via 7 routes.
Permissions piggyback on the global before-request CSRF+auth hooks
in app.py, so this blueprint doesn't do its own auth checks.
"""
from __future__ import annotations

import logging
import sys
from flask import Blueprint, jsonify, request

from . import live_recorder

log = logging.getLogger(__name__)

live_recorder_bp = Blueprint("live_recorder", __name__)


def _err(msg: str, status: int = 400, **extra):
    """Compact error response builder."""
    body = {"ok": False, "error": msg}
    body.update(extra)
    resp = jsonify(body)
    resp.status_code = status
    return resp


@live_recorder_bp.route("/api/live/status", methods=["GET"])
def live_status():
    """Module-wide capability + current counts. Cheap, polled by the UI."""
    backends = {
        "streamlink": bool(live_recorder.preferred_backend() == "streamlink"
                           or live_recorder._detect_backends().get("streamlink")),
        "ffmpeg": bool(live_recorder._detect_backends().get("ffmpeg")),
    }
    recs = live_recorder.list_recordings(include_finished=False)
    counts: dict[str, int] = {}
    for r in recs:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return jsonify({
        "ok": True,
        "available": live_recorder.is_available(),
        "preferred_backend": live_recorder.preferred_backend(),
        "backends": backends,
        "active_count": len(recs),
        "max_active": live_recorder._max_active_recordings(),
        "counts": counts,
        "tunables": {
            "poll_interval_s": live_recorder._poll_interval_s(),
            "disconnect_tolerance_s": live_recorder._disconnect_tolerance_s(),
        },
    })


@live_recorder_bp.route("/api/live/recordings", methods=["GET"])
def live_recordings_list():
    """List all recordings (active + finished). Used by the panel."""
    include_finished = request.args.get("include_finished", "1") not in ("0", "false")
    return jsonify({
        "ok": True,
        "recordings": live_recorder.list_recordings(
            include_finished=include_finished
        ),
    })


@live_recorder_bp.route("/api/live/recordings/<recording_id>", methods=["GET"])
def live_recording_one(recording_id: str):
    """Single-recording detail. 404 if not found."""
    rec = live_recorder.get_recording(recording_id)
    if rec is None:
        return _err("not_found", 404)
    return jsonify({"ok": True, "recording": rec})


@live_recorder_bp.route("/api/live/watch", methods=["POST"])
def live_watch():
    """Arm a watch on a live URL. Body: {url, output_dir}. Optional:
    site_override, room_override (advanced users only)."""
    body = request.get_json(silent=True) or {}
    url = body.get("url")
    output_dir = body.get("output_dir")
    if not url or not isinstance(url, str):
        return _err("url_required")
    if not output_dir or not isinstance(output_dir, str):
        return _err("output_dir_required")
    site_override = body.get("site_override")
    room_override = body.get("room_override")
    if (site_override and not isinstance(site_override, str)) or \
       (room_override and not isinstance(room_override, str)):
        return _err("invalid_override_types")
    result = live_recorder.watch(
        url=url,
        output_dir=output_dir,
        site_override=site_override or None,
        room_override=room_override or None,
    )
    if not result.get("ok"):
        # Convert internal error code to HTTP status.
        err = result.get("error", "")
        if err in ("no_backend", "too_many_active"):
            return _err(err, 503, message=result.get("message"))
        if err == "already_watching":
            return _err(err, 409,
                        recording_id=result.get("recording_id"))
        return _err(err, 400, message=result.get("message"))
    return jsonify(result)


@live_recorder_bp.route("/api/live/unwatch", methods=["POST"])
def live_unwatch():
    """Cancel a watch. Body: {recording_id}."""
    body = request.get_json(silent=True) or {}
    rid = body.get("recording_id")
    if not rid or not isinstance(rid, str):
        return _err("recording_id_required")
    result = live_recorder.unwatch(rid)
    if not result.get("ok"):
        return _err(result.get("error", "unknown"), 404)
    return jsonify(result)


@live_recorder_bp.route("/api/live/parse_url", methods=["POST"])
def live_parse_url():
    """Helper: tell the UI whether a given URL is recognized as a
    live-cam URL, without arming a watch. Body: {url}."""
    body = request.get_json(silent=True) or {}
    url = body.get("url")
    if not url or not isinstance(url, str):
        return _err("url_required")
    parsed = live_recorder.parse_live_url(url)
    if parsed is None:
        return jsonify({"ok": True, "recognized": False})
    site, room = parsed
    return jsonify({"ok": True, "recognized": True, "site": site, "room": room})


def register_routes(app) -> int:
    """Register the live-recorder blueprint. Returns route count for
    startup-log accounting. Mirrors the captcha-relay blueprint pattern."""
    app.register_blueprint(live_recorder_bp)
    count = sum(1 for r in app.url_map.iter_rules()
                if r.endpoint.startswith("live_recorder."))
    # v3.43.78 (F1): use stderr to match the rest of the codebase's
    # startup-log style; the other route-registration helpers
    # (app_widgets_api, app_captcha_relay) do the same.
    sys.stderr.write(f"[live-recorder-api] registered {count} live recorder routes\n")
    return count


__all__ = ["register_routes", "live_recorder_bp"]
