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

# Ordered list of (signature substrings, cause, suggestion, confidence).
# First match wins, so put the most specific signatures first. All
# matching is case-insensitive on the error string.
_FAILURE_SIGNATURES = [
    (("rate limit", "429", "too many requests", "slow down"),
     "Rate limiting",
     "The site is throttling requests. Increase the site's `delay` "
     "and `wait`, lower `max_concurrent`, or enable a warmup schedule.",
     "high"),
    (("captcha", "turnstile", "recaptcha", "hcaptcha", "challenge"),
     "Captcha challenge",
     "The site presented a captcha. Use 'Take over' to solve it "
     "manually, or configure a captcha provider (2captcha / capsolver) "
     "in the site's settings.",
     "high"),
    (("login", "auth", "401", "403", "forbidden", "sign in",
      "session expired", "unauthorized"),
     "Authentication / session expiry",
     "Login failed or the session expired. Re-login for this site; "
     "if it keeps happening, the stored cookies are stale — refresh "
     "them. Check `bdctl doctor` for the cookie age.",
     "high"),
    (("selector", "no such element", "element not found",
      "waiting for selector", "timeout.*selector"),
     "Selector drift",
     "A configured CSS selector no longer matches the page — the site "
     "changed its layout. Re-teach the site or update the "
     "trigger_selector / dl_selector in the editor.",
     "medium"),
    (("disk", "no space", "ENOSPC", "quota exceeded"),
     "Disk pressure",
     "The download disk is full or near-full. Free space, lower the "
     "site's `disk_threshold_gb`, or configure a spillover directory.",
     "high"),
    (("timeout", "timed out", "connection reset", "connection refused",
      "network", "dns", "unreachable", "ssl"),
     "Network / connectivity",
     "A network error interrupted the download. Often transient — a "
     "retry usually clears it. If it persists, check connectivity, "
     "any proxy config, or whether the site is down.",
     "medium"),
    (("404", "not found", "gone", "410"),
     "Content removed",
     "The target URL returned 404/410 — the content was taken down or "
     "the URL is wrong. Verify the URL is still valid on the site.",
     "medium"),
]


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
                "confidence": "none"}
    haystack = error_message.lower()
    for signatures, cause, suggestion, confidence in _FAILURE_SIGNATURES:
        for sig in signatures:
            # Plain substring match — cheap and predictable. The few
            # regex-looking entries above are treated as literals on
            # purpose; a substring of "timeout" still catches them.
            if sig.replace(".*", " ") in haystack or sig in haystack:
                return {"matched": True, "cause": cause,
                        "suggestion": suggestion,
                        "confidence": confidence}
    return {
        "matched": False,
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
