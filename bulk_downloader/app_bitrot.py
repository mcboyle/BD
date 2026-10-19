"""bitrot API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/bitrot views moved onto a Flask Blueprint.
Endpoint labels gain a "bitrot." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

bitrot_bp = Blueprint("bitrot", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


# Highest accepted target_mbps (100 Gbit/s); above it pacing is off in effect.
TARGET_MBPS_MAX = 100000.0


class _BadParam(ValueError):
    """A request field that does not parse: a 400 naming the field (F13),
    never the conversion's exception text."""


def _number(body: dict, key: str, default, cast, *, positive: bool = False,
            maximum: float | None = None):
    """body[key] (``default`` when absent or null) through ``cast``; finite,
    > 0 when ``positive``, <= ``maximum`` when given, else _BadParam."""
    raw = body.get(key)
    if raw is None:
        return default
    try:
        if isinstance(raw, bool):
            raise ValueError(key)
        val = cast(raw)
    except (TypeError, ValueError, OverflowError):
        raise _BadParam(f"{key} must be a number") from None
    if not math.isfinite(val) or (positive and val <= 0) or (
            maximum is not None and val > maximum):
        raise _BadParam(f"{key} must be a finite number"
                        + (" > 0" if positive else "")
                        + (f" <= {maximum:g}" if maximum is not None else ""))
    return val


def _bound_dirs(dirs):
    """download_dirs from a request, each inside a configured download root
    (F11: ["/etc"] used to become the scrubber's walk); None stays None."""
    if dirs is None:
        return None
    from . import paths as _paths
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        raise _BadParam("download_dirs must be a list of paths")
    outside = [d for d in dirs if not _paths.within_download_roots(d)]
    if outside:
        raise _BadParam("download_dirs must lie inside a download root: "
                        + ", ".join(d[:120] for d in outside[:5]))
    return list(dirs)


def _handle_scrubber_action(scrubber, body: dict, action: str | None):
    target_mbps = _number(body, "target_mbps", None, float, positive=True,
                          maximum=TARGET_MBPS_MAX)
    starting = (action not in ("stop", "pause", "resume", "status")
                and (action == "start" or body.get("continuous")))
    if starting:
        download_dirs = _bound_dirs(body.get("download_dirs"))
        min_age = _number(body, "min_age_days", 7, int)
        reverify = _number(body, "reverify_after_days", 90, int)
        batch_size = _number(body, "batch_size", 25, int)
        idle_sleep = _number(body, "idle_sleep_seconds", 30.0, float)
    if target_mbps is not None and scrubber.pacer:
        scrubber.pacer.set_nominal_rate(target_mbps * 125000)
    if action == "stop":
        scrubber.stop()
    elif action == "pause":
        scrubber.pause()
    elif action == "resume":
        scrubber.resume()
    elif action == "status":
        pass
    elif starting:
        ok = scrubber.start(continuous=bool(body.get("continuous", True)), download_dirs=download_dirs, min_age_days=min_age, reverify_after_days=reverify, batch_size=batch_size, idle_sleep_seconds=idle_sleep)
        if not ok:
            return (jsonify({"ok": False, "action": action or "start", "error": "download roots unavailable", "scrubber": scrubber.status()}), 503)
    return (jsonify({"ok": True, "action": action or "start", "scrubber": scrubber.status()}), 200)

@bitrot_bp.route("/api/bitrot/scan", methods=["POST"])
def api_bitrot_scan():
    _check_csrf()
    body = request.json or {}
    try:
        from . import bitrot as _br
        action = body.get("action")
        if action or body.get("continuous"):
            scrubber = _br.get_scrubber()
            return _handle_scrubber_action(scrubber, body, action)
        result = _br.run_scan(scan_fraction=_number(body, "scan_fraction", 0.05, float), min_age_days=_number(body, "min_age_days", 7, int), max_files=_number(body, "max_files", 100, int))
        return (jsonify(result), 200 if result.get("available", True) else 503)
    except _BadParam as e:
        return (jsonify({"ok": False, "error": str(e)}), 400)
    except Exception as e:
        return (jsonify({"error": str(e)[:200]}), 500)

@bitrot_bp.route("/api/bitrot/issues")
def api_bitrot_issues():
    try:
        from . import bitrot as _br
        kind = request.args.get("kind") or None
        repaired = request.args.get("repaired")
        rep = None if repaired is None else (repaired in ("1", "true", "yes"))
        return jsonify({"issues": _br.list_issues(kind=kind, repaired=rep)})
    except Exception as e:
        return jsonify({
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "issues": None,
            "error": str(e)[:200],
        }), 503

@bitrot_bp.route("/api/bitrot/stats")
def api_bitrot_stats():
    try:
        from . import bitrot as _br
        result = _br.stats()
        return jsonify(result), (200 if result.get("available", True) else 503)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(bitrot_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("bitrot."))
