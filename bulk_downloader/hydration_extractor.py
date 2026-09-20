"""Async Metadata Hydration Watcher for React and Next.js (Row 949).

Parses embedded framework state containers (e.g. Next.js __NEXT_DATA__,
Nuxt.js __NUXT_DATA__ / window.__NUXT__) to extract rich media metadata,
titles, and direct stream URLs directly without scraping DOM nodes.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

# Framework state payload patterns
NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
NUXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
NUXT_WINDOW_RE = re.compile(
    r"window\.__NUXT__\s*=\s*(.*?)(?:;?\s*</script>)",
    re.DOTALL | re.IGNORECASE,
)

# Key patterns for targeted metadata extraction
TITLE_KEYS = {"title", "scenetitle", "release_title", "name", "headline"}
DESC_KEYS = {"description", "summary", "desc", "overview"}
DURATION_KEYS = {"duration", "duration_seconds", "runtime", "length"}
THUMB_KEYS = {
    "thumbnail",
    "thumbnail_url",
    "thumbnailurl",
    "poster",
    "posterurl",
    "poster_url",
    "image",
    "cover",
}
URL_KEYS = {"url", "src", "stream_url", "download_url", "downloadurl", "href", "link"}
MEDIA_EXT_RE = re.compile(r"\.(mp4|m4v|mov|webm|mkv|m3u8|mpd)(\?|$)", re.I)


@dataclass
class StreamSource:
    """A direct stream rendition extracted from hydration state."""

    url: str
    quality: str = ""
    format: str = ""
    bitrate: int | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HydratedMediaMetadata:
    """Rich media metadata extracted from embedded framework state."""

    framework: str  # 'nextjs', 'nuxt', 'generic'
    title: str = ""
    stream_urls: list[str] = field(default_factory=list)
    streams: list[StreamSource] = field(default_factory=list)
    description: str = ""
    duration: float | None = None
    thumbnail_url: str = ""
    raw_state: dict[str, Any] | list[Any] = field(default_factory=dict)
    extraction_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_metadata_schema(meta: HydratedMediaMetadata) -> tuple[bool, list[str]]:
    """Validate extracted media metadata against structural constraints."""
    errors: list[str] = []

    if not isinstance(meta.framework, str) or not meta.framework.strip():
        errors.append("framework must be a non-empty string")

    if not isinstance(meta.title, str):
        errors.append("title must be a string")

    if meta.duration is not None and (
        not isinstance(meta.duration, (int, float)) or meta.duration < 0
    ):
        errors.append("duration must be a non-negative number")

    for url in meta.stream_urls:
        if not isinstance(url, str) or not (
            url.startswith("http://") or url.startswith("https://")
        ):
            errors.append(f"stream URL must start with http:// or https://: {url!r}")

    for stream in meta.streams:
        if not isinstance(stream.url, str) or not (
            stream.url.startswith("http://") or stream.url.startswith("https://")
        ):
            errors.append(
                f"StreamSource URL must start with http:// or https://: {stream.url!r}"
            )

    return (len(errors) == 0, errors)


def _safe_json_loads(payload_raw: str) -> dict[str, Any] | list[Any]:
    """Parse JSON or JS object literal into Python dictionary/list."""
    payload = payload_raw.strip()
    if payload.endswith(";"):
        payload = payload[:-1].strip()

    try:
        return json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        # Handle unquoted JavaScript object keys: { state: ... } -> { "state": ... }
        clean = re.sub(r"([{\s,])([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'\1"\2":', payload)
        clean = re.sub(r",\s*([}\]])", r"\1", clean)
        return json.loads(clean)


class HydrationExtractor:
    """High-throughput parser for inline React/Next.js and Nuxt.js hydration states."""

    def extract(self, html: str) -> HydratedMediaMetadata | None:
        """Extract media metadata from the first valid framework hydration state in HTML."""
        t0 = time.perf_counter()

        # 1. Check Next.js (__NEXT_DATA__)
        match_next = NEXT_DATA_RE.search(html)
        if match_next:
            payload_raw = match_next.group(1).strip()
            try:
                data = _safe_json_loads(payload_raw)
                meta = self._parse_state(data, framework="nextjs")
                meta.extraction_time_ms = (time.perf_counter() - t0) * 1000.0
                return meta
            except (json.JSONDecodeError, ValueError):
                pass

        # 2. Check Nuxt 3 JSON (__NUXT_DATA__)
        match_nuxt_data = NUXT_DATA_RE.search(html)
        if match_nuxt_data:
            payload_raw = match_nuxt_data.group(1).strip()
            try:
                data = _safe_json_loads(payload_raw)
                meta = self._parse_state(data, framework="nuxt")
                meta.extraction_time_ms = (time.perf_counter() - t0) * 1000.0
                return meta
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. Check Nuxt 2 Window state (window.__NUXT__)
        match_nuxt_win = NUXT_WINDOW_RE.search(html)
        if match_nuxt_win:
            payload_raw = match_nuxt_win.group(1).strip()
            try:
                data = _safe_json_loads(payload_raw)
                meta = self._parse_state(data, framework="nuxt")
                meta.extraction_time_ms = (time.perf_counter() - t0) * 1000.0
                return meta
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def _parse_state(
        self, data: dict[str, Any] | list[Any], framework: str
    ) -> HydratedMediaMetadata:
        """Walk extracted JSON payload to collect titles, durations, and streams."""
        meta = HydratedMediaMetadata(framework=framework, raw_state=data)
        seen_urls: set[str] = set()

        def _is_url(val: Any) -> bool:
            return isinstance(val, str) and (
                val.startswith("http://") or val.startswith("https://")
            )

        def _resolve(val: Any) -> Any:
            if isinstance(val, int) and isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
                if 0 <= val < len(data):
                    return data[val]
            return val

        def _walk(node: Any, depth: int = 0) -> None:
            if depth > 12:
                return

            if isinstance(node, Mapping):
                # 1. Search for title
                if not meta.title:
                    for k in TITLE_KEYS:
                        v = _resolve(node.get(k))
                        if isinstance(v, str) and len(v.strip()) > 2:
                            meta.title = v.strip()
                            break

                # 2. Search for description
                if not meta.description:
                    for k in DESC_KEYS:
                        v = _resolve(node.get(k))
                        if isinstance(v, str) and len(v.strip()) > 2:
                            meta.description = v.strip()
                            break

                # 3. Search for duration
                if meta.duration is None:
                    for k in DURATION_KEYS:
                        v = _resolve(node.get(k))
                        if isinstance(v, (int, float)) and v > 0:
                            meta.duration = float(v)
                            break

                # 4. Search for thumbnail
                if not meta.thumbnail_url:
                    for k in THUMB_KEYS:
                        v = _resolve(node.get(k))
                        if _is_url(v):
                            meta.thumbnail_url = v
                            break

                # 5. Check if current dict is a stream descriptor
                url_candidate = None
                for uk in URL_KEYS:
                    candidate = _resolve(node.get(uk))
                    if _is_url(candidate):
                        url_candidate = candidate
                        break

                if url_candidate and (
                    MEDIA_EXT_RE.search(url_candidate)
                    or "stream" in url_candidate
                    or "hls" in url_candidate
                    or "video" in url_candidate
                    or any(
                        sk in node
                        for sk in ("quality", "bitrate", "resolution", "format")
                    )
                ):
                    if url_candidate not in seen_urls:
                        seen_urls.add(url_candidate)
                        meta.stream_urls.append(url_candidate)
                        meta.streams.append(
                            StreamSource(
                                url=url_candidate,
                                quality=str(
                                    node.get("quality")
                                    or node.get("resolution")
                                    or ""
                                ),
                                format=str(node.get("format") or ""),
                                bitrate=node.get("bitrate")
                                if isinstance(node.get("bitrate"), int)
                                else None,
                                width=node.get("width")
                                if isinstance(node.get("width"), int)
                                else None,
                                height=node.get("height")
                                if isinstance(node.get("height"), int)
                                else None,
                            )
                        )

                # Continue traversal
                for val in node.values():
                    resolved_val = _resolve(val)
                    if isinstance(resolved_val, (Mapping, Sequence)) and not isinstance(
                        resolved_val, (str, bytes)
                    ):
                        _walk(resolved_val, depth + 1)

            elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
                for item in node:
                    resolved_item = _resolve(item)
                    if isinstance(resolved_item, (Mapping, Sequence)) and not isinstance(
                        resolved_item, (str, bytes)
                    ):
                        _walk(resolved_item, depth + 1)

        _walk(data)
        return meta
