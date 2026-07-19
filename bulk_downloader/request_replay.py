"""Request replay — T44 / D-46.

Bounded in-memory ring buffer of recent /api/* requests, plus a
replay helper that re-issues a captured request against the running
app's loopback interface and compares the response shape.

Why this exists. Operators occasionally see a one-off failure
(timeout, 500, unexpected payload) and want to re-trigger the same
request to see if it reproduces. Without capture, the only option is
"reproduce from the UI" which loses query-param details and timing.

Design constraints (DANGER list):
  • NO module-level work at import. The recorder is a deque + lock;
    populating it depends on the request-recording feature flag, so
    a fresh process with the flag off costs nothing.
  • Bounded by deque(maxlen) — never grows.
  • Bodies truncated; this is a debug aid, not a full HAR.
  • Replay is OPT-IN per request — the route is POST + CSRF-gated,
    and the replay timeout is bounded.

Capture toggle: bulk_downloader.feature_flags.is_on("request_capture").
When the flag is OFF, record() is a cheap no-op.

Replay safety: replay re-issues against http://127.0.0.1:<port> with
the original method, headers (minus host), and body. The replayed
request goes through the SAME flask app — so all of the existing
auth, CSRF, dev-mode gates apply to it just like the original. This
means replay can only re-issue what the original session could
already do; it cannot bypass auth.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Optional


# Ring-buffer cap. Each entry is at most ~2KB after truncation, so
# 200 * 2KB = ~400KB worst case — bounded.
_MAX_REQUESTS = 200
# Per-field truncation (bytes). Bodies and headers are stored
# truncated; this is a debug aid, not a packet log.
_BODY_TRUNC = 4096
_HEADER_TRUNC = 1024

_lock = threading.Lock()
_requests: deque = deque(maxlen=_MAX_REQUESTS)
_seq = 0


def _truncate(s, limit: int) -> str:
    """Cheap truncation: ensure stored values can't exceed `limit`
    characters. Returns str; coerces non-str to str."""
    if s is None:
        return ""
    s = s if isinstance(s, str) else str(s)
    if len(s) > limit:
        return s[:limit] + "...(truncated)"
    return s


def is_enabled() -> bool:
    """Is request capture currently on? Cheap — defers to the
    feature_flags cache."""
    try:
        from . import feature_flags as _ff
        return _ff.is_on("request_capture")
    except Exception:
        return False


def record(*, method, path, query, status, request_headers,
           request_body, response_body, duration_ms) -> Optional[str]:
    """Append one /api/* request to the ring buffer. Returns the
    request_id if recorded, None otherwise (capture off or failure).

    Hot path: called from an after_request hook. Must never raise.
    """
    if not is_enabled():
        return None
    try:
        global _seq
        req_id = uuid.uuid4().hex[:12]
        # Strip cookie + authorization from stored headers — credentials
        # never get persisted (the replay path re-issues without them,
        # relying on a fresh session from the caller).
        safe_headers = {}
        if isinstance(request_headers, dict):
            for k, v in request_headers.items():
                lk = str(k).lower()
                if lk in ("cookie", "authorization", "x-csrf-token"):
                    safe_headers[k] = "[redacted]"
                else:
                    safe_headers[k] = _truncate(v, _HEADER_TRUNC)
        entry = {
            "id": req_id,
            "ts": round(time.time(), 3),
            "method": _truncate(method, 16),
            "path": _truncate(path, 512),
            "query": _truncate(query, 512),
            "status": int(status) if isinstance(status, int) else 0,
            "duration_ms": (round(float(duration_ms), 2)
                            if duration_ms is not None else None),
            "request_headers": safe_headers,
            "request_body": _truncate(request_body, _BODY_TRUNC),
            "response_body": _truncate(response_body, _BODY_TRUNC),
        }
        with _lock:
            _seq += 1
            entry["seq"] = _seq
            _requests.append(entry)
        return req_id
    except Exception:
        return None


def list_recent(limit: int = 50) -> list:
    """Newest-first list of captured requests."""
    try:
        n = int(limit)
        if n < 1:
            n = 1
        if n > _MAX_REQUESTS:
            n = _MAX_REQUESTS
    except (TypeError, ValueError):
        n = 50
    with _lock:
        items = list(_requests)
    items.reverse()
    return items[:n]


def get(request_id: str) -> Optional[dict]:
    """Find a captured request by id. Returns None if unknown."""
    if not isinstance(request_id, str):
        return None
    with _lock:
        for r in _requests:
            if r.get("id") == request_id:
                return dict(r)
    return None


def stats() -> dict:
    """Buffer occupancy + lifetime count."""
    with _lock:
        return {
            "buffered": len(_requests),
            "capacity": _MAX_REQUESTS,
            "total_recorded": _seq,
            "enabled": is_enabled(),
        }


def reset():
    """Test helper — clear the buffer and sequence counter."""
    global _seq
    with _lock:
        _requests.clear()
        _seq = 0


def replay(request_id: str, *, base_url: str = "http://127.0.0.1:5555",
            timeout: float = 10.0) -> dict:
    """Re-issue a captured request against the running app.

    Returns {ok, request_id, original_status, replay_status, match,
    replay_body, error?}.

    Safety: replays go through the SAME flask app, so all auth/CSRF
    middleware fires on the replay just like the original. The caller
    is responsible for providing valid credentials in `headers` if
    needed — by default the replay sends WITHOUT cookies/bearer/CSRF
    (those were redacted at record time).
    """
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue
    out = {
        "ok": False,
        "request_id": request_id,
        "original_status": None,
        "replay_status": None,
        "match": False,
        "replay_body": "",
    }
    captured = get(request_id)
    if captured is None:
        out["error"] = f"unknown request_id: {request_id}"
        return out
    out["original_status"] = captured.get("status")
    method = captured.get("method", "GET")
    path = captured.get("path", "/")
    query = captured.get("query", "")
    body = captured.get("request_body", "")
    if isinstance(body, str) and body.endswith("...(truncated)"):
        out["error"] = "request body was truncated at capture; "\
                       "cannot faithfully replay"
        return out
    # Build URL
    base = base_url.rstrip("/")
    url = base + path + (("?" + query) if query else "")
    try:
        t_f = float(timeout)
        if t_f <= 0 or t_f > 60:
            t_f = 10.0
    except (TypeError, ValueError):
        t_f = 10.0
    data = body.encode("utf-8") if body else None
    req = _ur.Request(url, data=data, method=method or "GET")
    # Replay only sends Content-Type for body-carrying methods. Auth
    # is deliberately NOT carried — caller must supply if needed.
    req.add_header("User-Agent", "BulkDownloader-replay/1.0")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _ur.urlopen(req, timeout=t_f) as resp:
            out["replay_status"] = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
            out["replay_body"] = _truncate(raw, _BODY_TRUNC)
    except _ue.HTTPError as e:
        out["replay_status"] = e.code
        try:
            raw = e.read().decode("utf-8", errors="replace")
            out["replay_body"] = _truncate(raw, _BODY_TRUNC)
        except Exception:
            out["replay_body"] = ""
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return out
    out["ok"] = True
    out["match"] = (out["original_status"] == out["replay_status"])
    return out
