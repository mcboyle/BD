"""bulk_downloader.player_config -- JavaScript player inline configuration extractor.

Extracts media sources, quality tiers, and authentication tokens from web player
inline configurations (JWPlayer, Video.js, generic player objects) without live
playback simulation. 0 site logins touched (Rule 21).
"""

from __future__ import annotations

import ast
import html
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class QualityTier:
    """Structured media quality tier."""
    label: Optional[str] = None
    height: Optional[int] = None
    width: Optional[int] = None
    bitrate: Optional[int] = None
    url: Optional[str] = None


@dataclass
class PlayerSource:
    """Individual media stream source."""
    url: str
    quality_label: Optional[str] = None
    height: Optional[int] = None
    width: Optional[int] = None
    bitrate: Optional[int] = None
    mime_type: Optional[str] = None
    auth_token: Optional[str] = None


@dataclass
class PlayerConfig:
    """Extracted web player configuration."""
    player_type: str  # "jwplayer", "videojs", "generic"
    sources: list[PlayerSource] = field(default_factory=list)
    auth_tokens: dict[str, str] = field(default_factory=dict)
    raw_config: dict[str, Any] = field(default_factory=dict)

    @property
    def quality_ladder(self) -> list[QualityTier]:
        """Derive quality ladder from sources and config metadata."""
        tiers: list[QualityTier] = []
        for src in self.sources:
            if src.height or src.quality_label or src.bitrate:
                tiers.append(
                    QualityTier(
                        label=src.quality_label,
                        height=src.height,
                        width=src.width,
                        bitrate=src.bitrate,
                        url=src.url,
                    )
                )

        # Fallback: check raw_config for explicit qualities/ladder definitions
        if not tiers and "qualities" in self.raw_config:
            raw_qualities = self.raw_config.get("qualities", [])
            if isinstance(raw_qualities, list):
                for q in raw_qualities:
                    if isinstance(q, dict):
                        tiers.append(
                            QualityTier(
                                label=q.get("label") or q.get("res"),
                                height=q.get("height"),
                                width=q.get("width"),
                                bitrate=q.get("bitrate"),
                                url=q.get("url") or q.get("src") or q.get("file"),
                            )
                        )

        # Sort ladder by resolution (height) or bitrate descending
        tiers.sort(key=lambda t: (t.height or 0, t.bitrate or 0), reverse=True)
        return tiers


# ── Balanced brace extractor ──────────────────────────────────────────────────

def _find_balanced_object(text: str, start_index: int) -> Optional[tuple[str, int]]:
    """Extract a balanced `{...}` substring starting from start_index."""
    brace_start = text.find("{", start_index)
    if brace_start == -1:
        return None

    depth = 0
    in_string = False
    quote_char = ""
    escaped = False

    for i in range(brace_start, len(text)):
        char = text[i]

        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if in_string:
            if char == quote_char:
                in_string = False
            continue

        if char in ('"', "'", "`"):
            in_string = True
            quote_char = char
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start : i + 1], i + 1

    return None


# ── JS Object Literal to Python Dict Parser ───────────────────────────────────

_UNQUOTED_KEY_RE = re.compile(r'([,{]\s*)([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:', re.MULTILINE)
_TRAILING_COMMA_RE = re.compile(r',\s*([}\]])')


def parse_js_object(text: str) -> Optional[dict[str, Any]]:
    """Parse a JavaScript object literal into a Python dictionary.

    Supports unquoted property names, single-quoted strings, trailing commas,
    and JS booleans/null via regex normalization and Python AST literal evaluation.
    """
    if not text or not text.strip():
        return None

    cleaned = text.strip()

    # Fast path: strict JSON
    try:
        res = json.loads(cleaned)
        if isinstance(res, dict):
            return res
    except Exception:
        pass

    # Normalize JS object syntax to Python literal syntax
    # 1. Quote unquoted keys: { key: ... } -> { "key": ... }
    normalized = _UNQUOTED_KEY_RE.sub(r'\1"\2":', cleaned)

    # 2. Remove trailing commas before } or ]
    normalized = _TRAILING_COMMA_RE.sub(r'\1', normalized)

    # 3. Replace JS keywords with Python equivalents
    normalized = re.sub(r'\btrue\b', 'True', normalized)
    normalized = re.sub(r'\bfalse\b', 'False', normalized)
    normalized = re.sub(r'\bnull\b', 'None', normalized)

    # 4. Parse using Python AST literal_eval
    try:
        node = ast.parse(normalized, mode='eval')
        evaluated = ast.literal_eval(node)
        if isinstance(evaluated, dict):
            return evaluated
    except Exception:
        pass

    # Fallback: attempt JSON decoding on normalized string with single-quote conversion
    try:
        # Simple single to double quote substitution outside existing double quotes
        sq_to_dq = re.sub(r"(?<!\\)'", '"', normalized)
        res = json.loads(sq_to_dq)
        if isinstance(res, dict):
            return res
    except Exception:
        pass

    return None


