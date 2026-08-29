"""Translate raw exception text into operator-readable failure messages.

F6 finding from the 22-repo line-by-line review. The original
inspiration (reclip-main) just bubbled `str(e)` straight to the user;
this module is BD's version: a pattern-matching translator that turns
the most common yt-dlp / playwright / requests / curl_cffi failure
strings into a short, actionable sentence the operator can act on
without reading a stack trace.

Usage from runner.py / extractors.py:

    from .friendly_error import friendly_error
    try:
        download(url)
    except Exception as e:
        msg = friendly_error(e, context={"site": "vixen"})
        self.jobs[url]["message"] = msg

The translator is best-effort and fail-open: unmatched errors fall
through to `str(e)` truncated to 200 chars so the operator still gets
the raw text. Adding new patterns is a one-line change; the dict at
the top of this module is the only thing to update.

Design rules:
  • Translated messages must be ≤120 chars (queue row UI is tight)
  • Must include a "what to do next" hint when possible
  • Must NOT swallow the original error class — that's the job of
    classify_failure() in retry_policy.py
"""
from __future__ import annotations

import re
from typing import Any, Optional


_TURNSTILE_AVAILABLE_MESSAGE = (
    "Cloudflare Turnstile. Scrapling/nodriver bypass usually works."
)

# Pattern → (friendly_message, hint). Patterns are matched with
# re.search() (substring match, not anchored). First match wins.
# Order matters: put specific patterns above general ones.
#
# When the pattern needs to reference a captured group, use {0}, {1}
# etc in the friendly message — _format() will fill them in.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── yt-dlp ─────────────────────────────────────────────────────────
    (re.compile(r"Sign in to confirm you'?re not a bot", re.I),
     "Site demands sign-in to prove not a bot. Action: log in via Take Over."),
    (re.compile(r"This video is unavailable", re.I),
     "Video unavailable at source (taken down or geo-blocked)."),
    (re.compile(r"Private video", re.I),
     "Video is private — needs the original uploader's account."),
    (re.compile(r"members[- ]only", re.I),
     "Members-only content. Action: verify subscription on this account."),
    (re.compile(r"requested format( is)? not available", re.I),
     "Requested quality not available. Try a different quality preference."),
    (re.compile(r"unable to extract.*?initial player response", re.I),
     "Extractor broken — site changed its player. Action: update yt-dlp."),
    (re.compile(r"HTTP Error 429", re.I),
     "Rate-limited (429). Workers will back off automatically."),
    (re.compile(r"HTTP Error 403", re.I),
     "Forbidden (403). Likely IP-blocked or cookie expired. Try rotating account."),
    (re.compile(r"HTTP Error 404", re.I),
     "Not found (404). URL is dead or moved."),
    (re.compile(r"HTTP Error 5\d\d", re.I),
     "Server error at source. Will retry automatically."),
    # ── Playwright / browser ──────────────────────────────────────────
    (re.compile(r"Target page, context or browser has been closed", re.I),
     "Browser tab closed mid-operation. Often a Chrome crash; retry."),
    (re.compile(r"Timeout \d+ms exceeded", re.I),
     "Page took too long to load. May be slow site or blocked CDN."),
    (re.compile(r"net::ERR_NAME_NOT_RESOLVED", re.I),
     "DNS lookup failed. Check VPN/network connectivity."),
    (re.compile(r"net::ERR_CONNECTION_REFUSED", re.I),
     "Connection refused. Server may be down or blocking your IP."),
    (re.compile(r"net::ERR_CERT_", re.I),
     "TLS certificate error. Site may be misconfigured or MITM'd."),
    (re.compile(r"Page\.goto: net::ERR_ABORTED", re.I),
     "Navigation aborted. Often a CSP or download interception issue."),
    (re.compile(r"locator\.click", re.I),
     "UI selector failed. Site changed layout. Action: re-teach this site."),
    # ── Network / HTTP libs ───────────────────────────────────────────
    (re.compile(r"SSLError|SSL: CERTIFICATE_VERIFY_FAILED", re.I),
     "TLS verify failed. Site cert may be expired or self-signed."),
    (re.compile(r"ConnectTimeout|Connection timed out", re.I),
     "Connection timed out. Site slow, VPN down, or firewall blocking."),
    (re.compile(r"ReadTimeout|Read timed out", re.I),
     "Read timed out mid-download. Server stopped sending data."),
    (re.compile(r"ChunkedEncodingError|IncompleteRead", re.I),
     "Server cut the connection mid-download. Will resume from .part if possible."),
    (re.compile(r"TooManyRedirects", re.I),
     "Too many redirects — likely a login/captcha loop."),
    # ── Disk / filesystem ─────────────────────────────────────────────
    (re.compile(r"No space left on device|ENOSPC", re.I),
     "Out of disk space. Action: free space or move download_dir."),
    (re.compile(r"Permission denied|EACCES", re.I),
     "Permission denied. Check filesystem ACLs on download_dir."),
    (re.compile(r"OSError.*?reserved", re.I),
     "Filename uses a Windows-reserved name (CON/PRN/...). Will rename."),
    # ── Captcha / bot detection ───────────────────────────────────────
    (re.compile(r"cloudflare|cf[- ]chl[- ]?bypass", re.I),
     "Cloudflare challenge. FlareSolverr should auto-handle; check it's running."),
    (re.compile(r"hcaptcha|recaptcha", re.I),
     "Captcha required. 2Captcha/Capmonster will solve if configured."),
    (re.compile(r"turnstile", re.I),
     _TURNSTILE_AVAILABLE_MESSAGE),
    # ── FFmpeg / media ─────────────────────────────────────────────────
    (re.compile(r"ffmpeg.*?exited with code", re.I),
     "FFmpeg failed. Check the source file isn't corrupt; will be retried."),
    (re.compile(r"moov atom not found", re.I),
     "MP4 file is incomplete (no moov atom). Download interrupted; retrying."),
]

# Truncation cap. The queue row UI shows ~120 chars max before truncating
# with ellipsis; raw errors longer than this clutter the log noise.
_FALLBACK_CAP = 200


def friendly_error(err: Any, context: Optional[dict] = None) -> str:
    """Translate `err` into a short, operator-readable message.

    `err` can be an Exception, a string, or any object with __str__.
    `context` may include a measured ``turnstile_bypass`` capability state.
    Without that measurement, Turnstile advice is UNKNOWN rather than an
    availability claim.
    """
    raw = str(err) if err is not None else ""
    if not raw:
        return "Unknown error."
    for pattern, friendly in _PATTERNS:
        m = pattern.search(raw)
        if m:
            if friendly == _TURNSTILE_AVAILABLE_MESSAGE:
                supplied = (context or {}).get("turnstile_bypass")
                state = supplied if isinstance(supplied, dict) else {
                    "available": False,
                    "status": "unknown",
                    "reason": "measurement_not_supplied",
                }
                status = state.get("status")
                available = state.get("available")
                reason = state.get("reason") or "measurement_not_supplied"
                if status == "available" and available is True:
                    return friendly
                if status != "unavailable" or available is not False:
                    return (
                        "Cloudflare Turnstile. Bypass availability unknown "
                        f"({reason}). Use Take Over."
                    )
                return (
                    "Cloudflare Turnstile. Bypass unavailable "
                    f"({reason}). Use Take Over."
                )
            try:
                return friendly.format(*m.groups())
            except (IndexError, KeyError):
                return friendly  # message had no placeholders
    # Fallback: truncate raw error. Strip leading exception-class names
    # like "playwright._impl._api_types.Error: " which add noise without
    # information for the operator.
    cleaned = re.sub(r"^[\w.]+(?:Error|Exception): ", "", raw)
    if len(cleaned) > _FALLBACK_CAP:
        cleaned = cleaned[: _FALLBACK_CAP - 1] + "…"
    return cleaned


def assert_url_round_trip(url: str) -> str:
    """F4: cheap defense against URL-mangling bugs.

    Parses `url` and serializes it back. If the round-trip changes the
    string (beyond trivial normalization), something earlier in the
    pipeline corrupted it — log and return the parsed version so the
    download still has a chance to succeed.

    Returns the (possibly normalized) URL. Never raises — fail-open is
    the policy for a defensive assertion.
    """
    if not url or not isinstance(url, str):
        return url
    try:
        from urllib.parse import urlparse, urlunparse
        parts = urlparse(url)
        if not parts.scheme or not parts.netloc:
            return url  # not a parseable URL; let the caller deal
        canonical = urlunparse(parts)
        # Allow trailing-slash differences — they're semantically equal
        # and noisy to log. Anything else is real corruption.
        if canonical.rstrip("/") != url.rstrip("/"):
            import sys
            sys.stderr.write(
                f"[url-round-trip] mismatch:\n  in:  {url}\n  out: {canonical}\n"
            )
        return canonical
    except Exception:
        return url  # parser failure must never break the caller
