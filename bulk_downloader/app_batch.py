"""batch API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/batch views moved onto a Flask Blueprint.
Endpoint labels gain a "batch." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

batch_bp = Blueprint("batch", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


# dsq-batch-ops F6: a non-object body answered 500 with the raw exception text (the
# body.get call sat inside the try), and dry_run "false" / 0 / null went through bool():
# the string ran as a dry run, null and 0 ran LIVE. F4: a filter the server cannot read
# answered 200 "matched 0", exactly like "nothing matched".
_BODY_ERR = "request body must be a JSON object"


def _json_body():
    """r2 (cx219 R2): `request.json or {}` turned an empty list into {} -- a 200 dry run for
    the [] the F6 fix promised to refuse. Only a JSON null (or no value) means "no body"."""
    body = request.json
    return {} if body is None else body


def _bad(msg):
    return jsonify({"ok": False, "error": msg}), 400


def _flag(body, key, default):
    """(value, error): absent -> default; only a JSON boolean is a flag."""
    if key not in body:
        return default, None
    v = body[key]
    return (v, None) if isinstance(v, bool) else (None, f"{key} must be true or false")


def _filter_body(body):
    """(filter, dry_run, error) shared by retry / delete / move. A non-object body never
    gets here as a 500: body.get now runs outside the try, so app._on_attribute_error
    answers 400 "request body must be a JSON object" (as it already did for move)."""
    from . import batch_ops as _bo
    flt = body.get("filter", {})
    err = _bo.filter_error(flt)
    if err:
        return None, None, err
    dry_run, err = _flag(body, "dry_run", True)
    return flt, dry_run, err


def _requeue_in_runners(by_site):
    """F1: hand the reset URLs to each site's runner (runner.bulk_retry re-queues the
    retryable ones in memory and in the queue table). Counts per outcome, in URLs."""
    import importlib
    runners = getattr(importlib.import_module("bulk_downloader.app_state"), "runners")
    got = {"requeued": 0, "no_runner": 0, "runner_skipped": 0}
    for sid, urls in by_site.items():
        runner = runners.get(sid) if sid else None
        if runner is None:
            got["no_runner"] += len(urls)
            continue
        n = int(runner.bulk_retry(urls) or 0)
        got["requeued"] += n
        got["runner_skipped"] += len(urls) - n
    return got


@batch_bp.route("/api/batch/retry", methods=["POST"])
def api_batch_retry():
    _check_csrf()
    body = _json_body()
    flt, dry_run, err = _filter_body(body)
    if err:
        return _bad(err)
    from . import batch_ops as _bo
    reset_to = body.get("reset_to_status", "pending")
    if reset_to not in _bo.RETRY_STATUSES:
        return _bad(f"reset_to_status must be one of {', '.join(_bo.RETRY_STATUSES)}")
    try:
        return jsonify(_bo.bulk_retry(
            flt, dry_run=dry_run, reset_to_status=reset_to,
            requeue=_requeue_in_runners,
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@batch_bp.route("/api/batch/delete", methods=["POST"])
def api_batch_delete():
    _check_csrf()
    body = _json_body()
    flt, dry_run, err = _filter_body(body)
    if err:
        return _bad(err)
    delete_files, err = _flag(body, "delete_files", False)
    if err:
        return _bad(err)
    try:
        from . import batch_ops as _bo
        return jsonify(_bo.bulk_delete(
            flt, dry_run=dry_run, delete_files=delete_files,
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@batch_bp.route("/api/batch/move", methods=["POST"])
def api_batch_move():
    _check_csrf()
    body = _json_body()
    target = body.get("target_dir", "") or ""
    if not target:
        return jsonify({"ok": False, "error": "target_dir required"}), 400
    flt, dry_run, err = _filter_body(body)
    if err:
        return _bad(err)
    try:
        from . import batch_ops as _bo
        return jsonify(_bo.bulk_move(
            flt, target_dir=target, dry_run=dry_run,
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@batch_bp.route("/api/batch/dedup_scan", methods=["POST"])
def api_batch_dedup():
    _check_csrf()
    body = _json_body()
    if not isinstance(body, dict):
        return _bad(_BODY_ERR)
    try:
        from . import batch_ops as _bo
        return jsonify(_bo.bulk_dedup_scan(
            site_id=body.get("site_id"),
            min_file_size_mb=int(body.get("min_file_size_mb", 50)),
            limit=int(body.get("limit", 5000)),
        ))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(batch_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("batch."))

