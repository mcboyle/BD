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

import json
import re
import threading
import time
import uuid
from collections import deque
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from .capture_redact import is_secret_header, redact_query


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

# request-capture-redact. What a masked value is stored as.
_REDACTED = "[redacted]"
# Route families whose request AND response bodies are never stored: a body
# there IS a credential (a login / vault / pairing secret). A route is in the
# family when one of its static path segments has a "-"/"_"/"."-separated
# word starting with a stem below. The concrete route set is derived from the
# app's url_map by :func:`withheld_rules`, never listed by hand.
_WITHHELD_STEMS = ("auth", "secret", "pair", "vault", "login", "relogin",
                   "cookie", "credential", "password")
# Bodies larger than this are not parsed for masking; they are not stored.
_MASK_PARSE_LIMIT = 1 << 20
_MASK_DEPTH = 64
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*")


def _rule_is_withheld(rule) -> bool:
    """True iff a url_map rule (or a request path) belongs to a credential
    route family (``_WITHHELD_STEMS``). ``<converter>`` segments are skipped."""
    if not isinstance(rule, str):
        return False
    for seg in rule.lower().split("/"):
        if not seg or seg.startswith("<"):
            continue
        for word in re.split(r"[-_.]+", seg):
            if word.startswith(_WITHHELD_STEMS):
                return True
    return False


def withheld_rules(url_map) -> frozenset:
    """The /api/ rules of ``url_map`` whose bodies are never captured."""
    return frozenset(r.rule for r in url_map.iter_rules()
                     if r.rule.startswith("/api/") and _rule_is_withheld(r.rule))


# r2 (PM A3): short credential keys the config-domain class does not name.
# Compared after case folding with '-' read as '_'.
_EXTRA_SECRET_KEYS = frozenset({"pwd", "pin", "otp", "api_key", "x_api_key"})


def _secret_key(k) -> bool:
    """A JSON / form / multipart KEY whose value is a credential (password,
    passphrase, secret, token, api_key, pwd, pin, otp, ...): the shared
    config-domain SoT, also on the case-folded '-'->'_' form, plus
    _EXTRA_SECRET_KEYS."""
    from .site_editor import is_secret_config_key
    if is_secret_config_key(k):
        return True
    if not isinstance(k, str):
        return False
    norm = k.strip().casefold().replace("-", "_")
    return norm in _EXTRA_SECRET_KEYS or is_secret_config_key(norm)


def _mask_keys(obj, depth: int = 0):
    """Copy of a parsed JSON value with every secret-keyed value masked, at any
    depth."""
    if depth > _MASK_DEPTH:
        return _REDACTED
    if isinstance(obj, dict):
        return {k: (_REDACTED if _secret_key(k) else _mask_keys(v, depth + 1))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_keys(v, depth + 1) for v in obj]
    return obj


def _mask_pairs(text: str):
    """Mask secret-keyed values of an urlencoded string. None when ``text``
    is not urlencoded."""
    try:
        pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if not pairs:
        return None
    if not any(_secret_key(k) for k, _v in pairs):
        return text
    return urlencode([(k, _REDACTED if _secret_key(k) else v)
                      for k, v in pairs])


def _mask_body(body) -> str:
    """A body as it may be stored: JSON and urlencoded bodies with every
    secret-keyed value masked; other text kept unless it names a secret
    field (then withheld whole -- it could not be parsed to mask)."""
    if body is None:
        return ""
    text = body if isinstance(body, str) else str(body)
    if not text:
        return text
    if len(text) > _MASK_PARSE_LIMIT:
        return f"<body withheld: {len(text)} chars, too large to mask>"
    if text.lstrip()[:1] in ("{", "["):
        try:
            return json.dumps(_mask_keys(json.loads(text)), ensure_ascii=False)
        except (ValueError, RecursionError):
            pass
    elif "=" in text and not any(c in text for c in " \r\n\t"):
        masked = _mask_pairs(text)
        if masked is not None:
            return masked
    if any(_secret_key(w) for w in _WORD_RE.findall(text)):
        return "<body withheld: unparsed and names a secret field>"
    return text


def _mask_query(query) -> str:
    """The query string as it may be stored: every secret-keyed value masked
    by the canonical capture_redact.redact_query, whatever the shape (r2 F1:
    no local parse that can fail open)."""
    text = query if isinstance(query, str) else ("" if query is None else str(query))
    if not text:
        return text
    return redact_query("?" + text)[1:]


