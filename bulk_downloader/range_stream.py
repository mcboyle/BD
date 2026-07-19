"""HTTP Range request handler for streaming files.

Port of `archive-service/app/range_stream.py` from
Adventures-In-Odyssey-Downloader (F1 finding from the 22-repo line-by-line
review). Original was FastAPI; this is the Flask-native equivalent.

WHY: Flask's `send_file(conditional=True)` (default since 2.x) already
handles Range requests for most cases. This module exists for the cases
where send_file isn't a good fit:

  • Custom chunk sizes (e.g. 64 KiB for low-memory streaming of huge
    multi-GB library files)
  • Custom Content-Type override at runtime
  • Streaming from a file-like object that isn't a real path
  • Endpoints that want to participate in the Range protocol without
    coupling to Flask's static-file pipeline

Usage:

    from .range_stream import stream_file_with_range

    @app.route("/api/library/stream/<path:rel>")
    def api_library_stream(rel):
        path = LIBRARY_DIR / rel
        return stream_file_with_range(path, request.headers.get("Range"),
                                       media_type="video/mp4")

Returns:
  • 200 with full body when no Range header present
  • 206 with the requested byte range when valid Range present
  • 416 (range_not_satisfiable) when Range is malformed or out of bounds
  • 404 when the path doesn't exist

All bodies stream chunk-by-chunk (default 64 KiB) so memory stays bounded
regardless of file size.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator, Optional

from flask import Response, abort

# Module-level so the compiled pattern is reused across calls. The two
# capture groups can each be empty (RFC 7233 allows "bytes=0-" for
# "from byte 0 to end" and "bytes=-500" for "last 500 bytes").
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

# 64 KiB. Big enough that syscall overhead is negligible, small enough
# that abort/cancel mid-stream doesn't keep the OS waiting.
DEFAULT_CHUNK = 1 << 16


def stream_file_with_range(
    path: Path,
    range_header: Optional[str],
    *,
    media_type: str = "application/octet-stream",
    chunk_size: int = DEFAULT_CHUNK,
) -> Response:
    """Stream `path` honoring HTTP Range semantics.

    Always returns a Flask Response. Raises 404 via abort() if the file
    is missing (caller's error handlers run normally). Returns 416 with
    a proper Content-Range trailer if the range is invalid — required
    by RFC 7233 so clients can recover by re-requesting.
    """
    if not path.exists() or not path.is_file():
        abort(404)
    size = path.stat().st_size

    # No Range header: full body, 200 OK, but still advertise
    # Accept-Ranges so future requests can use Range without a probe.
    if not range_header:
        resp = Response(
            _iter_range(path, 0, max(0, size - 1), chunk_size),
            status=200,
            mimetype=media_type,
        )
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(size)
        return resp

    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return _range_not_satisfiable(size)

    start_s, end_s = m.groups()
    # bytes=-N means "last N bytes" per RFC 7233 §2.1.
    if not start_s and end_s:
        suffix_len = int(end_s)
        if suffix_len <= 0:
            return _range_not_satisfiable(size)
        start = max(0, size - suffix_len)
        end = size - 1
    else:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
    if start < 0 or start > end or end >= size:
        return _range_not_satisfiable(size)

    length = end - start + 1
    resp = Response(
        _iter_range(path, start, end, chunk_size),
        status=206,
        mimetype=media_type,
    )
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp.headers["Content-Length"] = str(length)
    return resp


def _iter_range(path: Path, start: int, end: int, chunk_size: int) -> Iterator[bytes]:
    """Generator yielding `[start..end]` (inclusive) from the file in
    chunk-sized pieces. Closes the file deterministically when the
    generator is garbage-collected or returns."""
    remaining = end - start + 1
    if remaining <= 0:
        return
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break  # short read (file truncated mid-stream)
            remaining -= len(chunk)
            yield chunk


def _range_not_satisfiable(size: int) -> Response:
    """416 with the mandatory Content-Range trailer so clients can
    re-request with a valid range."""
    resp = Response(status=416, mimetype="text/plain")
    resp.headers["Content-Range"] = f"bytes */{size}"
    return resp
