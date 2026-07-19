"""retry_policy API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/retry_policy views moved onto a Flask Blueprint.
Endpoint labels gain a "retry_policy." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

retry_policy_bp = Blueprint("retry_policy", __name__)


@retry_policy_bp.route("/api/retry_policy")
def api_retry_policy():
    """Show the configured backoff curves per failure class. Used
    by the docs/help UI; helps users understand why a particular URL
    isn't retrying as fast (or as often) as expected."""
    try:
        from bulk_downloader import retry_policy as _rp
        result = {"classes": {}}
        for cls in _rp.get_all_classes():
            cfg = _rp.get_class_config(cls)
            # Compute the actual delay sequence (no jitter — easier
            # to read in the UI)
            delays = []
            for attempt in range(cfg["max_attempts"]):
                d = _rp.compute_next_delay(cls, attempt,
                                             apply_jitter=False)
                if d > 0:
                    delays.append(d)
            result["classes"][cls] = {
                "config": cfg,
                "delays_seconds": delays,
                "total_window_seconds": sum(delays),
            }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@retry_policy_bp.route("/api/retry_policy/classify", methods=["POST"])
def api_retry_policy_classify():
    """Test the classifier with a sample error message and/or HTTP
    status. Body: {message, status_code}. Returns the predicted
    class. Useful for debugging 'why was my URL marked permanent'."""
    data = request.get_json(silent=True) or {}
    try:
        from bulk_downloader import retry_policy as _rp
        cls = _rp.classify_failure(
            message=data.get("message", ""),
            status_code=data.get("status_code"))
        cfg = _rp.get_class_config(cls)
        # Show what would happen for the first 3 attempts
        sample = []
        for attempt in range(min(3, cfg["max_attempts"])):
            d = _rp.compute_next_delay(cls, attempt, apply_jitter=False)
            sample.append({"attempt": attempt + 1, "delay_seconds": d})
        return jsonify({
            "class": cls,
            "config": cfg,
            "sample_delays": sample,
        })
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

def register_routes(app) -> int:
    app.register_blueprint(retry_policy_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("retry_policy."))

