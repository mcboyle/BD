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


# A cookie name as browsers apply RFC 6265 (aac-fix r2, D170 R2): Chromium
# stores and sends cart[1], user[id], a:b, x@y, q(1), k/v, "na me" and an empty
# name; it refuses a name with a control character (TAB included), ';' or '=',
# or with leading/trailing whitespace. The strict RFC token rule dropped real
# site cookies. Junk from a half-matched JSON / cURL paste is kept out by
# parsing those pastes only with their own parser (F2), not by this rule.
_BAD_NAME_RE = re.compile(r"[\x00-\x1f\x7f;=]")


def _valid_cookie_name(name) -> bool:
    return (isinstance(name, str) and not _BAD_NAME_RE.search(name)
            and name == name.strip(" "))


def _shown_name(name) -> str:
    """A cookie name as it may appear in a reply or a log: escaped, short.
    Never the value (aac-fix r2, D170 R3)."""
    if not isinstance(name, str):
        return f"<{type(name).__name__}>"
    return name.encode("unicode_escape").decode("ascii")[:64]


def _skip(skipped, name, why) -> None:
    """Record a cookie a parser left out, so no cookie is dropped silently."""
    if skipped is not None:
        skipped.append({"name": _shown_name(name), "why": why})


def _expiry(value) -> int:
    """An expiry field as whole epoch seconds; 0 (session) when it is not a
    number ("Session", "", None) -- never an exception (aac-fix F6c/F7)."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _parse_netscape(text: str, skipped: Optional[list] = None) -> list:
    """Parse Netscape cookies.txt format. Each non-comment line is:
        domain  flag  path  secure  expires  name  value
    Tab-separated."""
    out = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        http_only = False
        if not line or line.startswith("#"):
            # A #HttpOnly_ prefix marks an http-only cookie (curl/yt-dlp).
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
                http_only = True
            else:
                continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, flag, path, secure, expires, name, value = parts[:7]
        if not _valid_cookie_name(name):
            _skip(skipped, name, "name a browser refuses")
            continue
        out.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure.lower() == "true",
            "expires": _expiry(expires),
            "httpOnly": http_only,
        })
    return out


def _parse_json_array(text: str, skipped: Optional[list] = None) -> Optional[list]:
    """EditThisCookie / Cookie Editor format: a JSON array of
    {name, value, domain, path, expirationDate, secure, httpOnly}; also a
    Playwright storage_state object {"cookies": [...]} (whose cookies carry
    `expires`). A cookie whose name is not a cookie token, or whose value is
    not text or a number, is skipped (aac-fix F2/F7)."""
    try:
        data = json.loads(text)
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        data = data["cookies"]
    if not isinstance(data, list):
        return None
    out = []
    for c in data:
        if not isinstance(c, dict) or "name" not in c or "value" not in c:
            continue
        name, value = c.get("name"), c.get("value")
        if not _valid_cookie_name(name):
            _skip(skipped, name, "name a browser refuses")
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            _skip(skipped, name, "value is not text or a number")
            continue
        domain, path = c.get("domain", ""), c.get("path", "/")
        out.append({
            "name": name,
            "value": str(value),
            "domain": domain if isinstance(domain, str) else "",
            "path": path if isinstance(path, str) and path else "/",
            "secure": bool(c.get("secure", False)),
            "expires": _expiry(c.get("expirationDate", c.get("expires", 0))),
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": c.get("sameSite", "lax"),
        })
    return out


def _parse_cookie_header(text: str, skipped: Optional[list] = None) -> list:
    """Parse a raw Cookie header value like 'k1=v1; k2=v2; k3=v3'.
    No metadata available — domain/path/expires defaulted."""
    text = text.strip()
    # The docstring's "Cookie:" header form: drop the header name itself.
    if text[:7].lower() == "cookie:":
        text = text[7:]
    out = []
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not _valid_cookie_name(name):
            _skip(skipped, name, "name a browser refuses")
        else:  # an empty value is still a cookie
            out.append({
                "name": name,
                "value": value.strip(),
                "domain": "",
                "path": "/",
                "secure": True,  # safer default
                "expires": 0,
                "httpOnly": False,
            })
    return out


def _parse_curl_cookies(text: str, skipped: Optional[list] = None) -> Optional[list]:
    """Extract from a 'curl ... -b "..." ...' command paste."""
    # -b / --cookie 'value' or "value" (each quote style may hold the other),
    # else a -H / --header 'Cookie: ...' (DevTools "Copy as cURL" form).
    m = re.search(r"""(?:-b|--cookie)\s+(?:'([^']*)'|"([^"]*)")""", text)
    if not m:
        m = re.search(r"""(?:-H|--header)\s+(?:'([Cc]ookie:[^']*)'|"([Cc]ookie:[^"]*)")""", text)
    if not m:
        return None
    return _parse_cookie_header(m.group(1) if m.group(1) is not None else m.group(2), skipped)


