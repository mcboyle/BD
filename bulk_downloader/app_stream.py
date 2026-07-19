"""stream API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/stream views moved onto a Flask Blueprint.
Endpoint labels gain a "stream." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request, stream_with_context

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


@stream_bp.route("/api/stream/token/<int:hid>", methods=["POST"])
def api_stream_token(hid):
    _check_csrf()
    body = request.json or {}
    try:
        from . import stream_relay as _sr
        token = _sr.generate_token(hid,
            ttl_seconds=int(body.get("ttl_seconds", 3600)))
        return jsonify({"ok": True, "token": token, "history_id": hid})
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