# ── Token extraction helper ───────────────────────────────────────────────────

_TOKEN_PARAM_KEYS = {"token", "auth", "jwt", "sig", "signature", "key", "access_token"}


def _extract_tokens_from_url(url: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        for k, vals in params.items():
            if k.lower() in _TOKEN_PARAM_KEYS and vals:
                tokens[k] = vals[0]
    except Exception:
        pass
    return tokens


def _find_tokens_in_dict(data: Any, tokens: dict[str, str], depth: int = 0) -> None:
    if depth > 10:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str) and k.lower() in {
                "token", "auth", "jwt", "signature", "accesstoken",
                "access_token", "authtoken", "auth_token", "securitytoken",
            }:
                if isinstance(v, (str, int)):
                    tokens[k] = str(v)
            if isinstance(v, (dict, list)):
                _find_tokens_in_dict(v, tokens, depth + 1)
    elif isinstance(data, list):
        for item in data:
            _find_tokens_in_dict(item, tokens, depth + 1)


# ── Configuration Extractors ─────────────────────────────────────────────────

_JWPLAYER_SETUP_RE = re.compile(r'jwplayer\s*\([^)]*\)\s*\.\s*setup\s*\(', re.IGNORECASE)
_VIDEOJS_CALL_RE = re.compile(r'videojs\s*\([^,]+,\s*', re.IGNORECASE)
_DATA_SETUP_RE = re.compile(r'data-setup\s*=\s*(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL)
_INITIAL_STATE_RE = re.compile(
    r'(?:window\.)?__(?:INITIAL_STATE|INITIAL_PLAYER_STATE|PLAYER_CONFIG)__\s*=\s*',
    re.IGNORECASE
)


def _tolerant_int(value) -> Optional[int]:
    """A page's height/width/bitrate as an int, or None. Real players emit
    "auto", "720p", "2500k", 1080.0: take the leading digits of a string,
    truncate a number, never raise (one odd field must not take the whole
    extractor down for the page)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m and int(m.group(1)) else None


def _parse_source_entry(entry: dict[str, Any]) -> Optional[PlayerSource]:
    """Parse a single source dict entry."""
    url = (
        entry.get("file")
        or entry.get("src")
        or entry.get("url")
        or entry.get("streamUrl")
        or entry.get("hlsUrl")
    )
    if not url or not isinstance(url, str):
        return None

    label = entry.get("label") or entry.get("res") or entry.get("resolution")
    height = entry.get("height")
    width = entry.get("width")
    bitrate = entry.get("bitrate")
    mime_type = entry.get("type") or entry.get("mime_type")

    # If height missing but label has e.g. "720p", infer height
    if not height and label and isinstance(label, str):
        m = re.search(r'(\d{3,4})p?', label)
        if m:
            try:
                height = int(m.group(1))
            except ValueError:
                pass

    # Extract token from URL query params if present
    url_tokens = _extract_tokens_from_url(url)
    primary_token = next(iter(url_tokens.values())) if url_tokens else None

    return PlayerSource(
        url=url,
        quality_label=str(label) if label else None,
        height=_tolerant_int(height),
        width=_tolerant_int(width),
        bitrate=_tolerant_int(bitrate),
        mime_type=str(mime_type) if mime_type else None,
        auth_token=primary_token,
    )


def _extract_jwplayer_configs(text: str) -> list[PlayerConfig]:
    configs: list[PlayerConfig] = []
    for match in _JWPLAYER_SETUP_RE.finditer(text):
        obj_info = _find_balanced_object(text, match.end() - 1)
        if not obj_info:
            continue
        obj_text, _ = obj_info
        data = parse_js_object(obj_text)
        if not data:
            continue

        sources: list[PlayerSource] = []
        tokens: dict[str, str] = {}
        _find_tokens_in_dict(data, tokens)

        # JWPlayer supports playlist: [{ sources: [...] }] or sources: [...] or file: "..."
        playlist = data.get("playlist", [])
        if isinstance(playlist, list):
            for item in playlist:
                if isinstance(item, dict):
                    item_sources = item.get("sources", [])
                    if isinstance(item_sources, list):
                        for s in item_sources:
                            if isinstance(s, dict):
                                parsed_s = _parse_source_entry(s)
                                if parsed_s:
                                    sources.append(parsed_s)
                    elif item.get("file"):
                        parsed_s = _parse_source_entry(item)
                        if parsed_s:
                            sources.append(parsed_s)

        if not sources and "sources" in data and isinstance(data["sources"], list):
            for s in data["sources"]:
                if isinstance(s, dict):
                    parsed_s = _parse_source_entry(s)
                    if parsed_s:
                        sources.append(parsed_s)

        if not sources and "file" in data:
            parsed_s = _parse_source_entry(data)
            if parsed_s:
                sources.append(parsed_s)

        if sources or data:
            configs.append(
                PlayerConfig(
                    player_type="jwplayer",
                    sources=sources,
                    auth_tokens=tokens,
                    raw_config=data,
                )
            )

    return configs


def _extract_videojs_configs(text: str) -> list[PlayerConfig]:
    configs: list[PlayerConfig] = []

    # 1. Video.js HTML data-setup attribute
    for match in _DATA_SETUP_RE.finditer(text):
        # JSON inside a double-quoted attribute arrives entity-escaped from
        # every HTML-escaping template (&quot;, &amp;): decode before parsing
        raw_val = html.unescape(match.group(2))
        data = parse_js_object(raw_val)
        if data:
            sources: list[PlayerSource] = []
            tokens: dict[str, str] = {}
            _find_tokens_in_dict(data, tokens)
            src_list = data.get("sources", [])
            if isinstance(src_list, list):
                for s in src_list:
                    if isinstance(s, dict):
                        parsed_s = _parse_source_entry(s)
                        if parsed_s:
                            sources.append(parsed_s)
            elif "src" in data:
                parsed_s = _parse_source_entry(data)
                if parsed_s:
                    sources.append(parsed_s)

            if sources:
                configs.append(
                    PlayerConfig(
                        player_type="videojs",
                        sources=sources,
                        auth_tokens=tokens,
                        raw_config=data,
                    )
                )

    # 2. Video.js JS function calls: videojs("id", {...})
    for match in _VIDEOJS_CALL_RE.finditer(text):
        obj_info = _find_balanced_object(text, match.end())
        if not obj_info:
            continue
        obj_text, _ = obj_info
        data = parse_js_object(obj_text)
        if not data:
            continue

        sources = []
        tokens = {}
        _find_tokens_in_dict(data, tokens)
        src_list = data.get("sources", [])
        if isinstance(src_list, list):
            for s in src_list:
                if isinstance(s, dict):
                    parsed_s = _parse_source_entry(s)
                    if parsed_s:
                        sources.append(parsed_s)
        elif "src" in data:
            parsed_s = _parse_source_entry(data)
            if parsed_s:
                sources.append(parsed_s)

        if sources:
            configs.append(
                PlayerConfig(
                    player_type="videojs",
                    sources=sources,
                    auth_tokens=tokens,
                    raw_config=data,
                )
            )

    return configs


def _extract_generic_configs(text: str) -> list[PlayerConfig]:
    configs: list[PlayerConfig] = []
    for match in _INITIAL_STATE_RE.finditer(text):
        obj_info = _find_balanced_object(text, match.end())
        if not obj_info:
            continue
        obj_text, _ = obj_info
        data = parse_js_object(obj_text)
        if not data:
            continue

        sources: list[PlayerSource] = []
        tokens: dict[str, str] = {}
        _find_tokens_in_dict(data, tokens)

        # Inspect stream or qualities dictionaries
        stream_info = data.get("stream", {})
        if isinstance(stream_info, dict):
            qualities = stream_info.get("qualities", [])
            if isinstance(qualities, list):
                for q in qualities:
                    if isinstance(q, dict):
                        parsed_s = _parse_source_entry(q)
                        if parsed_s:
                            sources.append(parsed_s)
            if not sources and "hlsUrl" in stream_info:
                sources.append(PlayerSource(url=str(stream_info["hlsUrl"])))

        if sources or tokens:
            configs.append(
                PlayerConfig(
                    player_type="generic",
                    sources=sources,
                    auth_tokens=tokens,
                    raw_config=data,
                )
            )

    return configs


def extract_player_configs(html_or_js: str) -> list[PlayerConfig]:
    """Extract all inline web player configurations from HTML or JavaScript."""
    if not html_or_js or not isinstance(html_or_js, str):
        return []

    results: list[PlayerConfig] = []
    results.extend(_extract_jwplayer_configs(html_or_js))
    results.extend(_extract_videojs_configs(html_or_js))
    results.extend(_extract_generic_configs(html_or_js))

    return results
