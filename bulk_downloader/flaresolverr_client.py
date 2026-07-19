"""v3.43.74: FlareSolverr proxy client.

# What this module is

FlareSolverr is a containerized service (typically Docker) that runs
an undetected-Chromedriver-based headless browser as a localhost HTTP
endpoint. BD POSTs `{"cmd": "request.get", "url": "..."}` and
FlareSolverr returns the post-challenge HTML + cookies (cf_clearance,
__cf_bm, etc.) after silently solving Cloudflare's challenges.

# Why this exists alongside v3.43.73's Scrapling StealthyFetcher

StealthyFetcher (v3.43.73) spawns Chromium INSIDE BD's process to
solve Turnstile. That's fine when it works, but:

  - It's heavy: ~150MB extra RAM per active bypass
  - It can conflict with BD's main Playwright runtime if profile
    paths or fingerprint state collide
  - It can't be shared across multiple BD instances or moved off-box

FlareSolverr is an external service approach:

  - Runs in its own container/process on `http://localhost:8191/v1`
  - One container serves any number of BD calls
  - Can run on a different machine entirely (e.g. a dedicated
    "scraper helper" host)
  - Same approach as BD's existing qB / JD bridges — external
    daemons reached via HTTP

Both approaches stay enabled simultaneously when configured; BD
prefers FlareSolverr when configured (lighter on BD's process) and
falls back to StealthyFetcher when not.

# Reference

  - GitHub: https://github.com/FlareSolverr/FlareSolverr
  - Docker: `docker run -d -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest`
  - API: POST JSON to `/v1` with `{"cmd": "request.get|request.post",
    "url": "...", "session": "optional", "maxTimeout": 60000}`

# Architecture

  - `is_configured(endpoint)` — basic shape check
  - `ping(endpoint)` — GET / and check version response
  - `solve_cloudflare(url, ...)` — POST `request.get`, returns
    cookies + html + ua + status. Fail-open on every error.
  - `FlareSolverrStats` — recoveries counter, accessible via /api
  - Sessions: optional named sessions persist cookies across calls.
    We use them when the caller passes `session_id`; otherwise each
    call is stateless.

# Fail-open contract

Every entrypoint catches all exceptions and returns a clean result
with `ok=False` + an error string. The runner integration treats
FlareSolverr as a "try-once, fall through on miss" — never blocks
a download attempt on FlareSolverr being healthy.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Default values — overridable per site
DEFAULT_ENDPOINT = "http://localhost:8191/v1"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_TIMEOUT_MS = 60000  # how long FlareSolverr itself waits


# ─── Availability ──────────────────────────────────────────────────


def _httpx_available() -> bool:
    try:
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


def is_configured(endpoint: str) -> bool:
    """Cheap sanity check on the endpoint string. Doesn't actually
    contact the server."""
    if not endpoint or not isinstance(endpoint, str):
        return False
    s = endpoint.strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def ping(endpoint: str, timeout_s: float = 5.0) -> dict:
    """GET the FlareSolverr root and parse its version response.

    Returns:
      {"ok": True, "version": "...", "userAgent": "..."} on success
      {"ok": False, "error": "..."} on any failure
    """
    if not is_configured(endpoint):
        return {"ok": False, "error": "endpoint_not_configured"}
    if not _httpx_available():
        return {"ok": False, "error": "httpx_not_installed"}
    # FlareSolverr's root is at the base URL (strip /v1 suffix if any)
    root = endpoint.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        import httpx
        r = httpx.get(root + "/", timeout=timeout_s)
        if r.status_code != 200:
            return {"ok": False, "error": f"http_{r.status_code}"}
        data = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {}
        # Sample response shape (from FlareSolverr docs):
        #   {"msg": "FlareSolverr is ready!", "version": "3.3.21",
        #    "userAgent": "Mozilla/5.0 (...)"}
        if isinstance(data, dict) and "version" in data:
            return {
                "ok": True,
                "version": str(data.get("version", "")),
                "userAgent": str(data.get("userAgent", "")),
                "msg": str(data.get("msg", "")),
            }
        # Some versions return text only; treat 200 as live
        return {"ok": True, "version": "", "userAgent": ""}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}:{str(e)[:120]}"}


# ─── Stats ─────────────────────────────────────────────────────────


@dataclass
class FlareSolverrStats:
    """Counters for observability."""
    enabled: bool = False
    last_ping_ok: bool = False
    last_ping_at: float = 0.0
    last_ping_version: str = ""
    last_solve_ok: bool = False
    last_solve_at: float = 0.0
    solves_attempted: int = 0
    solves_succeeded: int = 0
    solves_failed: int = 0
    last_error: str = ""


_stats = FlareSolverrStats()
_stats_lock = threading.Lock()


def stats() -> dict:
    with _stats_lock:
        return {
            "enabled": _stats.enabled,
            "last_ping_ok": _stats.last_ping_ok,
            "last_ping_at": _stats.last_ping_at,
            "last_ping_version": _stats.last_ping_version,
            "last_solve_ok": _stats.last_solve_ok,
            "last_solve_at": _stats.last_solve_at,
            "solves_attempted": _stats.solves_attempted,
            "solves_succeeded": _stats.solves_succeeded,
            "solves_failed": _stats.solves_failed,
            "last_error": _stats.last_error,
        }


def reset_stats() -> None:
    """Reset counters but preserve `enabled` flag."""
    with _stats_lock:
        was_enabled = _stats.enabled
        _stats.__init__()
        _stats.enabled = was_enabled


def _bump_attempt() -> None:
    with _stats_lock:
        _stats.solves_attempted += 1


def _bump_success() -> None:
    with _stats_lock:
        _stats.solves_succeeded += 1
        _stats.last_solve_ok = True
        _stats.last_solve_at = time.time()


def _bump_failure(error: str = "") -> None:
    with _stats_lock:
        _stats.solves_failed += 1
        _stats.last_solve_ok = False
        _stats.last_solve_at = time.time()
        if error:
            _stats.last_error = error[:200]


def set_enabled(flag: bool) -> None:
    with _stats_lock:
        _stats.enabled = bool(flag)


# ─── Cookie translation ────────────────────────────────────────────


def _translate_cookies_to_playwright(
    flare_cookies: list, default_domain: str = "",
) -> list:
    """Convert FlareSolverr's cookie format to Playwright's add_cookies()
    format.

    FlareSolverr returns cookies as:
      [{"name": "cf_clearance", "value": "...", "domain": ".example.com",
        "path": "/", "secure": True, "httpOnly": False, ...}]

    Playwright accepts mostly the same shape but is picky:
      - `expires` is float Unix-time, not int milliseconds
      - `sameSite` is "Strict"|"Lax"|"None" with that capitalization
      - missing `domain` is OK if `url` is set; we don't set url here
        so domain must be present

    Returns a list of Playwright-shaped dicts. Drops invalid entries
    silently.
    """
    out: list = []
    if not isinstance(flare_cookies, list):
        return out
    for c in flare_cookies:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        domain = c.get("domain") or default_domain
        if not domain:
            continue
        out_c = {
            "name": str(name),
            "value": str(value),
            "domain": str(domain),
            "path": str(c.get("path") or "/"),
        }
        # Optional fields
        if "secure" in c:
            out_c["secure"] = bool(c["secure"])
        if "httpOnly" in c:
            out_c["httpOnly"] = bool(c["httpOnly"])
        # SameSite normalization
        ss = c.get("sameSite")
        if ss:
            ss_low = str(ss).lower()
            if ss_low == "strict":
                out_c["sameSite"] = "Strict"
            elif ss_low == "lax":
                out_c["sameSite"] = "Lax"
            elif ss_low in ("none", "no_restriction"):
                out_c["sameSite"] = "None"
        # Expiry: FlareSolverr may return as int ms or float seconds
        exp = c.get("expires") or c.get("expiry")
        if isinstance(exp, (int, float)):
            # Heuristic: values > 1e11 are clearly milliseconds (year ~5000)
            # so divide; otherwise treat as seconds. Real Unix epoch
            # seconds for "year 2030" is ~1.9e9.
            if exp > 1e11:
                exp = exp / 1000.0
            out_c["expires"] = float(exp)
        out.append(out_c)
    return out


# ─── solve_cloudflare ──────────────────────────────────────────────


@dataclass
class SolveResult:
    """Outcome of a FlareSolverr solve attempt."""
    ok: bool = False
    cookies: list = field(default_factory=list)    # Playwright format
    html: str = ""
    user_agent: str = ""
    status_code: int = 0
    elapsed_s: float = 0.0
    error: str = ""


def solve_cloudflare(
    url: str,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_timeout_ms: int = DEFAULT_MAX_TIMEOUT_MS,
    user_agent: str = "",
    session_id: str = "",
    cookies_to_send: Optional[list] = None,
) -> SolveResult:
    """Ask FlareSolverr to fetch `url` and solve any Cloudflare
    challenge in the way. Returns the post-challenge cookies and HTML.

    `session_id`: if set, FlareSolverr will reuse cookies across calls
    in the same named session. We don't manage session lifecycle here
    (no automatic create/destroy); caller is responsible for calling
    FlareSolverr's session.destroy when done.

    `user_agent`: if set, override FlareSolverr's default UA. Note that
    Cloudflare ties cookies to UA, so the UA used to solve must match
    the UA used when later replaying the cookies.

    Fail-open: any error returns SolveResult(ok=False).
    """
    start = time.monotonic()
    if not is_configured(endpoint):
        return SolveResult(ok=False, error="endpoint_not_configured")
    if not url or not isinstance(url, str):
        return SolveResult(ok=False, error="invalid_url")
    if not _httpx_available():
        return SolveResult(ok=False, error="httpx_not_installed")

    _bump_attempt()
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": int(max_timeout_ms),
    }
    if session_id:
        payload["session"] = str(session_id)
    if user_agent:
        # FlareSolverr accepts userAgent override in newer versions;
        # silently ignored by older versions which is fine.
        payload["userAgent"] = user_agent
    if cookies_to_send:
        # Format expected by FlareSolverr — name/value/domain triples
        sane_cookies = []
        for c in cookies_to_send:
            if isinstance(c, dict) and c.get("name") and c.get("value"):
                cc = {"name": c["name"], "value": c["value"]}
                if c.get("domain"):
                    cc["domain"] = c["domain"]
                sane_cookies.append(cc)
        if sane_cookies:
            payload["cookies"] = sane_cookies

    try:
        import httpx
        r = httpx.post(endpoint, json=payload, timeout=timeout_s)
    except Exception as e:
        err = f"request_failed:{type(e).__name__}:{str(e)[:120]}"
        _bump_failure(err)
        return SolveResult(ok=False, error=err,
                           elapsed_s=time.monotonic() - start)

    elapsed = time.monotonic() - start
    if r.status_code != 200:
        err = f"http_{r.status_code}"
        _bump_failure(err)
        return SolveResult(ok=False, error=err, elapsed_s=elapsed)

    try:
        data = r.json()
    except Exception as e:
        err = f"json_parse:{type(e).__name__}"
        _bump_failure(err)
        return SolveResult(ok=False, error=err, elapsed_s=elapsed)

    # FlareSolverr response shape:
    # {
    #   "status": "ok"|"error",
    #   "message": "...",
    #   "solution": {
    #     "url": "...",
    #     "status": 200,
    #     "cookies": [...],
    #     "userAgent": "...",
    #     "response": "<html>...</html>"
    #   },
    #   "startTimestamp": ..., "endTimestamp": ..., "version": "..."
    # }
    if not isinstance(data, dict):
        err = "bad_response_type"
        _bump_failure(err)
        return SolveResult(ok=False, error=err, elapsed_s=elapsed)
    status = str(data.get("status", "")).lower()
    if status != "ok":
        err = f"flare_status_{status}:{data.get('message', '')[:120]}"
        _bump_failure(err)
        return SolveResult(ok=False, error=err, elapsed_s=elapsed)

    sol = data.get("solution") or {}
    if not isinstance(sol, dict):
        err = "no_solution_block"
        _bump_failure(err)
        return SolveResult(ok=False, error=err, elapsed_s=elapsed)

    # Extract a default domain for cookie translation from the URL
    default_domain = ""
    try:
        from urllib.parse import urlparse
        default_domain = urlparse(url).hostname or ""
    except Exception:
        pass

    cookies_pw = _translate_cookies_to_playwright(
        sol.get("cookies") or [], default_domain=default_domain)

    _bump_success()
    return SolveResult(
        ok=True,
        cookies=cookies_pw,
        html=str(sol.get("response", "") or ""),
        user_agent=str(sol.get("userAgent", "") or ""),
        status_code=int(sol.get("status", 0) or 0),
        elapsed_s=elapsed,
    )


# ─── Session lifecycle helpers ─────────────────────────────────────


def create_session(
    *, endpoint: str = DEFAULT_ENDPOINT,
    session_id: str = "",
    timeout_s: float = 10.0,
) -> dict:
    """POST sessions.create. Returns {"ok": bool, "session_id": str}.
    Fail-open."""
    if not is_configured(endpoint):
        return {"ok": False, "error": "endpoint_not_configured"}
    if not _httpx_available():
        return {"ok": False, "error": "httpx_not_installed"}
    payload = {"cmd": "sessions.create"}
    if session_id:
        payload["session"] = session_id
    try:
        import httpx
        r = httpx.post(endpoint, json=payload, timeout=timeout_s)
        if r.status_code != 200:
            return {"ok": False, "error": f"http_{r.status_code}"}
        d = r.json()
        sid = d.get("session") if isinstance(d, dict) else None
        return {"ok": bool(sid), "session_id": sid or ""}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}:{str(e)[:120]}"}


def destroy_session(
    session_id: str,
    *, endpoint: str = DEFAULT_ENDPOINT,
    timeout_s: float = 10.0,
) -> bool:
    """POST sessions.destroy. Fail-open; returns True on success."""
    if not session_id or not is_configured(endpoint) or not _httpx_available():
        return False
    try:
        import httpx
        r = httpx.post(endpoint,
                       json={"cmd": "sessions.destroy", "session": session_id},
                       timeout=timeout_s)
        return r.status_code == 200
    except Exception:
        return False


def list_sessions(
    *, endpoint: str = DEFAULT_ENDPOINT,
    timeout_s: float = 5.0,
) -> list:
    """POST sessions.list. Returns list of session IDs."""
    if not is_configured(endpoint) or not _httpx_available():
        return []
    try:
        import httpx
        r = httpx.post(endpoint, json={"cmd": "sessions.list"},
                       timeout=timeout_s)
        if r.status_code != 200:
            return []
        d = r.json()
        if isinstance(d, dict) and isinstance(d.get("sessions"), list):
            return [str(s) for s in d["sessions"]]
        return []
    except Exception:
        return []


def update_ping_stats(endpoint: str) -> dict:
    """Helper that pings + records the result into stats. Returns the
    raw ping dict."""
    result = ping(endpoint)
    with _stats_lock:
        _stats.last_ping_ok = bool(result.get("ok"))
        _stats.last_ping_at = time.time()
        _stats.last_ping_version = result.get("version", "")
    return result


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_MAX_TIMEOUT_MS",
    "is_configured",
    "ping",
    "FlareSolverrStats",
    "stats",
    "reset_stats",
    "set_enabled",
    "SolveResult",
    "solve_cloudflare",
    "create_session",
    "destroy_session",
    "list_sessions",
    "update_ping_stats",
]
