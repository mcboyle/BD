"""stream API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/stream views moved onto a Flask Blueprint.
Endpoint labels gain a "stream." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

stream_bp = Blueprint("stream", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _dashboard_snapshot(*_a, **_k):
    """Delegate to app._dashboard_snapshot at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_dashboard_snapshot")(*_a, **_k)

def _status_snapshot(*_a, **_k):
    """Delegate to app._status_snapshot at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_status_snapshot")(*_a, **_k)

# Per-client cap on /api/stream: each SSE connection holds one WSGI worker
# for its whole life, so over the cap answer 429 rather than hold another.
# Hooks, not code in api_stream: the slot is taken before the Response exists
# and released when the server closes it (call_on_close), even if the
# generator never ran.
@stream_bp.before_request
def _stream_slot_acquire():
    if request.endpoint != "stream.api_stream":
        return None
    from . import sse_broker as _sse
    slot = _sse.get_broker().acquire_stream(request.remote_addr or "")
    if slot is None:
        resp = jsonify({"ok": False,
                        "error": "too many open streams from this client",
                        "limit": _sse.STREAMS_PER_CLIENT_MAX})
        resp.status_code = 429
        resp.headers["Retry-After"] = "30"
        return resp
    g._stream_slot = slot
    return None

@stream_bp.after_request
def _stream_slot_bind(resp):
    slot = g.pop("_stream_slot", None)
    if slot is not None:
        resp.call_on_close(slot.release)
    return resp

@stream_bp.teardown_request
def _stream_slot_teardown(_exc):
    # Only reached with a slot when after_request did not run.
    slot = g.pop("_stream_slot", None)
    if slot is not None:
        slot.release()


@stream_bp.route("/api/stream/token/<int:hid>", methods=["POST"])
def api_stream_token(hid):
    _check_csrf()
    body = request.json or {}
    from . import stream_relay as _sr
    # stream-token F3: ttl_seconds / max_uses must be positive JSON integers;
    # anything else is the caller's error (400), never an exception echo.
    ttl = body.get("ttl_seconds", 3600) if isinstance(body, dict) else None
    max_uses = (body.get("max_uses", _sr.DEFAULT_MAX_USES)
                if isinstance(body, dict) else None)
    for name, val in (("ttl_seconds", ttl), ("max_uses", max_uses)):
        if name == "max_uses" and val is None:
            continue
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            return jsonify({"ok": False,
                            "error": f"{name} must be a positive integer"}), 400
    try:
        from . import db as _db
        # dsw-fix F2: a token is a read grant for one row's file; never mint
        # one for a row that does not exist.
        with _db.db_conn() as cx:
            row = cx.execute("SELECT status FROM history WHERE id = ?",
                             (hid,)).fetchone()
        if row is None:
            return jsonify({"ok": False,
                            "error": f"history row {hid} not found"}), 404
        # stream-token F12: only a completed download has a whole file to
        # stream; failed / paused / running rows hold a partial or no file.
        if row[0] != "done":
            return jsonify({"ok": False,
                            "error": f"history row {hid} is not completed"}), 409
        token = _sr.generate_token(hid, ttl_seconds=ttl, max_uses=max_uses)
        return jsonify({"ok": True, "token": token, "history_id": hid,
                        "ttl_seconds": min(ttl, _sr.MAX_TTL_SECONDS),
                        "max_uses": max_uses})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
@stream_bp.route("/api/stream/rotate_secret", methods=["POST"])
def api_stream_rotate_secret():
    """Rotate the stream-token signing secret. Invalidates every
    currently-issued token in one shot. Use as a 'panic button' when
    a shared URL may have leaked.

    No revocation list is maintained (stateless tokens by design);
    rotation is the bulk-invalidate primitive."""
    _check_csrf()
    try:
        from .global_config import set_config
        import secrets
        new_sec = secrets.token_urlsafe(32)
        set_config({"stream_token_secret": new_sec})
        return jsonify({"ok": True,
                        "message": ("All previously-issued stream tokens "
                                    "have been invalidated.")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
@stream_bp.route("/api/stream")
def api_stream():
    import time as _t
    import json as _json
    from . import sse_broker as _sse
    def gen():
        # Initial push so the client renders immediately rather than
        # waiting up to a full poll interval for the first event.
        try:
            yield f"event: dashboard\ndata: {_json.dumps(_dashboard_snapshot())}\n\n"
            yield f"event: status\ndata: {_json.dumps(_status_snapshot(light=True))}\n\n"
        except Exception: pass
        # Register this connection as a subscriber. The broker
        # delivers events synchronously into our queue.
        broker = _sse.get_broker()
        sub = broker.subscribe()
        # Periodic dashboard refresh — not driven by mutations because
        # the dashboard aggregates state across all sites and "nothing
        # changed for 2.5s" is itself useful information. Spawn a
        # background pusher that calls publish() on a schedule.
        # We piggyback on the same connection's pull loop rather than
        # adding a separate thread per connection.
        last_dashboard = 0.0
        last_heartbeat = _t.time()
        try:
            while True:
                # Pull next event from the broker (blocks up to 5s).
                # If timeout, the heartbeat/dashboard logic below
                # still gets a chance to fire.
                msg = broker.pull(sub, timeout=5.0)
                if msg is not None:
                    yield msg
                now = _t.time()
                # Dashboard refresh on a 2.5s cadence regardless of
                # other events. Sent inline (no broker publish needed,
                # this subscriber is the only consumer).
                if now - last_dashboard >= 2.5:
                    try:
                        yield (f"event: dashboard\ndata: "
                                f"{_json.dumps(_dashboard_snapshot(), default=str)}\n\n")
                        last_dashboard = now
                    except Exception: pass
                # Heartbeat comment — keeps reverse-proxy timeouts at bay.
                # SSE comments (lines starting with ":") are ignored by the
                # browser but reset proxy idle timers.
                if now - last_heartbeat >= 15:
                    yield f": heartbeat {int(now)}\n\n"
                    last_heartbeat = now
        except Exception as e:
            try:
                yield (f"event: error\ndata: "
                        f"{_json.dumps({'message': str(e)[:200]})}\n\n")
            except Exception: pass
        finally:
            # AUDIT v3.43.46: previously, unsubscribe lived in the
            # `except GeneratorExit` and `except Exception` branches
            # but not in a finally. A BaseException (e.g. system
            # exit propagating, or future asyncio cancellation) would
            # leak the subscriber. `finally` guarantees cleanup.
            try:
                broker.unsubscribe(sub)
            except Exception: pass
    resp = Response(stream_with_context(gen()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"   # tell nginx-style proxies not to buffer
    # v3.66.794 (F0.4): do NOT set Connection -- it is a hop-by-hop header owned
    # by the WSGI server (PEP 3333). waitress ENFORCES this and 500s the stream
    # ("Connection is a hop-by-hop header"); werkzeug's dev server tolerated it.
    # Keep-alive is the HTTP/1.1 default and the server manages it regardless.
    return resp

def register_routes(app) -> int:
    app.register_blueprint(stream_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("stream."))