def auto_detect_and_parse(text: str) -> dict:
    """Try every parser; return whichever produces the most cookies.

    Returns {format, cookies, count, confidence, all_attempts, skipped}; `skipped`
    names (never values) each cookie the chosen parser left out, and why."""
    if not isinstance(text, str) or not text.strip():
        return {"format": None, "cookies": [], "count": 0, "confidence": 0}

    candidates = []
    head = text.lstrip()
    # aac-fix F2: a JSON value or a cURL command is parsed only by its own
    # parser. The header parser on such a paste half-matches and returns junk
    # that a save would write over a good jar.
    structured = head.startswith(("[", "{")) or head.startswith("curl ")

    # JSON array, or a storage_state object
    if head.startswith(("[", "{")):
        skipped = []
        parsed = _parse_json_array(text, skipped)
        if parsed or skipped:
            candidates.append(("json_array", parsed or [], skipped))

    # Netscape — recognized by leading "# Netscape HTTP Cookie File"
    # or tab-delimited rows
    if "Netscape HTTP Cookie File" in text or \
       any("\t" in line and line.count("\t") >= 5
           for line in text.splitlines()[:20]):
        skipped = []
        parsed = _parse_netscape(text, skipped)
        if parsed or skipped:
            candidates.append(("netscape", parsed, skipped))

    # cURL command paste
    if "curl " in text:
        skipped = []
        parsed = _parse_curl_cookies(text, skipped)
        if parsed or skipped:
            candidates.append(("curl", parsed or [], skipped))

    # Cookie header (last resort — most permissive); one "name=value" counts
    if "=" in text and not structured:
        skipped = []
        parsed = _parse_cookie_header(text, skipped)
        if parsed or skipped:
            candidates.append(("cookie_header", parsed, skipped))

    if not candidates:
        return {"format": None, "cookies": [], "count": 0,
                "confidence": 0,
                "error": "no parser matched"}

    # Pick the parser that found the most cookies
    candidates.sort(key=lambda x: (-len(x[1]), -len(x[2])))
    fmt, cookies, skipped = candidates[0]
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
        "skipped": skipped,
    }


def to_playwright_format(cookies: list, *,
                        default_domain: str = "",
                        skipped: Optional[list] = None) -> list:
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
            _skip(skipped, c.get("name", ""), "control character in a field")
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
        # Extension exports say "no_restriction" for SameSite=None (aac-fix
        # F5); None is kept only on a Secure cookie, as cookies.py does.
        ss = str(c.get("sameSite") or "lax").strip().lower()
        same_site = {"strict": "Strict", "lax": "Lax", "none": "None",
                     "no_restriction": "None"}.get(ss, "Lax")
        if same_site == "None" and not item["secure"]:
            same_site = "Lax"
        item["sameSite"] = same_site
        out.append(item)
    return out


def to_netscape_text(cookies: list) -> str:
    """Render cookies back as Netscape cookies.txt format."""
    lines = ["# Netscape HTTP Cookie File",
             "# Generated by BulkDownloader cookie_clipboard"]
    for c in cookies:
        # aac-fix F8: a leading dot is a domain cookie (flag TRUE); a bare host
        # is host-only (flag FALSE). A cookie with no domain cannot be written.
        domain = c.get("domain", "")
        if not domain:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        name = c.get("name", "")
        value = c.get("value", "")
        if not _cookie_fields_safe(domain, path, name, value):
            # a TAB/CR/LF in any field would terminate a field or forge a row
            continue
        lines.append("\t".join([
            domain,
            flag,
            path,
            "TRUE" if c.get("secure") else "FALSE",
            str(_expiry(c.get("expires", 0))),
            name,
            value,
        ]))
    return "\n".join(lines) + "\n"
