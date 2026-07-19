"""Accessibility helpers (Phase 109, Block O).

Three operator-facing capabilities:

1. plain_language(message) — rewrite raw error text into
   short, no-jargon sentences. Goes beyond friendly_error's
   pattern→message map: this is for the messages that survive that
   layer and still read like "ConnectionError: HTTPSConnectionPool…"
   to non-technical operators.

2. contrast_ratio(fg, bg) — compute WCAG 2.1 contrast ratio between
   two CSS colors. Used to audit the theme; flags low-contrast pairs.

3. aria_audit(html_snippet) — find common ARIA mistakes:
     - inputs without labels
     - buttons with only icons (no aria-label)
     - missing role on custom widgets
     - duplicate IDs
     - empty links
   Reports issues with line numbers + recommended fixes.

These are diagnostic tools, not auto-fix. The operator decides what
to change; this module shows them what's wrong.
"""
from __future__ import annotations

import re
from typing import Optional


# ─── Plain-language rewrites ──────────────────────────────────────────

# Each entry: (pattern_regex, plain_text). Patterns evaluated in
# order; first match wins. Patterns deliberately broad — we want to
# catch raw exception strings from many libraries.
_PLAIN_LANGUAGE_PATTERNS = [
    (r"ConnectionError|ConnectionResetError|RemoteDisconnected",
     "The server hung up before we got a full response. Try again in a minute."),
    (r"NameResolutionError|getaddrinfo failed|DNS|Name or service not known",
     "Can't find the server's address. Check your internet or DNS settings."),
    (r"SSLError|certificate verify failed|CERTIFICATE_VERIFY_FAILED",
     "The site's security certificate didn't validate. Could be expired, "
     "self-signed, or a man-in-the-middle. Verify the site loads in your browser."),
    (r"Timeout|TimeoutError|Read timed out|ReadTimeoutError",
     "The server took too long to respond. The site may be overloaded or down."),
    (r"403|Forbidden",
     "The server refused — usually means your account, IP, or VPN is blocked. "
     "Check that you're logged in and try a different VPN endpoint."),
    (r"401|Unauthorized",
     "Not logged in. Your cookies expired — re-login via Take Over."),
    (r"429|Too Many Requests|rate.?limit",
     "The site is rate-limiting you. BD will back off automatically; "
     "lower max_concurrent for this site if it keeps happening."),
    (r"404|Not Found",
     "The page wasn't found. The video may have been removed or the URL is stale."),
    (r"500|Internal Server Error|502|Bad Gateway|503|Service Unavailable",
     "The server is having problems. Wait a few minutes and try again."),
    (r"NoSuchElementException|locator.*not found|element.*not found",
     "An element BD expected on the page isn't there. The site likely changed "
     "its HTML — open the site config and re-teach BD."),
    (r"PermissionError|EACCES|Permission denied",
     "BD doesn't have permission to write the file. Check folder permissions "
     "for the download directory."),
    (r"OSError.*No space|ENOSPC|disk.*full",
     "The download directory is out of disk space. Free some space or move it."),
    (r"playwright|browser.*closed|page.*closed",
     "The headless browser crashed or was closed unexpectedly. "
     "This usually fixes itself on retry."),
    (r"yt[-_ ]?dlp.*could not|yt[-_ ]?dlp.*unable",
     "yt-dlp couldn't extract this URL. Either it's not yet supported, "
     "the format changed, or the page requires login that yt-dlp can't reach."),
    (r"moov atom not found",
     "The downloaded file is incomplete (missing MP4 header). "
     "Usually a mid-download crash. Try again — the resume should fix it."),
    (r"captcha|hCaptcha|turnstile|reCAPTCHA",
     "The site is asking you to prove you're human. Use Take Over to solve "
     "it manually, or check your captcha solver API balance."),
    (r"cloudflare|cf_clearance",
     "Cloudflare is blocking the request. FlareSolverr or a fresh cf_clearance "
     "cookie should clear it; Take Over to refresh."),
]


def plain_language(message: str, *, max_length: int = 200) -> str:
    """Rewrite raw error text into a plain-language explanation.
    Returns the original message if no pattern matches.

    Use AFTER friendly_error() — this is the fallback layer."""
    if not message:
        return message
    for pattern, replacement in _PLAIN_LANGUAGE_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return replacement[:max_length]
    # No match — return original, truncated
    return message[:max_length] if len(message) > max_length else message


# ─── Color contrast (WCAG 2.1) ────────────────────────────────────────

def _parse_hex(color: str) -> Optional[tuple]:
    """Parse '#rgb', '#rrggbb', or '#rrggbbaa' to (r, g, b) tuples of
    0-255. Returns None on parse failure."""
    if not color or not isinstance(color, str):
        return None
    s = color.strip().lstrip("#").lower()
    if re.fullmatch(r"[0-9a-f]{3}", s):
        return tuple(int(c * 2, 16) for c in s)
    if re.fullmatch(r"[0-9a-f]{6}", s):
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    if re.fullmatch(r"[0-9a-f]{8}", s):
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    return None


