"""Cookie clipboard helper (Phase 158, Block Q).

Operators commonly extract cookies from their browser via DevTools
or a "Cookie Editor" extension, then paste them into BD. This module
parses several common formats:

  • Netscape cookie file (the format yt-dlp's --cookies flag accepts)
  • JSON dump (one object per cookie)
  • DevTools "Copy as cURL" → we extract the -b "k1=v1; k2=v2" part
  • Raw "Cookie:" header value ("k1=v1; k2=v2; ...")
  • EditThisCookie / CookieEditor JSON exports

Returns normalized cookie dicts ready to drop into BD's cookies/<sid>.json.

Auto-detection by structure — operator pastes, we figure out which
format and convert. Reports parse confidence so the operator can
verify.
"""
from __future__ import annotations

import json
import re
from typing import Optional


_CTL_RE = re.compile(r"[\t\r\n]")


def _cookie_fields_safe(*fields) -> bool:
    """True if no field contains a TAB/CR/LF. A cookie carrying a control
    character in a name/value/domain/path is malformed (RFC 6265 forbids control
    characters in cookie octets) and is a field/line-injection vector when the
    cookie is serialized, so such a cookie is dropped rather than emitted
    (F-AUTH02-02)."""
    return not any(_CTL_RE.search(str(f)) for f in fields if f is not None)


def _parse_netscape(text: str) -> list:
    """Parse Netscape cookies.txt format. Each non-comment line is:
        domain  flag  path  secure  expires  name  value
    Tab-separated."""
    out = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line or line.startswith("#"):
            # Sometimes prefixed with #HttpOnly_ (this prefix indicates
            # http-only but we accept the cookie either way)
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            else:
                continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, flag, path, secure, expires, name, value = parts[:7]
        try:
            exp = int(expires)
        except ValueError:
            exp = 0
        out.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure.lower() == "true",
            "expires": exp,
            "httpOnly": False,  # netscape format doesn't carry this reliably
        })
    return out


def _parse_json_array(text: str) -> Optional[list]:
    """EditThisCookie / Cookie Editor format: a JSON array of
    {name, value, domain, path, expirationDate, secure, httpOnly}."""
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out = []
    for c in data:
        if not isinstance(c, dict) or "name" not in c or "value" not in c:
            continue
        out.append({
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", False)),
            "expires": int(c.get("expirationDate", 0) or 0),
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": c.get("sameSite", "lax"),
        })
    return out


def _parse_cookie_header(text: str) -> list:
    """Parse a raw Cookie header value like 'k1=v1; k2=v2; k3=v3'.
    No metadata available — domain/path/expires defaulted."""
    out = []
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name and value:
            out.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": "",
                "path": "/",
                "secure": True,  # safer default
                "expires": 0,
                "httpOnly": False,
            })
    return out


def _parse_curl_cookies(text: str) -> Optional[list]:
    """Extract from a 'curl ... -b "..." ...' command paste."""
    # Match -b 'value' or -b "value" or --cookie ...
    m = re.search(r"""(?:-b|--cookie)\s+['"]([^'"]+)['"]""", text)
    if not m:
        return None
    return _parse_cookie_header(m.group(1))


def auto_detect_and_parse(text: str) -> dict:
    """Try every parser; return whichever produces the most cookies.

    Returns {format, cookies, count, confidence}."""
    if not text or not text.strip():
        return {"format": None, "cookies": [], "count": 0, "confidence": 0}

    candidates = []

    # JSON array
    if text.lstrip().startswith("["):
        parsed = _parse_json_array(text)
        if parsed:
            candidates.append(("json_array", parsed))

    # Netscape — recognized by leading "# Netscape HTTP Cookie File"
    # or tab-delimited rows
    if "Netscape HTTP Cookie File" in text or \
       any("\t" in line and line.count("\t") >= 5
           for line in text.splitlines()[:20]):
        parsed = _parse_netscape(text)
        if parsed:
            candidates.append(("netscape", parsed))

    # cURL command paste
    if "curl " in text and ("-b " in text or "--cookie" in text):
        parsed = _parse_curl_cookies(text)
        if parsed:
            candidates.append(("curl", parsed))

    # Cookie header (last resort — most permissive)
    if "=" in text and ";" in text:
        parsed = _parse_cookie_header(text)
        if parsed:
            candidates.append(("cookie_header", parsed))

    if not candidates:
        return {"format": None, "cookies": [], "count": 0,
                "confidence": 0,
                "error": "no parser matched"}

    # Pick the parser that found the most cookies
    candidates.sort(key=lambda x: -len(x[1]))
    fmt, cookies = candidates[0]
    # Confidence: higher when format markers were strong
    confidence = 90 if fmt in ("netscape", "json_array") else (
        70 if fmt == "curl" else 40)
    return {
        "format": fmt,
        "cookies": cookies,
        "count": len(cookies),
        "confidence": confidence,
        "all_attempts": [{"format": c[0], "count": len(c[1])}
                         for c in candidates],
    }


def to_playwright_format(cookies: list, *,
                        default_domain: str = "") -> list:
    """Convert normalized cookies to Playwright's expected shape.

    Playwright wants: name, value, domain (or url), path, expires,
    httpOnly, secure, sameSite (Strict/Lax/None)."""
    out = []
    for c in cookies:
        domain = c.get("domain") or default_domain
        if not _cookie_fields_safe(c.get("name", ""), c.get("value", ""),
                                   c.get("path", "/"), domain):
            # drop a cookie carrying a control char (injection vector) rather
            # than pass a malformed name/value/domain to the browser context
            continue
        item = {
            "name": c["name"],
            "value": c["value"],
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
        }
        if domain:
            item["domain"] = domain.lstrip(".")
        if c.get("expires"):
            item["expires"] = int(c["expires"])
        ss = (c.get("sameSite") or "lax").lower()
        item["sameSite"] = {"strict": "Strict", "lax": "Lax",
                            "none": "None"}.get(ss, "Lax")
        out.append(item)
    return out


def to_netscape_text(cookies: list) -> str:
    """Render cookies back as Netscape cookies.txt format."""
    lines = ["# Netscape HTTP Cookie File",
             "# Generated by BulkDownloader cookie_clipboard"]
    for c in cookies:
        domain = c.get("domain", "")
        if domain and not domain.startswith("."):
            domain = "." + domain
        path = c.get("path", "/")
        name = c.get("name", "")
        value = c.get("value", "")
        if not _cookie_fields_safe(domain, path, name, value):
            # a TAB/CR/LF in any field would terminate a field or forge a row
            continue
        lines.append("\t".join([
            domain or ".unknown",
            "TRUE",
            path,
            "TRUE" if c.get("secure") else "FALSE",
            str(int(c.get("expires", 0))),
            name,
            value,
        ]))
    return "\n".join(lines) + "\n"
