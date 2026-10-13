"""Async Metadata Hydration Watcher for React and Next.js (Row 949).

Parses embedded framework state containers (e.g. Next.js __NEXT_DATA__,
Nuxt.js __NUXT_DATA__ / window.__NUXT__) to extract rich media metadata,
titles, and direct stream URLs directly without scraping DOM nodes.
"""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
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
# Ordered by priority: the first key present wins. Tuples, not sets -- set
# iteration order of str changes with PYTHONHASHSEED (O1826-C30 M068).
TITLE_KEYS = ("title", "scenetitle", "release_title", "name", "headline")
DESC_KEYS = ("description", "summary", "desc", "overview")
DURATION_KEYS = ("duration", "duration_seconds", "runtime", "length")
THUMB_KEYS = (
    "thumbnail",
    "thumbnail_url",
    "thumbnailurl",
    "poster",
    "posterurl",
    "poster_url",
    "image",
    "cover",
)
URL_KEYS = ("url", "src", "stream_url", "download_url", "downloadurl", "href", "link")
# O1789 dax-pages H1/H2: "url"/"href"/"link" also name ordinary PAGE links
# ({url: "/videos/about"}). From those keys a URL is a stream only with a media
# extension or beside stream descriptor keys; the media-specific keys keep the
# looser keyword test. Every key is tried, so a page "url" no longer hides "src".
GENERIC_URL_KEYS = frozenset({"url", "href", "link"})
STREAM_DESCRIPTOR_KEYS = ("quality", "bitrate", "resolution", "format")
# H4: the unquoted-key rewrite must skip string literals ("Ep 2, time: 10").
_JS_STRING = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
_JS_KEY_RE = re.compile(r"(" + _JS_STRING + r")|([{\s,])([a-zA-Z_][a-zA-Z0-9_]*)\s*:")
_JS_TRAILING_COMMA_RE = re.compile(r"(" + _JS_STRING + r")|,\s*([}\]])")
MEDIA_EXT_RE = re.compile(r"\.(mp4|m4v|mov|webm|mkv|m3u8|mpd)(\?|$)", re.I)
# H8: a protocol-relative URL is taken only when "//" is followed by a DNS host
# name (optional port) and the rest has no whitespace: "///etc/x", "// x" -> "".
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_PROTO_REL_RE = re.compile(r"//" + _LABEL + r"(?:\." + _LABEL + r")*(?::\d{1,5})?(?:[/?#]\S*)?\Z")


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
        # String literals are matched first and kept verbatim (H4).
        clean = _JS_KEY_RE.sub(
            lambda m: m.group(1) or f'{m.group(2)}"{m.group(3)}":', payload)
        clean = _JS_TRAILING_COMMA_RE.sub(lambda m: m.group(1) or m.group(2), clean)
        return json.loads(clean)


def _title_ok(text: str) -> bool:
    """H9: a title is longer than 2 characters, or written in wide (CJK) script,
    where one or two characters are a whole word ("愛")."""
    return len(text) > 2 or any(
        unicodedata.east_asian_width(ch) in ("W", "F") for ch in text)


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
            except (json.JSONDecodeError, ValueError, RecursionError):
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
            except (json.JSONDecodeError, ValueError, RecursionError):
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
            except (json.JSONDecodeError, ValueError, RecursionError):
                pass

        return None

    def _parse_state(
        self, data: dict[str, Any] | list[Any], framework: str
    ) -> HydratedMediaMetadata:
        """Walk extracted JSON payload to collect titles, durations, and streams."""
        meta = HydratedMediaMetadata(framework=framework, raw_state=data)
        seen_urls: set[str] = set()

        def _abs_url(val: Any) -> str:
            """``val`` as an absolute http(s) URL, or "". H8: protocol-relative
            ``//cdn/x.m3u8`` is https; without a valid host it is "" (r2)."""
            if not isinstance(val, str):
                return ""
            if val.startswith("//"):
                return "https:" + val if _PROTO_REL_RE.match(val) else ""
            return val if val.startswith(("http://", "https://")) else ""

        def _is_stream(key: str, url: str, node: Mapping) -> bool:
            if MEDIA_EXT_RE.search(url) or any(sk in node for sk in STREAM_DESCRIPTOR_KEYS):
                return True
            if key in GENERIC_URL_KEYS:  # H2: a page link is not a stream
                return False
            return "stream" in url or "hls" in url or "video" in url

        # Shallowest depth each container was walked at, by id(): devalue
        # references let one node be reached many times, or reach itself
        # (O1826-C30 M069). Re-walk only from a shallower depth, so the depth
        # cap never hides a subtree and each node is walked at most 13 times.
        visited: dict[int, int] = {}

        def _resolve(val: Any) -> Any:
            if (
                isinstance(val, int)
                and not isinstance(val, bool)
                and isinstance(data, Sequence)
                and not isinstance(data, (str, bytes))
            ):
                if 0 <= val < len(data):
                    return data[val]
            return val

        def _walk(node: Any, depth: int = 0) -> None:
            if depth > 12 or visited.get(id(node), depth + 1) <= depth:
                return
            visited[id(node)] = depth

            if isinstance(node, Mapping):
                # 1. Search for title
                if not meta.title:
                    for k in TITLE_KEYS:
                        v = _resolve(node.get(k))
                        if isinstance(v, str) and _title_ok(v.strip()):
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
                        # H6: a bool is not a duration, nor is inf/nan.
                        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                                and math.isfinite(v) and v > 0):
                            meta.duration = float(v)
                            break

                # 4. Search for thumbnail
                if not meta.thumbnail_url:
                    for k in THUMB_KEYS:
                        v = _abs_url(_resolve(node.get(k)))
                        if v:
                            meta.thumbnail_url = v
                            break

                # 5. Check if current dict is a stream descriptor
                url_candidate = None
                for uk in URL_KEYS:
                    candidate = _abs_url(_resolve(node.get(uk)))
                    if candidate and _is_stream(uk, candidate, node):
                        url_candidate = candidate
                        break

                if url_candidate:
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
