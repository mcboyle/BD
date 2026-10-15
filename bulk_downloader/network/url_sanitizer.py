from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH_CHARS = {"\u200b", "\u200c", "\u200d", "\ufeff"}
_BIDI_CONTROLS = {"\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"}

class SanitizationError(ValueError):
    pass

class PayloadLimitExceeded(SanitizationError):
    pass

class MalformedPayloadError(SanitizationError):
    pass

def _try_parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    clean = host.strip("[]")
    try:
        return ipaddress.ip_address(clean)
    except ValueError:
        pass
    if clean.isdigit():
        try:
            val = int(clean)
            if 0 <= val <= 4294967295:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass
    if clean.lower().startswith("0x"):
        try:
            val = int(clean, 16)
            if 0 <= val <= 4294967295:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass
    parts = clean.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        try:
            octets = []
            for p in parts:
                if len(p) > 1 and p.startswith("0"):
                    octets.append(int(p, 8))
                else:
                    octets.append(int(p, 10))
            if all(0 <= o <= 255 for o in octets):
                return ipaddress.IPv4Address(".".join(str(o) for o in octets))
        except ValueError:
            pass
    return None

def _is_restricted_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return _is_restricted_ip(ip.ipv4_mapped)
        if ip.sixtofour:
            return _is_restricted_ip(ip.sixtofour)
        if ip.teredo:
            server, client = ip.teredo
            if _is_restricted_ip(server) or _is_restricted_ip(client):
                return True
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified

def sanitize_url(url: str | None, max_length: int=4096, allow_schemes: tuple[str, ...]=("http", "https"), block_private_ips: bool=True, normalize_homoglyphs: bool=True) -> str:
    if url is None:
        raise TypeError("URL must be a string, got None")
    if not isinstance(url, str):
        raise TypeError(f"URL must be a string, got {type(url).__name__}")
    if len(url) > max_length:
        raise PayloadLimitExceeded(f"URL length ({len(url)}) exceeds maximum limit of {max_length} characters")
    cleaned = url
    for zw in _ZERO_WIDTH_CHARS:
        if zw in cleaned:
            cleaned = cleaned.replace(zw, "")
    for bidi in _BIDI_CONTROLS:
        if bidi in cleaned:
            cleaned = cleaned.replace(bidi, "")
    if normalize_homoglyphs:
        cleaned = unicodedata.normalize("NFKC", cleaned)
    stripped = cleaned.strip()
    if not stripped:
        raise SanitizationError("URL cannot be empty or zero-length")
    if "\x00" in stripped:
        raise SanitizationError("URL contains illegal null byte")
    if _CONTROL_CHAR_RE.search(stripped):
        raise SanitizationError("URL contains illegal unescaped control characters")
    try:
        parsed = urllib.parse.urlsplit(stripped)
    except Exception as exc:
        raise SanitizationError(f"Malformed URL structure: {exc}") from exc
    if not parsed.scheme:
        raise SanitizationError("URL is missing a valid scheme")
    scheme = parsed.scheme.lower()
    allowed_lower = tuple(s.lower() for s in allow_schemes)
    if scheme not in allowed_lower:
        raise SanitizationError(f"Scheme '{scheme}' not in allowed schemes: {allowed_lower}")
    if scheme in ("http", "https"):
        if not parsed.hostname:
            raise SanitizationError("Network URL is missing a valid hostname")
        hostname = parsed.hostname.lower().rstrip(".")
        if not hostname:
            raise SanitizationError("Hostname cannot be empty")
        try:
            port = parsed.port
        except ValueError as exc:
            raise SanitizationError(f"Invalid port specification: {exc}") from exc
        if port is not None and (port < 1 or port > 65535):
            raise SanitizationError(f"Port {port} out of valid bounds (1-65535)")
        if block_private_ips:
            ip = _try_parse_ip(hostname)
            if ip is not None and _is_restricted_ip(ip):
                raise SanitizationError(f"SSRF violation: target address '{hostname}' ({ip}) is a restricted IP")
    path = parsed.path
    if "\x00" in path:
        raise SanitizationError("URL path contains illegal null byte")
    netloc = parsed.netloc
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))

