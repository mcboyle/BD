"""v3.54 (Phase 7) — bd-doctor: diagnostics companion.

A standalone diagnostic pass over the install: environment, optional
dependencies, external tools, cookie freshness, and a failure-pattern
diagnoser. The roadmap's high-priority "selftest extension" item lives
here rather than bloating selftest.py — selftest.py runs on every boot
and must stay <100ms/check, whereas doctor checks are allowed to be
slower (subprocess calls to ffmpeg -version, etc.) because they run
on demand.

Surfaces:
  - `bdctl doctor`  — CLI entry (added in Phase 7)
  - `/api/doctor`   — JSON endpoint for the Status tab

The four diagnostic groups:

  environment_checks()
      Python version, ffmpeg/ffprobe presence + version, Playwright.

  dependency_checks()
      Every optional dependency reported with a friendly name and a
      yes/no — "curl_cffi: installed" rather than a raw ImportError.
      The operator can see at a glance what's available.

  cookie_freshness(sites_config)
      Per-site cookie_file age. A cookie older than the warn threshold
      is flagged before the runner hits an auth-expiry mid-download.

  diagnose_failure(error_message)
      Pattern-matches a needs_review / failed error string against
      known signatures (rate-limit, auth expiry, captcha, selector
      drift, network, disk) and returns {cause, suggestion, confidence}.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

# Status constants — mirror selftest.py so a doctor report and a
# selftest report can be rendered by the same UI code.
OK = "ok"
WARN = "warn"
FAIL = "fail"


def _result(status: str, test: str, message: str, **detail) -> dict:
    r = {"status": status, "test": test, "message": message}
    if detail:
        r["detail"] = detail
    return r


# ── Environment ─────────────────────────────────────────────────────────

def _tool_version(tool: str, version_arg: str = "-version") -> str | None:
    """Return the first line of `<tool> <version_arg>`, or None if the
    tool isn't on PATH / didn't run."""
    path = shutil.which(tool)
    if not path:
        return None
    try:
        out = subprocess.run([path, version_arg], capture_output=True,
                             text=True, timeout=5)
        first = (out.stdout or out.stderr or "").splitlines()
        return first[0].strip() if first else path
    except Exception:
        return path  # on PATH but version probe failed — still "present"


def environment_checks() -> list[dict]:
    """Python + external-tool diagnostics."""
    checks: list[dict] = []

    # Python version — the app targets 3.9+
    pyver = sys.version_info
    if pyver < (3, 9):
        checks.append(_result(
            FAIL, "python",
            f"Python {pyver.major}.{pyver.minor} is too old "
            f"(need 3.9+).",
            version=f"{pyver.major}.{pyver.minor}.{pyver.micro}"))
    else:
        checks.append(_result(
            OK, "python",
            f"Python {pyver.major}.{pyver.minor}.{pyver.micro}"))

    # ffmpeg / ffprobe — needed for metadata embedding, thumbnail
    # generation, integrity probing. Missing them isn't fatal (those
    # features degrade gracefully) so it's a WARN, not a FAIL.
    for tool in ("ffmpeg", "ffprobe"):
        ver = _tool_version(tool)
        if ver:
            checks.append(_result(OK, tool, ver))
        else:
            checks.append(_result(
                WARN, tool,
                f"{tool} not found on PATH.",
                hint=f"Metadata embedding / thumbnails / integrity "
                     f"checks that rely on {tool} will be skipped. "
                     f"Install ffmpeg to enable them."))

    # Playwright — reuse selftest's check so the two reports agree
    try:
        from .selftest import check_playwright
        checks.append(check_playwright())
    except Exception as e:
        checks.append(_result(
            WARN, "playwright",
            f"could not run playwright check: {type(e).__name__}"))

    return checks


# ── Optional dependencies ───────────────────────────────────────────────

# Friendly names + what each optional dep unlocks. The import name may
# differ from the pip name (e.g. curl_cffi).
_OPTIONAL_DEPS = [
    ("curl_cffi",   "curl_cffi",   "TLS-impersonating HTTP downloads"),
    ("cryptography", "cryptography", "encrypted cookie storage"),
    ("pywebpush",   "pywebpush",   "browser push notifications"),
    ("qrcode",      "qrcode",      "QR codes for share links"),
    ("apprise",     "apprise",     "notifications (80+ services)"),
    ("videohash",   "videohash",   "perceptual video dedup"),
    ("scrapling",   "scrapling",
     "adaptive scraping recovery and Turnstile bypass"),
    ("psutil",      "psutil",      "CPU/RAM dashboard widgets"),
    ("yt_dlp",      "yt-dlp",      "yt-dlp CDN fallback"),
]


def dependency_checks() -> list[dict]:
    """Report every optional dependency as installed / not-installed
    with a friendly description. Most missing optional packages are rendered
    OK-with-note; Scrapling is WARN when its advertised runtime bypass is not
    usable or cannot be measured."""
    import importlib
    checks: list[dict] = []
    for import_name, pip_name, unlocks in _OPTIONAL_DEPS:
        if import_name == "scrapling":
            try:
                from . import scrapling_adapter
                state = scrapling_adapter.capability_status()[
                    "turnstile_bypass"]
            except Exception as exc:
                state = {
                    "available": False,
                    "status": "unknown",
                    "reason": f"capability_probe_failed:{type(exc).__name__}",
                }
            if not isinstance(state, dict):
                state = {
                    "available": False,
                    "status": "unknown",
                    "reason": "capability_probe_returned_invalid_state",
                }
            status = state.get("status")
            available = state.get("available")
            reason = state.get("reason") or "measurement_not_supplied"
            installed = reason != "scrapling_not_installed"
            detail = {
                "unlocks": unlocks,
                "installed": installed,
                "available": status == "available" and available is True,
                "capability_status": status or "unknown",
                "reason": reason,
                "hint": "pip install 'scrapling[fetchers]'",
            }
            if status == "available" and available is True:
                checks.append(_result(
                    OK, "dep:scrapling", "installed; Turnstile bypass usable",
                    **detail))
            elif status == "unavailable" and available is False:
                checks.append(_result(
                    WARN, "dep:scrapling",
                    f"installed without usable Turnstile bypass ({reason})"
                    if installed else f"not installed ({reason})",
                    **detail))
            else:
                checks.append(_result(
                    WARN, "dep:scrapling",
                    f"Turnstile bypass availability unknown ({reason})",
                    **detail))
            continue
        try:
            importlib.import_module(import_name)
            checks.append(_result(
                OK, f"dep:{pip_name}", "installed",
                unlocks=unlocks, installed=True))
        except Exception:
            # Not installed — this is fine, just informational. We use
            # OK status (not WARN) so a clean optional-dep-free install
            # doesn't show a wall of warnings.
            checks.append(_result(
                OK, f"dep:{pip_name}",
                f"not installed — {unlocks} unavailable",
                unlocks=unlocks, installed=False,
                hint=f"pip install {pip_name}"))
    return checks


# ── Cookie freshness ────────────────────────────────────────────────────

def cookie_freshness(sites_config: dict,
                     warn_age_days: float = 14.0) -> list[dict]:
    """Per-site cookie_file age. A stale cookie is the single most
    common cause of a site that logs in fine one day and 'needs review'
    the next — flagging it here lets the operator re-auth proactively.

    `sites_config` is the {sid: cfg} mapping. Sites without a
    cookie_file are simply skipped (not every site uses one)."""
    checks: list[dict] = []
    now = time.time()
    warn_age_s = warn_age_days * 86400
    for sid, cfg in (sites_config or {}).items():
        if not isinstance(cfg, dict):
            continue
        cookie_file = cfg.get("cookie_file") or ""
        if not cookie_file:
            continue
        name = cfg.get("name") or sid
        try:
            mtime = os.path.getmtime(cookie_file)
        except OSError:
            checks.append(_result(
                WARN, f"cookie:{sid}",
                f"{name}: cookie_file is set but missing on disk.",
                site=name, path=cookie_file,
                hint="Re-export cookies or clear the cookie_file "
                     "field if the site doesn't need login."))
            continue
        age_days = (now - mtime) / 86400
        if (now - mtime) > warn_age_s:
            checks.append(_result(
                WARN, f"cookie:{sid}",
                f"{name}: cookies are {age_days:.0f} days old.",
                site=name, age_days=round(age_days, 1),
                hint="Stale cookies often cause auth-expiry mid-run. "
                     "Re-login to refresh them."))
        else:
            checks.append(_result(
                OK, f"cookie:{sid}",
                f"{name}: cookies {age_days:.0f} days old.",
                site=name, age_days=round(age_days, 1)))
    return checks


# ── Failure diagnoser ───────────────────────────────────────────────────

# Ordered list of (signatures, rule id, cause, suggestion, confidence,
# remedy). First match wins, so the most specific signatures come first.
#
# row1033: every rule carries a STABLE id (an automation keys on the id, not
# on prose that gets reworded) and a machine-readable remedy -- the command an
# operator would have had to retype out of the suggestion, or None where the
# repair is a human decision rather than a command.
#
# A single-word signature is matched on WORD BOUNDARIES. As bare substrings,
# "auth" also matched "author" and "authored" and answered with confidence
# "high"; a confidently wrong cause sends the operator to re-login instead of
# reading the HTTP 500 in front of them.
_FAILURE_SIGNATURES = [
    (("rate limit", "rate limited", "rate limiting", "429",
      "too many requests", "slow down"),
     "rate-limit",
     "Rate limiting",
     "The site is throttling requests. Increase the site's `delay` "
     "and `wait`, lower `max_concurrent`, or enable a warmup schedule.",
     "high",
     {"action": "throttle", "command": "bdctl site set <site> --delay +2"}),
    (("captcha", "turnstile", "recaptcha", "hcaptcha", "challenge"),
     "captcha",
     "Captcha challenge",
     "The site presented a captcha. Use 'Take over' to solve it "
     "manually, or configure a captcha provider (2captcha / capsolver) "
     "in the site's settings.",
     "high",
     {"action": "takeover", "command": "bdctl takeover <site>"}),
    (("login", "auth", "401", "403", "forbidden", "sign in",
      "session expired", "unauthorized"),
     "auth",
     "Authentication / session expiry",
     "Login failed or the session expired. Re-login for this site; "
     "if it keeps happening, the stored cookies are stale \u2014 refresh "
     "them. Check `bdctl doctor` for the cookie age.",
     "high",
     {"action": "relogin", "command": "bdctl login <site>"}),
    (("selector", "no such element", "element not found",
      "waiting for selector"),
     "selector-drift",
     "Selector drift",
     "A configured CSS selector no longer matches the page \u2014 the site "
     "changed its layout. Re-teach the site or update the "
     "trigger_selector / dl_selector in the editor.",
     "medium",
     {"action": "reteach", "command": "bdctl teach <site>"}),
    (("disk", "no space", "ENOSPC", "quota exceeded"),
     "disk",
     "Disk pressure",
     "The download disk is full or near-full. Free space, lower the "
     "site's `disk_threshold_gb`, or configure a spillover directory.",
     "high",
     {"action": "free-space", "command": "bdctl prune --older-than 30d"}),
    (("timeout", "timed out", "connection reset", "connection refused",
      "network", "dns", "unreachable", "ssl"),
     "network",
     "Network / connectivity",
     "A network error interrupted the download. Often transient \u2014 a "
     "retry usually clears it. If it persists, check connectivity, "
     "any proxy config, or whether the site is down.",
     "medium",
     {"action": "retry", "command": "bdctl retry <site>"}),
    (("404", "not found", "gone", "410"),
     "content-removed",
     "Content removed",
     "The target URL returned 404/410 \u2014 the content was taken down or "
     "the URL is wrong. Verify the URL is still valid on the site.",
     "medium",
     None),
]

# A single-word signature is matched with stem and boundary awareness.
# As a bare substring, "auth" also matched "author" and "authored" and
# answered with confidence "high"; a confidently wrong cause sends the
# operator to re-login instead of reading the HTTP 500 in front of them.
# Similarly, "gone" matched "undergone".
#
# Matching preserves genuine stems, plurals, and exception compound shapes
# (e.g. "selectors", "timeouterror", "connecttimeout", "openssl", "diskfull",
# "authentication", "authorization", "oauth") while strictly rejecting false
# positives ("author", "authored", "authority", "undergone").

_STATUS_PREFIX = (r"(?<![a-z0-9])(?:http(?:/\d(?:\.\d)?)?|status(?:[\s_-]*code)?"
                  r"|error(?:[\s_-]*code)?|code)[\s:=#]*")
_STATUS_PHRASE = (r"(?:too many requests|unauthorized|forbidden|not found"
                  r"|gone)(?![a-z0-9])")
_WORD_SUFFIX = (r"(?:e?s)?(?:error|exception|required|full|failed|failure)?"
                r"(?![a-z0-9])")


def _signature_matches(signature: str, haystack: str) -> bool:
    sig = signature.lower()
    # Numeric status codes match ONLY in a status context ("HTTP 429",
    # "status 403", "code: 429", "429 Too Many Requests"). A standalone number
    # is a byte count ("received 429 of 1048576 bytes"), a path segment
    # (/gallery/429) or a filename (429.jpg) at least as often as a status.
    if sig.isdigit():
        return (re.search(_STATUS_PREFIX + sig + r"(?!\w)", haystack) is not None
                or re.search(rf"(?<![\w/.-]){sig}\s+{_STATUS_PHRASE}",
                             haystack) is not None)
    # 'gone' indicates content removal; must not match 'undergone' or 'foregone'.
    if sig == "gone":
        return re.search(r"(?<![a-z0-9])gone(?![a-z0-9])", haystack) is not None
    # 'auth' must match authentication, authorization, authenticate, oauth, etc.,
    # but never 'author', 'authored', 'authoring', 'authorship', 'authority',
    # or 'authentic' / 'authenticity'.
    if sig == "auth":
        words = re.findall(r"[a-z0-9]+", haystack)
        for w in words:
            if "auth" in w:
                if re.search(r"author(?!iz|is)", w):
                    continue
                if re.search(r"authentic(?!at)", w):
                    continue
                return True
        return False
    # 'ssl' matches ssl, openssl, libssl, sslerror, etc., but not words like seamlessly/lossless.
    if sig == "ssl":
        return re.search(r"(?<![a-z0-9])(?:open|lib|py)?ssl", haystack) is not None
    # 'timeout' matches standalone or compound exceptions (ConnectTimeout, ReadTimeout, TimeoutError).
    if sig == "timeout":
        return "timeout" in haystack
    # Every other signature is bounded on BOTH ends. The right end admits a
    # plural and an exception-name suffix (selectors, captchas, networkerror,
    # loginerror, loginrequired, diskfull) and nothing else: as bare
    # substrings "sign in" fired inside "design in", "slow down" inside
    # "downstream", "rate limit" inside "accurate limit", "no space" inside
    # "spacecraft", "disk" inside "diskette", "challenge" inside "challenged".
    return re.search(rf"(?<![a-z0-9]){re.escape(sig)}{_WORD_SUFFIX}",
                     haystack) is not None


def diagnose_failure(error_message: str) -> dict:
    """Pattern-match a failure / needs_review error string against
    known signatures.

    Returns {matched, cause, suggestion, confidence}. When nothing
    matches, `matched` is False and a generic suggestion is given —
    never a confident wrong answer.
    """
    if not error_message or not isinstance(error_message, str):
        return {"matched": False, "cause": "Unknown",
                "suggestion": "No error text to analyze.",
                "confidence": "none", "rule": None, "remedy": None}
    haystack = error_message.lower()
    for signatures, rule, cause, suggestion, confidence, remedy in _FAILURE_SIGNATURES:
        for sig in signatures:
            if _signature_matches(sig, haystack):
                return {"matched": True, "cause": cause,
                        "suggestion": suggestion,
                        "confidence": confidence,
                        "rule": rule,
                        "remedy": dict(remedy) if remedy else None}
    return {
        "matched": False,
        "rule": None,
        "remedy": None,
        "cause": "Unrecognized error",
        "suggestion": "No known pattern matched. Check the full event "
                      "log for this URL, and the screenshot if one was "
                      "captured. Common next step: retry once — many "
                      "one-off errors clear on a second attempt.",
        "confidence": "none",
    }


# ── Top-level run ───────────────────────────────────────────────────────

def run_diagnostics(sites_config: dict | None = None) -> dict:
    """Run every diagnostic group, return one structured report.

    Shape mirrors selftest.run_all: {ok, checks, summary, elapsed_ms}.
    `ok` is True iff there are zero FAILs (WARNs don't break ok).
    """
    t0 = time.time()
    checks: list[dict] = []
    checks.extend(environment_checks())
    checks.extend(dependency_checks())
    checks.extend(cookie_freshness(sites_config or {}))

    summary = {OK: 0, WARN: 0, FAIL: 0}
    for c in checks:
        summary[c["status"]] = summary.get(c["status"], 0) + 1
    return {
        "ok": summary[FAIL] == 0,
        "checks": checks,
        "summary": summary,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }
