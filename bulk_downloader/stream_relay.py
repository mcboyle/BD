"""Stream relay (Phase 132, Block Q).

Lets the operator stream their library on the go: BD acts as an HTTP
range-proxy in front of the local files, so a mobile player can hit
the BD URL and seek/scrub like a normal video CDN.

Why not just serve files via Flask's send_file? Two reasons:

  1. Per-request access control is easier via this module: each stream
     URL carries a short-lived signed token naming ONE history row.
     The token is a second factor, not a credential on its own: when the
     app has an auth token configured, /stream/<token> still requires the
     normal app authentication (session / bearer) and answers 401 without
     it. The token narrows what an authenticated caller can fetch; it
     never stands in for authentication.

  2. We can rewrite the byte range to the underlying ffmpeg or to a
     transcoded variant if requested — useful if the source is
     2160p HEVC and the phone wants 720p H.264.

Token scheme:
  • generate_token(history_id, *, ttl_seconds=3600, max_uses=None) →
    "v1.<history_id>.<expires>.<sig>", or with a use limit
    "v2.<history_id>.<expires>.<max_uses>.<sig>" (the limit is signed).
    ttl_seconds must be a positive int and is capped at MAX_TTL_SECONDS.
  • verify_token(token) → {ok, history_id, expires, error}. Each
    successful verify of a v2 token consumes one use; past max_uses it
    answers "use limit reached". Use counts live in this process only, so
    a restart resets them (the TTL cap still bounds the token).
  • Tokens are signed with stream_token_secret, persisted in
    global_config: tokens survive a restart. POST /api/stream/rotate_secret
    replaces the secret and so revokes every issued token.

Range serving:
  • serve_range(filename, range_header) — yields bytes for a Flask
    Response. Supports the standard "bytes=START-END" syntax. The body
    never exceeds the Content-Length it announces. A relative filename (the
    bare basename history records) is resolved against the download roots
    by resolve_history_file and must stay inside them.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from typing import Optional

# Longest lifetime a stream token may be minted with (24 h). Longer requests
# are clamped, not refused.
MAX_TTL_SECONDS = 24 * 3600
# Use limit applied when the caller names none. None = unlimited (playback and
# every seek re-request the same URL).
DEFAULT_MAX_USES: Optional[int] = None

# Serialises first-secret provisioning and the use counters.
_lock = threading.Lock()
# sig -> [uses_left, expires]; pruned of expired entries on every mint.
_uses: dict = {}


def _configured_secret() -> str:
    from .global_config import get_config
    return (get_config() or {}).get("stream_token_secret", "") or ""


def _get_secret() -> str:
    """Retrieve the stream token signing secret, persisted in global_config.
    If none is configured, provision one under _lock (re-read inside the lock
    so concurrent first mints all sign with the same secret) and persist it."""
    try:
        sec = _configured_secret()
        if sec:
            return sec
        with _lock:
            sec = _configured_secret()
            if sec:
                return sec
            import secrets
            from .global_config import set_config
            sec = secrets.token_urlsafe(32)
            try:
                set_config({"stream_token_secret": sec})
            except Exception:
                pass
            return sec
    except Exception:
        # Last-resort: in-process random (resets on restart, ok for dev)
        if not hasattr(_get_secret, "_fallback"):
            import secrets
            _get_secret._fallback = secrets.token_urlsafe(32)  # type: ignore
        return _get_secret._fallback  # type: ignore


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:24]


def _positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def generate_token(history_id: int, *,
                  ttl_seconds: int = 3600,
                  max_uses: Optional[int] = DEFAULT_MAX_USES) -> str:
    """Mint a signed token that grants read access to one history
    row's file for min(ttl_seconds, MAX_TTL_SECONDS) seconds and, when
    max_uses is given, for at most max_uses successful verifies.
    Raises ValueError for a non-positive / non-int ttl_seconds or max_uses."""
    ttl = min(_positive_int(ttl_seconds, "ttl_seconds"), MAX_TTL_SECONDS)
    if max_uses is not None:
        max_uses = _positive_int(max_uses, "max_uses")
    secret = _get_secret()
    expires = int(time.time() + ttl)
    if max_uses is None:
        body = f"v1.{int(history_id)}.{expires}"
    else:
        body = f"v2.{int(history_id)}.{expires}.{max_uses}"
    sig = _sign(secret, body)
    if max_uses is not None:
        now = time.time()
        with _lock:
            for k in [k for k, v in _uses.items() if v[1] < now]:
                del _uses[k]
            _uses[sig] = [max_uses, expires]
    return f"{body}.{sig}"


def verify_token(token: str) -> dict:
    """Verify a token. Returns {ok, history_id, expires, error}. A
    successful verify of a use-limited (v2) token consumes one use."""
    parts = token.split(".") if token else []
    if len(parts) == 4 and parts[0] == "v1":
        version, hid_str, exp_str, sig = parts
        uses_str = None
    elif len(parts) == 5 and parts[0] == "v2":
        version, hid_str, exp_str, uses_str, sig = parts
    elif len(parts) in (4, 5):
        return {"ok": False, "error": "unsupported token version"}
    else:
        return {"ok": False, "error": "malformed token"}
    try:
        hid = int(hid_str)
        exp = int(exp_str)
        max_uses = None if uses_str is None else int(uses_str)
    except ValueError:
        return {"ok": False, "error": "non-numeric fields"}
    if exp < time.time():
        return {"ok": False, "error": "expired", "expires": exp}
    secret = _get_secret()
    body = ".".join(parts[:-1])
    if not hmac.compare_digest(_sign(secret, body), sig):
        return {"ok": False, "error": "bad signature"}
    if max_uses is not None:
        with _lock:
            left = _uses.setdefault(sig, [max_uses, exp])
            if left[0] <= 0:
                return {"ok": False, "error": "use limit reached",
                        "history_id": hid}
            left[0] -= 1
    return {"ok": True, "history_id": hid, "expires": exp}


# ─── Range parsing + streaming ────────────────────────────────────────

# RFC 9110 14.2: a Range header with an unknown unit or an invalid
# ranges-specifier is ignored (200, full body), not answered 416.
RANGE_IGNORED = "ignore"


def _parse_range_header(header: str, file_size: int) -> Optional[tuple | str]:
    """Parse 'bytes=START-END'. Returns (start, end_inclusive), None when
    the range is unsatisfiable (416), or RANGE_IGNORED when the header
    must be ignored (no header, unknown unit, invalid spec)."""
    if not header or not header.startswith("bytes="):
        return RANGE_IGNORED
    spec = header[len("bytes="):].strip()
    if "," in spec:
        # Multi-range: not supported — take first range only
        spec = spec.split(",", 1)[0].strip()
    parts = spec.split("-")
    if len(parts) != 2:
        return RANGE_IGNORED
    start_s, end_s = parts[0].strip(), parts[1].strip()
    # 1*DIGIT, ASCII only: int() alone also takes +0, 1_0, non-ASCII digits
    if not (start_s or end_s) or not all(
            x.isascii() and x.isdigit() for x in (start_s, end_s) if x):
        return RANGE_IGNORED
    try:
        if start_s == "" and end_s != "":
            # Suffix: bytes=-N → last N bytes
            n = int(end_s)
            start = max(0, file_size - n)
            end = file_size - 1
        elif start_s != "" and end_s == "":
            start = int(start_s)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s)
            if end < start:
                # last-pos < first-pos: invalid range-spec (RFC 9110 14.1.1)
                return RANGE_IGNORED
    except ValueError:
        # Past int()'s digit limit: valid syntax, beyond any file
        return None
    if start < 0 or start >= file_size or end < start:
        return None
    end = min(end, file_size - 1)
    return (start, end)


def resolve_history_file(filename: str, roots=None) -> tuple:
    """(real_path, 200) or (None, 404|403) for a relative history filename.

    history.filename is a bare BASENAME (runner_transport records
    ``final_path.name``); handed to os.path.isfile as-is it resolved against
    the process CWD, so /stream/<token> answered 404 for every real download.
    It is resolved against the configured download roots instead
    (``paths.download_roots``), with library_final._resolve_recorded's rules:
    a flat join first, the basename index for a file the filename template
    put in a subfolder, and 404 -- never a guess -- when the name matches
    several files. The resolved file must stay inside the roots (realpath, so
    a ``..`` walk or a symlink pointing out is caught): 403 otherwise.
    """
    from . import app_state, library_final, paths
    if roots is None:
        try:
            roots = paths.download_roots(app_state.s_cfg)
        except Exception:
            roots = []
    roots = [os.path.abspath(r) for r in roots]
    if not filename or not roots:
        return None, 404
    path, state = library_final._resolve_recorded(filename, roots, {})
    if state == "absent" and os.path.basename(filename) == filename:
        path, state = library_final._resolve_recorded(
            filename, roots, library_final._basename_index(roots))
    if state != "resolved":
        return None, 404
    if not paths.within_download_roots(str(path), roots):
        return None, 403
    return os.path.realpath(path), 200


def serve_range(filename: str, range_header: str = "",
               *, chunk_size: int = 64 * 1024):
    """Generator + headers for a Flask Response that serves a file
    with HTTP range support.

    Returns (generator, headers, status_code).
      • 206 Partial Content for valid Range
      • 200 OK for no Range header
      • 200 OK for an ignored Range (unknown unit, invalid spec)
      • 416 Range Not Satisfiable for an unsatisfiable Range
    """
    if not os.path.isabs(filename):
        filename, status = resolve_history_file(filename)
        if filename is None:
            return None, {"Content-Type": "text/plain"}, status
    if not os.path.isfile(filename):
        return None, {"Content-Type": "text/plain"}, 404
    file_size = os.path.getsize(filename)
    rng = _parse_range_header(range_header, file_size)
    # No range, or a Range header that must be ignored
    if rng is RANGE_IGNORED:
        def full_stream():
            # Bounded to the file_size announced in Content-Length: a file
            # still growing (or appended to) must not send extra bytes.
            with open(filename, "rb") as f:
                remaining = file_size
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        return
                    yield chunk
                    remaining -= len(chunk)
        return full_stream(), {
            "Content-Length": str(file_size),
            "Content-Type": _mime_for(filename),
            "Accept-Ranges": "bytes",
        }, 200
    if rng is None:
        return None, {
            "Content-Range": f"bytes */{file_size}",
            "Content-Type": "text/plain",
        }, 416
    start, end = rng
    length = end - start + 1

    def range_stream():
        with open(filename, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    return
                yield chunk
                remaining -= len(chunk)

    headers = {
        "Content-Length": str(length),
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Type": _mime_for(filename),
        "Accept-Ranges": "bytes",
    }
    return range_stream(), headers, 206


_MIME_BY_EXT = {
    ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".mov": "video/quicktime", ".avi": "video/x-msvideo",
    ".wmv": "video/x-ms-wmv", ".ts": "video/mp2t",
}


def _mime_for(filename: str) -> str:
    from pathlib import Path
    return _MIME_BY_EXT.get(Path(filename).suffix.lower(),
                            "application/octet-stream")