def is_safe_url(url: Any, **kwargs: Any) -> bool:
    try:
        sanitize_url(url, **kwargs)
        return True
    except (SanitizationError, TypeError):
        return False

def validate_json_payload(raw_data: str | bytes | dict[str, Any] | list[Any] | Any, max_size_bytes: int=50 * 1024 * 1024, max_depth: int=100, require_dict: bool=True) -> dict[str, Any] | list[Any]:
    if raw_data is None:
        raise MalformedPayloadError("Payload cannot be None")
    parsed: Any
    if isinstance(raw_data, bytes):
        if len(raw_data) > max_size_bytes:
            raise PayloadLimitExceeded(f"Payload size ({len(raw_data)} bytes) exceeds limit of {max_size_bytes} bytes")
        if len(raw_data) == 0:
            raise MalformedPayloadError("Payload cannot be zero-length")
        try:
            text = raw_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MalformedPayloadError(f"Malformed UTF-8 byte sequence: {exc}") from exc
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MalformedPayloadError(f"Malformed JSON syntax: {exc}") from exc
    elif isinstance(raw_data, str):
        byte_len = len(raw_data.encode("utf-8"))
        if byte_len > max_size_bytes:
            raise PayloadLimitExceeded(f"Payload size ({byte_len} bytes) exceeds limit of {max_size_bytes} bytes")
        stripped = raw_data.strip()
        if not stripped:
            raise MalformedPayloadError("Payload cannot be empty or whitespace")
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise MalformedPayloadError(f"Malformed JSON syntax: {exc}") from exc
    elif isinstance(raw_data, (dict, list)):
        parsed = raw_data
    else:
        raise MalformedPayloadError(f"Invalid payload input type: {type(raw_data).__name__}")
    if require_dict and (not isinstance(parsed, dict)):
        raise MalformedPayloadError(f"Top-level payload must be a JSON object (dict), got {type(parsed).__name__}")

    def _check_depth(node: Any, current_depth: int) -> None:
        if current_depth > max_depth:
            raise MalformedPayloadError(f"Payload nesting depth ({current_depth}) exceeds maximum limit of {max_depth}")
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    raise MalformedPayloadError(f"Dict key must be str, got {type(k).__name__}")
                if "\x00" in k:
                    raise MalformedPayloadError("Dict key contains null byte")
                _check_depth(v, current_depth + 1)
        elif isinstance(node, list):
            for item in node:
                _check_depth(item, current_depth + 1)
    _check_depth(parsed, 1)
    return parsed

def sanitize_unicode_text(text: str | None, max_length: int=1048576, strip_homoglyphs: bool=True) -> str:
    if text is None:
        raise TypeError("Text cannot be None")
    if not isinstance(text, str):
        raise TypeError(f"Text must be a str, got {type(text).__name__}")
    if len(text) > max_length:
        raise PayloadLimitExceeded(f"Text length ({len(text)}) exceeds maximum limit of {max_length}")
    res = text
    for zw in _ZERO_WIDTH_CHARS:
        if zw in res:
            res = res.replace(zw, "")
    for bidi in _BIDI_CONTROLS:
        if bidi in res:
            res = res.replace(bidi, "")
    res = _CONTROL_CHAR_RE.sub("", res)
    if strip_homoglyphs:
        res = unicodedata.normalize("NFKC", res)
    return res

def safe_atomic_write(target_path: str | Path, content: str | bytes, mode: int=420) -> int:
    path = Path(target_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    fd, tmp_file = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_file, mode)
        os.replace(tmp_file, str(path))
    finally:
        try:
            if os.path.exists(tmp_file):
                os.unlink(tmp_file)
        except OSError:
            pass
    return len(data)
