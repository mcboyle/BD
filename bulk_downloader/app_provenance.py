"""provenance API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/provenance views moved onto a Flask Blueprint.
Endpoint labels gain a "provenance." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

provenance_bp = Blueprint("provenance", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@provenance_bp.route("/api/provenance/query")
def api_provenance_query():
    """Query the ledger by url/filename/sha256/site/time range."""
    try:
        from . import provenance as _p
        kwargs = {}
        for k in ("url", "filename", "sha256", "site_id"):
            v = request.args.get(k)
            if v:
                kwargs[k] = v
        for k in ("ts_from", "ts_to"):
            v = request.args.get(k)
            if v:
                try:
                    kwargs[k] = float(v)
                except (TypeError, ValueError):
                    pass
        try:
            kwargs["limit"] = min(500, int(request.args.get("limit", 100)))
        except (TypeError, ValueError):
            kwargs["limit"] = 100
        return jsonify({"rows": _p.query(**kwargs)})
    except Exception as e:
        return jsonify({"rows": [], "error": str(e)[:200]}), 500

@provenance_bp.route("/api/provenance/verify", methods=["POST"])
def api_provenance_verify():
    """Run a full chain verification. Slow on large ledgers; intended
    to be invoked manually or from a nightly task, not on every poll."""
    _check_csrf()
    try:
        from . import provenance as _p
        return jsonify(_p.verify_chain())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@provenance_bp.route("/api/provenance/stats")
def api_provenance_stats():
    try:
        from . import provenance as _p
        return jsonify(_p.stats())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@provenance_bp.route("/api/provenance/digest")
def api_provenance_digest():
    """Row 1063. Chain digest of the local ledger for a peer/replica to
    reconcile against: checkpoints every ?checkpoint_every= ids (+ head,
    + ?want_ids=1,2,3). Read-only."""
    from . import ledger_reconcile as _lr
    try:
        every = int(request.args.get("checkpoint_every", _lr.DEFAULT_CHECKPOINT_EVERY))
        want = [int(x) for x in (request.args.get("want_ids") or "").split(",") if x.strip()]
    except ValueError as e:
        return jsonify({"ok": False, "error": f"bad query: {e}"}), 400
    try:
        with _lr.local_conn() as cx:
            d = _lr.digest_from_conn(cx, checkpoint_every=every, want_ids=want)
        return jsonify({"ok": True, "digest": d})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@provenance_bp.route("/api/provenance/reconcile", methods=["POST"])
def api_provenance_reconcile():
    """Row 1063. Body {digest: <peer digest>, checkpoint_every?}. Answers the
    verdict (in_sync|behind|ahead|forked|unknown) plus a local digest that
    includes the peer's checkpoint ids, so the peer can compute the same
    verdict. Read-only; POST only because it carries the peer digest."""
    refused = _check_csrf()   # returns a 403 response to refuse; it does not abort
    if refused is not None:
        return refused
    from . import ledger_reconcile as _lr
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "bad digest: body must be a JSON object"}), 400
    try:
        peer = _lr.validate_digest(body.get("digest"))
        every = int(body.get("checkpoint_every", _lr.DEFAULT_CHECKPOINT_EVERY))
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": f"bad digest: {e}"}), 400
    try:
        want = [i for i, _h in peer["checkpoints"]] + [peer["head_id"]]
        with _lr.local_conn() as cx:
            local = _lr.digest_from_conn(cx, checkpoint_every=every, want_ids=want)
        return jsonify({"ok": True, "verdict": _lr.reconcile(local, peer), "digest": local})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(provenance_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("provenance."))

