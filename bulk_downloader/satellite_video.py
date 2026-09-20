"""bulk_downloader.satellite_video -- Row 862: LAN hardware-accelerated video validation.

Routes eligible video validation requests to an approved LAN hardware acceleration
RPC endpoint (e.g. http://10.0.70.125:8090/api/video/validate or http://10.0.70.162:8090)
with a fail-soft local fallback to CPU ffprobe in :mod:`bulk_downloader.integrity`.
Preserves validation semantics: valid video containers return (True, ""), invalid containers
return (False, reason). Touches no captured-site authentication flow (Fleet Rule 21) --
this is internal fleet acceleration, not a captured site.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from typing import Union

# Approved LAN hardware acceleration endpoints
DEFAULT_ENDPOINT = os.getenv(
    "SATELLITE_VIDEO_ENDPOINT",
    "http://10.0.70.125:8090/api/video/validate",
)
DEFAULT_TIMEOUT = 0.5  # Fast LAN timeout so fallback to CPU is prompt

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".m4v", ".ts", ".flv"
})


def is_eligible_for_offload(path: Union[str, Path]) -> bool:
    """Return True if the file format is eligible for satellite video validation."""
    ext = os.path.splitext(str(path))[1].lower()
    return ext in VIDEO_EXTENSIONS


class SatelliteResponseError(ValueError):
    """The satellite answered, but not with a usable verdict (non-200 status or
    a body outside the strict schema). Always a local-fallback trigger."""


def _parse_verdict(data: object) -> tuple[bool, bool, str]:
    """Strict schema: ``valid`` and ``has_video`` MUST be JSON booleans. A string
    "false" (truthy), a number, null or a missing key is a schema violation ->
    SatelliteResponseError, never a verdict on the file."""
    if not isinstance(data, dict):
        raise SatelliteResponseError("response body is not a JSON object")
    valid = data.get("valid")
    has_video = data.get("has_video")
    if not isinstance(valid, bool) or not isinstance(has_video, bool):
        raise SatelliteResponseError(
            f"non-boolean verdict fields: valid={valid!r} has_video={has_video!r}")
    err = data.get("error", "")
    if err is None:
        err = ""
    if not isinstance(err, str):
        raise SatelliteResponseError(f"non-string error field: {err!r}")
    return valid, has_video, err


def validate_video_rpc(
    path: Union[str, Path],
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = DEFAULT_TIMEOUT,
    fallback_on_error: bool = True,
) -> tuple[bool, str]:
    """Validate a video file via the satellite RPC endpoint.

    On connection error, timeout, or unexpected failure, falls back to
    local CPU ffprobe verification (bulk_downloader.integrity._verify_with_ffprobe)
    when fallback_on_error is True. Never raises.
    """
    p = Path(path)
    if not p.exists():
        from . import integrity
        return integrity._verify_with_ffprobe(str(p))

    conn = None
    try:
        # Read container header bytes (up to 64KB, which covers moov/ftyp/header atoms)
        file_size = p.stat().st_size
        with open(p, "rb") as fh:
            header_bytes = fh.read(min(file_size, 65536))

        payload = json.dumps({
            "path": str(p),
            "size": file_size,
            "header_b64": base64.b64encode(header_bytes).decode("ascii"),
            "format": p.suffix.lower(),
        }).encode("utf-8")

        parsed = urlparse(endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path_query = parsed.path or "/"
        if parsed.query:
            path_query += f"?{parsed.query}"

        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)

        conn.request(
            "POST",
            path_query,
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            # 5xx/4xx is the SERVICE failing, not a verdict on the file: fall back.
            raise SatelliteResponseError(f"HTTP {resp.status} from {endpoint}")
        data = json.loads(raw.decode("utf-8"))
        valid, has_video, err = _parse_verdict(data)

        if valid and has_video:
            return True, ""
        if not has_video:
            return False, err or "no video stream"
        return False, err or "container invalid"

    except Exception as _err:
        if fallback_on_error:
            _ = str(_err)
            from . import integrity
            return integrity._verify_with_ffprobe(str(p))
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as _ce:
                _ = str(_ce)