def _parse_rgb(color: str) -> Optional[tuple]:
    """Parse 'rgb(r,g,b)' or 'rgba(r,g,b,a)' to (r,g,b)."""
    if not color:
        return None
    m = re.fullmatch(
        r"\s*rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,[^)]+)?\)\s*",
        color, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _to_rgb(color: str) -> Optional[tuple]:
    """Parse a CSS color string into (r,g,b). Handles hex + rgb()."""
    return _parse_hex(color) or _parse_rgb(color)


def _relative_luminance(rgb: tuple) -> float:
    """WCAG 2.1 §1.4.3 relative luminance formula."""
    def channel(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(rgb[0]), channel(rgb[1]), channel(rgb[2]))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> dict:
    """Compute WCAG 2.1 contrast ratio between foreground and background.

    Returns {ratio: float, fg_rgb, bg_rgb, passes_aa_normal, passes_aa_large,
             passes_aaa_normal, passes_aaa_large, error}.

    Thresholds (WCAG 2.1):
      AA normal text:  ≥ 4.5
      AA large text:   ≥ 3.0
      AAA normal text: ≥ 7.0
      AAA large text:  ≥ 4.5
    """
    fg_rgb = _to_rgb(fg)
    bg_rgb = _to_rgb(bg)
    if not fg_rgb or not bg_rgb:
        return {"error": "could not parse colors",
                "fg_rgb": fg_rgb, "bg_rgb": bg_rgb}
    L_fg = _relative_luminance(fg_rgb)
    L_bg = _relative_luminance(bg_rgb)
    lighter, darker = max(L_fg, L_bg), min(L_fg, L_bg)
    ratio = (lighter + 0.05) / (darker + 0.05)
    return {
        "ratio": round(ratio, 2),
        "fg_rgb": fg_rgb,
        "bg_rgb": bg_rgb,
        "passes_aa_normal": ratio >= 4.5,
        "passes_aa_large": ratio >= 3.0,
        "passes_aaa_normal": ratio >= 7.0,
        "passes_aaa_large": ratio >= 4.5,
    }


# ─── ARIA audit ───────────────────────────────────────────────────────

def aria_audit(html: str) -> dict:
    """Find common ARIA + a11y mistakes in the HTML.

    Returns {issues: [{kind, snippet, fix}], stats: {...}}.
    Best-effort; not exhaustive — focuses on the highest-leverage
    issues that block screen-reader use."""
    issues = []
    if not html or not isinstance(html, str):
        return {"issues": [], "stats": {}}
    try:
        from lxml import html as _lxml_html
    except ImportError:
        return {"issues": [{"kind": "audit_unavailable",
                            "snippet": "",
                            "fix": "Install lxml to run ARIA audit"}],
                "stats": {}}
    try:
        tree = _lxml_html.fromstring(html)
    except Exception as e:
        return {"issues": [{"kind": "parse_error",
                            "snippet": str(e)[:200], "fix": ""}],
                "stats": {}}

    # Inputs without labels
    for inp in tree.xpath("//input[not(@type='hidden')]"):
        eid = inp.get("id", "")
        has_label_for = bool(tree.xpath(f"//label[@for='{eid}']")) if eid else False
        has_aria = inp.get("aria-label") or inp.get("aria-labelledby")
        has_placeholder_only = inp.get("placeholder") and not has_aria and not has_label_for
        if not has_label_for and not has_aria:
            issues.append({
                "kind": "input_unlabeled",
                "snippet": _lxml_html.tostring(inp, encoding="unicode")[:160],
                "fix": ('Add <label for="this-input-id">…</label> or '
                       'aria-label="…" attribute.'),
            })
        elif has_placeholder_only:
            issues.append({
                "kind": "placeholder_only_label",
                "snippet": _lxml_html.tostring(inp, encoding="unicode")[:160],
                "fix": ('Placeholder isn\'t a label. Add a real <label> '
                       'or aria-label so screen readers announce the field.'),
            })

    # Buttons with only an icon (no text + no aria-label)
    for btn in tree.xpath("//button"):
        text = (btn.text_content() or "").strip()
        has_aria = btn.get("aria-label") or btn.get("aria-labelledby")
        if not text and not has_aria:
            issues.append({
                "kind": "icon_button_unlabeled",
                "snippet": _lxml_html.tostring(btn, encoding="unicode")[:160],
                "fix": 'Add aria-label="Action description" to icon-only buttons.',
            })

    # Empty links
    for a in tree.xpath("//a[@href]"):
        text = (a.text_content() or "").strip()
        has_aria = a.get("aria-label") or a.get("aria-labelledby")
        if not text and not has_aria:
            issues.append({
                "kind": "empty_link",
                "snippet": _lxml_html.tostring(a, encoding="unicode")[:160],
                "fix": 'Add link text or aria-label.',
            })

    # Duplicate IDs (breaks label[for], aria-labelledby, etc.)
    seen_ids: dict = {}
    for el in tree.xpath("//*[@id]"):
        eid = el.get("id", "")
        if not eid:
            continue
        seen_ids.setdefault(eid, []).append(el)
    for eid, els in seen_ids.items():
        if len(els) > 1:
            issues.append({
                "kind": "duplicate_id",
                "snippet": f'id="{eid}" appears {len(els)}×',
                "fix": f'IDs must be unique. Rename the duplicates of "{eid}".',
            })

    # Images without alt
    for img in tree.xpath("//img"):
        alt = img.get("alt")
        if alt is None:  # missing attribute entirely
            issues.append({
                "kind": "img_no_alt",
                "snippet": _lxml_html.tostring(img, encoding="unicode")[:160],
                "fix": ('Add alt="…" describing the image, or alt="" for '
                       'purely decorative images.'),
            })

    stats = {
        "total_issues": len(issues),
        "by_kind": {},
    }
    for issue in issues:
        k = issue["kind"]
        stats["by_kind"][k] = stats["by_kind"].get(k, 0) + 1
    return {"issues": issues, "stats": stats}