def _mask_headers(headers) -> dict:
    """Header values masked by the shared secret-header class
    (capture_redact.is_secret_header); names kept. A URL-valued header keeps
    its URL with the query's secret values scrubbed."""
    out = {}
    if not isinstance(headers, dict):
        return out
    for k, v in headers.items():
        if is_secret_header(k):
            out[k] = _REDACTED
            continue
        if isinstance(v, str) and v[:5].lower().startswith(("http:", "https")):
            v = redact_query(v)
        out[k] = _truncate(v, _HEADER_TRUNC)
    return out


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
           request_body, response_body, duration_ms,
           rule=None) -> Optional[str]:
    """Append one /api/* request to the ring buffer. Returns the
    request_id if recorded, None otherwise (capture off or failure).

    ``rule`` is the matched url_map rule. A credential route (rule or path,
    :func:`_rule_is_withheld`) stores neither body; every other body is
    stored with its secret-keyed values masked. Header values are masked by
    the shared secret-header class.

    Hot path: called from an after_request hook. Must never raise.
    """
    if not is_enabled():
        return None
    try:
        global _seq
        req_id = uuid.uuid4().hex[:12]
        # Credentials never get stored (the replay path re-issues without
        # them, relying on a fresh session from the caller).
        safe_headers = _mask_headers(request_headers)
        withheld = _rule_is_withheld(rule) or _rule_is_withheld(path)
        if withheld:
            req_body = resp_body = ""
        else:
            req_body = _mask_body(request_body)
            resp_body = _mask_body(response_body)
        entry = {
            "id": req_id,
            "ts": round(time.time(), 3),
            "method": _truncate(method, 16),
            "path": _truncate(path, 512),
            "query": _truncate(_mask_query(query), 512),
            "status": int(status) if isinstance(status, int) else 0,
            "duration_ms": (round(float(duration_ms), 2)
                            if duration_ms is not None else None),
            "request_headers": safe_headers,
            "request_body": _truncate(req_body, _BODY_TRUNC),
            "response_body": _truncate(resp_body, _BODY_TRUNC),
            "body_withheld": withheld,
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
    # Defence in depth: what leaves the ring for a reader also passes the
    # canonical capture-artifact floor. Fail closed if it cannot load.
    try:
        from .capture_artifact_redact import redact_artifact
    except Exception:
        return []
    return [redact_artifact(i) for i in items[:n]]


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


# r2 (PM F2a): every replayed request carries this header; the request_capture
# flag change refuses a request bearing it (app_dev_config).
REPLAY_MARKER_HEADER = "X-BD-Replay"


def _carries_marker(body: str) -> bool:
    """True iff ``body`` holds the redaction marker, raw or URL-encoded (to a
    few levels, either hex case) -- a masked body must never be replayed."""
    text = body
    for _ in range(4):
        if _REDACTED in text:
            return True
        nxt = unquote(text.replace("+", " "))
        if nxt == text:
            return False
        text = nxt
    return _REDACTED in text


def _targets_self(base_url: str) -> bool:
    """True iff ``base_url`` addresses this box: a loopback / unspecified
    address in any IPv4 spelling, localhost, or a name / address that resolves
    to one, or to an address bound on a local interface. Unresolvable = True
    (fail closed)."""
    import ipaddress
    import socket
    try:
        host = (urlsplit(base_url).hostname or "").rstrip(".").casefold()
    except ValueError:
        return True
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    # getaddrinfo reads every IPv4 spelling (127.1, 0x7f.1, 2130706433) as
    # inet_aton does, and is what the connection itself resolves with.
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return True
    for fam, _t, _p, _c, sa in infos:
        try:
            ip = ipaddress.ip_address(sa[0].split("%")[0])
        except ValueError:
            return True
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
            fam = socket.AF_INET
        if ip.is_loopback or ip.is_unspecified:
            return True
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as so:
                so.bind((str(ip), 0))
            return True       # bindable here = a local interface address
        except OSError:
            pass
    return False


def _caller_has_master_token() -> bool:
    """True iff the CURRENT request presents an accepted server token
    (app._request_has_master_token). No request context = False."""
    try:
        from flask import has_request_context
        if not has_request_context():
            return False
        import importlib
        return bool(importlib.import_module(
            "bulk_downloader.app")._request_has_master_token())
    except Exception:  # noqa: BLE001 -- fail closed
        return False


class _SelfRedirectRefused(Exception):
    """A replay's redirect pointed at this box without the master token."""


def _self_hop_gate(url: str) -> None:
    """r2 (PM F2b): a redirect is a new target -- one aimed at this box needs
    the master token, as the first hop does."""
    if _targets_self(url) and not _caller_has_master_token():
        raise _SelfRedirectRefused(url)


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
    if captured.get("body_withheld"):
        out["error"] = "request body was withheld at capture (credential "\
                       "route); cannot faithfully replay"
        return out
    if isinstance(body, str) and (_carries_marker(body)
                                  or body.startswith("<body withheld")):
        out["error"] = "request body was masked at capture; "\
                       "cannot faithfully replay"
        return out
    if isinstance(body, str) and body.endswith("...(truncated)"):
        out["error"] = "request body was truncated at capture; "\
                       "cannot faithfully replay"
        return out
    # r2 (PM F2b): a replay aimed at this box arrives from loopback, so it
    # could launder loopback-only gates; it needs the master token.
    if _targets_self(base_url) and not _caller_has_master_token():
        out["error"] = "a replay to this box needs the master token"
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
    req.add_header(REPLAY_MARKER_HEADER, "1")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        # BD-SEC-3 r3: base_url comes from the request and the body goes back to
        # the caller. Resolved once, every answer judged, the socket pinned to the
        # vetted literal, each redirect hop judged again (loopback/LAN admitted:
        # the replay target is normally this app; link-local/metadata refused).
        # r9: each hop also passes the F2b self-redirect gate.
        from . import outbound_guard as _outbound_guard
        with _outbound_guard.urlopen(req, timeout=t_f,
                                     hop_gate=_self_hop_gate) as resp:
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
    except _SelfRedirectRefused:
        out["error"] = "a replay redirected to this box needs the master token"
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return out
    out["ok"] = True
    out["match"] = (out["original_status"] == out["replay_status"])
    return out
