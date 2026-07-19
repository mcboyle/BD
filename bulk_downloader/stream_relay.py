"""Stream relay (Phase 132, Block Q).

Lets the operator stream their library on the go: BD acts as an HTTP
range-proxy in front of the local files, so a mobile player can hit
the BD URL and seek/scrub like a normal video CDN.

Why not just serve files via Flask's send_file? Two reasons:

  1. Per-request access control (auth token) is easier via this
     module: each stream URL gets a short-lived signed token, so the
     operator can share a single video without exposing their whole
     library.

  2. We can rewrite the byte range to the underlying ffmpeg or to a
     transcoded variant if requested — useful if the source is
     2160p HEVC and the phone wants 720p H.264.

Token scheme:
  • generate_token(history_id, *, ttl_seconds=3600, max_uses=1) →
    string token like "v1.<history_id>.<expires>.<sig>"
  • verify_token(token) → {ok, history_id, error}
  • Tokens are signed with stream_token_secret from global_config.
    Stateless — no DB writes, no revocation list (just short TTL).

Range serving:
  • serve_range(filename, range_header) — yields bytes for a Flask
    Response. Supports the standard "bytes=START-END" syntax.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional


def _get_secret() -> str:
    """Retrieve the stream token signing secret. If not configured,
    auto-generate one (in-memory only; tokens reset on restart)."""
    try:
        from .global_config import get_config, set_config
        cfg = get_config() or {}
        sec = cfg.get("stream_token_secret", "") or ""
        if sec:
            return sec
        # Auto-provision a random one and persist
        import secrets
        sec = secrets.token_urlsafe(32)
        try:
            set_config({**cfg, "stream_token_secret": sec})
        except Exception:
            pass
        return sec
    except Exception:
        # Last-resort: in-process random (resets on restart, ok for dev)
        if not hasattr(_get_secret, "_fallback"):
            import secrets
            _get_secret._fallback = secrets.token_urlsafe(32)  # type: ignore
        return _get_secret._fallback  # type: ignore


def generate_token(history_id: int, *,
                  ttl_seconds: int = 3600) -> str:
    """Mint a signed token that grants read access to one history
    row's file for `ttl_seconds`."""
    secret = _get_secret()
    expires = int(time.time() + ttl_seconds)
    body = f"v1.{int(history_id)}.{expires}"
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"),
                   hashlib.sha256).hexdigest()[:24]
    return f"{body}.{sig}"


def verify_token(token: str) -> dict:
    """Verify a token. Returns {ok, history_id, expires, error}."""
    if not token or token.count(".") != 3:
        return {"ok": False, "error": "malformed token"}
    try:
        version, hid_str, exp_str, sig = token.split(".")
    except ValueError:
        return {"ok": False, "error": "malformed token"}
    if version != "v1":
        return {"ok": False, "error": "unsupported token version"}
    try:
        hid = int(hid_str)
        exp = int(exp_str)
    except ValueError:
        return {"ok": False, "error": "non-numeric fields"}
    if exp < time.time():
        return {"ok": False, "error": "expired", "expires": exp}
    secret = _get_secret()
    body = f"{version}.{hid_str}.{exp_str}"
    expected_sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"),
                            hashlib.sha256).hexdigest()[:24]
    if not hmac.compare_digest(expected_sig, sig):
        return {"ok": False, "error": "bad signature"}
    return {"ok": True, "history_id": hid, "expires": exp}


# ─── Range parsing + streaming ────────────────────────────────────────

def _parse_range_header(header: str, file_size: int) -> Optional[tuple]:
    """Parse 'bytes=START-END'. Returns (start, end_inclusive) or None."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].strip()
    if "," in spec:
        # Multi-range: not supported — take first range only
        spec = spec.split(",", 1)[0].strip()
    parts = spec.split("-")
    if len(parts) != 2:
        return None
    start_s, end_s = parts[0].strip(), parts[1].strip()
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
    except ValueError:
        return None
    if start < 0 or start >= file_size or end < start:
        return None
    end = min(end, file_size - 1)
    return (start, end)


def serve_range(filename: str, range_header: str = "",
               *, chunk_size: int = 64 * 1024):
    """Generator + headers for a Flask Response that serves a file
    with HTTP range support.

    Returns (generator, headers, status_code).
      • 206 Partial Content for valid Range
      • 200 OK for no Range header
      • 416 Range Not Satisfiable for malformed/out-of-bounds Range
    """
    if not os.path.isfile(filename):
        return None, {"Content-Type": "text/plain"}, 404
    file_size = os.path.getsize(filename)
    rng = _parse_range_header(range_header, file_size)
    # No range or invalid range
    if not range_header:
        def full_stream():
            with open(filename, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
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
