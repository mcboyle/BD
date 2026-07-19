"""Cookie health monitor (Phase 197).

A nightly job pings each site's login URL (or a configured
auth-check URL) with the stored cookies, classifies the response,
and persists `auth_health: green/yellow/red`. Surfaces in the
Review tab so the operator notices expired sessions BEFORE the
download queue starts mass-failing.

Classification:
  green:  request returned 200 with content that doesn't look like
          a login redirect. Probably authenticated.
  yellow: request returned 200 but the URL or content suggests
          a soft session expiry (e.g. redirect to /login, "please
          log in" string in the body).
  red:    request failed (401, 403, network error) or the
          configured `cookies_file` is missing/empty.

Storage: a single row per site in `auth_health` table. Each check
overwrites the previous result; we keep `last_green_ts` so the UI
can show "last verified ok 3 days ago" rather than just "red".

Scheduling: caller (app.py bg scheduler) runs `check_all_sites()`
once per day at ~3am local. Each site check is independent +
isolated, so one failure can't poison another.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from . import db as _db


_TABLE_READY = False
# Limit response read so a misbehaving site can't OOM the worker
_MAX_BODY_READ_BYTES = 64 * 1024
# Per-site timeout — cookie health is best-effort, not blocking
_DEFAULT_TIMEOUT_S = 15
# Strings that signal a logged-out state when found in response body.
# Conservative: a real "Logout" link could also match. Worker uses
# multiple signals before classifying as yellow.
_LOGOUT_SIGNALS = (
    b"please log in", b"please sign in", b"please login",
    b"login required", b"sign in required",
    b'action="/login"', b"action='/login'",
    b'name="username"', b"name='username'",
    b'name="password"', b"name='password'",
)


def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS auth_health (
                    site_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_check_ts REAL,
                    last_green_ts REAL,
                    last_http_code INTEGER,
                    note TEXT DEFAULT '',
                    response_url TEXT DEFAULT ''
                )
            """)
        _TABLE_READY = True
    except Exception:
        pass


def _classify_response(*, http_code: int, final_url: str,
                       body_head: bytes,
                       login_url_hint: str = "") -> tuple[str, str]:
    """Return (status, note). Pure: takes raw signals, returns
    a label. Testable without making any network calls."""
    if http_code in (401, 403):
        return "red", f"HTTP {http_code} — auth rejected"
    if http_code >= 500:
        # Server problem; not cookies' fault. Yellow so we re-check
        # later but don't alarm the operator.
        return "yellow", f"HTTP {http_code} — server error"
    if http_code >= 400:
        return "yellow", f"HTTP {http_code} — client error"
    if http_code < 200 or http_code >= 300:
        return "yellow", f"HTTP {http_code} — unexpected status"

    # 2xx response — look at where we ended up and what we got
    final_lower = (final_url or "").lower()
    if login_url_hint:
        login_lower = login_url_hint.lower().rstrip("/")
        if login_lower and login_lower in final_lower:
            return "yellow", "200 OK but landed on login URL"
    # Generic login-page heuristics on the final URL
    for tok in ("/login", "/signin", "/sign-in", "/auth/login"):
        if tok in final_lower:
            return "yellow", f"200 OK but URL contains {tok!r}"

    # Body sniffing — count matching signals; one is iffy, two is real
    body_lower = (body_head or b"").lower()
    hits = sum(1 for sig in _LOGOUT_SIGNALS if sig in body_lower)
    if hits >= 2:
        return "yellow", f"200 OK but body has {hits} login-page signals"

    return "green", "200 OK, no login signals detected"


def _record(site_id: str, *, status: str, note: str,
            http_code: Optional[int] = None,
            response_url: str = ""):
    """Upsert the auth_health row for this site."""
    _ensure_table()
    now = time.time()
    try:
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT last_green_ts FROM auth_health WHERE site_id = ?",
                (site_id,)).fetchone()
            prev_green = row["last_green_ts"] if row else None
            new_green = now if status == "green" else prev_green
            cx.execute("""
                INSERT INTO auth_health
                (site_id, status, last_check_ts, last_green_ts,
                 last_http_code, note, response_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id) DO UPDATE SET
                    status = excluded.status,
                    last_check_ts = excluded.last_check_ts,
                    last_green_ts = excluded.last_green_ts,
                    last_http_code = excluded.last_http_code,
                    note = excluded.note,
                    response_url = excluded.response_url
            """, (site_id, status, now, new_green, http_code,
                  note[:200], response_url[:500]))
    except Exception:
        pass


def check_site(site_id: str, site_cfg: dict) -> dict:
    """Check one site's auth health. Returns a dict with the
    decision + any data needed by the UI."""
    # Pick the URL to ping. Order: explicit auth_check_url, then
    # success_url (page operator hits after login), then login_url
    # (last resort — should redirect us somewhere telling).
    check_url = ((site_cfg or {}).get("auth_check_url", "")
                 or (site_cfg or {}).get("success_url", "")
                 or (site_cfg or {}).get("login_url", "")).strip()
    if not check_url:
        _record(site_id, status="red", note="no URL to check")
        return {"site_id": site_id, "status": "red",
                "note": "no auth_check_url, success_url, or login_url"}

    # Cookies file
    cookies_file = (site_cfg or {}).get("cookies_file", "")
    if not cookies_file:
        _record(site_id, status="red", note="no cookies_file configured")
        return {"site_id": site_id, "status": "red",
                "note": "no cookies_file configured"}
    cookies_path = Path(cookies_file)
    if not cookies_path.is_file() or cookies_path.stat().st_size == 0:
        _record(site_id, status="red",
                note=f"cookies file missing or empty: {cookies_file}")
        return {"site_id": site_id, "status": "red",
                "note": "cookies file missing or empty"}

    # Load Netscape-format cookies; build a cookies dict for httpx
    try:
        import http.cookiejar
        cj = http.cookiejar.MozillaCookieJar(str(cookies_path))
        cj.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        _record(site_id, status="red",
                note=f"can't parse cookies: {str(e)[:100]}")
        return {"site_id": site_id, "status": "red",
                "note": f"cookies parse error: {str(e)[:100]}"}

    # Send cookies THROUGH the jar so httpx enforces per-host domain (and
    # path/secure) matching on every hop. The old flat {name:value} dict was
    # domain-blind: it sent cookies scoped to OTHER domains to check_url (and,
    # across redirects, to any redirect target). Neutralize local expiry first
    # so the server -- not the cookie file -- decides validity, matching the
    # deliberate ignore_expires load above (the health check tests
    # locally-"expired" cookies against the live server on purpose).
    for _c in cj:
        _c.expires = None

    # Perform request
    try:
        import httpx
        with httpx.Client(
                timeout=_DEFAULT_TIMEOUT_S,
                follow_redirects=True,
                headers={"User-Agent":
                    "Mozilla/5.0 BD-cookie-health-check/1.0"},
        ) as client:
            r = client.get(check_url, cookies=cj)
            http_code = r.status_code
            final_url = str(r.url)
            body_head = r.content[:_MAX_BODY_READ_BYTES]
    except Exception as e:
        _record(site_id, status="red",
                note=f"request failed: {str(e)[:100]}")
        return {"site_id": site_id, "status": "red",
                "note": f"network: {str(e)[:100]}"}

    status, note = _classify_response(
        http_code=http_code, final_url=final_url, body_head=body_head,
        login_url_hint=(site_cfg or {}).get("login_url", ""))
    _record(site_id, status=status, note=note,
            http_code=http_code, response_url=final_url)
    return {"site_id": site_id, "status": status, "note": note,
            "http_code": http_code, "final_url": final_url}


def check_all_sites(s_cfg: dict, *, only_if_stale: bool = False) -> dict:
    """Run check_site for every site that has a cookies_file. If
    only_if_stale=True, skip sites checked in the last 22h (cron-
    friendly: lets the scheduler re-fire harmlessly)."""
    _ensure_table()
    now = time.time()
    stale_threshold = 22 * 3600

    skipped = []
    checked = []
    if only_if_stale:
        try:
            with _db.db_conn() as cx:
                rs = cx.execute(
                    "SELECT site_id, last_check_ts FROM auth_health"
                ).fetchall()
                fresh = {r["site_id"] for r in rs
                         if r["last_check_ts"]
                         and (now - r["last_check_ts"]) < stale_threshold}
        except Exception:
            fresh = set()
    else:
        fresh = set()

    for sid, cfg in (s_cfg or {}).items():
        if not (cfg or {}).get("cookies_file"):
            skipped.append({"site_id": sid, "reason": "no cookies_file"})
            continue
        if sid in fresh:
            skipped.append({"site_id": sid, "reason": "checked recently"})
            continue
        try:
            res = check_site(sid, cfg)
            checked.append(res)
        except Exception as e:
            checked.append({"site_id": sid, "status": "red",
                            "note": f"check_site raised: {str(e)[:100]}"})

    summary = {
        "checked": len(checked),
        "skipped": len(skipped),
        "by_status": {
            "green": sum(1 for c in checked if c.get("status") == "green"),
            "yellow": sum(1 for c in checked if c.get("status") == "yellow"),
            "red": sum(1 for c in checked if c.get("status") == "red"),
        },
        "results": checked,
        "skipped_sites": skipped,
    }
    return summary


def status_all() -> list:
    """Snapshot of every site's last-known auth health for the UI."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT site_id, status, last_check_ts, last_green_ts,
                       last_http_code, note, response_url
                FROM auth_health
            """).fetchall()
        out = []
        for r in rs:
            out.append({
                "site_id": r["site_id"],
                "status": r["status"],
                "last_check_ts": r["last_check_ts"],
                "last_green_ts": r["last_green_ts"],
                "last_http_code": r["last_http_code"],
                "note": r["note"],
                "response_url": r["response_url"],
            })
        # Sort: red first (most urgent), then yellow, then green, then null
        order = {"red": 0, "yellow": 1, "green": 2}
        out.sort(key=lambda x: order.get(x.get("status"), 3))
        return out
    except Exception:
        return []
