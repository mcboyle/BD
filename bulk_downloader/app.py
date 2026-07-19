"""Flask app: routes + HTML/CSS/JS serving.

Phase 4.1: HTML/CSS/JS still inline in HTML constant. Later phases
will extract to templates/static when we need to edit them heavily."""
import json, os, re, sys, time, uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, Response, send_file, stream_with_context

from .constants import SCREENSHOTS_DIR


from .db import (
    db_init, db_conn, db_search,
    db_stats, db_prune, db_vacuum,
    queue_upsert, db_integrity_check,
)
from .runner import SiteRunner, _ts

# v3.43.24: self-test at startup. Catches environment problems
# (corrupt DB, missing dirs, Playwright not installed, firewall
# blocking loopback) before they manifest as cryptic worker errors.
# Runs BEFORE db_init() so auto_recover_sqlite() can move aside a
# corrupt db and let db_init() recreate the schema cleanly.
from . import selftest as _selftest
from .constants import DB_PATH as _DB_PATH

# Load sites_config eagerly so we can validate it in the self-test.
# (Full load happens later via _load_sites_config; here we just need
# the file path + any configured download_dirs for the disk check.)
import os as _os
_SITES_CFG_PATH = _os.environ.get("BD_SITES_CONFIG_PATH", "sites_config.json")
_DOWNLOAD_DIRS_FOR_SELFTEST = []
try:
    import json as _json
    with open(_SITES_CFG_PATH, "r", encoding="utf-8") as _f:
        _cfg_for_st = _json.load(_f)
    # Tolerate two shapes: dict-with-"sites" key (new) and bare list (old).
    if isinstance(_cfg_for_st, dict):
        _sites_list = _cfg_for_st.get("sites") or []
    elif isinstance(_cfg_for_st, list):
        _sites_list = _cfg_for_st
    else:
        _sites_list = []
    for _s in _sites_list:
        if not isinstance(_s, dict):
            continue
        _dd = _s.get("download_dir")
        if _dd:
            _DOWNLOAD_DIRS_FOR_SELFTEST.append(_dd)
except (FileNotFoundError, _json.JSONDecodeError, OSError, AttributeError, TypeError):
    pass  # First run or unparseable; self-test will report cleanly

# Skip the self-test entirely under the same env flag as keep-alive
# (tests don't want startup noise + auto-recover side effects).
def _dom_analyzer_capture_store_root():
    """Resolved capture store root (Cut 1.3) for the startup selftest disk check,
    so a relocated store's disk is the one checked. Falls back to PROJECT_ROOT."""
    try:
        from . import dom_analyzer as _da
        return _da._capture_store_root()
    except Exception:
        return _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))


if not _os.environ.get("BD_DISABLE_KEEPALIVE"):
    _STARTUP_SELFTEST = _selftest.run_all(
        sites_config_path=_SITES_CFG_PATH,
        db_path=_DB_PATH,
        cookies_dir="cookies",
        download_dirs=_DOWNLOAD_DIRS_FOR_SELFTEST,
        captures_root=str(_dom_analyzer_capture_store_root()),
    )
    _selftest.log_to_stderr(_STARTUP_SELFTEST)
else:
    _STARTUP_SELFTEST = {"ok": True, "checks": [], "summary": {"ok": 0, "warn": 0, "fail": 0}, "elapsed_ms": 0.0}

# ─── FLASK ────────────────────────────────────────────────────────────────────
db_init()
# B1 (post-365): run-history substrate tables (job_runs + run_events). Advisory
# store — init failing is non-fatal (every writer no-ops if the store is absent).
from . import run_history as _run_history
_run_history.init()
# v3.47.8 (#42): one-shot integrity check on boot (rate-limited to 24h
# via sentinel file). Catches SQLite corruption from power loss or disk
# errors before the user wonders why a download "completed" but vanished.
try:
    _ok, _problems = db_integrity_check()
    if not _ok:
        from . import log as _boot_log
        _boot_log.get_logger(__name__).error(
            "DB INTEGRITY CHECK FAILED — %d problem(s) reported by SQLite. "
            "Consider running `bdctl db-vacuum` or restoring from backup. "
            "First 3 problems: %s",
            len(_problems), _problems[:3]
        )
except Exception as _e:
    from . import log as _boot_log
    _boot_log.get_logger(__name__).warning(
        "db_integrity_check raised: %s — skipping", _e
    )

# v3.48 (#75): weekly FTS5 index optimization. No-op if FTS isn't
# present or if it ran recently (7-day sentinel rate limit).
try:
    from .db import db_fts_optimize as _fts_opt
    _fts_ran, _fts_msg = _fts_opt()
    if _fts_ran:
        from . import log as _boot_log
        _boot_log.get_logger(__name__).info("FTS optimize: %s", _fts_msg)
except Exception as _e:
    pass  # FTS optimize is best-effort

# v3.48 (#127): log queue-recovery summary so the operator can confirm
# no jobs were silently dropped across the restart.
try:
    from .db import db_queue_recovery_summary
    _recover = db_queue_recovery_summary()
    from . import log as _boot_log
    if _recover.get("total", 0) > 0:
        _boot_log.get_logger(__name__).info(
            "queue recovery: %d job(s) restored from persistence — %s",
            _recover["total"], _recover.get("by_status", {})
        )
except Exception as _e:
    pass

# v3.47.8 (#42): fire a deep PRAGMA integrity_check on a background thread
# once per 24 hours. Boot-time selftest already does PRAGMA quick_check
# synchronously; this runs the slower, more thorough variant async so the
# app comes up at full speed even on a multi-GB DB. Result goes to the
# standard log; corruption is reported at ERROR.
try:
    from .db import run_integrity_check as _run_db_integrity
    _run_db_integrity()  # debounced — does nothing if last run was <24h ago
except Exception:
    # Integrity scheduling failures must NOT break startup. The selftest
    # quick_check already provided minimum coverage.
    pass

# Phase 44 (v3.37.0): templates and static files live alongside the package
# now (extracted from the 5,500-line HTML constant that used to be inline).
# Flask defaults to looking for `templates/` and `static/` next to the
# Flask object, which is exactly where they sit.
app = Flask(__name__,
            template_folder="templates",
            static_folder="static",
            static_url_path="/static")

# v3.47.8 (#26): record boot timestamp for /api/health uptime calc.
_app_boot_time = time.time()

# v3.47.8 (#82): cap incoming request body at 4 MB. Defends against
# accidental config-bloat from malformed clients AND closes the DAST
# "1MB JSON accepted" defensive note. Chosen as 4 MB not 1 MB because:
#   - /api/secrets/extension/pair posts a cookie jar that can legitimately
#     run to a few hundred KB for sites with large session state
#   - /api/sites bulk import (sites_config.json) can exceed 1 MB once
#     site count is in the high dozens
#   - Anything genuinely north of 4 MB is a bug or attack, not real config
# Flask raises 413 RequestEntityTooLarge before the route handler sees
# the body, which is what we want.
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024

# ── DECOMP-R2a: config + mutable-state kernels hoisted to leaf modules ──
# Defined inline below until v3.66.455; now live in clean DAG leaves and are
# imported (and thereby RE-EXPORTED) here so every existing reader resolves to
# the SAME objects: the 149 blueprint getattr() back-edges and
# `from bulk_downloader.app import s_cfg/CFG_FIELDS/...` (runner.py, tests).
# (DECOMP-R2b will repoint those importers straight at the leaves.)
from .app_kernel import (
    SESSION_IDLE_TTL, PAIRING_TTL, RATE_LIMIT_WINDOW,
    CFG_FIELDS, DEFAULTS, _app_cfg, _APP_CFG_DEFAULTS,
)
from .app_state import (
    runners, s_cfg, s_meta,
    _watch_threads, _watch_stops,
    _pairing_tokens, _pairing_lock,
    _dedup_scan_state, _dedup_scan_lock,
)


# ── Phase 41.7: Global JSON error handling ──────────────────────────────
# Two purposes:
#   1) When request body is malformed JSON or wrong shape (array instead of
#      object), routes that do `request.json.get(...)` raise AttributeError.
#      Without these handlers, Flask returns its HTML error page — terrible
#      UX for an API client. Return JSON 400 instead.
#   2) For /api/* paths, ALL 4xx/5xx returns should be JSON for consistency
#      with the rest of the API contract. Browser routes (/ and friends) can
#      still get HTML.
def _json_error(status, message, **extra):
    payload = {"ok": False, "error": message, **extra}
    return jsonify(payload), status

@app.errorhandler(AttributeError)
def _on_attribute_error(e):
    # Triggered when request.json was sent as an array/null but route expects dict
    # v3.66.523 (VR-P09): also rescue /cockpit/api/ (24 cockpit JSON endpoints
    # were raising an HTML 500 on a non-object body because they fell through
    # this /api/-only guard).
    if request.path.startswith(("/api/", "/cockpit/api/")):
        # Log for diagnostics — but don't echo back details (would help attackers
        # fingerprint internals)
        try:
            from .log import get_logger
            get_logger("bulk_downloader.app").warning(
                "AttributeError at %s (body type mismatch): %s",
                request.path, str(e)[:120])
        except Exception: pass
        return _json_error(400, "request body must be a JSON object",
                           hint="check Content-Type and body shape")
    raise e  # non-API: let Flask handle

@app.errorhandler(400)
def _on_bad_request(e):
    if request.path.startswith("/api/"):
        msg = "bad request"
        try: msg = (e.description or msg) if hasattr(e, "description") else msg
        except Exception: pass
        return _json_error(400, msg)
    return e  # non-API: default HTML

@app.errorhandler(404)
def _on_not_found(e):
    if request.path.startswith("/api/"):
        return _json_error(404, "endpoint not found")
    return e

@app.errorhandler(405)
def _on_method_not_allowed(e):
    if request.path.startswith("/api/"):
        return _json_error(405, "method not allowed",
                           allowed=list(getattr(e, "valid_methods", []) or []))
    return e

# v3.47.8 (#82): JSON 413 for /api/* paths — matches the rest of the error
# contract. Tells the client the actual limit so they can split a bulk
# upload if they hit it.
@app.errorhandler(413)
def _on_request_too_large(e):
    limit_mb = app.config.get("MAX_CONTENT_LENGTH", 0) / (1024 * 1024)
    if request.path.startswith("/api/"):
        return _json_error(413,
                           f"request body exceeds {limit_mb:.0f} MB limit",
                           limit_bytes=app.config.get("MAX_CONTENT_LENGTH"))
    return e

@app.errorhandler(500)
def _on_internal_error(e):
    # Last-resort catch — log and return generic JSON to API clients
    try:
        from .log import get_logger
        get_logger("bulk_downloader.app").exception(
            "500 at %s: %s", request.path, e)
    except Exception: pass
    if request.path.startswith("/api/"):
        return _json_error(500, "internal server error")
    return e

from .dev_metrics import RequestBudgetExceeded as _RequestBudgetExceeded

@app.errorhandler(_RequestBudgetExceeded)
def _on_request_budget_exceeded(e):
    # OBS-1: a cooperative long route called dev_metrics.check_budget() and hit the
    # per-request wall-clock ceiling, so it aborted instead of running unbounded.
    # Map to 503 (retryable) carrying the diagnostic fields + a Retry-After hint.
    try:
        from .log import get_logger
        get_logger("bulk_downloader.app").warning(
            "request budget exceeded at %s: %s", request.path, e)
    except Exception:
        pass
    from flask import make_response
    resp = make_response(
        jsonify({"ok": False, "error": "request exceeded its time budget",
                 "elapsed_ms": round(getattr(e, "elapsed_ms", 0.0), 1),
                 "budget_ms": round(getattr(e, "budget_ms", 0.0), 1)}),
        503)
    try:
        resp.headers["Retry-After"] = "5"
    except Exception:
        pass
    return resp


# Phase 22.4: Optional bearer token auth. Off by default — preserves
# legacy behavior where the app is unauthenticated on a trusted LAN.
# When BD_AUTH_TOKEN is set in the environment OR app_config has an
# `auth_token` key, requests to /api/* must carry a matching
# `Authorization: Bearer <token>` header OR (for the web UI's same-origin
# requests) a session cookie. Allows the CLI and the browser to share
# one mechanism. Phase 26 will tighten further; this is the minimum
# infrastructure so bdctl can speak to a locked-down deployment when
# the operator opts in.
def _expected_token():
    # v3.66.317 (CLI->GUI parity): the global_config store wins (full GUI-writable
    # control), then the env seed, then the app_config file fallback. A blank store
    # value = unset -> defer (so a blank GUI field can NEVER disable auth / lock you
    # out). SETTING it overrides the env token.
    try:
        from . import global_config as _gc
        st = (_gc.get("auth_token", "") or "").strip()
        if st: return st
    except Exception:
        pass
    env = os.environ.get("BD_AUTH_TOKEN", "")
    if env: return env.strip()
    try:
        from . import app as _app
        return (getattr(_app, "_app_config", {}) or {}).get("auth_token", "") or ""
    except Exception:
        return ""


def _bd_token_secondary():
    """v3.66.317: optional SECOND accepted server-side token (BD_TOKEN promoted
    full). store > env; blank = not accepted. Before 317 the server had no read
    for BD_TOKEN — it was the extension's configured value only."""
    try:
        from . import global_config as _gc
        st = (_gc.get("bd_token", "") or "").strip()
        if st: return st
    except Exception:
        pass
    return os.environ.get("BD_TOKEN", "").strip()


def _accepted_tokens():
    """All server-side tokens that authenticate an /api/ request: the primary
    (_expected_token) plus the optional secondary (_bd_token_secondary). Empty
    list when auth is unconfigured (the allow-all case). v3.66.317."""
    out = []
    p = _expected_token()
    if p: out.append(p)
    s = _bd_token_secondary()
    if s and s not in out: out.append(s)
    return out


def app_test_mode():
    """v3.66.317: advisory TEST-MODE indicator (BD_TEST_MODE promoted full).
    store > env > False. NO security/behavior effect — surfaced in /api/health as
    `test_mode` so an operator can see a test/diagnostic boot at a glance."""
    try:
        from . import global_config as _gc
        v = _gc.get("test_mode", None)
        if v is not None:
            if isinstance(v, bool):
                return v
            return str(v).strip().lower() not in ("", "0", "false", "off", "no")
    except Exception:
        pass
    return os.environ.get("BD_TEST_MODE", "").strip().lower() not in ("", "0", "false", "off", "no")

# B12 (v3.66.38): the ONLY extension routes a vault-scoped Bearer token
# may reach. These are read-only data routes that do their own vault-token
# validation in-handler. Management routes (pair_issue / list_paired /
# revoke) are deliberately excluded — a leaked vault token must not be able
# to mint pairings, enumerate, or revoke. They require the real
# BD_AUTH_TOKEN bearer or a session cookie instead.
_EXT_VAULT_DATA_ROUTES = (
    "/api/secrets/extension/list_for_origin",
    "/api/secrets/extension/fetch_one",
    "/api/secrets/extension/ping",
)

# v3.66.227 / F4.3: scoped API-token authorization policy. A *valid* API
# token (bulk_downloader.api_tokens, prefix "bdapi_") may reach ONLY the
# (route, method) pairs enumerated here, and only when its scope level is
# >= the listed minimum. This is FAIL-CLOSED: any route absent from this
# map, any method not listed for a present route, or insufficient scope →
# 403. A newly added route is unreachable by API tokens until explicitly
# added here. Keyed by the Flask *rule* (request.url_rule.rule), so dynamic
# segments (e.g. <sid>) are matched structurally, not by fragile regex.
# This restricts ONLY token-authenticated callers; an operator on a session
# cookie or the master bearer is authorized earlier in _check_token and
# never reaches this gate.
_API_TOKEN_ROUTE_POLICY = {
    # read surface (GET-only data)
    "/api/capacity":                ({"GET", "HEAD"}, "read"),
    "/api/dashboard":               ({"GET", "HEAD"}, "read"),
    "/api/queue/v2":                ({"GET", "HEAD"}, "read"),
    "/api/history":                 ({"GET", "HEAD"}, "read"),
    # enqueue surface (add to the download queue)
    "/api/queue/v2/add_url":        ({"POST"}, "enqueue"),
    "/api/sites/<sid>/queue_url":   ({"POST"}, "enqueue"),
    # admin surface (destructive retention + token management).
    # preview discloses deletion candidates (paths/sizes) so it is admin.
    "/api/retention/preview/<sid>": ({"GET", "HEAD"}, "admin"),
    "/api/retention/apply":         ({"POST"}, "admin"),
    "/api/api_tokens":              ({"GET", "POST"}, "admin"),
    "/api/api_tokens/<token_id>":   ({"DELETE"}, "admin"),
}


def _token_eq(sent: str, expected: str) -> bool:
    """AF1 (v3.66.41): constant-time comparison for auth secrets. Plain
    `==` on a token leaks length/prefix via timing. Mirrors the CSRF check
    that already uses secrets.compare_digest. Non-ASCII input (which
    compare_digest rejects) is treated as a non-match rather than raising."""
    try:
        return secrets.compare_digest(sent or "", expected or "")
    except Exception:
        return False


@app.before_request
def _check_token():
    _toks = _accepted_tokens()        # v3.66.317: primary + optional secondary
    if not _toks: return None   # auth not configured → allow all
    tok = _toks[0]
    path = request.path or ""
    # Allow service-worker, manifest, icons, the SPA shell (incl. /m/ mobile)
    if path in ("/",
                "/manifest.json", "/sw.js", "/icon.svg",
                "/apple-touch-icon.png", "/favicon.ico",
                "/m/", "/m"):
        return None
    # v3.43.16: extension pairing endpoint must be reachable without
    # auth, same as /api/pair/redeem for the URL-queue flow. The
    # pairing token IS the auth — it's a one-shot value the user just
    # generated from the authenticated app UI. The extension can't
    # have any other auth on its first call.
    if path == "/api/secrets/extension/pair":
        return None
    # AUDIT FIX (v3.43.16): /metrics is documented as unauthenticated for
    # Prometheus scrapers on a trusted network. Previously when global
    # auth was enabled this route returned 401 to Prometheus, contradicting
    # the docstring. If you need /metrics gated, put a reverse proxy in
    # front with HTTP basic auth — Prometheus supports that natively.
    if path == "/metrics":
        return None
    # v3.47.8 (#26): /api/health is the lightweight monitor probe — same
    # reasoning as /metrics. External monitors poll this without holding
    # a session token. Response is public-information-only (version,
    # uptime, queue depth) so the unauth surface is intentional.
    if path == "/api/health":
        return None
    # Phase 40: a redeemed session cookie counts as authenticated. The
    # cookie is set by /api/pair/redeem in exchange for a one-shot QR
    # pairing token. Lets a phone authenticate by scanning, without
    # ever seeing the bearer token.
    sess_cookie = request.cookies.get("bd_session", "")
    if sess_cookie and _session_valid(sess_cookie):
        _session_touch(sess_cookie)
        return None
    # Same-origin browser requests carry the Referer; we accept those too
    # so the user doesn't have to wire a token through the JS. Operator
    # who wants strict can disable this in Phase 26.
    #
    # v3.43.16: the previous `host in ref` substring check could be bypassed
    # by an attacker page whose URL *contained* the victim's host as a path
    # component — e.g. https://evil.com/10.0.70.181:5555/x. Browser sets the
    # Referer to that URL, and "10.0.70.181:5555" appears as a substring →
    # check passes despite request not actually coming from our origin.
    # Now we parse the Referer and compare hostnames + ports.
    ref = request.headers.get("Referer", "")
    host = request.headers.get("Host", "")
    if ref and host:
        try:
            from urllib.parse import urlparse
            r = urlparse(ref)
            # Build the referer's host:port (netloc minus userinfo/path).
            ref_netloc = (r.hostname or "").lower()
            if r.port:
                ref_netloc = f"{ref_netloc}:{r.port}"
            host_lc = host.lower()
            # Accept either bare host or host:port match. Strip default ports
            # so "example.com:80" matches "example.com" for http.
            if r.scheme == "http" and r.port == 80:
                ref_netloc = r.hostname.lower()
            elif r.scheme == "https" and r.port == 443:
                ref_netloc = r.hostname.lower()
            if ref_netloc == host_lc:
                return None
        except Exception:
            pass  # malformed Referer — fall through, require Bearer
    # Check Authorization header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and any(_token_eq(auth[7:].strip(), t) for t in _toks):
        return None
    # AUDIT v3.43.46: extension sends X-BD-Token (configured by the
    # user during pairing). The earlier code only read Authorization
    # Bearer, so an extension with BD_TOKEN configured would have
    # its requests silently 401'd. Now we accept X-BD-Token as a
    # bearer-equivalent — same secret, different header.
    bd_token = request.headers.get("X-BD-Token", "")
    if bd_token and any(_token_eq(bd_token.strip(), t) for t in _toks):
        return None
    # v3.43.16 / B12 (v3.66.38): vault-scoped extension tokens are accepted
    # ONLY for the read-only DATA routes (_EXT_VAULT_DATA_ROUTES). The old
    # code opened the entire /api/secrets/extension/* prefix to any Bearer,
    # so a leaked vault token could reach pair_issue / list_paired / revoke
    # (privilege amplification). Management routes now fall through to
    # require the real BD_AUTH_TOKEN bearer (checked above) or a session
    # cookie. The data-route handlers still validate the vault token
    # themselves via _require_vault_token(); we only gate the URL here.
    if path in _EXT_VAULT_DATA_ROUTES and auth.startswith("Bearer "):
        return None
    # v3.66.227 F4.3: scoped API tokens. Presented via
    # `Authorization: Bearer bdapi_<...>` or `X-BD-API-Token: bdapi_<...>`.
    # A bdapi_ value never equals the master bearer (the constant-time
    # _token_eq check above already failed for it), so we only reach here
    # for a non-master token. Authorization is FAIL-CLOSED against
    # _API_TOKEN_ROUTE_POLICY — an authenticated token that isn't allowed
    # for this (route, method) gets 403, never a silent allow. An invalid /
    # expired / revoked token falls through to the normal 401 below so it
    # can't downgrade a request that also carries other valid auth.
    api_tok = ""
    _ah = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if _ah.startswith("bdapi_"):
        api_tok = _ah
    else:
        _xh = request.headers.get("X-BD-API-Token", "").strip()
        if _xh.startswith("bdapi_"):
            api_tok = _xh
    if api_tok:
        try:
            from . import api_tokens as _apitok
            _ip = request.remote_addr or ""
            res = _apitok.verify_token(api_tok, client_ip=_ip,
                                       request_path=path,
                                       method=request.method)
            if res.get("ok"):
                rule = request.url_rule.rule if request.url_rule else None
                policy = (_API_TOKEN_ROUTE_POLICY.get(rule)
                          if rule else None)
                if policy is None:
                    _apitok.record_decision(
                        res["token_id"], path, request.method,
                        res.get("scope", ""), False, _ip,
                        "route not in api-token policy")
                    return jsonify(
                        {"error": "api token not authorized "
                                  "for this route"}), 403
                allowed_methods, min_scope = policy
                if request.method not in allowed_methods:
                    _apitok.record_decision(
                        res["token_id"], path, request.method,
                        res.get("scope", ""), False, _ip,
                        "method not allowed")
                    return jsonify(
                        {"error": "method not allowed for "
                                  "api token"}), 403
                if res.get("scope_level", 0) < _apitok.scope_level(min_scope):
                    _apitok.record_decision(
                        res["token_id"], path, request.method,
                        res.get("scope", ""), False, _ip,
                        f"insufficient scope (<{min_scope})")
                    return jsonify({"error": "insufficient token scope",
                                    "required_scope": min_scope}), 403
                _apitok.record_decision(
                    res["token_id"], path, request.method,
                    res.get("scope", ""), True, _ip, "ok")
                return None
            # invalid/expired/revoked api token → fall through to 401
        except Exception:
            pass
    # v3.43.80 Phase 138: read-only share tokens. A token from
    # /api/shares grants narrow read access to specific endpoints
    # (status, capacity, events, history, metrics) per its scopes.
    # Tokens come via ?ro_token=<t> OR X-Share-Token header.
    # Method-restricted to GET/HEAD/OPTIONS — never writes.
    share_token = (request.args.get("ro_token", "")
                   or request.headers.get("X-Share-Token", ""))
    if share_token:
        try:
            from . import shares as _shares
            client_ip = request.remote_addr or ""
            result = _shares.check_share_access(
                share_token, path, request.method,
                client_ip=client_ip)
            if result.get("ok"):
                return None
            # Fall through — share token didn't grant access; the
            # normal auth path still requires a bearer / session.
            # We do NOT 401 here; that way an invalid share token on
            # a session-authenticated request doesn't reject the
            # operator. Audit log is in shares._log_access.
        except Exception:
            pass
    return jsonify({"error": "authentication required"}), 401


# ── Phase 40: Session + CSRF infrastructure ──────────────────────────
# In-memory store. Sessions are short-lived (8h idle, sliding) and
# tokens are random 32-byte URL-safe strings. State is intentionally
# not persisted — a server restart logs everyone out, which is correct
# behavior for a single-user LAN tool. If you need persistent sessions,
# put a reverse proxy with its own auth in front.

import secrets, threading as _t40_threading

_sessions: dict = {}            # session_token → {"created", "last_used", "source"}
_session_lock = _t40_threading.Lock()



def _session_valid(token: str) -> bool:
    """True iff the session exists and hasn't expired (idle TTL)."""
    if not token: return False
    with _session_lock:
        rec = _sessions.get(token)
        if not rec: return False
        if time.time() - rec["last_used"] > SESSION_IDLE_TTL:
            del _sessions[token]
            return False
        return True


def _session_touch(token: str) -> None:
    """Slide the idle TTL by updating last_used."""
    with _session_lock:
        rec = _sessions.get(token)
        if rec: rec["last_used"] = time.time()


def _session_create(source: str = "manual") -> str:
    """Mint a new session. source is for diagnostics — "pair_redeem",
    "manual", "csrf_bootstrap" are typical values."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _session_lock:
        _sessions[token] = {"created": now, "last_used": now, "source": source}
    return token


def _csrf_token_for(session_token: str) -> str:
    """Derive a CSRF token from the session token. Deterministic per
    session but unguessable without the session value — uses HMAC with
    a process-local random key. This is the double-submit pattern: the
    cookie holds the session token, the meta tag / response holds the
    derived CSRF token. An attacker who can issue cross-origin requests
    can't read either cookie OR derive the CSRF token without knowing
    the session value."""
    import hmac, hashlib
    return hmac.new(_csrf_key, session_token.encode(), hashlib.sha256).hexdigest()[:32]


# Process-local CSRF key. Random per-process so token forgery is hard
# even if an attacker reads the JS bundle. Lost on restart, which is fine
# — all sessions and tokens are also lost on restart.
_csrf_key: bytes = secrets.token_bytes(32)


# ── CSRF route policy — THE SINGLE SOURCE OF TRUTH ────────────────────────────
# v3.66.748 (audit R11-13). These three constants ARE the route-level CSRF
# policy. `_check_csrf` below reads them, and tools/build_endpoint_catalog.py
# IMPORTS them (it does not re-type them) — so ROUTE_INDEX, ENDPOINT_CATALOG and
# the SHIPPED OpenAPI spec cannot disagree with the hook about which routes are
# guarded.
#
# WHY THIS EXISTS: the scanner used to keep its own copy of the prefix rule and
# never learned about "/cockpit/api/" when PHC-1 brought cockpit under the
# guard. 28 cockpit write endpoints were PROTECTED by the app and reported
# `csrf: false` by every artifact — including the OpenAPI spec served to
# clients, which therefore omitted the required X-CSRF-Token header and the 403
# response. Worse: because the artifact already said `false`, dropping the guard
# from the hook would have changed NO artifact and tripped NO gate. The wrong
# mirror had burned down the alarm for a real regression.
#
# A predicate that must be manually synced WILL drift. Don't mirror — derive.
CSRF_TRIPPING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# PHC-1 (B1): the cockpit JSON endpoints live under /cockpit/api/ (not /api/)
# and their UI already sends X-CSRF-Token; the VR-P09 (@523) precedent puts them
# under the same CSRF + same-origin guard as /api/ writes.
CSRF_GUARDED_PREFIXES = ("/api/", "/cockpit/api/")
# The bootstrap for cookie-based sessions: by definition the first request
# cannot carry a CSRF token yet. The ONE declared exemption.
CSRF_EXEMPT_PATHS = frozenset({"/api/pair/redeem"})


def csrf_fires_for(method: str, path: str) -> bool:
    """Route-level answer to: would `_check_csrf` 403 a cookie-session browser
    request (no bearer, same-origin) for this method+path?

    This is the ONE predicate. `_check_csrf` applies it to the live request and
    the catalog scanner applies it to the route table; there is no second copy.
    Per-request escapes (Bearer auth, no session cookie) are deliberately NOT
    modelled here — they are properties of a request, not of a route, and the
    artifacts describe routes.
    """
    if method not in CSRF_TRIPPING_METHODS:
        return False
    if not path.startswith(CSRF_GUARDED_PREFIXES):
        return False
    if path in CSRF_EXEMPT_PATHS:
        return False
    return True


@app.before_request
def _check_csrf():
    """For state-changing requests (POST/PUT/PATCH/DELETE) under /api/,
    require X-CSRF-Token to match the derived CSRF for the session.
    Skip when:
      - The route is GET or OPTIONS (read-only / preflight)
      - The request has a valid Bearer token (CLI usage)
      - No session cookie is present (legacy / unauthenticated requests
        are already handled by _check_token; this layer is only for
        cookie-based browser sessions)
    """
    if request.method in ("GET", "OPTIONS", "HEAD"): return None
    path = request.path or ""
    # v3.66.748: the guarded-prefix rule is CSRF_GUARDED_PREFIXES, above — the
    # same object the catalog scanner imports. Inlining the tuple here is what
    # let the scanner's copy drift; there is now nothing to copy.
    if not path.startswith(CSRF_GUARDED_PREFIXES): return None
    # Bearer auth bypasses CSRF — the bearer is itself a secret that the
    # attacker doesn't have, so there's no cross-origin attack to defend
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "): return None
    # Defense-in-depth (rec #4): refuse a CROSS-ORIGIN state-changing /api/
    # request by Origin, independent of whether a session exists. This closes
    # CSRF on a no-auth-token / sessionless deployment, where _check_token does
    # not gate (auth unconfigured) and the cookie-session CSRF check below is
    # skipped. Origin is sent by browsers on cross-origin state-changing requests
    # and (unlike Referer) is not suppressed by referrer-policy. A same-origin or
    # absent Origin falls through to the existing checks, so same-origin browser
    # use and non-browser clients are unaffected.
    origin = request.headers.get("Origin", "")
    if origin:
        host = request.headers.get("Host", "")
        try:
            from urllib.parse import urlparse
            o = urlparse(origin)
            o_netloc = (o.hostname or "").lower()
            # Keep an explicit non-default port; strip the default (80/443) so
            # "host:80"/"host:443" match a bare "host" — mirrors _check_token.
            if o.port and not ((o.scheme == "http" and o.port == 80)
                               or (o.scheme == "https" and o.port == 443)):
                o_netloc = f"{o_netloc}:{o.port}"
            if host and o_netloc and o_netloc != host.lower():
                return jsonify({"error": "cross-origin request refused",
                                "hint": "state-changing /api/ requests must be "
                                        "same-origin"}), 403
        except Exception:
            pass  # malformed Origin — fall through to the existing checks
    # No session? CSRF doesn't apply (the existing same-origin Referer
    # check in _check_token is the defense)
    sess = request.cookies.get("bd_session", "")
    if not sess: return None
    # The bootstrap exemption is CSRF_EXEMPT_PATHS, above — the same object the
    # catalog scanner imports (a second hardcoded copy here is a second thing to
    # drift).
    if path in CSRF_EXEMPT_PATHS: return None
    # Validate
    sent = request.headers.get("X-CSRF-Token", "")
    expected = _csrf_token_for(sess)
    if not sent or not secrets.compare_digest(sent, expected):
        # v3.43.55: demoted from WARNING → INFO. The JS fetch wrapper
        # auto-retries on 403 with a fresh /api/csrf token, so a
        # single rejection here is benign — happens on first page
        # load after a server restart (browser had stale CSRF from
        # the pre-restart session). The retry attempt won't fire
        # this branch (it sends the fresh token). If you see many
        # of these warnings IN A ROW for the same path, that's a
        # real client bug (no retry happening); single isolated
        # ones are noise.
        try:
            from .log import get_logger
            get_logger("bulk_downloader.app").info(
                "CSRF rejected (client should auto-retry): path=%s, "
                "sent_header=%r (len=%d), session_prefix=%r",
                path, sent[:8] if sent else "<missing>", len(sent), sess[:8])
        except Exception: pass
        return jsonify({"error": "csrf token missing or invalid",
                        "hint": "send X-CSRF-Token header matching the value embedded in the HTML meta tag"}), 403
    return None


@app.after_request
def _add_security_headers(response):
    """Audit 2026-05 / Phase 2C: defense-in-depth response headers.

    Set on every response unless the route already provided its own
    value (we use setdefault semantics by checking existing keys).

      X-Content-Type-Options: nosniff
          Blocks IE/Edge from MIME-sniffing a response into an
          executable type (e.g. treating a JSON file as JS).

      X-Frame-Options: SAMEORIGIN
          Blocks third-party sites from iframing the dashboard
          (clickjacking defense). Permits same-origin iframing for
          things like the teach overlay.

      Referrer-Policy: strict-origin-when-cross-origin
          Suppresses URL leakage in the Referer header on outbound
          links (which could carry session-derived state).

      Content-Security-Policy: blanket policy that disallows inline
          scripts EXCEPT where currently present. We can't be strict
          on inline because the existing UI has inline event handlers
          (onclick=, onload=) all over app.js. So we use a lenient
          policy that still blocks the worst — frame-ancestors 'self',
          base-uri 'self', form-action 'self', no plugins. This
          shrinks blast radius without breaking the current UI.

    Skipped on the teach CORS endpoints since their response headers
    have to satisfy cross-origin semantics — adding our headers
    doesn't conflict, but better to keep that surface minimal.
    """
    # Skip if response is from a different origin already (CORS endpoints set their own)
    # Use setdefault semantics so individual routes can override.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # CSP: deliberately lenient because the existing UI uses inline event
    # handlers extensively. We block the worst categories instead.
    # - frame-ancestors 'self': clickjacking defense (also covered by X-Frame-Options)
    # - base-uri 'self':  attacker can't inject a <base> tag to redirect resources
    # - form-action 'self': forms can't submit cross-origin
    # - object-src 'none': blocks Flash/embed plugins (no legitimate use)
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none'",
    )
    return response


# ── dev metrics: request timing + unhandled-exception capture ─────────
# Feeds the in-process ring buffers in dev_metrics, which the dev-suite
# latency / slow-endpoint / error-rate / exception tools read. Both
# hooks are wrapped so a metrics bug can never affect the response.
@app.before_request
def _dev_metrics_start():
    try:
        from flask import g
        g._dev_t0 = time.time()
    except Exception:
        pass

@app.after_request
def _dev_metrics_record(response):
    try:
        from flask import g
        t0 = getattr(g, "_dev_t0", None)
        if t0 is not None:
            from . import dev_metrics as _dm
            rule = (str(request.url_rule) if request.url_rule
                    else request.path)
            duration_ms = (time.time() - t0) * 1000.0
            _dm.record_request(request.method, request.path, rule,
                               response.status_code, duration_ms)
            _slow = _dm.slow_request_note(request.method, request.path,
                                          duration_ms)
            if _slow:
                import logging
                logging.getLogger("bulk_downloader.app").warning(_slow)
            # OBS-1: flag ANY response that outran the hard budget. A cooperative
            # route raises RequestBudgetExceeded mid-flight (handled -> 503); this
            # catches the non-cooperative ones after the fact so a slow route is
            # visible before it wedges, even though it can't be killed here.
            if _dm.over_budget(duration_ms):
                import logging
                logging.getLogger("bulk_downloader.app").warning(
                    "over budget: %s %s ran %.1fs (>%ds) status=%s",
                    request.method, request.path, duration_ms / 1000.0,
                    int(_dm.REQUEST_BUDGET_MS / 1000.0), response.status_code)
            # T44 request_replay capture — gated by feature flag.
            # No-op when the flag is off (the recorder short-circuits).
            # Only capture /api/* — same scope as CSRF middleware.
            if (request.path or "").startswith("/api/"):
                try:
                    from . import request_replay as _rr
                    if _rr.is_enabled():
                        # Pull request body best-effort; flask buffers
                        # it but get_data() works after the view ran.
                        try:
                            body_bytes = request.get_data(
                                cache=True, as_text=False)
                            req_body = (body_bytes.decode(
                                "utf-8", errors="replace")
                                if body_bytes else "")
                        except Exception:
                            req_body = ""
                        # Response body — flask exposes via get_data.
                        try:
                            resp_body = response.get_data(
                                as_text=True) or ""
                        except Exception:
                            resp_body = ""
                        _rr.record(
                            method=request.method,
                            path=request.path,
                            query=request.query_string.decode(
                                "ascii", errors="replace"),
                            status=response.status_code,
                            request_headers=dict(request.headers),
                            request_body=req_body,
                            response_body=resp_body,
                            duration_ms=duration_ms)
                except Exception:
                    pass
    except Exception:
        pass
    return response

# Capture truly-unhandled request exceptions (a view that handles its
# own errors never reaches this — so the buffer holds only real bugs).
try:
    from flask import got_request_exception as _got_request_exception

    def _dev_capture_exception(sender, exception, **extra):
        try:
            from . import dev_metrics as _dm
            _dm.record_exception(exception, getattr(request, "path", ""))
        except Exception:
            pass

    _got_request_exception.connect(_dev_capture_exception, app)
except Exception:
    pass


@app.after_request
def _bootstrap_session(response):
    """If the request is for the HTML shell (GET /) and there's no
    existing session, mint one and set the cookie. This makes CSRF
    Just Work for the browser without an explicit login step.

    Also injects the CSRF token into the response so JS can read it
    from a meta tag we add to the HTML shell.

    v3.43.55: serve_index() now mints inline so the meta tag carries
    the correct CSRF in the very first response. If it did, this
    hook becomes a no-op (the request is marked).
    """
    if request.method != "GET": return response
    # Phase 1 root flip (v3.66.203): the hook covers both HTML shells —
    # "/" (the SPA: cookie warmth so the first /api/csrf finds a valid
    # session and the first POST never races a Set-Cookie). The legacy
    # shell was deleted in P4 (v3.66.334), so "/" is the only HTML shell.
    if request.path != "/": return response
    # v3.43.55: serve_index already minted + set the cookie
    if getattr(request, "_bd_session_just_minted", None):
        return response
    sess = request.cookies.get("bd_session", "")
    if not sess or not _session_valid(sess):
        sess = _session_create(source="csrf_bootstrap")
        # Cookie attributes:
        #   HttpOnly: JS can't read the session itself — CSRF defense
        #   SameSite=Lax: not sent on cross-site POSTs but ok for top-level GET
        #   Secure: only when on HTTPS (set by the proxy or env)
        secure = request.scheme == "https"
        response.set_cookie("bd_session", sess,
                            max_age=SESSION_IDLE_TTL, httponly=True,
                            samesite="Lax", secure=secure)
    return response


# /api/csrf -> app_csrf.py (Phase 4 thin-core-shell extraction)
# ── v3.47.8 (#26): lightweight health probe ─────────────────────────────
# Designed for external monitors (Prometheus blackbox, Uptime Kuma, cron
# heartbeat scripts). Always returns 200 if the app process is responsive
# AND the DB is reachable — that's the minimum a monitor cares about.
# Detailed component diagnostics live in /api/diagnostics_bundle.
#
# Unauth on purpose: monitors should be able to poll without holding a
# session token. The response carries only public information (version,
# uptime, queue depth) — no operator-identifying data.
# api_health -> app_health.py (Phase 4 multi-block extraction)


# ── v3.54 (Phase 7): bd-doctor diagnostics endpoint ─────────────────────
# Deeper than /api/health — runs the environment / dependency / cookie
# checks from doctor.py. Slower (subprocess calls to ffmpeg etc.) so it's
# on-demand, not a liveness probe. The Status tab calls this.

# /api/doctor -> app_doctor.py (Phase 4 thin-core-shell extraction)
# A second, deliberately minimal HTML page at /m/ designed for one-thumb
# scrolling on phones. Polls /api/health + /api/status?light=1 every 5s
# and renders. No state mutations possible — no app.js, no auth-gated
# action buttons, no edit forms. Just "what's happening right now".
#
# Use case: walking around, glancing at the phone, "did the queue clear?"
@app.route("/m/")
@app.route("/m")
def serve_mobile_view():
    # Phase 1 root flip (v3.66.203): the SPA now lives at `/`, so the
    # mobile shim redirects there. Chain history: /m served a template
    # pre-D4, then 302 -> /m2/ (v3.65.1), now 302 -> / . Old bookmarks
    # keep working — they just resolve to the SPA at root.
    from flask import redirect
    return redirect("/", code=302)


# PT8 — mobile OPS page. Retired in D4 (v3.65.1) per D3_OPT_IN.md's
# v3.65.0 retirement schedule. The companion /m/ops admin page is now
# an unconditional 302 to /m2/ — the SPA covers per-site stop via its
# API (POST /api/sites/<id>/stop with CSRF). The m_ops.html template
# stays on disk until v3.66.0 alongside mobile.html for the same
# emergency-rollback reason.
@app.route("/m/ops/")
@app.route("/m/ops")
def serve_mobile_ops_view():
    from flask import redirect
    # Phase 1 root flip (v3.66.203): SPA is at / now.
    return redirect("/", code=302)


# ── D3 U1 (v3.64.0 prep): React SPA mount at /m2/* ─────────────────────
# The D3 redesign ships as a Vite-built React SPA under `frontend/dist/`,
# served here. Behaviour:
#   - If `frontend/dist/index.html` exists → serve the built SPA (HTML
#     for any path that isn't a real asset; assets pass through with
#     correct MIME).
#   - If the dist dir is missing → return 503 with a clear actionable
#     message naming the right installer. This is the "Node missing"
#     surface from OPEN_THREADS §D3.
#
# Coexistence: `/` (desktop) and `/m` (mobile) are *untouched*. /m2
# is opt-in until U9 flips it on by default and v3.65.0 retires /m.
# Mount path matches `frontend/vite.config.ts :: base = "/m2/"` — if
# you change one, change the other or every asset URL 404s.
#
# Security: `send_from_directory` enforces that the resolved path is
# within the dist root, so path traversal via `..` is blocked by Flask.
# No filesystem walks, no arbitrary path acceptance.
#
# This handler is intentionally module-level source insertion with no
# threads/DB/work — adding any of those would violate the app.py
# import-time invariant (every test import would pay the cost).
_M2_DIST_ROOT = Path(__file__).parent.parent / "frontend" / "dist"


def _m2_503_node_missing() -> Response:
    """Uniform 503 response when frontend/dist/ is missing.

    Returned for every /m2 request when the SPA build hasn't happened
    yet — e.g. installer ran without Node, or fresh git clone. Message
    names both installers explicitly so the operator doesn't have to
    guess which OS-specific path to take. JSON body for /m2/api/*
    futureproofing; plain text for the HTML route.
    """
    msg = (
        "BulkDownloader /m2 (D3 React UI) is not built.\n\n"
        "The SPA bundle lives at frontend/dist/ and was not produced "
        "during install. This usually means Node.js was missing.\n\n"
        "To fix:\n"
        "  1. Install Node.js 18+ (https://nodejs.org/)\n"
        "  2. Re-run the installer:\n"
        "       Linux:   ./install_linux.sh\n"
        "       Windows: install_windows.bat\n"
        "     OR build manually:\n"
        "       cd frontend && npm ci && npm run build\n\n"
        "The existing UIs at / and /m are unaffected and continue to "
        "work normally.\n"
    )
    resp = Response(msg, status=503, mimetype="text/plain; charset=utf-8")
    # Header lets a future /m2 health probe distinguish "not built"
    # from generic 503 without parsing the body.
    resp.headers["X-BD-M2-Status"] = "not-built"
    return resp


# ── D3 U9: ?ui=v2 opt-in cookie ─────────────────────────────────────────
#
# The D3 SPA at /m2 ships behind an explicit opt-in. Until v3.65.0
# (one release after this), both the legacy /m and the new /m2 are
# available; the operator chooses which one /m serves them via a
# cookie. The cookie is set by visiting either URL with ?ui=v2 or
# ?ui=v1 in the query string.
#
# Lookup order:
#   1. Query string `?ui=v2` / `?ui=v1` — explicit choice, sets cookie
#   2. Cookie `bd_ui=v2` / `bd_ui=v1` — persisted preference
#   3. Default — serve the legacy /m (no preference yet)
#
# The cookie name is `bd_ui`, NOT `bd_session` — these are independent.
# `bd_session` is the CSRF-bound auth session; `bd_ui` is a pure UI
# preference with no security implications.
#
# At v3.65.0 (next release after D3 ships), this whole mechanism gets
# removed: /m redirects unconditionally to /m2 and the legacy template
# is dropped. See OPEN_THREADS for the retirement schedule.

_M2_COOKIE_NAME = "bd_ui"
_M2_COOKIE_TTL = 365 * 24 * 3600  # 1 year — survives sensible browser caches


def _m2_opt_state(req) -> str:
    """Return one of 'v2', 'v1', or 'default' based on query + cookie.

    Query takes precedence over cookie. Unknown values fall through to
    cookie / default — invalid input is silently the same as no input,
    rather than 400ing on a UI preference URL.
    """
    qs_val = (req.args.get("ui") or "").strip().lower()
    if qs_val in ("v2", "v1"):
        return qs_val
    cookie_val = (req.cookies.get(_M2_COOKIE_NAME) or "").strip().lower()
    if cookie_val in ("v2", "v1"):
        return cookie_val
    return "default"


def _m2_apply_opt_cookie(resp, req) -> None:
    """If the request had `?ui=v2` or `?ui=v1`, persist that as the
    cookie on the response. No-op for other requests."""
    qs_val = (req.args.get("ui") or "").strip().lower()
    if qs_val not in ("v2", "v1"):
        return
    secure = req.scheme == "https"
    resp.set_cookie(
        _M2_COOKIE_NAME, qs_val,
        max_age=_M2_COOKIE_TTL,
        httponly=False,    # frontend may want to read this for "you are on v2" UX
        secure=secure,
        samesite="Lax",
    )


@app.route("/m2/")
@app.route("/m2")
@app.route("/m2/<path:subpath>")
def serve_m2_spa(subpath: str = ""):
    """Phase 1 root flip (v3.66.203): the D3 SPA moved from /m2 to `/`
    (serve_spa_root below). /m2 is now a deep-link-preserving 302 shim:
    /m2/queue -> /queue, /m2/sites/3 -> /sites/3, query string kept.
    Old bookmarks, the legacy shell's "New UI" link history, and any
    installed-PWA start_url that captured /m2 all keep resolving.

    The opt-in cookie machinery (_m2_opt_state, _m2_apply_opt_cookie,
    _M2_COOKIE_NAME) stays in the module — pinned by test_d3_u9 as a
    deliberate keep — but the shim no longer consumes it: the SPA is
    the only UI at root, there is nothing to opt into.

    Vite base + router basename are both "/" now (frontend re-rooted in
    this same cut), so no /m2-prefixed asset URLs are emitted anymore.
    """
    from flask import redirect
    target = "/" + subpath.lstrip("/")
    qs = request.query_string.decode("utf-8", errors="replace")
    if qs:
        target += "?" + qs
    return redirect(target, code=302)


# ── v3.48 (#143): "Open file location" — reveal in native file manager ──
# Posts a path; the server invokes the OS-native "show this file in its
# parent folder" command. Useful for jumping from a queue/history row
# straight to the downloaded file in Explorer/Finder/Nautilus.
#
# Security model: the path MUST be under one of the configured
# path_allowlist roots (or, if allowlist is empty, under BD_HOME). This
# is identical to _validate_path semantics — we can't trust arbitrary
# client-supplied paths even on a single-user LAN tool because XSS in
# a site name could craft a reveal request for /etc/.
# /api/file -> app_file.py (Phase 4 thin-core-shell extraction)
# ── v3.48 (#25): audit log read API ──────────────────────────────────────
# UI surfaces these on the Settings → Audit panel and on each site's
# detail page. Two endpoints to keep query shapes simple.
# /api/audit -> app_audit.py (Phase 4 thin-core-shell extraction)
# api_pair_redeem -> app_pair.py (Phase 4 multi-block extraction)



# ─── SITES CONFIG PERSISTENCE (Phase 4.2 supplement) ──────────────────────────
# Phase 4 added queue persistence in SQLite, but sites themselves were still
# in-memory only — making the queue persistence pointless because all sites
# vanished on restart. This module mirrors the in-memory s_meta dict to
# `sites_config.json` on every change, and rehydrates SiteRunners on startup.
SITES_FILE = Path("sites_config.json")

def _save_sites_config():
    """Write current sites to disk. Called on every add/update/delete.

    NOTE: this file contains plaintext credentials (login passwords) so
    that auto-login still works after restart. Treat sites_config.json
    like a config file with secrets — restrict permissions appropriately.
    Common practice for self-hosted tools (Sonarr/Radarr/Prowlarr behave
    the same way).

    v3.43.16: explicit UTF-8 encoding. Without it, Path.write_text uses the
    system default (cp1252 on US Windows) and crashes on non-ASCII site
    names / usernames / cookie file paths.

    v3.43.19: atomic write via .tmp + replace. A crash mid-write would
    previously leave a truncated JSON file, and on next start
    `_load_sites_config` would fail to parse and log everything as
    "malformed" — effectively losing every site definition. With the
    rename-in-place pattern, the destination file is either the old
    complete content or the new complete content, never partial.

    v3.47.7: auto-fill cookie_file for any site that has it blank.
    Without a cookie_file path, manual-login captures cookies into
    memory only and they're lost on restart — root cause of the
    "login screen reappears even though it said online" bug. Default
    path is BD_HOME/cookies/<site_id>.json which respects BD_HOME
    overrides (used by the dev install) and stays inside the project
    tree."""
    try:
        # Compute default cookie dir from BD_HOME or current working dir.
        # We resolve lazily on each save so a BD_HOME change picks up
        # without a restart.
        _bd_home = Path(os.environ.get("BD_HOME") or ".").resolve()
        _cookie_dir = _bd_home / "cookies"
        try:
            _cookie_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Disk full / permission denied / read-only mount — skip the
            # auto-fill rather than failing the whole config save. The
            # original blank cookie_file behavior is recovered.
            _cookie_dir = None
        # F4: snapshot s_cfg.items() into a list before iterating — a
        # concurrent add/delete from another request thread would otherwise
        # raise "dict changed size during iteration", which the outer
        # try/except swallows, silently dropping the save that triggered it.
        # Same pattern already used at the other save sites in this module.
        for _sid, _cfg in list(s_cfg.items()):
            if _cookie_dir is None: break
            if not isinstance(_cfg, dict): continue
            if not (_cfg.get("cookie_file") or "").strip():
                _cfg["cookie_file"] = str(_cookie_dir / f"{_sid}.json")
        payload = {sid: dict(cfg) for sid, cfg in list(s_cfg.items())}
        # v3.65.2: strip transient _-prefixed fields (e.g. _autopick, set
        # by _create_site so callers can surface what auto-pick applied)
        # so they never leak into sites_config.json. These are
        # ephemeral handoffs between functions, not persistent state.
        for _entry in payload.values():
            if isinstance(_entry, dict):
                for _k in [k for k in _entry if isinstance(k, str)
                           and k.startswith("_")]:
                    _entry.pop(_k, None)
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = SITES_FILE.with_suffix(".json.tmp")
        tmp.write_text(content, encoding="utf-8")
        # Path.replace is atomic on POSIX and on Windows NTFS for same-volume
        # renames (which this always is since tmp sits next to SITES_FILE).
        tmp.replace(SITES_FILE)
    except Exception as e:
        sys.stderr.write(f"  ! sites_config save failed: {e}\n")

def _build_meta(cfg: dict) -> dict:
    """Return a copy of cfg with secrets stripped — top-level password
    + every account.password. Other secret-style fields (cookie_file,
    captcha_api_key, etc.) stay; they're paths and identifiers, not
    bearer tokens, and the UI legitimately needs to see them.

    v3.43.16: also exposes a boolean `has_password` so the edit form
    can show "saved — leave blank to keep existing" vs "enter password"
    in the placeholder. The actual password is never sent."""
    meta = {k: v for k, v in cfg.items() if k != "password"}
    # has_password mirrors the top-level password field's existence
    meta["has_password"] = bool(cfg.get("password"))
    # v3.43.26: same treatment for qB password. Never echo to UI,
    # but tell the form whether one is on file.
    meta["has_qb_password"] = bool(cfg.get("qb_password"))
    meta.pop("qb_password", None)
    # v3.43.81 Phase 161: same pattern for TPDB API key (v3.43.80
    # module flag). UI uses the boolean to render the placeholder; the
    # key itself stays out of the response.
    meta["has_tpdb_api_key"] = bool(cfg.get("tpdb_api_key"))
    meta.pop("tpdb_api_key", None)
    if isinstance(meta.get("accounts"), list):
        meta["accounts"] = [
            {**{ak: av for ak, av in a.items() if ak != "password"},
             "has_password": bool(a.get("password"))}
            for a in meta["accounts"] if isinstance(a, dict)
        ]
    # v3.66.240 (B2 Decision 4): compact standing-indicator for a per-site
    # draft-test override. The full override dict (which carries the draft
    # template) is bulky, so expose only booleans the SPA/cockpit render as a
    # "running off draft override" badge, and drop the dict from meta.
    _ov = cfg.get("draft_test_override")
    meta["draft_test_override_active"] = bool(_ov)
    meta["draft_test_override_persist"] = bool(
        _ov.get("persist")) if isinstance(_ov, dict) else False
    meta.pop("draft_test_override", None)
    return meta


def _load_sites_config():
    """Read sites from disk on startup and instantiate SiteRunners.
    Each runner's __init__ rehydrates its queue from the SQLite queue table.
    Missing file is fine (first run); malformed file is reported but ignored."""
    if not SITES_FILE.exists(): return
    try:
        # v3.43.16: explicit UTF-8 encoding (mirrors _save_sites_config).
        data = json.loads(SITES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        sys.stderr.write(f"  ! sites_config.json malformed, ignoring: {e}\n")
        return
    for sid, cfg_in in data.items():
        cfg = {k: cfg_in.get(k, DEFAULTS.get(k, "")) for k in CFG_FIELDS}
        cfg["name"] = cfg_in.get("name", f"Site {sid}")
        # Apply defaults only where genuinely unset (preserves explicit 0/False)
        for k, d in DEFAULTS.items():
            if cfg.get(k) in ("", None): cfg[k] = d
        # B2 (GUI cut): carry the per-site draft-test override across a restart.
        # It is transient runtime state set by POST /api/template/test_extract,
        # NOT an operator-edited config field, so it is deliberately absent from
        # CFG_FIELDS — which means the CFG_FIELDS rebuild above would silently
        # DROP it, breaking Decision 4 ("override persists per-site, across
        # sessions"). _save_sites_config already writes it to disk (only
        # _-prefixed keys are stripped); this carries it back in on load.
        # Forwarded only when present + well-formed, so sites that never used the
        # override stay clean. Fail direction without this is fail-safe (a
        # restart reverts to the enabled-only matcher), but the stated contract
        # is cross-restart persistence with explicit clear — honor it.
        if isinstance(cfg_in.get("draft_test_override"), dict):
            cfg["draft_test_override"] = cfg_in["draft_test_override"]
        # v3.43.16: auto-heal misaligned parallel-array url_attribute.
        # If a previous version's merge_learned added row_selectors at
        # the front without prepending matching url_attribute slots,
        # the index-based resolver in runner.py reads the wrong
        # attribute for matched selectors and files download via
        # Chrome's bar to the default folder instead of via httpx to
        # download_dir.
        #
        # Heal direction matters: merge_learned PREPENDS row_selectors,
        # so the misalignment is "url_attribute is shorter at the front
        # of the parallel array." Pad with empty strings AT THE FRONT
        # to preserve the original tail entries' alignment. (Padding
        # at the end would make every original slot read the wrong
        # attribute — the very bug we're trying to fix.)
        learned = cfg.get("learned") or {}
        dl = learned.get("download") if isinstance(learned, dict) else None
        if isinstance(dl, dict):
            ua = dl.get("url_attribute")
            rows = dl.get("row_selectors") or []
            if isinstance(ua, list) and isinstance(rows, list) and rows:
                if len(ua) != len(rows):
                    old_len = len(ua)
                    if len(ua) < len(rows):
                        # Front-pad with empty strings — preserves the
                        # alignment of original entries with the tail
                        # of row_selectors (which are the older,
                        # pre-prepend entries).
                        ua = [""] * (len(rows) - len(ua)) + ua
                    else:
                        # Same direction for truncate: keep the tail.
                        ua = ua[-len(rows):]
                    dl["url_attribute"] = ua
                    sys.stderr.write(
                        f"  ! healed {sid}: url_attribute list "
                        f"({old_len} entries) front-padded to row_selectors "
                        f"({len(rows)} entries) — fixes misalignment from "
                        f"a pre-v3.43.16 merge bug\n"
                    )
        # v3.43.16: advise when a site has headless explicitly False —
        # workers running with visible windows can end up sharing OS
        # state (Chrome's configured download dir) with a takeover
        # window. The file may still land in the right place but
        # bypasses the app's download pipeline (integrity check,
        # atomic write, retry). If you saw this in earlier versions
        # without realizing — this is the fix. Set headless=True in
        # the site Config tab to opt into the new safer default.
        if cfg.get("headless") is False:
            sys.stderr.write(
                f"  ! note: site {sid} has headless=False explicitly. "
                f"Workers will open visible Chrome windows. This is fine "
                f"for debugging but can confuse the download pipeline "
                f"if a takeover is open at the same time. Consider "
                f"setting headless=True in the site Config tab.\n"
            )
        s_cfg[sid] = cfg
        s_meta[sid] = _build_meta(cfg)
        runners[sid] = SiteRunner(sid, cfg)
        # v3.43.35: initialize the account pool from cfg. Restores
        # persisted state (cooldown_until, fail_count) so accounts
        # that were dead/cooling-down before restart remain so.
        try:
            from . import account_pool as _ap
            accounts = cfg.get("accounts") or []
            if accounts:
                _ap.configure_pool(sid, accounts,
                    cooldown_seconds=int(cfg.get(
                        "account_cooldown_seconds",
                        _ap.DEFAULT_COOLDOWN_S)))
        except Exception as e:
            sys.stderr.write(f"  account_pool init for {sid} failed: {e}\n")
        cf = cfg.get("cookie_file","")
        if cf and Path(cf).exists():
            try:
                runners[sid].set_cookies_from_file(cf)
            except Exception as _cookie_err:
                # Audit 2026-05: visible warning rather than silent swallow.
                # If cookies fail to load, the user will see "login required"
                # for this site at first run — explaining why in the log helps.
                sys.stderr.write(
                    f"  cookie file load failed for {sid}: "
                    f"{type(_cookie_err).__name__}: {_cookie_err}\n"
                )
    sys.stderr.write(f"  ↺ restored {len(data)} site(s) from {SITES_FILE.name}\n")

_load_sites_config()


# v3.43.16: spawn session keep-alive threads for sites that have a
# password configured + keep_alive_enabled (default True).
#
# Each (site, account) pair gets its own daemon thread. The keeper
# periodically verifies the site's session is still valid; if not, it
# calls the do_login flow to re-authenticate without user intervention.
# State is exposed via /api/session_status for the dashboard UI.
#
# Set BD_DISABLE_KEEPALIVE=1 to skip startup (used by tests and any
# environment where you don't want the background threads).
def _start_session_keepers():
    if os.environ.get("BD_DISABLE_KEEPALIVE", "").strip() == "1":
        return
    from . import session_keeper as _sk
    from . import login as _login
    import json as _json

    def _do_login_for_keeper(site_id, account_idx, cfg):
        """Adapter from the keeper's callback signature to do_login.
        Returns (ok, detail). On success, writes cookies to the shared
        cookie file so workers see the fresh session."""
        try:
            # If multiple accounts, pick the right one
            accounts = cfg.get("accounts") or []
            if accounts and 0 <= account_idx < len(accounts):
                acc = accounts[account_idx]
                login_cfg = dict(cfg)
                login_cfg["username"] = acc.get("username", "")
                login_cfg["password"] = acc.get("password", "")
            else:
                login_cfg = cfg
            # allow_manual_takeover=False: keeper runs headless, no
            # human present to solve captchas.
            result = _login.do_login(login_cfg, allow_manual_takeover=False)
            if not isinstance(result, tuple) or len(result) < 3:
                return False, f"do_login returned unexpected: {type(result).__name__}"
            ok, info, cookies = result[0], result[1], result[2]
            if ok and cookies:
                # Write cookies to shared cookie file. v3.43.32: route
                # through save_cookies_to_file so the round-trip
                # validation runs — catches the disk-full case where
                # the partial file would silently break the next worker
                # launch. Previously this was hand-rolled .tmp+replace.
                try:
                    from .cookies import save_cookies_to_file, CookieRoundTripError
                    cookie_path = Path("cookies") / f"{site_id}.json"
                    save_cookies_to_file(cookie_path, cookies)
                except CookieRoundTripError as e:
                    return True, (f"login ok; cookie round-trip validation "
                                   f"FAILED ({e.kind}): {e}")
                except Exception as e:
                    return True, f"login ok; cookie write failed: {e}"
                return True, f"relogin ok ({info or 'no info'})"
            return False, f"login failed: {info or 'no info'}"
        except Exception as e:
            return False, f"login raised: {type(e).__name__}: {e}"

    for sid, cfg in list(s_cfg.items()):
        if not isinstance(cfg, dict): continue
        if not cfg.get("keep_alive_enabled", True): continue
        accounts = cfg.get("accounts") or []
        if accounts:
            # Spawn one keeper per account
            for idx in range(len(accounts)):
                if accounts[idx].get("password"):
                    _sk.start_keeper(sid, idx, cfg, _do_login_for_keeper)
        elif cfg.get("password"):
            _sk.start_keeper(sid, 0, cfg, _do_login_for_keeper)

_start_session_keepers()

# v3.43.24: process-alive heartbeat to disk. External monitors (Windows
# scheduled task, systemd, nagios) can watch this file's mtime to detect
# hung processes. Every 60s the helper writes {pid, ts, version, sites}
# to `state/heartbeat.json` (atomic via .tmp + replace). Stale by >5min
# = process is unresponsive even if it's still showing as running in
# Task Manager.
def _heartbeat_to_disk_loop():
    import json as _hb_json
    import time as _hb_time
    from pathlib import Path as _hb_Path
    state_dir = _hb_Path("state")
    while True:
        try:
            state_dir.mkdir(exist_ok=True)
            beat = {
                "pid": _os.getpid(),
                "ts": _hb_time.time(),
                "version": "3.43.24",
                "sites": len(runners),
                "running_workers": sum(
                    len(r._worker_threads) for r in runners.values()
                    if hasattr(r, "_worker_threads")
                ),
            }
            tmp = state_dir / "heartbeat.json.tmp"
            tmp.write_text(_hb_json.dumps(beat), encoding="utf-8")
            tmp.replace(state_dir / "heartbeat.json")
        except Exception as e:
            # Heartbeat is non-essential; log but don't crash the
            # thread. A failing disk shouldn't take down the app.
            sys.stderr.write(f"[heartbeat] write failed: {e}\n")
        _hb_time.sleep(60)

if not _os.environ.get("BD_DISABLE_KEEPALIVE"):
    import threading as _th_hb
    _th_hb.Thread(target=_heartbeat_to_disk_loop, daemon=True,
                   name="heartbeat-disk").start()

# v3.43.30: per-site watch-folder daemon. Each site with
# watch_enabled=True gets one polling thread that scans the
# configured watch_folder for .txt files of URLs to import.
# We launch one thread per site at startup; the loop itself checks
# the live config each iteration so toggling watch_enabled at
# runtime takes effect on the next poll. Gated by the same env
# flag as session keepers and heartbeat so tests don't spawn.

def _start_watch_folder_threads():
    """Spawn one daemon thread per configured site. The thread's
    own poll cycle handles enable/disable changes — we don't need
    to start/stop threads on config change."""
    if _os.environ.get("BD_DISABLE_KEEPALIVE"):
        return
    try:
        from . import watch_folder as _wf
    except Exception:
        return
    import threading as _wf_th
    for _sid, _r in runners.items():
        # One thread per site, regardless of whether watch is
        # currently enabled — the loop short-circuits when disabled.
        # This keeps thread count predictable.
        if _sid in _watch_threads:
            continue
        stop = _wf_th.Event()
        t = _wf_th.Thread(
            target=_wf.watch_loop_for_site,
            args=(_r, stop),
            daemon=True,
            name=f"watch-folder-{_sid}")
        _watch_threads[_sid] = t
        _watch_stops[_sid] = stop
        t.start()

_start_watch_folder_threads()


# ── v3.43.41: download-window scheduler ───────────────────────────────
# Single daemon thread watches the clock and pauses/resumes sites at
# their configured window boundaries. The callback bridges back to
# runner.start() / .stop() through the app's runners dict.
def _start_window_scheduler():
    import os, sys as _sys
    if os.environ.get("BD_DISABLE_KEEPALIVE"):
        # Same gate as session keepers — tests opt out of background
        # threads en masse via this env var
        return
    try:
        from . import download_window as _dw
    except Exception as e:
        _sys.stderr.write(f"  window_scheduler: import failed: {e}\n")
        return

    def _on_transition(site_id, should_run):
        """Bridge from the scheduler's "site X transitioned" signal
        to runner.start()/stop(). Called from the scheduler thread,
        so we must not hold any of app.py's locks long."""
        runner = runners.get(site_id)
        if runner is None:
            return
        action = ((s_cfg.get(site_id, {}) or {}).get(
            "window_action_outside") or "paused").strip().lower()
        if should_run:
            # Entering an active window — resume
            try:
                runner.log_event("window",
                    "Entered active window — resuming workers")
            except Exception:
                pass
            try:
                runner.start()
            except Exception as e:
                _sys.stderr.write(
                    f"  window: start({site_id}) failed: {e}\n")
        else:
            # Leaving the active window — pause or stop depending on
            # the per-site action_outside setting
            try:
                runner.log_event("window",
                    f"Left active window — applying {action!r}")
            except Exception:
                pass
            if action == "stopped":
                try:
                    runner.stop()
                except Exception as e:
                    _sys.stderr.write(
                        f"  window: stop({site_id}) failed: {e}\n")
            else:
                # "paused": just flip state. Don't tear down in-flight
                # workers; let them finish naturally. The next start()
                # call will respect the window check.
                try:
                    runner._state = "window_paused"
                except Exception:
                    pass

    sched = _dw.get_scheduler()
    sched.set_transition_callback(_on_transition)
    sched.start()
    _sys.stderr.write(
        "  ↺ window scheduler started\n")

_start_window_scheduler()


# ── v3.43.42: storage-tier scheduler ──────────────────────────────────
# Hourly daemon that moves completed downloads older than N days from
# the primary download_dir to a cold-storage tier. Distinct from
# spillover_dirs (write-side overflow); this is read-side cleanup.
def _start_storage_tier_scheduler():
    import os, sys as _sys
    if os.environ.get("BD_DISABLE_KEEPALIVE"):
        return
    try:
        from . import storage_tier as _st
    except Exception as e:
        _sys.stderr.write(f"  storage_tier: import failed: {e}\n")
        return
    sched = _st.get_scheduler()
    sched.start()
    _sys.stderr.write("  ↺ storage tier scheduler started\n")

_start_storage_tier_scheduler()


# Phase 6.4: load global concurrency cap. Stored separately from per-site
# config in `app_config.json` so user-tunable settings survive restart.
APP_CFG_FILE = Path("app_config.json")
def _load_app_config():
    from . import log as _log
    _llog = _log.get_logger(__name__)
    first_run = not APP_CFG_FILE.exists()
    if APP_CFG_FILE.exists():
        try:
            # v3.43.16: explicit UTF-8 encoding (consistency with sites_config).
            data=json.loads(APP_CFG_FILE.read_text(encoding="utf-8"))
            _app_cfg.update(data)
        except Exception as e:
            _llog.error("app_config.json malformed: %s", e)
    # ── v3.47.8 (#80): path allowlist auto-populate on first run ────────
    # The path_allowlist mechanism in _validate_path() is opt-in: an empty
    # allowlist means "permit any absolute non-traversing path", which is
    # the documented design choice for the single-operator threat model.
    # However the DAST audit flagged that this allows cookie_file=/etc/passwd
    # (the file isn't actually written to without further user action, but
    # the *acceptance* is a defensive gap).
    #
    # Fix: on TRULY first run (no app_config.json exists), seed the
    # allowlist with the two paths the operator will legitimately use:
    #   - $BD_HOME (where the app stores its working data)
    #   - ~/Downloads/bulk_downloader (default download directory)
    # This narrows the attack surface for fresh installs WITHOUT changing
    # behavior for any existing install (those keep their empty allowlist
    # = permissive setting, which the operator can already widen/narrow
    # via the Settings → Global config UI).
    if first_run and not _app_cfg.get("path_allowlist"):
        bd_home = str(Path(os.environ.get("BD_HOME") or Path.cwd()).resolve())
        downloads_default = str(
            (Path.home() / "Downloads" / "bulk_downloader").resolve()
        )
        # Dedupe in case BD_HOME happens to equal the downloads default
        seeded = list(dict.fromkeys([bd_home, downloads_default]))
        _app_cfg["path_allowlist"] = seeded
        try:
            _save_app_config()
            _llog.info(
                "first run: seeded path_allowlist with %s "
                "(edit in Settings → Global to adjust)", seeded
            )
        except Exception as e:
            _llog.warning("failed to persist seeded path_allowlist: %s", e)
    from .runner import set_global_concurrent_cap
    set_global_concurrent_cap(int(_app_cfg.get("global_max_concurrent",0) or 0))
    try:
        from . import daily_budget as _dbud
        _dbud.set_global_budget(int(_app_cfg.get("global_daily_byte_budget", 0) or 0))
    except Exception:
        pass
    # Phase 27: hand the AI config to the module. Off by default;
    # users opt in via the global config UI.
    # v3.43.43: now supports provider selection (ollama/claude/openai/gemini).
    try:
        from . import aiassist
        provider_name = (_app_cfg.get("ai_provider") or "ollama").strip().lower()
        # Default endpoint depends on provider — ollama localhost,
        # others use the cloud provider's public API.
        if _app_cfg.get("ai_endpoint"):
            endpoint = _app_cfg["ai_endpoint"]
        elif provider_name == "claude":
            endpoint = "https://api.anthropic.com"
        elif provider_name == "openai":
            endpoint = "https://api.openai.com"
        elif provider_name == "gemini":
            endpoint = "https://generativelanguage.googleapis.com"
        else:
            endpoint = "http://localhost:11434"
        aiassist.configure(
            provider=provider_name,
            endpoint=endpoint,
            model_vision=_app_cfg.get("ai_model_vision") or "",
            model_text=_app_cfg.get("ai_model_text") or "",
            api_key=_app_cfg.get("ai_api_key") or "",
            enabled=bool(_app_cfg.get("ai_enabled", False)),
        )
    except Exception as e:
        _llog.error("AI assist init failed: %s", e, exc_info=True)
    # Phase 34: apply persisted log level. Default INFO if not configured.
    try:
        _log.set_level(_app_cfg.get("log_level") or "INFO")
    except Exception as e:
        _llog.error("log level init failed: %s", e)
def _save_app_config():
    """Persist global app config. Atomic via .tmp + replace (v3.43.19):
    a crash mid-write would otherwise corrupt app_config.json and lose
    the user's global settings (concurrency cap, AI endpoint, log
    level, watch folder, etc.) on next start."""
    try:
        tmp = APP_CFG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_app_cfg, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(APP_CFG_FILE)
    except Exception as e:
        from . import log as _log
        _log.get_logger(__name__).error("app_config save failed: %s", e)
_load_app_config()

# v3.43.80 Phase 155: apply any pending schema migrations on boot.
# Idempotent — each migration tracks its applied version so a repeat
# run is a no-op. Fail-open: a broken migration logs but doesn't
# prevent startup.
try:
    from . import migrations as _migrations
    _mig_result = _migrations.apply_pending()
    if _mig_result.get("applied", 0) > 0:
        sys.stderr.write(
            f"[migrations] applied {_mig_result['applied']} "
            f"migration(s) at boot\n")
    if _mig_result.get("errors", 0) > 0:
        sys.stderr.write(
            f"[migrations] WARNING: {_mig_result['errors']} "
            f"migration error(s)\n")
except Exception as e:
    sys.stderr.write(f"migrations init failed: {e}\n")

# v3.43.80: start background services. Gated on BD_DISABLE_KEEPALIVE
# (same gate as the session keeper) so tests don't spawn timer threads
# that race with their tmpdirs. Pattern matches existing keeper init.
if not os.environ.get("BD_DISABLE_KEEPALIVE"):
    # bg_scheduler: drives periodic tasks (saved_searches, bitrot scan,
    # cookie_relogin, alerts_engine, site_weather, discovery,
    # maintenance, scheduled_exports, vpn_stats auto-blacklist,
    # federation claim expiry).
    try:
        from . import bg_scheduler as _bg
        _bg.register_default_tasks(
            s_cfg_getter=lambda: s_cfg,
            runners_getter=lambda: runners,
            # Deferred like the getters above: _capture_enqueue is def'd far
            # below this module-level init, so a bare reference here is a
            # forward NameError at load time (bg_scheduler init failed). The
            # lambda resolves the name at CALL time, when the module is loaded.
            capture_enqueue_fn=lambda site_id, urls: _capture_enqueue(site_id, urls),
        )
        _bg.start()
        sys.stderr.write("[bg_scheduler] started\n")
    except Exception as e:
        sys.stderr.write(f"bg_scheduler init failed: {e}\n")

    # webhooks: dedicated drain worker. Polls webhook_queue every 10s,
    # POSTs each enqueued event with retry/backoff. Separate from
    # bg_scheduler because webhook delivery wants tighter cadence
    # (subscribers expect ~10s p99 latency, not 60s scheduler tick).
    try:
        from . import webhooks as _webhooks
        _webhooks.start_drain_worker()
        sys.stderr.write("[webhooks] drain worker started\n")
    except Exception as e:
        sys.stderr.write(f"webhooks init failed: {e}\n")

# v3.43.31: apply per-domain rate-limit config from app_config.json.
# Reload after each /api/app_config save so live edits take effect
# without restart.
try:
    from . import rate_limit as _rate_limit
    _rate_limit.configure_from_app_config(_app_cfg)
except Exception as e:
    sys.stderr.write(f"rate_limit init failed: {e}\n")


# ── Phase 18.22: Folder watcher ─────────────────────────────────────────
# A background thread that polls a configured directory for new .txt files
# and auto-imports the URLs through the same routing logic /api/route_urls
# uses. Files are moved to <folder>/processed/ (or .imported.txt suffix on
# Windows where moves can be tricky) so the same file isn't re-imported.
#
# Configured via app_config.json:
#   watch_folder         — absolute path to watch (empty = disabled)
#   watch_interval_sec   — poll interval (default 30)
#   watch_archive        — true/false (default true) — move processed files
#                          to <watch_folder>/processed/ instead of just
#                          renaming. Off → just rename in-place to avoid
#                          re-import on next scan.
import threading
_watcher_thread = None
_watcher_stop = threading.Event()

def _route_urls_internal(urls):
    """Internal helper: same routing logic as /api/route_urls but without
    the Flask request layer. Returns (per_site_summary_dict, unrouted_list).

    Thread-safety: this gets called from the folder-watcher thread AND from
    Flask request handlers. We snapshot s_cfg.items() into a list at the
    top so concurrent site add/delete in another thread doesn't raise
    'dict changed size during iteration'."""
    from urllib.parse import urlparse
    by_site = {}; unrouted = []
    # Snapshot for thread-safe iteration
    cfg_snapshot = list(s_cfg.items())
    for url in urls:
        best_sid, best_score, _reason = _score_url_against_sites(
            url, cfg_snapshot)
        if best_score > 0 and best_sid:
            by_site.setdefault(best_sid, []).append(url)
        else:
            unrouted.append(url)
    summary = {}
    for sid, site_urls in by_site.items():
        if sid not in runners: continue
        added, dupes, *rest = runners[sid].load_urls(site_urls)
        summary[sid] = {"added": added, "dupes": dupes, "total": len(site_urls)}
    return summary, unrouted


# F3.1: wire the saved-search 'enqueue' action into the normal pipeline.
# The handler routes URLs exactly like /api/route_urls (site auto-detect +
# runners[sid].load_urls), so every admission gate, the review path, and the
# F1.5 dedup preflight apply unchanged. Registered once at import (after
# _route_urls_internal exists); a saved search with action='enqueue' calls
# this via saved_searches.run_one. Returns the count actually accepted.
def _saved_search_enqueue_handler(urls):
    try:
        summary, _unrouted = _route_urls_internal(list(urls))
        return sum(int(v.get("added", 0) or 0) for v in summary.values())
    except Exception:
        return 0


try:
    from . import saved_searches as _ss_wire
    _ss_wire.set_enqueue_handler(_saved_search_enqueue_handler)
except Exception as _ss_wire_err:  # pragma: no cover
    sys.stderr.write(f"[saved_searches] enqueue handler wire failed: {_ss_wire_err}\n")


def _score_url_against_sites(url: str, cfg_snapshot=None):
    """v3.43.40: extracted scoring helper used by both routing
    (just picks the best) and preview (also exposes why a given
    site won). Returns (best_sid, best_score, reason_text).

    The scoring algorithm is shared with quick_add and route_urls:
      - url_patterns regex match → 200
      - exact hostname match against login_url / success_url → 100
      - subdomain / apex match → 50
      - Tie-breaker: site with more existing URLs (small bonus)

    The reason_text describes WHY the winning site won, in a form
    suitable for showing to the user in the extension preview UI
    ('matched url_patterns', 'hostname match on login_url:
    wowgirls.com', etc.). Returns ('', 0, '') when nothing matched.

    Defensive: malformed entries (None config dict, non-string
    url_patterns, non-string login_url) are silently skipped rather
    than crashing — a corrupted sites_config.json shouldn't break
    every routing call."""
    from urllib.parse import urlparse
    if cfg_snapshot is None:
        cfg_snapshot = list(s_cfg.items())
    # AUDIT v3.43.46: coerce url to string. A caller passing an
    # int (e.g. corrupted JSON, test typo) used to crash
    # `re.search(pat, url, ...)` with TypeError below — the
    # urlparse() catch above doesn't help once we leave that try.
    if not isinstance(url, str):
        try:
            url = str(url) if url is not None else ""
        except Exception:
            url = ""
    try:
        target_host = (urlparse(url).hostname or "").lower()
    except Exception:
        target_host = ""
    best_sid = None
    best_score = -1
    best_reason = ""
    for sid, cfg in cfg_snapshot:
        if not isinstance(cfg, dict):
            continue  # corrupted entry
        score = 0
        reason = ""
        patterns_raw = cfg.get("url_patterns") or ""
        if isinstance(patterns_raw, str):
            patterns = patterns_raw.strip()
            if patterns:
                for line in patterns.replace(",", "\n").splitlines():
                    pat = line.strip()
                    if not pat:
                        continue
                    # v3.46.4 F9: skip pathologically long patterns to
                    # bound regex evaluation time
                    if len(pat) > 512:
                        continue
                    try:
                        if re.search(pat, url[:4096], re.IGNORECASE):
                            score = max(score, 200)
                            reason = f"matched url_patterns: {pat}"
                            break
                    except re.error:
                        continue
        for fld in ("login_url", "success_url"):
            v_raw = cfg.get(fld) or ""
            if not isinstance(v_raw, str):
                continue
            v = v_raw.lower()
            if not v:
                continue
            try:
                h = (urlparse(v).hostname or "").lower()
            except Exception:
                continue
            if h and target_host:
                if h == target_host:
                    if score < 100:
                        score = 100
                        reason = f"hostname match on {fld}: {h}"
                elif h.endswith("." + target_host) or target_host.endswith("." + h):
                    if score < 50:
                        score = 50
                        reason = f"subdomain match on {fld}: {h}"
        # Tie-breaker: site with more URLs
        if sid in runners:
            score += min(len(runners[sid].urls), 99) * 0.01
        if score > best_score:
            best_sid = sid
            best_score = score
            best_reason = reason
    return best_sid, best_score, best_reason

def _watcher_loop():
    """Thread body. Polls watch_folder for new .txt files; imports URLs;
    renames the file so it won't be processed again. Failures are logged
    but never crash the thread — folder watcher is best-effort."""
    import sys
    while not _watcher_stop.is_set():
        try:
            folder = (_app_cfg or {}).get("watch_folder", "").strip()
            if not folder:
                _watcher_stop.wait(5); continue
            interval = max(5, int((_app_cfg or {}).get("watch_interval_sec", 30) or 30))
            archive = bool((_app_cfg or {}).get("watch_archive", True))
            watch_dir = Path(folder)
            if not watch_dir.exists() or not watch_dir.is_dir():
                _watcher_stop.wait(interval); continue
            archive_dir = watch_dir / "processed"
            if archive: archive_dir.mkdir(exist_ok=True)
            for txt in sorted(watch_dir.glob("*.txt")):
                # Skip if it's already in the archive subdir
                if txt.parent != watch_dir: continue
                # Skip already-renamed files (imported, skipped)
                if ".imported" in txt.name or ".skipped" in txt.name: continue
                try:
                    content = txt.read_text(encoding="utf-8", errors="ignore")
                except Exception as e:
                    sys.stderr.write(f"[watcher] {txt.name}: read failed {e}\n"); continue
                urls = [u.strip() for u in content.splitlines() if u.strip().startswith("http")]
                if not urls:
                    sys.stderr.write(f"[watcher] {txt.name}: no URLs — renaming to .skipped\n")
                    # Rename so we don't re-read this file every poll cycle.
                    # We use .skipped.txt suffix so the user can spot these
                    # easily and decide what to do.
                    try:
                        target = txt.with_suffix(".skipped.txt")
                        n = 1
                        while target.exists():
                            target = txt.parent / f"{txt.stem}.skipped.{n}.txt"
                            n += 1
                        txt.rename(target)
                    except Exception as e:
                        sys.stderr.write(f"[watcher] {txt.name}: skip-rename failed {e}\n")
                    continue
                summary, unrouted = _route_urls_internal(urls)
                routed_count = sum(s["added"] for s in summary.values())
                sys.stderr.write(f"[watcher] {txt.name}: imported {routed_count}/{len(urls)} URLs "
                                 f"to {len(summary)} site(s); {len(unrouted)} unrouted\n")
                # Move or rename to prevent re-import
                try:
                    if archive:
                        target = archive_dir / txt.name
                        # Append a counter if name conflicts
                        n = 1
                        while target.exists():
                            target = archive_dir / f"{txt.stem}.{n}{txt.suffix}"
                            n += 1
                        txt.rename(target)
                    else:
                        target = txt.with_suffix(".imported.txt")
                        n = 1
                        while target.exists():
                            target = txt.parent / f"{txt.stem}.imported.{n}.txt"
                            n += 1
                        txt.rename(target)
                except Exception as e:
                    sys.stderr.write(f"[watcher] {txt.name}: rename failed {e}\n")
            _watcher_stop.wait(interval)
        except Exception as e:
            sys.stderr.write(f"[watcher] loop error (continuing): {e}\n")
            _watcher_stop.wait(15)

def _start_watcher():
    """Idempotent start. Called at module import + whenever app_config
    changes. The thread polls _app_cfg directly each loop, so we don't
    actually need to restart it on config changes — it just notices."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive(): return
    _watcher_stop.clear()
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True,
                                       name="bd-folder-watcher")
    _watcher_thread.start()
_start_watcher()


# ── Phase 1 root flip (v3.66.203, LEGACY_MIGRATION_PLAN) ────────────────
# `/` now serves the D3 React SPA (Vite base "/" + router basename "/").
# The legacy shell was deleted in Phase 4 (v3.66.334); "legacy" stays a
# reserved prefix below so /legacy is a 404, not SPA HTML. The catch-all
# below is the standard SPA fallback: Werkzeug gives <path:> converter
# rules the lowest match priority, so every explicit Flask rule
# (/api/*, /static/*, /cockpit/*, blueprints, …) wins; only otherwise
# unrouted paths land here.
#
# 404 discipline: an unrouted path under a *reserved* first segment
# (api/static/cockpit/… — the infra namespaces) must stay a 404, not
# silently return SPA HTML — otherwise a typo'd API call "succeeds"
# with text/html and the error surfaces somewhere confusing. Same for
# asset-looking paths (file extension) that don't exist in dist.
_SPA_RESERVED_PREFIXES = (
    "api", "static", "cockpit", "fleet", "framework", "metrics",
    "screenshots", "stream", "m", "m2", "legacy",
)
_SPA_ASSET_EXT_RE = re.compile(
    r"\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|json|txt|webmanifest|html)$",
    re.I)


# ── v3.66.204: method-semantics parity (post-root-flip 405/404 repair) ──
# The root catch-all accepts GET on every path, which has two side
# effects on Werkzeug's native method handling, both repaired here:
#
#   1. GET on a POST-only route (e.g. /api/dev/vision_test) no longer
#      raises MethodNotAllowed at dispatch — it lands in serve_spa_root
#      and (pre-204) hit the reserved-prefix 404. Pre-flip this was a
#      405 with an Allow header, and 8 suite tests pin it. Repaired in
#      serve_spa_root: probe explicit rules; non-GET methods exist →
#      405 + Allow.
#   2. A non-GET request to a path with NO explicit rule at all (e.g.
#      POST /api/no_such_thing) now 405s at dispatch (the catch-all
#      matched the path for GET) where pre-flip it was a plain 404.
#      Repaired in the MethodNotAllowed handler below: if no explicit
#      rule serves the path under ANY method and the path is in a
#      reserved namespace (or asset-shaped), convert to 404. A non-GET
#      to a genuine SPA page path keeps the 405 (Allow: GET) — the
#      same answer the /m2 mount gave pre-flip.
_SPA_PROBE_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


def _explicit_methods_for_path(path: str) -> set:
    """Methods served by EXPLICIT rules (anything but the serve_spa_root
    catch-all) matching this exact path. Public adapter API only."""
    from werkzeug.exceptions import HTTPException
    adapter = app.url_map.bind("localhost")
    found = set()
    for m in _SPA_PROBE_METHODS:
        try:
            endpoint, _ = adapter.match(path, method=m)
        except HTTPException:
            continue
        except Exception:  # noqa: BLE001 — probe must never raise
            continue
        if endpoint != "serve_spa_root":
            found.add(m)
    return found


def _405_with_allow(methods: set) -> "Response":
    resp = Response("Method Not Allowed", status=405,
                    mimetype="text/plain; charset=utf-8")
    resp.headers["Allow"] = ", ".join(sorted(set(methods) | {"OPTIONS"}))
    return resp


@app.errorhandler(405)
def _method_not_allowed_parity(e):
    """Restore pre-flip 404/405 semantics for non-GET requests whose
    405 was manufactured solely by the catch-all's path match.

    If ANY explicit rule serves this path (under any method), the 405
    is genuine — return it untouched (default body + Allow). If none
    does, the path 'exists' only because the catch-all GETs everything:
    reserved-namespace and asset-shaped paths revert to the pre-flip
    404; SPA page paths keep a 405 with Allow: GET (matching what the
    /m2 mount answered pre-flip for e.g. POST /m2/queue)."""
    try:
        path = request.path or "/"
        explicit = _explicit_methods_for_path(path)
        if explicit:
            # Genuine endpoint: keep Werkzeug's native 405 response but
            # repair the Allow header — the native one includes GET/HEAD
            # purely because the catch-all matches every path for GET,
            # which is misleading (GET here answers 405 too). Pre-flip
            # Allow listed only the explicit methods (+OPTIONS).
            resp = e.get_response()
            resp.headers["Allow"] = ", ".join(sorted(explicit | {"OPTIONS"}))
            return resp
        first_seg = path.lstrip("/").split("/", 1)[0]
        if first_seg in _SPA_RESERVED_PREFIXES or _SPA_ASSET_EXT_RE.search(path):
            return Response("Not Found", status=404,
                            mimetype="text/plain; charset=utf-8")
        return _405_with_allow({"GET", "HEAD"})
    except Exception:  # noqa: BLE001 — never let the handler 500 a 405
        return e


@app.route("/", defaults={"subpath": ""})
@app.route("/<path:subpath>")
def serve_spa_root(subpath: str = ""):
    """Serve the D3 React SPA from frontend/dist/ at the site root.

    Asset paths (a real file under dist/) are served directly with
    correct MIME via send_from_directory (traversal blocked by Flask).
    Reserved-namespace and missing-asset paths 404. Anything else
    returns index.html so React Router can claim it. Missing dist
    returns the installer-aware 503 (same not-built surface /m2 had).

    Session note: no inline CSRF mint here — the dist index.html is a
    static artifact with no meta placeholder. The SPA bootstraps via
    /api/csrf, which self-mints since P0.1 (v3.66.202). The
    _bootstrap_session after_request hook still warms the cookie on
    GET / so the very first API POST never races a Set-Cookie.
    """
    from flask import send_from_directory, abort

    first_seg = subpath.split("/", 1)[0] if subpath else ""
    if first_seg in _SPA_RESERVED_PREFIXES:
        # v3.66.204: a GET that lands here for a path with explicit
        # non-GET rules (POST-only API routes) must answer 405 + Allow,
        # not 404 — the pre-flip Werkzeug semantics, pinned by 8 suite
        # tests (d3_u3/u5/u10/u11, t2 fixture, t9 vision, v3_66_8).
        explicit = _explicit_methods_for_path("/" + subpath)
        if explicit:
            return _405_with_allow(explicit)
        abort(404)

    if not _M2_DIST_ROOT.is_dir():
        return _m2_503_node_missing()
    index_path = _M2_DIST_ROOT / "index.html"
    if not index_path.is_file():
        # Dist exists but is empty/broken — same 503 surface. Distinct
        # cause but indistinguishable fix from the operator's side.
        return _m2_503_node_missing()

    if subpath:
        candidate = _M2_DIST_ROOT / subpath
        if candidate.is_file():
            return send_from_directory(str(_M2_DIST_ROOT), subpath)
        if _SPA_ASSET_EXT_RE.search(subpath):
            # Asset-looking but not in dist: a real 404 (stale hashed
            # bundle name after an upgrade, typo) — never SPA HTML.
            abort(404)

    # Not an asset — SPA client route. React Router resolves it.
    # index.html must NEVER be cached: hashed assets under the dist root are
    # content-addressed and stay long-cacheable, but a cached index pins the
    # browser to a stale bundle across overlay deploys (mobile Safari was
    # observed serving an old SPA indefinitely until site data was cleared).
    resp = send_from_directory(str(_M2_DIST_ROOT), "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# Phase 4 (v3.66.334): the legacy shell and its /legacy route were removed
# outright (dev-only tool, no external bookmarks to preserve). "legacy" stays
# in _SPA_RESERVED_PREFIXES so /legacy resolves to a clean 404 rather than
# falling through to the SPA catch-all.


# ── v3.48 (#32): Mobile read-only view at /m/ ───────────────────────────
# A lightweight read-only status page sized for phones. Renders all the
# information the operator needs while AFK — current queue depth, what's
# downloading, last 10 completed/failed — but with no controls, no JS-
# heavy panels, no PWA shell. Loads in <100ms even on 3G.
#
# Why "read-only": when checking from a phone, the operator overwhelmingly
# just wants to know "is it still running, how far through?" — not to
# fiddle with site configs. By stripping the control surface we can omit
# the entire app.js bundle (~400KB) and render a static-feeling page.
#
# Auth: same session cookie as the main app. /m/ doesn't bypass auth.
#
# v3.62.1: the previous mobile_view() handler registered here was dead
# code - it duplicated the /m and /m/ routes already claimed by
# serve_mobile_view() (Flask resolves a duplicate endpoint to the
# first registration). It also predated mobile.html: it built the
# page as a server-side HTML string, whereas the live route serves
# the client-fetch mobile.html. Removed - serve_mobile_view is the
# one true handler for /m.

@app.route("/manifest.json")
def pwa_manifest():
    # v3.62.2 fix: serve the real static/manifest.json. The route
    # previously referenced an undefined PWA_MANIFEST constant (lost in
    # the v3.62.1 mobile_view cleanup), 500-ing on every page load.
    try:
        path = Path(app.static_folder) / "manifest.json"
        body = path.read_text(encoding="utf-8")
    except OSError:
        # Static file missing — degrade to a minimal valid manifest
        # rather than 500. PWA install still works at a basic level.
        body = ('{"name":"BulkDownloader","short_name":"BD",'
                '"start_url":"/","display":"standalone"}')
    return Response(body, mimetype="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=3600"})

@app.route("/icon.svg")
def pwa_icon_svg():
    """Same icon as the favicon — bundled inline so we don't need a static dir."""
    svg = (b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
           b"<rect width='32' height='32' rx='7' fill='#6366f1'/>"
           b"<path d='M16 7v14m-5-5l5 5 5-5M9 25h14' stroke='white' "
           b"stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round' fill='none'/>"
           b"</svg>")
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})

# /apple-touch-icon.png -> app_apple.py (#12 thin-core-shell extraction)

SW_JS = r"""
// Bulk Downloader service worker — shell cache + web push handlers.
// We DON'T cache API endpoints (they need fresh data). We DO cache the
// HTML shell, fonts, and PWA assets so the app starts fast and shows
// "offline" gracefully. Push events display native OS notifications.
// v3.43.80 Phase 108: structured offline 503 for /api/* so the client
// can distinguish "no network" from a real server error, and
// SKIP_WAITING handling so the operator can refresh on demand.
const CACHE_NAME = 'bulk-dl-v3';  // bumped Phase 108
const SHELL = ['/', '/manifest.json', '/icon.svg'];
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});
self.addEventListener('message', (e) => {
  // Page asks us to take over now (operator clicked "Refresh now"
  // on the update banner) — accept and let the page reload itself
  // via the controllerchange listener it registered.
  if (e.data && e.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;  // never cache mutations
  const url = new URL(req.url);
  // API + SSE go to network. On offline failure, return a structured
  // 503 JSON so client JS can show an "offline" badge instead of
  // exploding on a TypeError. The marker {offline: true} lets the
  // fetch wrapper distinguish "we're offline" from "server error."
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/sse/')) {
    e.respondWith(
      fetch(req).catch(() =>
        new Response(JSON.stringify({error: 'offline', offline: true}),
          {status: 503, headers: {'Content-Type': 'application/json'}})
      )
    );
    return;
  }
  // Shell: cache-first with background revalidation
  e.respondWith(
    caches.match(req).then((cached) => {
      const fetched = fetch(req).then((resp) => {
        if (resp && resp.status === 200) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, clone));
        }
        return resp;
      }).catch(() => cached);
      return cached || fetched;
    })
  );
});

// Phase 16.43: Push notification handler. Fires when the server sends
// a push via VAPID; we render a native OS notification with the payload.
// Tag-based grouping means rapid-fire notifications collapse into one
// (e.g. 5 completions within 60s show as one updating banner instead
// of stacking).
self.addEventListener('push', (e) => {
  let data = {title: 'Bulk Downloader', body: 'Update', url: '/', tag: 'bulkdl'};
  try { if (e.data) data = Object.assign(data, e.data.json()); } catch (_) {}
  e.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    icon: '/icon.svg',
    badge: '/icon.svg',
    tag: data.tag,
    data: {url: data.url},
    renotify: false,  // don't re-vibrate when the same tag is updated
  }));
});

// Phase 16.43: clicking the notification focuses the app (or opens it)
// and navigates to the URL embedded in the payload (typically the
// queue or a specific site).
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const targetUrl = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then((clients) => {
      for (const c of clients) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          c.navigate(targetUrl); return c.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    })
  );
});
"""

@app.route("/sw.js")
def pwa_sw():
    return Response(SW_JS, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/",
                             "Cache-Control": "no-cache"})

# ── Phase 16.43: Web push notification endpoints ──────────────────────────
# /api/push -> app_push.py (Phase 4 thin-core-shell extraction)
# api_global_config_defaults -> app_global_config.py (Phase 4 multi-block extraction)


# ── B1 (post-365): settings source-of-truth resolver ────────────────────
# Read-only. For each global-config field, classify its true origin + static
# apply-timing, extending the live-vs-defaults diff above. Makes the Settings
# source/apply chips honest. SECRET DISCIPLINE: secret-bearing fields are
# reported as refs only — the payload NEVER carries a secret value.
_ORIGINS_SECRET_FIELDS = frozenset({"ai_api_key"})
# Static apply-timing map; anything unlisted is "immediate".
_ORIGINS_APPLY_RESTART = frozenset()  # none of the current global keys need a restart


def _origins_env_locked(field):
    """A field is env-locked when a BD_<UPPER(field)> env var pins it. Static
    check only (no behavior effect) — surfaces the lock in the UI."""
    return bool(os.environ.get("BD_" + field.upper()))


# api_global_config_origins -> app_global_config.py (Phase 4 multi-block extraction)


# ── B1 (post-365): run-history read surfaces ────────────────────────────
# /api/runs -> app_runs.py (Phase 4 thin-core-shell extraction)
# ── Cut 4: operator-intelligence composites (read-only) ───────────────
def _oi_flagged(sites_obj, flag_keys=(), markers=("expired", "stale",
                "unhealthy", "over", "exceeded", "drift", "error", "fail")):
    """Best-effort count of sites whose status looks problematic. Fail-soft:
    an unknown shape yields 0 rather than raising."""
    try:
        items = (sites_obj.values() if isinstance(sites_obj, dict)
                 else (sites_obj or []))
    except Exception:
        return 0
    n = 0
    for it in items:
        try:
            if not isinstance(it, dict):
                continue
            if flag_keys and any(it.get(k) for k in flag_keys):
                n += 1
                continue
            blob = " ".join(str(it.get(k, "")) for k in
                            ("status", "state", "health", "class", "level")).lower()
            if any(m in blob for m in markers):
                n += 1
        except Exception:
            pass
    return n


def _oi_dir_writable(path):
    """(exists, writable) for a candidate dir — read-only, never creates."""
    import os as _os
    try:
        return _os.path.isdir(path), _os.access(path, _os.W_OK)
    except Exception:
        return False, False


def _oi_default_download_dir():
    import os as _os
    cand = _os.environ.get("BD_DOWNLOAD_DIR")
    if cand:
        return cand
    try:
        gc = _load_global_config() if "_load_global_config" in globals() else {}
        if isinstance(gc, dict) and gc.get("download_dir"):
            return gc["download_dir"]
    except Exception:
        pass
    return None


def _chk(key, label, status, detail=""):
    return {"key": key, "label": label, "status": status, "detail": detail}


# api_queue_preflight -> app_queue.py (Phase 4 multi-block extraction)


# api_site_readiness -> app_sites.py (Phase 4 multi-block extraction)


# api_global_config -> app_global_config.py (Phase 4 multi-block extraction)


# ── v3.43.16: UI event logging endpoints ──────────────────────────────
# /api/ui_events -> app_ui_events.py (Phase 4 thin-core-shell extraction)
# ── Phase 27: AI assist endpoints ─────────────────────────────────────
# api_ai_status -> app_ai.py (Phase 4 multi-block extraction)


# api_ai_health -> app_ai.py (Phase 4 multi-block extraction)


# api_ai_models -> app_ai.py (Phase 4 multi-block extraction)


# api_ai_suggest_selectors -> app_ai.py (Phase 4 multi-block extraction)


# api_ai_classify -> app_ai.py (Phase 4 multi-block extraction)


# api_ai_normalize_resolution -> app_ai.py (Phase 4 multi-block extraction)


# api_ai_diff_repair -> app_ai.py (Phase 4 multi-block extraction)


# api_ai_chat -> app_ai.py (Phase 4 multi-block extraction)



# Given a URL that's stuck in needs_review, hand the AI:
#   - the URL itself + the failure message
#   - the screenshot taken at the time of failure (if we have one)
#   - the recent events for this URL (DOM hints, attempted selectors)
#   - the site's currently-learned download selectors (so the AI knows
#     what was tried)
# Ask for a diagnosis + 1-3 proposed new selectors. Result is shown in
# a toast / modal — never auto-applied.
# api_ai_reanalyze -> app_sites.py (Phase 4 multi-block extraction)


# /api/status -> app_status.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 87 (F23): yt-dlp version freshness ─────────────────
# yt-dlp ships extractor fixes multiple times per week. A stale binary
# is the single most common cause of "this site stopped working." This
# endpoint reports the installed version + age so the UI can warn the
# operator without auto-updating (which would surprise users who manage
# packages outside BD).
# /api/ytdlp_status -> app_ytdlp_status.py (Phase 4 thin-core-shell extraction)
# /api/ytdlp_update -> app_ytdlp_update.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 96: capacity planning ──────────────────────────────
# Combined disk/queue/bottleneck forecast endpoint. The UI's capacity
# panel (future Phase 96 UI work) renders this; for now /api/capacity
# is callable from bdctl or a custom dashboard.
# /api/capacity -> app_capacity.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phases 93-106: API surface for the new modules ──────────
# Each endpoint is thin — the module does the work; this layer just
# wires CSRF, request shape, and JSON serialization. Keeps the module
# layer Flask-independent (it can be re-used from bdctl, the TG bot,
# or future ASGI port).

# Saved searches CRUD (Phase 93)
# /api/saved_searches -> app_saved_searches.py (Phase 4 thin-core-shell extraction)
# Recommendations (Phase 95)
# /api/recommendations -> app_recommendations.py (Phase 4 thin-core-shell extraction)
# Account health (Phase 100)
# /api/accounts -> app_accounts.py (Phase 4 thin-core-shell extraction)
# Cost economics (Phase 101)
# /api/cost -> app_cost.py (Phase 4 thin-core-shell extraction)
# Provenance (Phase 104)
# /api/provenance -> app_provenance.py (Phase 4 thin-core-shell extraction)
# Bit-rot detection (Phase 105)
# /api/bitrot -> app_bitrot.py (Phase 4 thin-core-shell extraction)
# Wayback CDX (Phase 106) — useful for resurrecting dead URLs
# /api/wayback -> app_wayback.py (Phase 4 thin-core-shell extraction)
# Policy gates (Phases 102+103) — surface budget + quiet-hours state
# /api/budget -> app_budget.py (Phase 4 thin-core-shell extraction)
# RAM-disk staging status (Phase 98)
# /api/ramdisk -> app_ramdisk.py (Phase 4 thin-core-shell extraction)
# Circuit-breaker state (Phase 99)
# /api/circuit -> app_circuit.py (Phase 4 thin-core-shell extraction)
# Subtitle module status (Phase 89) — minimal surface for now
# api_subtitles_status -> app_subtitles.py (Phase 4 multi-block extraction)

# ── v3.45.7 Phase 181: alternate plex_deep backend via plexapi ────────
# api_plex_deep_backend_status -> app_plex.py (Phase 4 multi-block extraction)


# ── v3.45.7 Phase 181: plex_advanced (plexapi-backed read ops) ────────
# api_plex_adv_status -> app_plex.py (Phase 4 multi-block extraction)

# api_plex_adv_server_info -> app_plex.py (Phase 4 multi-block extraction)

# api_plex_adv_library_stats -> app_plex.py (Phase 4 multi-block extraction)

# api_plex_adv_recently_added -> app_plex.py (Phase 4 multi-block extraction)

# api_plex_adv_on_deck -> app_plex.py (Phase 4 multi-block extraction)

# api_plex_adv_search -> app_plex.py (Phase 4 multi-block extraction)

# api_plex_adv_mark -> app_plex.py (Phase 4 multi-block extraction)


# ── v3.47.0 Phase 195: crash recovery — orphan .part files ────────────
# /api/crash_recovery -> app_crash_recovery.py (Phase 4 thin-core-shell extraction)
# ── v3.47.3 Phase 200: dev tools — in-GUI test runner ─────────────────
# Every endpoint here returns 404 unless BD_DEV_MODE=1 is set. The dev
# tools tab + test runner are intentionally invisible to production
# users.
def _request_is_same_origin() -> bool:
    """True when the request's Referer host:port matches its Host -- i.e. it
    originates from a page this server served (the SPA). Mirrors the robust
    parse in _check_token (hostname+port compared, not a substring)."""
    ref = request.headers.get("Referer", "")
    host = request.headers.get("Host", "")
    if not (ref and host):
        return False
    try:
        from urllib.parse import urlparse
        r = urlparse(ref)
        ref_netloc = (r.hostname or "").lower()
        if r.port:
            ref_netloc = f"{ref_netloc}:{r.port}"
        if r.scheme == "http" and r.port == 80:
            ref_netloc = (r.hostname or "").lower()
        elif r.scheme == "https" and r.port == 443:
            ref_netloc = (r.hostname or "").lower()
        return ref_netloc == host.lower()
    except Exception:
        return False


def _dev_request_authorized() -> bool:
    """F-APP04-01: the /api/dev/* surface is privileged (lint / probe /
    leak-scan / perf tooling). Even on a box with no global auth token
    configured (where _check_token allows all), it must not be reachable from
    an untrusted network by an unauthenticated caller. Authorized requests:
      * loopback -- a LOCAL request (test-client, on-box curl, standalone),
        not the network;
      * a same-origin browser request -- the SPA keeps working with no token;
      * a valid redeemed session cookie;
      * the master bearer / X-BD-Token.
    A remote unauthenticated cross-origin request is refused (403). Scoped
    bdapi_ tokens already cannot reach /api/dev/* (_API_TOKEN_ROUTE_POLICY)."""
    ra = (request.remote_addr or "")
    if ra in ("127.0.0.1", "::1", "localhost"):
        return True
    sess = request.cookies.get("bd_session", "")
    if sess and _session_valid(sess):
        return True
    if _request_is_same_origin():
        return True
    _toks = _accepted_tokens()
    if _toks:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and any(_token_eq(auth[7:].strip(), t) for t in _toks):
            return True
        xt = request.headers.get("X-BD-Token", "")
        if xt and any(_token_eq(xt.strip(), t) for t in _toks):
            return True
    return False


def _dev_mode_guard():
    """Return a 404 response if dev mode is off, or a 403 if the request is not
    authorized to reach the dev surface. Used by every dev endpoint as the
    first line."""
    from . import dev_tools as _dt
    if not _dt.is_dev_mode():
        return jsonify({"error": "dev mode disabled "
                                  "(set BD_DEV_MODE=1 to enable)"}), 404
    if not _dev_request_authorized():
        return jsonify({"error": "dev endpoints require a same-origin session or a "
                                  "valid token (not reachable unauthenticated from "
                                  "the network)"}), 403
    return None

# api_dev_enabled -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_discover -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_run -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_run_status -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_run_cancel -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_runs_recent -> app_dev.py (Phase 4 multi-block extraction)


# ── perf lab: internal memory audit + load injector ───────────────────
# Dev-only, same gate as the rest of /api/dev/*. The memory audit is
# read-only; the load injector is a stress tool whose every profile is
# cancellable and reversible (see perf_lab.purge).
# api_dev_mem_audit -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_mem_audit_track -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_load -> app_dev.py (Phase 4 multi-block extraction)


# ── dev suite: read-only inspection tools ─────────────────────────────
# All GET, all dev-gated, all strictly read-only.
# api_dev_routes -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_threads -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_db_stats -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_runner_state -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_logtail -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_env -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_config -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_proc -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_invariants -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_template_audit -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_deep_detect -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_leak_scan -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_config_check -> app_dev.py (Phase 4 multi-block extraction)


# ── dev suite: maintenance actions (state-changing, CSRF-gated) ───────
# api_dev_gc -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_wal_checkpoint -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_log_level -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_sse_status -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_sql -> app_dev.py (Phase 4 multi-block extraction)


# ── dev suite: release & integrity tools (backlog Tier 0) ────────────
# All GET, all dev-gated, all read-only — they inspect the source tree,
# the route table, and the DB.
# api_dev_version_check -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_changelog_lint -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_bat_lint -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_sh_lint -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_zip_manifest -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_auth_map -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_csrf_check -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_integrity -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_backup_check -> app_dev.py (Phase 4 multi-block extraction)


# ── dev suite: diagnostic inspectors (backlog Tier 1) ────────────────
# All GET, all dev-gated, all read-only.
# api_dev_log_search -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_stuck_jobs -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_rate_limits -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_keepers -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_ollama -> app_dev.py (Phase 4 multi-block extraction)


# ── dev suite: request metrics & thread tools (backlog Tier 1) ───────
# All GET, all dev-gated, all read-only.
# api_dev_latency -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_slow_endpoints -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_error_rate -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_exceptions -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_thread_dump -> app_dev.py (Phase 4 multi-block extraction)

# api_dev_deadlock_check -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_dispatch_chain -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_dispatch_dryrun -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_config_audit -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_import_preflight -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_duplicate_sites -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_orphan_rows -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_stale_references -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_cookie_jar -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_cookie_age -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_auth_cookie_test -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_login_template_dryrun -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_credential_resolver -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_extractor_matrix -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_extractor_fastpath -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_ffmpeg_preview -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_resolution_test -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_manifest_probe -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_event_tap -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_event_tap_ui -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_slow_queries -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_index_advisor -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_migration_status -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_lockfile_scan -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_tempdir_clean -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_guard_status -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_coverage_map -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_test_run_diff -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_parametrize_fanout -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_flaky_tests -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_dependency_audit -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_secret_scan -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_path_allowlist_test -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_sast_summary -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_config_reload -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_cache_clear -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_fixture_site_start -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_fixture_site_stop -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_fixture_site_status -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_config_snapshot -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_config_snapshots -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_config_restore -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_config_snapshot_diff -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_runner_console -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_job_replay -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_systemd_check -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_pin_drift -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_queue_table -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_fts_index -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_db_growth -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_queue_throughput -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_retry_schedule -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_worker_profile -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_account_pool -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_manual_takeover_log -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_csrf_token -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_prompt_preview -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_ai_fallback -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_ai_latency -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_ai_health_history -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_vision_test -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_magic_bytes -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_mp4_metadata -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_dedup_hashes -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_partials -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_filename_template -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_vpn_config -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_vpn_rotation -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_vpn_probe -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_egress_ip -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_flaresolverr_health -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_captcha_relay -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_stealth_audit -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_disk_usage -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_download_scan -> app_dev.py (Phase 4 multi-block extraction)


# ── Tier-4 dev tools (T34-T38) ─────────────────────────────────────

# api_dev_dead_css -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_storage_tier_status -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_maintenance_mode -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_maintenance_enable -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_maintenance_disable -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_token_estimate -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_i18n_coverage -> app_dev.py (Phase 4 multi-block extraction)


# ── Tier-4 dev tools (T39-T43) ─────────────────────────────────────

# api_dev_model_pull_check -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_feature_flags -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_feature_flag_set -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_feature_flag_delete -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_window_simulate -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_golden_files -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_tls_check -> app_dev.py (Phase 4 multi-block extraction)


# ── Tier-4 dev tools (T44-T45) ─────────────────────────────────────

# api_dev_request_replay_list -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_request_replay -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_login_flows -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_login_flow_save -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_login_flow_delete -> app_dev.py (Phase 4 multi-block extraction)


# api_dev_test_timing -> app_dev.py (Phase 4 multi-block extraction)


# ── v3.47.0 Phase 199: resumable mass-import ──────────────────────────
# /api/import -> app_import.py (Phase 4 thin-core-shell extraction)
# ── v3.47.0 Phase 198: selector drift detection ───────────────────────
# /api/selector_drift -> app_selector_drift.py (Phase 4 thin-core-shell extraction)
# ── v3.47.0 Phase 197: cookie health monitor ──────────────────────────
# /api/auth_health -> app_auth_health.py (Phase 4 thin-core-shell extraction)
# ── v3.47.0 Phase 196: daily byte budget — per-site cap ───────────────
# /api/daily_budget -> app_daily_budget.py (Phase 4 thin-core-shell extraction)
# ── v3.45.0 Phase 194: content rights / takedown handling ─────────────
# /api/rights -> app_rights.py (Phase 4 thin-core-shell extraction)
# ── v3.44.6 Phase 186: macro recorder storage layer ───────────────────
# /api/macros -> app_macros.py (Phase 4 thin-core-shell extraction)
# api_post_reveal_decision -> app_sites.py (Phase 4 multi-block extraction)


# api_pending_approvals -> app_sites.py (Phase 4 multi-block extraction)


# api_auto_submit_decision -> app_sites.py (Phase 4 multi-block extraction)
# api_library_audit -> app_library.py (Phase 4 multi-block extraction)

# api_library_regen_nfos -> app_library.py (Phase 4 multi-block extraction)

# api_library_orphans_post -> app_library.py (Phase 4 multi-block extraction)

# ── v3.43.95 Phase 175: on-demand subtitle download for a history row ──
# api_subtitles_fetch -> app_subtitles.py (Phase 4 multi-block extraction)

# OpenAPI spec (Phase 113) — auto-generated from registered Flask routes
# api_openapi + api_openapi_parity (/api/openapi.json, /api/openapi/parity) -> app_openapi.py (Phase 4 thin-core-shell; both routes in ONE module -- dotted-module footgun)

# ── v3.43.80 Phase scheduler: periodic background tasks ───────────────
# The new modules (saved_searches.run_due, bitrot.run_scan, etc.) need
# to fire on a cadence. Rather than each module spinning up its own
# thread, the bg_scheduler module runs one coordinator that drives
# every periodic task. This endpoint surfaces task status; the
# scheduler starts automatically on first /api/bg/status hit (lazy
# start avoids holding open threads in tests / standalone tooling).
# /api/bg -> app_bg.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 111: knowledge management -> app_knowledge.py (Phase 4 thin-core-shell) ──

# ── v3.43.80 Phase 112: selector playground ───────────────────────────
# /api/playground -> app_playground.py (Phase 4 thin-core-shell extraction)
# ── F2.6 DOM Analyzer Workbench (post-hoc capture inspection) ──────────
# Replay half of the dev-tools loop: load an existing capture, browse its
# REDACTED DOM, test selectors against it, pin a review-only candidate.
# All DOM the family emits passes dom_analyzer's layered, fail-closed F2 gate
# (redact_dom_node + mask-propagation + redact_artifact, proven clean by
# scan_artifact_secrets). Captures are resolved by basename against the known
# capture dirs — never a client-supplied path. Selector testing reuses
# /api/playground/test (no analyzer test route).
# /api/analyzer -> app_analyzer.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 114: template marketplace ──────────────────────────
# /api/marketplace -> app_marketplace.py (Phase 4 thin-core-shell extraction)
# /api/plugins -> app_plugins.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 109: accessibility helpers ─────────────────────────
# /api/a11y -> app_a11y.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 117: diagnostics bundle ────────────────────────────
# /api/diagnostics -> app_diagnostics.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 115: edge deploy artifact generation ───────────────
# /api/deploy -> app_deploy.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 128: Prometheus /metrics ───────────────────────────
@app.route("/metrics")
def metrics_endpoint():
    """Prometheus text-format exposition. Scrape-friendly; no auth.
    Bind only to trusted networks if security matters."""
    try:
        from . import metrics_prom as _mp
        body = _mp.render(s_cfg=s_cfg, runners=runners)
        return Response(body, mimetype="text/plain; version=0.0.4")
    except Exception as e:
        return Response(f"# error: {e}\n", mimetype="text/plain"), 500

# ── v3.43.80 Phase 121: outgoing webhooks -> app_webhooks.py (Phase 4 cut 1, v3.66.405) ──
# ── v3.43.80 Phase 122: health checklist ──────────────────────────────
# api_health_checklist -> app_health.py (Phase 4 multi-block extraction)

# ── v3.43.80 Phase 127: batch operations ──────────────────────────────
# /api/batch -> app_batch.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 129: export helpers ────────────────────────────────
# /api/export -> app_export.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 130: cleanup helpers ───────────────────────────────
# /api/cleanup -> app_cleanup.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 118: gamification ──────────────────────────────────
# /api/gamification -> app_gamification.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 119: EOL export ────────────────────────────────────
# /api/eol -> app_eol.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 124: storage rebalance ─────────────────────────────
# /api/rebalance -> app_rebalance.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 125: cookie quality ────────────────────────────────
# api_cookie_quality -> app_cookie_quality.py (Phase 4 multi-block extraction)

# ── v3.62.2: per-site cookie export / import ───────────────────────────
# Export downloads the site's saved cookie JSON. Import accepts cookies
# three ways (pasted text, an absolute file path, or an uploaded file)
# and ALWAYS writes them into BD's own cookies dir
# (BD_HOME/cookies/<sid>.json), then points the site's cookie_file there
# so cookies live inside BD regardless of how they arrived.
def _bd_cookie_dir():
    _bd_home = Path(os.environ.get("BD_HOME") or ".").resolve()
    d = _bd_home / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d

# ── v3.66.144: reviewed-template visibility + manual onboarding ──────────
def _site_primary_url(cfg):
    """Resolve a site's primary URL from the usual config fields (mirrors
    tools/onboard_site_template.best_url_from_site)."""
    for key in ("login_url", "start_url", "base_url", "url",
                "homepage", "member_url", "site_url"):
        v = (cfg.get(key) or "").strip()
        if v.startswith(("http://", "https://")):
            return v
    return ""


# api_template_status -> app_sites.py (Phase 4 multi-block extraction)


# api_template_onboard -> app_sites.py (Phase 4 multi-block extraction)


# api_template_capture_cancel -> app_sites.py (Phase 4 multi-block extraction)


# api_candidates_inspect -> app_sites.py (Phase 4 multi-block extraction)


# api_template_dry_run -> app_sites.py (Phase 4 multi-block extraction)


# api_profile_seed -> app_sites.py (Phase 4 multi-block extraction)


# api_profile_status -> app_sites.py (Phase 4 multi-block extraction)


# /api/template_manager -> app_template_manager.py (Phase 4 thin-core-shell extraction)
# api_cookies_export -> app_sites.py (Phase 4 multi-block extraction)

# api_cookies_import -> app_sites.py (Phase 4 multi-block extraction)

# api_cookie_quality_all -> app_cookie_quality.py (Phase 4 multi-block extraction)

# ── v3.43.80 Phase 120: federation (multi-instance coordination) ──────
# /api/fed -> app_fed.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 123: backup verification ───────────────────────────
# api_backup_verify -> app_backup.py (Phase 4 multi-block extraction)

# api_backup_smoke -> app_backup.py (Phase 4 multi-block extraction)

# api_backup_drift -> app_backup.py (Phase 4 multi-block extraction)

# api_backup_history -> app_backup.py (Phase 4 multi-block extraction)

# ── v3.43.80 Phase 131: cookie relogin scheduler ──────────────────────
# /api/cookie_relogin -> app_cookie_relogin.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 132: stream relay ──────────────────────────────────
# api_stream_token -> app_stream.py (Phase 4 multi-block extraction)

@app.route("/stream/<token>")
def stream_serve(token):
    """Serve a video file with HTTP range support, gated by token."""
    from . import stream_relay as _sr
    v = _sr.verify_token(token)
    if not v.get("ok"):
        return Response(v.get("error", "denied"), status=403,
                        mimetype="text/plain")
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("SELECT filename FROM history WHERE id = ?",
                            (v["history_id"],)).fetchone()
        if not row:
            return Response("history row not found", status=404,
                            mimetype="text/plain")
        filename = row[0] if not hasattr(row, "keys") else row["filename"]
        if not filename:
            return Response("no file", status=404,
                            mimetype="text/plain")
        gen, headers, status = _sr.serve_range(
            filename, request.headers.get("Range", ""))
        if not gen:
            return Response("", status=status, headers=headers)
        return Response(stream_with_context(gen),
                        status=status, headers=headers)
    except Exception as e:
        return Response(f"error: {e}", status=500, mimetype="text/plain")

# ── v3.43.80 Phase 133: thumbnails / contact sheets ───────────────────
# /api/thumbs -> app_thumbs.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 135: RSS/sitemap discovery ─────────────────────────
# /api/discovery -> app_discovery.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 138: read-only share tokens ────────────────────────
# /api/shares -> app_shares.py (Phase 4 thin-core-shell extraction)
# ── v3.66.227 Phase F4.3: scoped API tokens (management) ───────────────
# These routes mint/list/revoke programmatic tokens that grant a SUBSET of
# API access by scope (read < enqueue < admin). Enforcement of what each
# token may reach lives in _check_token / _API_TOKEN_ROUTE_POLICY. The
# routes themselves are admin-only *for token-authenticated callers* (see
# the policy); an operator on a session cookie / master bearer reaches them
# directly. The full token value is returned exactly once, on create.
# /api/api_tokens -> app_api_tokens.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 145: command palette catalog ───────────────────────
# /api/palette -> app_palette.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 134: smart wakeup ──────────────────────────────────
# /api/wakeup -> app_wakeup.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 136: synthetic fixtures ────────────────────────────
# /api/fixtures -> app_fixtures.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 142: VPN profile stats ─────────────────────────────
# /api/vpn -> app_vpn.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 137: i18n ──────────────────────────────────────────
# /api/i18n/* (locales, load, template, save) -> app_i18n.py (Phase 4 thin-core-shell extraction)

# ── v3.43.80 Phase 144: cluster-wide rate limits ──────────────────────
# /api/cluster_rate -> app_cluster_rate.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 139: scheduled exports ─────────────────────────────
# /api/sched_exports -> app_sched_exports.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 140: user-applied tags ──────────────────────────────
# /api/tags -> app_tags.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 149: scene quality scoring ──────────────────────────
# /api/scene_score -> app_scene_score.py (Phase 4 thin-core-shell extraction)
# ── v3.43.91 Phase 171: thumbnail sheets (by-hid wrappers) ─────────────
# Note: /api/thumbs/* (Phase 133) takes {path}; these /api/thumbnail_sheets/*
# variants take a history_id and look up the path server-side.
# /api/thumbnail_sheets -> app_thumbnail_sheets.py (Phase 4 thin-core-shell extraction)
# ── v3.43.94 Phase 174: TPDB on-demand enrichment ──────────────────────
# /api/tpdb -> app_tpdb.py (Phase 4 thin-core-shell extraction)
# ── v3.43.93 Phase 173: per-site retention policies ────────────────────
# /api/retention -> app_retention.py (Phase 4 thin-core-shell extraction)
# ── v3.43.92 Phase 172: edge deploy helper ────────────────────────────
# /api/edge_deploy -> app_edge_deploy.py (Phase 4 thin-core-shell extraction)
# ── v3.43.90 Phase 170: storage rebalance ──────────────────────────────
# /api/storage_rebalance -> app_storage_rebalance.py (Phase 4 thin-core-shell extraction)
# ── v3.43.89 Phase 169: synthetic test runner ──────────────────────────
# /api/synthetic_tests -> app_synthetic_tests.py (Phase 4 thin-core-shell extraction)
# ── v3.43.88 Phase 168: diagnostics bundle on-demand ───────────────────
# /api/diagnostics_bundle -> app_diagnostics_bundle.py (Phase 4 thin-core-shell extraction)
# ── v3.43.86 Phase 166: stream-token bulk-revocation ──────────────────
# api_stream_rotate_secret -> app_stream.py (Phase 4 multi-block extraction)

# ── v3.43.84 Phase 164: scheduled exports manager ──────────────────────
# /api/scheduled_exports -> app_scheduled_exports.py (Phase 4 thin-core-shell extraction)
# ── Cut 8: recurring-capture schedules (first new write surface) ───────
# CRUD over capture_schedules + a force run_now. The recurring task
# enqueues via the EXISTING run path (SiteRunner.load_urls) -- the same
# seam discovery uses -- never via capture/extraction internals.
def _capture_enqueue(site_id, urls):
    """Inject seam for capture_schedules.run_*: append URL(s) to a site's
    runner queue via the existing run path. Returns count enqueued."""
    if site_id not in s_cfg or site_id not in runners:
        return 0
    # No explicit URLs -> fall back to the site's configured start URL(s).
    if not urls:
        cfg = (s_cfg or {}).get(site_id, {}) or {}
        start = cfg.get("start_url") or cfg.get("login_url")
        urls = [start] if start else []
    n = 0
    for url in (urls or [])[:1000]:  # safety cap, matches discovery
        try:
            runners[site_id].load_urls([url])  # type: ignore
            n += 1
        except Exception:
            pass
    return n

# /api/schedules -> app_schedules.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 148: site behavior changelog ────────────────────────
# /api/changelog -> app_changelog.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 146: alerts engine ──────────────────────────────────
# /api/alerts -> app_alerts.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 154: site weather ───────────────────────────────────
# /api/weather -> app_weather.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 156: bandwidth chart data ──────────────────────────
# /api/bw_chart -> app_bw_chart.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 151: keyboard shortcuts catalog ─────────────────────
# /api/shortcuts -> app_shortcuts.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 158: cookie clipboard helper ───────────────────────
# /api/cookie_clipboard -> app_cookie_clipboard.py (Phase 4 thin-core-shell extraction)
# ── v3.43.80 Phase 92: full-text search across history ────────────────
# SQLite FTS5 indexes url/filename/message/site_name on every history
# insert. Operators with 10k+ rows need to find scenes, performers, or
# error messages without scrolling. This endpoint accepts FTS5 MATCH
# syntax (bare terms AND-ed; quoted phrases; AND/OR/NOT) and returns
# results ranked by bm25 with HTML snippet highlights.
# api_search -> app_search.py (Phase 4 multi-block extraction)

# ── Phase 21.1: Dashboard aggregation ─────────────────────────────────
# Single endpoint that returns everything the health bar needs in one
# round-trip. Polled once per UI refresh; UI doesn't have to fan out N
# requests per site. The shape is intentionally flat — no per-site
# breakdown beyond what's needed for the bar — so it stays cheap even
# at 50+ sites.

# api_dashboard -> app_dashboard.py (Phase 4 multi-block extraction)

# ── Phase 21.4: Global event stream (across all sites) ────────────────
# Returns recent events from every site, merged + sorted by sequence.
# The UI polls this for the event-stream sidebar. Pagination via `after`
# is a (sid, seq) tuple-string so the client can resume cleanly.
# ── Phase 22.1/22.2 + v3.43.34: Server-Sent Events for real-time updates ──
# Replaces (or supplements) the polling loop. The browser subscribes via
# EventSource('/api/stream'). The server emits a `status` event whenever
# state changes meaningfully, and a `dashboard` event every 2.5s with the
# aggregated cross-site totals. Heartbeat comments every 15s keep proxies
# from timing out the connection. If a client can't connect (corporate
# proxy strips SSE, etc.), the frontend falls back to the existing 1.5s
# poll automatically — no breakage.
#
# Why SSE rather than WebSocket: we only send data server→client. SSE is
# native HTTP, works on the bundled Werkzeug server without an extra
# dependency, and the browser API (EventSource) auto-reconnects on its
# own. WebSocket would add a flask-sock or flask-socketio dependency for
# no meaningful benefit on a one-way feed.
#
# v3.43.34: switched from server-side polling (hash-compare every 1s) to
# real pub/sub. Server-side mutations push to a broker; the generator
# blocks on the broker's queue and forwards. Eliminates the 1s latency
# floor AND the hash-compare CPU. New event types: event_log,
# download_progress, queue_change.
# api_stream -> app_stream.py (Phase 4 multi-block extraction)


# /api/sse_status -> app_sse_status.py (Phase 4 thin-core-shell extraction)
# ── v3.43.37: retry policy inspection ──────────────────────────────
# /api/retry_policy -> app_retry_policy.py (Phase 4 thin-core-shell extraction)
# ── v3.43.35: account pool diagnostic + reset ──────────────────────
# api_account_pool_status -> app_sites.py (Phase 4 multi-block extraction)


# api_account_pool_reset -> app_sites.py (Phase 4 multi-block extraction)


# /api/account_pool -> app_account_pool.py (Phase 4 thin-core-shell extraction)
# ── v3.43.38: dashboard widgets diagnostic ─────────────────────────
# api_dashboard_widgets -> app_dashboard.py (Phase 4 multi-block extraction)


# ── D3 U2 (v3.64.0 prep): /api/*/v2 endpoints for the React SPA ────────
#
# These endpoints serve the /m2 SPA. They are SPA-shaped variants of
# existing v1 endpoints; the v1 endpoints stay unchanged so the
# existing /m and / UIs keep working untouched. Design notes:
#
#   - Read from the SAME source data as v1 (runners, s_cfg, history
#     table). No new state, no new locks, no new background work.
#   - Pure adapters. If U3+ needs a new computed field, add it here
#     rather than mutating runner / DB shapes.
#   - All return JSON, all have an `ok` field per the contract-test
#     convention, all 200/503 on a single rule (500s would be a bug).
#   - Deterministic where possible — `avatar_color` for a given site
#     name MUST be stable across requests or the SPA shows a flicker
#     on every refetch.

# 12-hue avatar palette — matches the mockup's per-site colored chips.
# Pure HSL endpoints, no theme dependency: same color appears in light
# and dark mode (saturation/lightness chosen to read on both).
_M2_AVATAR_HUES = (
    "#7c5cff", "#3a4cff", "#0ea5e9", "#10b981",
    "#f59e0b", "#ef4444", "#ec4899", "#8b5cf6",
    "#14b8a6", "#22c55e", "#eab308", "#f97316",
)


def _m2_avatar_color(name: str) -> str:
    """Deterministic name → color (one of 12 hues). Same input always
    yields same output; case-insensitive."""
    if not name:
        return _M2_AVATAR_HUES[0]
    # djb2-style hash, deterministic across Python versions (built-in
    # hash() is salted per-process, useless here).
    h = 5381
    for ch in name.lower():
        h = ((h * 33) + ord(ch)) & 0xFFFFFFFF
    return _M2_AVATAR_HUES[h % len(_M2_AVATAR_HUES)]


def _m2_site_drain_eta(pending_total, per_min):
    """F1.6: estimate seconds to drain one site's queue from that site's
    recent completion rate (jobs/min over the runner's ~5-min window — the
    same `_recent_per_min` the dashboard sums globally). Returns an int
    seconds estimate, or None when there's nothing pending or no rate yet
    (fail-soft: the SPA shows '—' rather than a bogus number). pending_total
    counts both waiting AND the in-flight job so the estimate reflects the
    whole site backlog, not just the queue tail."""
    try:
        pt = int(pending_total)
        rate = float(per_min)
    except (TypeError, ValueError):
        return None
    if pt <= 0 or rate <= 0:
        return None
    return int((pt / rate) * 60)


def _m2_auth_state(runner, cfg) -> str:
    """Bucket the runner's auth state into ok/expired/unknown.
    Reads cookie expiry info; doesn't probe (probing is the v1
    /api/sites/<sid>/health endpoint's job, much slower)."""
    try:
        from .cookies import cookies_expiry_info
        ei = cookies_expiry_info(runner.cookies or [])
        earliest = ei.get("earliest") or 0
        if earliest <= 0:
            return "unknown"
        import time as _t
        if earliest < _t.time():
            return "expired"
        return "ok"
    except Exception:
        return "unknown"


def _m2_attention_for_site(sid: str, runner, cfg) -> dict | None:
    """Return an attention-banner entry for a site, or None if it has
    no attention condition. Order of precedence:
      1. captcha_pending  (blocks downloads outright)
      2. login expired    (blocks the next login attempt)
      3. rate_limited     (transient; lowest priority)
    Site can only appear once in the attention list — the highest
    precedence condition wins."""
    import time as _t
    name = (cfg.get("name") or sid) if cfg else sid
    try:
        # Captcha pending — the runner exposes a flag set by the
        # captcha-aware login path.
        if getattr(runner, "_captcha_pending", False):
            return {
                "site_id": sid, "name": name,
                "kind": "captcha_pending",
                "label": "Captcha pending",
                "since_ts": getattr(runner, "_captcha_pending_ts", 0) or 0,
            }
    except Exception: pass
    # Login expired
    try:
        if _m2_auth_state(runner, cfg) == "expired":
            from .cookies import cookies_expiry_info
            ei = cookies_expiry_info(runner.cookies or [])
            expired_at = ei.get("earliest") or 0
            return {
                "site_id": sid, "name": name,
                "kind": "login_expired",
                "label": "Login expired",
                "since_ts": expired_at,
                "age_human": _m2_age_human(_t.time() - expired_at) if expired_at else "",
            }
    except Exception: pass
    # Rate limited
    try:
        if runner.is_rate_limited():
            until = getattr(runner, "_rl_until", 0) or 0
            return {
                "site_id": sid, "name": name,
                "kind": "rate_limited",
                "label": "Rate limited",
                "since_ts": 0,
                "until_ts": until,
            }
    except Exception: pass
    return None


def _m2_age_human(seconds: float) -> str:
    """Compact human age — '2h ago', '15m ago', '3d ago'. Empty if
    not positive. Pinned to whole units; the SPA never needs finer
    granularity for an attention banner."""
    if seconds is None or seconds <= 0:
        return ""
    s = int(seconds)
    if s < 60:   return f"{s}s ago"
    if s < 3600: return f"{s // 60}m ago"
    if s < 86400: return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


# api_dashboard_v2 -> app_dashboard.py (Phase 4 multi-block extraction)


# api_dashboard_v2_sparkline -> app_dashboard.py (Phase 4 multi-block extraction)


def _m2_honeypot_suggestion(sid):
    """Advisory per-site honeypot drop-threshold suggestion for the
    Sites UI (F3.4). Returns ``(suggested_float_or_None, sample_count)``.

    Surfacing only — this NEVER changes drop behaviour. The live filter is
    still the opt-in ``BD_HONEYPOT_SCORE_THRESHOLD`` path; this just lets
    the operator *see* what a learned per-site threshold would be. The
    suggestion is ``None`` until the site has >= ``DEFAULT_MIN_SAMPLES``
    confirmed-trap scores, so the chip stays hidden on thin data. Lazy
    import keeps cold-path cost at zero; fail-soft to ``(None, 0)`` so a
    missing history column can never break the Sites tab.
    """
    try:
        from . import honeypot_threshold as _hpt
        from .honeypot_score import DEFAULT_DROP_THRESHOLD as _dft
        scores = _hpt.trap_scores_for_site(sid)
        n = len(scores)
        if n < _hpt.DEFAULT_MIN_SAMPLES:
            return (None, n)
        return (_hpt.learn_threshold(scores, default=_dft), n)
    except Exception:
        return (None, 0)


# api_sites_v2 -> app_sites.py (Phase 4 multi-block extraction)


def _m2_activity_query_fragments(window_days, q):
    """Build (where_clauses, params) for activity_v2 + export endpoints.

    Centralised so the JSON endpoint and the CSV export endpoint can't
    drift on filter semantics. Returns a list of WHERE clauses (joined
    with AND by the caller) and the param list in matching order.

    Window filter uses `ts >= datetime('now', '-N days')`. The search
    filter (q) substring-matches against url/filename/message — same
    LIKE pattern as db_search. SQL is parameterised; the LIKE pattern
    string itself is built with %-wrapping, never interpolated.
    """
    where = []
    params = []
    if window_days is not None:
        where.append("ts >= datetime('now', ?)")
        params.append(f"-{window_days} days")
    if q:
        # Same shape as db.db_search. Cap query length so a pathological
        # 10MB string doesn't bog SQLite's LIKE.
        like = f"%{q[:200]}%"
        where.append("(url LIKE ? OR filename LIKE ? OR message LIKE ?)")
        params.extend([like, like, like])
    return where, params


# api_activity_v2 -> app_activity.py (Phase 4 multi-block extraction)


# api_activity_v2_export_csv -> app_activity.py (Phase 4 multi-block extraction)


# api_queue_v2 -> app_queue.py (Phase 4 multi-block extraction)


# api_health_v2 -> app_health.py (Phase 4 multi-block extraction)


# ── D3 U3: Resolve flow for the attention banner ────────────────────────
#
# The SPA's Home page renders an attention banner listing sites with
# active issues (captcha pending, login expired, rate limited). The
# Resolve button triggers the right corrective action for each kind:
#
#   captcha_pending → POST to /api/sites/<sid>/manual_login to open the
#                      Playwright window so the operator can solve it
#   login_expired   → POST to /api/sites/<sid>/login (re-run auto-login)
#   rate_limited    → no action — server-side cool-down, just visibility
#
# This endpoint is a thin dispatcher over the existing v1 endpoints so
# the SPA's Resolve button doesn't have to know which v1 route to call
# based on the issue kind. Body: {site_id, kind}. Returns {ok, action,
# detail} so the SPA can show a toast like "Login restart triggered".
#
# CSRF: this is a state-changing endpoint, so the existing global CSRF
# hook applies (registered in app.py initialization). The SPA gets its
# token from the bd_session cookie + meta-tag exposure that the /m2
# bootstrap will add in U7 (Settings); until then, the SPA can read
# it from /api/auth_surface (which already exists).
# api_dashboard_v2_resolve -> app_dashboard.py (Phase 4 multi-block extraction)


# ── D3 U5: Queue tab endpoints — cancel + job log ───────────────────────
#
# /api/queue/v2/cancel: cancel one URL within a site. Mirrors the
# existing /api/sites/<sid>/retry_one pattern but in the opposite
# direction — move pending/running URL to stopped.
#
# /api/queue/v2/job_log: return the last N event-log entries scoped
# to one URL. Backs the Queue tab's "click row → error modal" flow.
# The runner already keeps a 500-entry rolling event log per site;
# we filter to the target URL and cap the result.

# api_queue_v2_cancel -> app_queue.py (Phase 4 multi-block extraction)


# api_queue_v2_job_log -> app_queue.py (Phase 4 multi-block extraction)


# v3.64.x D3 follow-up U5 — log-diff side-by-side.
# Returns two job logs in one call, scoped by ?a=<site_id>:<url> +
# ?b=<site_id>:<url>. Both logs share the same shape as
# /api/queue/v2/job_log (events + current). A pre-computed unified
# diff is also returned so the SPA doesn't have to ship a diff
# library — the diff is between the events' message texts, line by
# line, with the standard difflib unified_diff context format.
#
# Both jobs are looked up the same way as single job_log: the
# runner's _event_log filtered to the URL. If either side resolves
# to no events, it's still a valid response — empty lists, empty diff.

def _diff_parse_target(spec: str) -> tuple[str, str] | None:
    """Parse a colon-separated 'site_id:url' from the query string.
    Returns (site_id, url) or None if malformed. URLs may contain
    colons themselves (http://, ports), so we split on the FIRST
    colon only — the site_id can't contain one (validated at site
    creation time by site_editor.validate_config)."""
    if not spec or ":" not in spec:
        return None
    ix = spec.index(":")
    sid = spec[:ix].strip()
    url = spec[ix + 1:].strip()
    if not sid or not url:
        return None
    return sid, url


def _diff_collect_one(spec: str, limit: int) -> dict:
    """Resolve one diff side. Returns a dict with keys site_id, url,
    events, current, ok, error. The ok/error pair mirrors how the
    single job_log endpoint communicates 'unknown site' without 500."""
    parsed = _diff_parse_target(spec)
    if parsed is None:
        return {
            "ok": False, "error": "malformed target (expected site_id:url)",
            "events": [], "current": None, "site_id": "", "url": "",
        }
    sid, url = parsed
    if sid not in runners:
        return {
            "ok": False, "error": "unknown site_id",
            "events": [], "current": None, "site_id": sid, "url": url,
        }
    runner = runners[sid]
    ev_log = list(getattr(runner, "_event_log", None) or [])
    matched = [ev for ev in ev_log if ev.get("url") == url]
    matched = matched[-limit:]
    events = [
        {
            "ts": ev.get("ts", 0),
            "kind": ev.get("kind", ""),
            "message": ev.get("message", "")[:500],
        }
        for ev in matched
    ]
    with runner._lock:
        job = runner.jobs.get(url) or {}
        current = {
            "status": job.get("status", ""),
            "message": (job.get("message") or "")[:500],
            "filename": job.get("filename", ""),
        }
    return {
        "ok": True, "error": None,
        "site_id": sid, "url": url,
        "events": events, "current": current,
    }


def _diff_lines_for(events: list[dict]) -> list[str]:
    """Render an events list as one string per event, formatted
    'KIND: message'. Used as the input to difflib.unified_diff."""
    out = []
    for ev in events:
        kind = (ev.get("kind") or "")[:32]
        msg = (ev.get("message") or "")[:500]
        out.append(f"{kind}: {msg}")
    return out


# api_queue_v2_job_log_diff -> app_queue.py (Phase 4 multi-block extraction)


# v3.64.x D3 follow-up U6 — site-health sparkline data per Activity row.
#
# Returns per-site daily completion counts over a window (default 14
# days). One windowed query against `history` — NOT N+1 per site —
# grouped on the SQL side by (site_id, day). Python then fills missing
# days with 0 so the SPA gets evenly-spaced points for its sparkline.
#
# Status filter: by default counts all terminal statuses (done, failed,
# stopped, needs_review) — the sparkline communicates "this site was
# active". Operator can pass ?status=done to count only successes.
#
# Why a separate endpoint and not inline in /api/activity/v2: the
# Activity payload returns N rows (per-file); the sparkline data is
# per-site aggregated. Joining the two would be N+1 query risk; a
# dedicated endpoint keeps the cost bounded.

# api_activity_v2_site_health -> app_activity.py (Phase 4 multi-block extraction)


# v3.64.x D3 follow-up — bulk select on Sites + Queue (Tier-3 #
# "Bulk select on Sites + Queue", undeferred by operator request).
#
# Two endpoints, both CSRF-gated, both partial-failure-tolerant:
#   POST /api/sites/v2/bulk  body: {action, site_ids}
#   POST /api/queue/v2/bulk_cancel  body: {jobs: [{site_id, url}, ...]}
#
# Allow-list of site actions is hardcoded (no getattr on raw input).
# Both return aggregate {ok, applied/cancelled, total, errors: [...]}.
# Per-item failures DO NOT raise — they're collected and reported, the
# rest of the batch still runs (same shape as the existing
# /api/pause_all aggregate). Empty input is a 400; an unknown action
# is a 400; an unknown site_id is logged in errors but not fatal.

_SITES_BULK_ACTIONS = ("pause", "resume", "start", "delete")

# api_sites_v2_bulk -> app_sites.py (Phase 4 multi-block extraction)


# api_queue_v2_bulk_cancel -> app_queue.py (Phase 4 multi-block extraction)


# api_queue_v2_add_url -> app_queue.py (Phase 4 multi-block extraction)


# /api/bulk -> app_bulk.py (Phase 4 thin-core-shell extraction)
def _status_snapshot(light=True):
    """Build the same dict shape that /api/status would return. Extracted
    here so the SSE generator can call it without re-entering the request
    context."""
    import time as _t, shutil as _shutil
    out = {}
    if not hasattr(api_status, "_disk_cache"):
        api_status._disk_cache = {}
    now_t = _t.time()
    for sid, runner in runners.items():
        try:
            st = runner.get_status(light=light)
        except Exception:
            continue
        meta = s_meta.get(sid) or {}
        st["name"] = meta.get("name", sid)
        st["config"] = meta
        dl_dir = (s_cfg.get(sid) or {}).get("download_dir") or ""
        if dl_dir:
            cache = api_status._disk_cache.get(dl_dir)
            if cache and (now_t - cache[0]) < 5:
                st["disk_free_gb"] = cache[1]
            else:
                try:
                    free_gb = round(_shutil.disk_usage(dl_dir).free / (1024**3), 2)
                    st["disk_free_gb"] = free_gb
                    api_status._disk_cache[dl_dir] = (now_t, free_gb)
                except Exception: pass
        out[sid] = st
    return out


def _dashboard_snapshot():
    """Build the same dict that /api/dashboard returns."""
    import time as _t
    from .cookies import cookies_expiry_info
    totals = {"running":0, "pending":0, "done":0, "failed":0,
              "needs_review":0, "stopped":0}
    active_workers = 0
    today_done = today_failed = today_review = 0
    today_iso = _t.strftime("%Y-%m-%d")
    expiring_cookies_sites = []
    rate_limited_sites = []
    low_disk_sites = []
    disk_aggregate = []
    for sid, runner in runners.items():
        if not runner: continue
        cfg = s_cfg.get(sid, {}) or {}
        st_state = runner.state()
        try:
            with runner._lock:
                for url, j in runner.jobs.items():
                    s = j.get("status","")
                    if s in totals: totals[s] += 1
                    ts = j.get("ts","") or ""
                    if ts.startswith(today_iso):
                        if s == "done": today_done += 1
                        elif s == "failed": today_failed += 1
                        elif s == "needs_review": today_review += 1
                active_workers += 1 if st_state == "running" else 0
        except Exception: continue
        dl_dir = cfg.get("download_dir","") or ""
        if dl_dir:
            try:
                import shutil as _shutil
                u = _shutil.disk_usage(dl_dir)
                disk_aggregate.append({
                    "site": cfg.get("name","") or sid, "path": dl_dir,
                    "free_gb": round(u.free/(1024**3), 2),
                    "total_gb": round(u.total/(1024**3), 2),
                    "free_pct": round((u.free/u.total)*100, 1),
                })
            except Exception: pass
        try:
            ei = cookies_expiry_info(runner.cookies or [])
            earliest = ei.get("earliest")
            if earliest:
                hours = (earliest - _t.time()) / 3600.0
                if 0 < hours < 1:
                    expiring_cookies_sites.append({
                        "site_id": sid, "name": cfg.get("name","") or sid,
                        "expires_in_minutes": round(hours*60, 1),
                    })
        except Exception: pass
        try:
            if runner.is_rate_limited():
                rate_limited_sites.append({"site_id": sid,
                    "name": cfg.get("name","") or sid,
                    "until": getattr(runner, "_rl_until", 0) or 0})
        except Exception: pass
        if st_state == "low_disk":
            low_disk_sites.append({"site_id": sid,
                "name": cfg.get("name","") or sid})
    pending_total = totals["pending"] + totals["running"]
    eta_seconds = None
    recent_per_min = 0.0
    for runner in runners.values():
        try: recent_per_min += float(getattr(runner, "_recent_per_min", 0) or 0)
        except Exception: pass
    if pending_total > 0 and recent_per_min > 0:
        eta_seconds = int((pending_total / recent_per_min) * 60)
    # v3.43.38: dashboard widgets — rolling rate, success %, active
    # workers, ETA. These replace the legacy throughput_bps=0 stub
    # and the recent-completion-rate-based eta_seconds (which only
    # worked once jobs were finishing). The widget snapshot is
    # bounded-memory and shortcuts return safe defaults on failure.
    try:
        from bulk_downloader import dashboard_widgets as _dw
        widgets = _dw.snapshot(runners_dict=runners, s_cfg=s_cfg)
    except Exception:
        widgets = {"bytes_per_sec": 0, "success_pct": None,
                    "success_sample_size": 0, "active_workers": 0,
                    "remaining_bytes": 0, "eta_seconds": None}
    # Prefer the widget's ETA when it has data (current download
    # rate * remaining bytes is more responsive than completion-
    # rate based estimation). Fall back to the legacy eta when the
    # rate window is empty (just started, no current downloads).
    widget_eta = widgets.get("eta_seconds")
    if widget_eta is not None:
        eta_seconds = widget_eta
    # Prefer the widget's active-workers count (it requires alive
    # worker threads, not just a 'running' state flag)
    widget_active = widgets.get("active_workers", 0)
    if widget_active > 0:
        active_workers = widget_active
    return {
        "ok": True, "totals": totals,
        "active_workers": active_workers,
        "today": {"done": today_done, "failed": today_failed,
                  "needs_review": today_review},
        "throughput_bps": widgets.get("bytes_per_sec", 0),
        "success_pct": widgets.get("success_pct"),
        "success_sample_size": widgets.get("success_sample_size", 0),
        "remaining_bytes": widgets.get("remaining_bytes", 0),
        "eta_seconds": eta_seconds,
        "disk": disk_aggregate,
        "expiring_cookies": expiring_cookies_sites,
        "rate_limited": rate_limited_sites,
        "low_disk": low_disk_sites,
    }



# /api/events_all -> app_events_all.py (Phase 4 thin-core-shell extraction)
# ── Phase 31: meta scrub helper. The `meta` dict for a site is what
# /api/status returns; it must never expose passwords or per-account
# credentials. Centralized here so add + update paths can both call it.



# ── Phase 26.6: Path-traversal validation ─────────────────────────────
# Reject path values that:
#   - aren't absolute, OR
#   - contain `..` segments, OR
#   - resolve outside the configured allowlist (when one is set)
#
# Applied to `download_dir`, `cookie_file`, and any spillover_dirs.
# Allowlist comes from app_config.json:
#   "path_allowlist": ["/data/downloads", "C:\\BulkDownloader\\data"]
# Empty allowlist (default) = legacy behavior: any absolute, non-traversing
# path is accepted. With an allowlist, every path must be a descendant
# of one of the listed roots — the only way to be truly safe.
def _validate_path(path, field_label: str = "path"):
    """Returns (ok: bool, normalized_path_or_error_message: str)."""
    import os as _os
    if not path:
        return True, path  # empty is allowed — means "use default"
    if not isinstance(path, str):
        return False, f"{field_label} must be a string"
    p = path.strip()
    if not p: return True, p
    # No `..` segments
    parts = re.split(r"[\\/]+", p)
    if ".." in parts:
        return False, f"{field_label} cannot contain '..' segments"
    # Must be absolute. On POSIX that means starts with /; on Windows,
    # drive-letter or UNC. We accept both regardless of host OS so
    # cross-platform sites_config.json imports work.
    is_abs = (p.startswith("/")
              or p.startswith("\\\\")
              or (len(p) >= 2 and p[1] == ":")
              or _os.path.isabs(p))
    if not is_abs:
        return False, f"{field_label} must be an absolute path (got '{p[:60]}')"
    # Allowlist check (when configured)
    allowlist = _app_cfg.get("path_allowlist") or []
    if allowlist:
        try:
            real = _os.path.realpath(p)
        except Exception:
            real = p
        ok = False
        for root in allowlist:
            try:
                root_real = _os.path.realpath(root)
            except Exception:
                root_real = root
            try:
                if _os.path.commonpath([real, root_real]) == root_real:
                    ok = True; break
            except ValueError:
                continue  # different drives on Windows
        if not ok:
            return False, (f"{field_label} '{p[:60]}' is not under any configured "
                           f"allowlist root: {', '.join(allowlist)}")
    return True, p

def _reveal_safe_roots():
    """F-APP06-01: the effective allowlist for the reveal action -- the
    configured path_allowlist if set, else BD's default download roots (BD_HOME,
    ~/Downloads/bulk_downloader, and any configured per-site / global
    download_dir). Never empty, so reveal cannot open an arbitrary absolute path
    when the global allowlist is the legacy-permissive empty default."""
    import os as _os
    allow = _app_cfg.get("path_allowlist") or []
    if allow:
        return list(allow)
    roots = []
    try:
        roots.append(str(Path(_os.environ.get("BD_HOME") or Path.cwd()).resolve()))
    except Exception:
        pass
    try:
        roots.append(str((Path.home() / "Downloads" / "bulk_downloader").resolve()))
    except Exception:
        pass
    try:
        for _cfg in (s_cfg.values() if isinstance(s_cfg, dict) else []):
            dd = (_cfg or {}).get("download_dir")
            if dd:
                roots.append(str(Path(dd).resolve()))
    except Exception:
        pass
    try:
        gd = _oi_default_download_dir()
        if gd:
            roots.append(str(Path(gd).resolve()))
    except Exception:
        pass
    return list(dict.fromkeys(r for r in roots if r))


def _validate_reveal_path(path, field_label: str = "path"):
    """F-APP06-01: reveal-scoped path check. Runs the standard _validate_path
    (absolute, no '..'), then REQUIRES the resolved path be within a reveal-safe
    root so an empty (legacy-permissive) path_allowlist cannot let the reveal
    action open an arbitrary absolute path in the host file manager."""
    import os as _os
    ok, msg = _validate_path(path, field_label)
    if not ok:
        return ok, msg
    if not msg:
        return False, f"{field_label} required"
    try:
        real = _os.path.realpath(msg)
    except Exception:
        real = msg
    for root in _reveal_safe_roots():
        try:
            root_real = _os.path.realpath(root)
            if _os.path.commonpath([real, root_real]) == root_real:
                return True, msg
        except Exception:
            continue
    return False, (f"{field_label} is outside the allowed download/reveal roots; "
                   "add it to path_allowlist in Settings -> Global to reveal it")

def _validate_config_paths(cfg: dict):
    """Run _validate_path on every path-bearing field. Returns
    (ok: bool, error_string: str). On failure, the caller should refuse the
    config write with a 400."""
    path_fields = [
        ("download_dir", cfg.get("download_dir")),
        ("cookie_file",  cfg.get("cookie_file")),
    ]
    for label, val in path_fields:
        ok, msg = _validate_path(val, label)
        if not ok: return False, msg
    # spillover_dirs is multi-line
    for i, line in enumerate((cfg.get("spillover_dirs") or "").splitlines()):
        line = line.strip()
        if not line: continue
        ok, msg = _validate_path(line, f"spillover_dirs line {i+1}")
        if not ok: return False, msg
    return True, ""


# ── v3.47.8 (#81): unicode normalization for display fields ─────────────
# The site `name` is shown in the UI, used in filenames, written to logs,
# and emitted as JSON. Accepting raw unicode means accepting:
#   - U+202E RIGHT-TO-LEFT OVERRIDE  ('trojan source' / filename spoofing)
#   - U+FEFF ZERO WIDTH NO-BREAK SPACE (BOM)  (invisible, breaks searching)
#   - U+0000..U+001F C0 control chars         (terminal injection in logs)
#   - Other Cc (control) and Cf (format) characters
# Closes the DAST defensive note flagged in v3.47.7 audit. NFKC additionally
# folds visually-confusable variants (full-width digits → ASCII digits etc.)
# so "Ｓｉｔｅ１" and "Site1" hash to the same thing.
#
# Applies ONLY to `name`. Credentials (password, tokens) and structural
# fields (URLs, CSS selectors) are byte-preserved — normalization there
# would silently corrupt valid secrets.
def _sanitize_display_name(value):
    """Normalize a user-facing display string. Returns the cleaned value.
    Returns the input unchanged if it isn't a string."""
    if not isinstance(value, str):
        return value
    import unicodedata
    # NFKC: canonical decomposition + compatibility composition. Folds
    # half-width/full-width forms, ligatures, etc.
    normalized = unicodedata.normalize("NFKC", value)
    # Strip Cc (control) and Cf (format). The latter includes RLO/LRO/PDF
    # (bidi overrides), ZWNJ/ZWJ (zero-width joiners), BOM, and language
    # tag chars — all of which are display-invisible and either useless or
    # actively dangerous in a display field.
    cleaned = "".join(
        ch for ch in normalized
        if unicodedata.category(ch) not in ("Cc", "Cf")
    )
    return cleaned.strip()


def _create_site(data, actor="api"):
    """Create one site from a config dict. Returns (sid, error).

    Shared by POST /api/sites and the CSV bulk importer so both go
    through identical path-validation, string-coercion, default-fill,
    fingerprint, persistence and audit. On failure returns (None, msg).

    v3.65.2: auto-applies matching login + download templates when
    nothing has been user-supplied yet. The result is stashed as a
    transient `_autopick` field on s_cfg[sid] for the caller to
    consume; it's stripped before persistence so it never reaches
    sites_config.json. The 2-tuple return signature is preserved so
    existing callers continue to work."""
    data = dict(data or {})
    # v3.47.8 (#81): normalize the display name before any other handling.
    if "name" in data:
        data["name"] = _sanitize_display_name(data["name"])
    # Phase 26.6: validate paths BEFORE creating anything so a bad
    # request doesn't leave half-initialized state on disk
    ok, err = _validate_config_paths(data)
    if not ok:
        return None, err
    # Preflight-discovered bug: non-string names (int, None, list, dict, bool)
    # corrupted s_cfg and crashed config import later. Coerce string fields
    # to strings; reject obviously-bad collection types.
    for str_field in ("name","login_url","username","password","cookie_file",
                      "download_dir","trigger_selector","dl_selector",
                      "user_field","pass_field","submit_btn","success_url",
                      "filename_template","sched_time","min_resolution"):
        if str_field in data:
            v = data[str_field]
            if v is None: data[str_field] = ""
            elif isinstance(v, (list, dict)):
                return None, f"{str_field} must be a string"
            elif not isinstance(v, str): data[str_field] = str(v)
    sid=uuid.uuid4().hex[:8]
    cfg={k:data.get(k,"") for k in CFG_FIELDS}
    cfg.setdefault("name",f"Site {len(runners)+1}")
    # Apply defaults only when value is genuinely unset — preserve explicit
    # False / 0 / 0.0 so users can disable disk checks etc.
    for k,d in DEFAULTS.items():
        if cfg.get(k) in ("",None): cfg[k]=d
    # Phase 7.1: auto-generate a randomized fingerprint at site creation.
    # User can rotate later via /api/sites/<sid>/randomize_fingerprint.
    from .constants import make_fingerprint
    cfg["fingerprint"]=make_fingerprint()
    s_cfg[sid]=cfg; s_meta[sid]=_build_meta(cfg)
    runners[sid]=SiteRunner(sid,cfg)
    if cfg.get("cookie_file") and Path(cfg["cookie_file"]).exists():
        runners[sid].set_cookies_from_file(cfg["cookie_file"])
    # v3.65.2: best-effort auto-apply of matching login + download
    # templates. Only applies when the user hasn't already supplied
    # selectors/templates — so power users importing detailed configs
    # don't get clobbered. The picked template ids are persisted via
    # _save_sites_config inside the apply helpers, so this runs BEFORE
    # the outer _save_sites_config call. Failures are swallowed
    # internally — site creation never depends on auto-pick succeeding.
    autopick = _auto_pick_templates(sid, cfg)
    # Refresh cfg snapshot in case auto-pick mutated it via the apply
    # helpers (which write back to s_cfg).
    cfg = s_cfg.get(sid, cfg)
    # Stash the autopick result on cfg as a transient field. Callers
    # that want to surface it (api_add response, CSV import results)
    # can read s_cfg[sid]["_autopick"] right after _create_site
    # returns. The field is stripped before _save_sites_config so it
    # never leaks into sites_config.json.
    if autopick:
        cfg["_autopick"] = autopick
        s_cfg[sid] = cfg
    _save_sites_config()
    # v3.48 (#25): audit the create. before=None, after=cfg (redacted).
    try:
        from . import audit as _audit
        _audit.audit_log(
            source="api", action="create",
            target=f"sites_config:{sid}",
            before=None, after=cfg, actor=actor)
    except Exception:
        pass  # audit failures must never block the actual create
    return sid, None


def _apply_template_by_id(sid, tpl_id):
    """Merge a template's learned block + config_defaults into a site,
    non-destructively (same merge the teach-commit and /templates/apply
    route use). Shared by the CSV bulk importer. Returns (ok, message)."""
    if sid not in runners:
        return False, "site not found"
    from . import templates as _tpls
    from .learn import merge_learned
    tpl = _tpls.get(tpl_id)
    if not tpl:
        return False, f"unknown template: {tpl_id}"
    cfg = s_cfg.get(sid, {})
    download = (tpl.get("learned") or {}).get("download") or {}
    if download:
        merge_learned(cfg, download, kind="download")
    for key, val in (tpl.get("config_defaults") or {}).items():
        if cfg.get(key) in (None, "", 0, 0.0) or key not in cfg:
            cfg[key] = val
    # v3.62.2: record that a template was applied. The runner's
    # auto-teach preflight treats a templated site as ready-to-run and
    # skips the first-run teach prompt — teach only kicks in if the
    # template's selectors actually fail at download time.
    cfg["applied_template"] = tpl_id
    s_cfg[sid] = cfg
    runners[sid].update_config(cfg)
    _save_sites_config()
    return True, tpl.get("name", tpl_id)


def _apply_login_template_by_id(sid, login_tpl_id):
    """Merge a LOGIN template's selectors into a site's learned.login.

    The login-template counterpart of _apply_template_by_id. Writes the
    template's user/pass/submit selectors into the site's learned.login
    block; the runner's login auto-teach preflight then treats the site
    as ready-to-log-in and skips the first-run manual-login teach
    (login still falls back to manual capture if it fails at runtime).
    Returns (ok, message)."""
    if sid not in runners:
        return False, "site not found"
    from . import login_templates_data as _lt
    from .learn import merge_learned
    tpl = _lt.get_login_template(login_tpl_id)
    if not tpl:
        return False, f"unknown login template: {login_tpl_id}"
    cfg = s_cfg.get(sid, {})
    login = tpl.get("login") or {}
    if any(login.get(k) for k in ("user_field", "pass_field",
                                  "submit_btn")):
        merge_learned(cfg, login, kind="login")
    cfg["applied_login_template"] = login_tpl_id
    s_cfg[sid] = cfg
    runners[sid].update_config(cfg)
    _save_sites_config()
    return True, tpl.get("name", login_tpl_id)


def _apply_detected_selectors(sid, login_block=None, download_block=None):
    """v3.66.0: merge selectors discovered by auto_detect.detect_site_config
    into a site's learned config, without an associated template id.

    Mirrors _apply_template_by_id / _apply_login_template_by_id but skips
    the registry lookup and records `applied_template = '<auto-detect>'`
    so the UI can show the user where the selectors came from. Returns
    (ok, message)."""
    if sid not in runners:
        return False, "site not found"
    from .learn import merge_learned
    cfg = s_cfg.get(sid, {})
    touched = []
    # F1/CAP-1: record-time auto-narrow. A fresh detection emits a spray of
    # candidate selectors with no runtime hit/miss data yet; narrow to a
    # minimal, stable set before merge so the site starts lean. Default-on;
    # opt out per-site with cfg["record_time_narrow"] = False. Non-destructive
    # (never empties a role); the dropped candidates are recorded under
    # cfg["_record_time_narrow"] for audit/reversibility. (app -> auto_detect
    # import edge already exists — see detect_site_config callers below.)
    _narrow_on = cfg.get("record_time_narrow", True)
    if _narrow_on:
        from . import auto_detect as _ad
    if download_block and isinstance(download_block, dict):
        if download_block.get("row_selectors"):
            if _narrow_on:
                download_block, _rep = _ad.narrow_detected_block(download_block)
                if _rep:
                    cfg.setdefault("_record_time_narrow", {})["download"] = _rep
            merge_learned(cfg, download_block, kind="download")
            cfg["applied_template"] = "<auto-detect>"
            touched.append("download")
    if login_block and isinstance(login_block, dict):
        if any(login_block.get(k) for k in ("user_field", "pass_field",
                                            "submit_btn")):
            if _narrow_on:
                login_block, _rep = _ad.narrow_detected_block(login_block)
                if _rep:
                    cfg.setdefault("_record_time_narrow", {})["login"] = _rep
            merge_learned(cfg, login_block, kind="login")
            cfg["applied_login_template"] = "<auto-detect>"
            touched.append("login")
    if not touched:
        return False, "nothing to apply"
    s_cfg[sid] = cfg
    runners[sid].update_config(cfg)
    _save_sites_config()
    return True, f"auto-detect: applied {'+'.join(touched)}"


def _auto_pick_templates(sid, cfg):
    """v3.65.2: Automatically apply matching login + download templates
    when creating a new site whose URL hostname matches a known entry.

    Conservative by design — skips when the user has already brought
    their own data:

      - Skips download template if cfg already has `applied_template`,
        `dl_selector`, `trigger_selector`, or non-empty
        `learned.download.row_selectors`.
      - Skips login template if cfg already has `applied_login_template`,
        non-empty user_field/pass_field/submit_btn, or non-empty
        `learned.login.user_field`.

    The auto-pick uses the TOP suggestion only. If multiple templates
    match the URL, the user still sees the others in the picker — we
    just commit to the most-relevant one immediately so the first
    download attempt has selectors to work with. Auto-pick is a
    QUALITY-OF-LIFE feature, not authoritative; the user can re-apply
    a different template or clear the auto-pick afterward.

    Returns a dict with what got applied (for caller logging/UI):
      {
        "download_template_applied": <id or None>,
        "download_template_name":    <human name or None>,
        "login_template_applied":    <id or None>,
        "login_template_name":       <human name or None>,
      }
    """
    result = {
        "download_template_applied": None,
        "download_template_name": None,
        "login_template_applied": None,
        "login_template_name": None,
    }
    if sid not in runners:
        return result

    # Build the URL pool to query against. Prefer login_url for both
    # template stores — that's the most reliable hostname signal at
    # site-creation time. start_url and any user-supplied seed URLs
    # are additional candidates if present.
    url_pool = []
    for k in ("login_url", "start_url", "success_url"):
        v = (cfg.get(k) or "").strip()
        if v and v not in url_pool:
            url_pool.append(v)
    if not url_pool:
        return result

    # v3.66.0: dispatch on the global mode flag. Three modes:
    #   "static"             — legacy hostname lookup in the registries
    #   "static"             — original behavior: registry lookup only
    #                          (default; behavior unchanged from v3.65.2).
    #   "detect"             — only the auto-detector; if it can't find
    #                          selectors the site is left unconfigured
    #                          and the user falls through to teach.
    #   "detect_then_static" — try the detector; on failure fall back to
    #                          the registry lookup so a known site is
    #                          still covered even when the page won't
    #                          load (e.g. behind a paywall).
    #   "deep"               — v3.66.5: run deep_detect against a live
    #                          page, then translate its output through
    #                          deep_detect.to_site_config_block. Picks
    #                          up resolution cards, multi-quality
    #                          download buttons, and richer login
    #                          classification (passwordless, MFA, SSO).
    #                          Falls back to the static registry on
    #                          failure, same as detect_then_static.
    mode = (_app_cfg.get("template_auto_detect_mode") or "static").strip().lower()
    if mode not in ("static", "detect", "detect_then_static", "deep"):
        mode = "static"

    if mode in ("detect", "detect_then_static", "deep"):
        try:
            detected = None
            if mode == "deep":
                # v3.66.5: drive deep_detect against a live page.
                # We try Playwright the same way auto_detect does;
                # on success we pass the rendered HTML to deep_detect.
                from . import deep_detect as _dd
                from . import auto_detect as _ad
                rendered_html = None
                base_url_used = None
                for u in url_pool:
                    snapped = _ad.snapshot_via_playwright(u)
                    if snapped and snapped.get("html"):
                        rendered_html = snapped["html"]
                        base_url_used = snapped.get("final_url") or u
                        break
                if rendered_html:
                    report = _dd.deep_detect(
                        rendered_html, base_url=base_url_used)
                    adapted = _dd.to_site_config_block(report)
                    if adapted.get("ok"):
                        detected = adapted
            else:
                from . import auto_detect as _ad
                # We try detection on each URL in the pool, stopping at the
                # first one that produces *anything* (login or download).
                # If neither yields useful selectors and the mode is
                # detect_then_static, the static path below picks up the
                # slack on later URLs.
                for u in url_pool:
                    r = _ad.detect_site_config(url=u, login_url=u)
                    if r.get("ok"):
                        detected = r
                        break
            if detected:
                learned = detected.get("learned") or {}
                ok, msg = _apply_detected_selectors(
                    sid,
                    login_block=learned.get("login"),
                    download_block=learned.get("download"),
                )
                if ok:
                    auto_tag = ("<deep-detect>" if mode == "deep"
                                else "<auto-detect>")
                    if learned.get("download"):
                        result["download_template_applied"] = auto_tag
                        result["download_template_name"] = msg
                    if learned.get("login"):
                        result["login_template_applied"] = auto_tag
                        result["login_template_name"] = msg
                    # Refresh cfg for the static-fallback decision below
                    cfg = s_cfg.get(sid, cfg)
        except Exception as e:
            import sys as _sys
            _sys.stderr.write(
                f"  auto_pick_templates: auto-detect failed: {e}\n")

    # Static lookup path. Runs unconditionally in "static" mode; runs
    # only to fill the GAPS the detector left in "detect_then_static"
    # mode; never runs in pure "detect" mode.
    if mode == "detect":
        return result

    # ── Download template ──────────────────────────────────────────
    learned_dl = (cfg.get("learned") or {}).get("download") or {}
    already_dl = bool(
        cfg.get("applied_template")
        or (cfg.get("dl_selector") or "").strip()
        or (cfg.get("trigger_selector") or "").strip()
        or learned_dl.get("row_selectors")
        or learned_dl.get("trigger_selectors")
    )
    if not already_dl:
        try:
            from . import templates as _tpls
            for u in url_pool:
                suggestions = _tpls.suggest_for_url(u)
                if suggestions:
                    top = suggestions[0]
                    ok, msg = _apply_template_by_id(sid, top)
                    if ok:
                        result["download_template_applied"] = top
                        result["download_template_name"] = msg
                    break
        except Exception as e:
            # Auto-pick is best-effort — never block site creation.
            import sys as _sys
            _sys.stderr.write(
                f"  auto_pick_templates: download lookup failed: {e}\n")

    # ── Login template ─────────────────────────────────────────────
    # Refresh cfg from s_cfg in case the download apply above mutated it
    cfg = s_cfg.get(sid, cfg)
    learned_lg = (cfg.get("learned") or {}).get("login") or {}
    already_lg = bool(
        cfg.get("applied_login_template")
        or (cfg.get("user_field") or "").strip()
        or (cfg.get("pass_field") or "").strip()
        or (cfg.get("submit_btn") or "").strip()
        or learned_lg.get("user_field")
        or learned_lg.get("pass_field")
        or learned_lg.get("submit_btn")
    )
    if not already_lg:
        try:
            from . import login_templates_data as _lt
            for u in url_pool:
                suggestions = _lt.suggest_login_for_url(u)
                if suggestions:
                    top = suggestions[0]
                    ok, msg = _apply_login_template_by_id(sid, top)
                    if ok:
                        result["login_template_applied"] = top
                        result["login_template_name"] = msg
                    break
        except Exception as e:
            import sys as _sys
            _sys.stderr.write(
                f"  auto_pick_templates: login lookup failed: {e}\n")

    return result


def _vault_guard_for_password():
    """v3.66.326: gate storing a site login password in the secrets vault.

    A typed password must never be persisted as plaintext in
    sites_config.json — it goes to the encrypted secrets backend as a
    ``@cred:`` reference (resolved at login by ``resolve_password``). This
    mirrors the contract ``/api/captures/setup_site`` already enforces:

      - plaintext backend  -> (False, 400, {secrets_plaintext}) : there is no
        encrypted store to put it in; the operator must switch to an encrypted
        backend (Settings -> Secrets) or create the site without a password.
      - locked encrypted   -> (False, 401, {secrets_locked})    : the SPA
        catches this and shows the unlock prompt, then retries the save.
      - unlocked encrypted -> (True, None, None)

    Returns (allowed, status, errbody). Callers create/mutate the site
    regardless and use this only to decide whether the CREDENTIAL can be
    vaulted; on (False, ...) they skip storing the password (never plaintext)
    and surface the flag so the SPA can prompt to unlock.
    """
    from . import secrets_store as ss
    backend = ss.get_backend()
    if getattr(backend, "name", "") == "plaintext":
        return (False, 400, {
            "error": "active secrets backend is plaintext; switch to an "
                     "encrypted backend in Settings \u2192 Secrets before "
                     "storing a credential, or create the site without a "
                     "password and log in by hand",
            "secrets_plaintext": True})
    if hasattr(backend, "is_unlocked") and not backend.is_unlocked():
        return (False, 401, {
            "error": "secrets backend is locked; unlock it first",
            "secrets_locked": True})
    return (True, None, None)


def _store_site_password_in_vault(sid, password):
    """v3.66.326: store ``password`` for ``sid`` in the secrets vault and
    write a ``@cred:`` reference onto ``s_cfg[sid]['password']``. Plaintext is
    NEVER written to the config. Caller must run ``_vault_guard_for_password``
    first (this assumes an unlocked, non-plaintext backend). Returns
    ``(ok, error)``; the site already exists, so a ``set()`` failure surfaces
    via ``error`` rather than rolling back (same posture as setup_site).
    """
    from . import secrets_store as ss
    backend = ss.get_backend()
    try:
        backend.set(ss.site_password_key(sid), password)
        s_cfg[sid]["password"] = ss.make_password_reference(sid)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# api_add -> app_sites.py (Phase 4 multi-block extraction)


# /api/captures -> app_captures.py (Phase 4 thin-core-shell extraction)
# api_sites_csv_template -> app_sites.py (Phase 4 multi-block extraction)


# api_sites_xlsx_template -> app_sites.py (Phase 4 multi-block extraction)


# api_sites_bulk_csv -> app_sites.py (Phase 4 multi-block extraction)

# ── v3.52 (Phase 5): site-editor support endpoints ──────────────────────
# Validation, export, import, and diff — the editor uses these to give
# the operator feedback before a save, and to move configs between
# installs. All four delegate to bulk_downloader/site_editor.py.

# api_sites_validate -> app_sites.py (Phase 4 multi-block extraction)


# api_sites_export -> app_sites.py (Phase 4 multi-block extraction)


# api_sites_import -> app_sites.py (Phase 4 multi-block extraction)


# api_sites_diff -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.59 (Phase 11, #58): JSON Schema endpoint ─────────────────────────
# Serves a Draft-07 schema for sites_config.json so editors (VS Code et
# al.) can offer autocomplete + type hints. Generated from CFG_FIELDS so
# it never drifts from the actual field set.

# api_sites_schema -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.59 (Phase 11, #103): selector confidence / drift surface ─────────
# selector_drift.py already tracks per-site selector failures. This
# endpoint surfaces it so the editor can show "this site's selector has
# missed N times in a row" before the operator wastes a run on it.

# api_sites_selector_health -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.61 (Phase 13): companion-tool registry ───────────────────────────
# tools/registry.json declares the companion tools BD knows about. This
# endpoint serves it (annotating each tool with whether its script is
# actually present on disk) so a "Tools" menu can list them. BD never
# auto-executes registry entries — the registry is descriptive only.

# api_tools (/api/tools) -> app_tools.py (Phase 4 thin-core-shell extraction; __file__ same-dir, path math preserved)


# ── Phase 18.23: Site cloning ────────────────────────────────────────────
# "Duplicate site" — copies config but DROPS fields that should be unique
# per site: credentials, learned selectors, fingerprint, accounts, cookies.
# Useful for setting up a similar site (e.g. one of three sister sites with
# the same UI) without re-entering all the tuning fields.
# api_clone_site -> app_sites.py (Phase 4 multi-block extraction)

# api_randomize_fingerprint -> app_sites.py (Phase 4 multi-block extraction)

# api_update -> app_sites.py (Phase 4 multi-block extraction)

# api_delete -> app_sites.py (Phase 4 multi-block extraction)

# api_login -> app_sites.py (Phase 4 multi-block extraction)


# ── Phase 31: Multi-account management endpoints ──────────────────────
# api_accounts_get -> app_sites.py (Phase 4 multi-block extraction)


# api_accounts_reset_cooldown -> app_sites.py (Phase 4 multi-block extraction)


# api_accounts_rotate -> app_sites.py (Phase 4 multi-block extraction)


# ── Phase 32: Captcha tuning endpoints ────────────────────────────────
# api_captcha_stats -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.21: JD bridge diagnostic ───────────────────────────────────
# api_jd_diagnose -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.33: AI-assisted login form detection ──────────────────────
# api_ai_detect_login -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.37: deep Jellyfin diagnostic + library discovery ─────────
# api_jellyfin_diagnose -> app_sites.py (Phase 4 multi-block extraction)


# api_jellyfin_libraries -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.31: per-domain rate limiter status ──────────────────────────
# /api/rate_limit -> app_rate_limit.py (Phase 4 thin-core-shell extraction)
# ── v3.43.30: watch folder ───────────────────────────────────────────
# api_watch_scan_now -> app_sites.py (Phase 4 multi-block extraction)


# api_watch_status -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.29: deep Plex diagnostic + section discovery ──────────────
# api_plex_diagnose -> app_sites.py (Phase 4 multi-block extraction)


# api_plex_sections -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.28: deep Stash diagnostic + scrape preview ────────────────
# api_stash_diagnose -> app_sites.py (Phase 4 multi-block extraction)


# api_stash_preview_url -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.26: qBittorrent bridge diagnostic ──────────────────────────
# api_qb_diagnose -> app_sites.py (Phase 4 multi-block extraction)


# api_captcha_test -> app_sites.py (Phase 4 multi-block extraction)


# ── Phase 37: Cross-device pairing (QR code) ──────────────────────────
# Generate a QR code that another device (phone, tablet) can scan to
# reach this same UI without typing the IP. The QR encodes:
#   http://<lan_ip>:<port>/?t=<one-time-pairing-token>
#
# The token is optional. When BD_AUTH_TOKEN auth is enabled, the token
# is a one-time bearer that the scanning device exchanges for a session.
# When auth is off, the token is omitted entirely — you're just
# transferring the URL.
#
# QR generation is OPTIONAL. If the `qrcode` Python package is installed,
# we return an inline SVG. If not, the UI shows the URL prominently and
# the user can copy/paste it.

def _lan_ip_guess() -> str:
    """Best-effort detection of this host's LAN IP. Uses the "connect
    to a non-routable address" trick — doesn't actually send packets,
    just resolves what source address the kernel WOULD use. Works on
    Linux/macOS/Windows without DNS dependency."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't have to be reachable — just used to pick the right
            # interface based on the routing table
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


# api_pair -> app_pair.py (Phase 4 multi-block extraction)


# ── Phase 34: Log access endpoint ─────────────────────────────────────
# /api/logs -> app_logs.py (Phase 4 thin-core-shell extraction)
# api_manual_login -> app_sites.py (Phase 4 multi-block extraction)

# ── Phase 20: hook test endpoints ─────────────────────────────────────
# These let the user verify their hook configs from the settings UI
# WITHOUT having to wait for a real download. Each test returns a clear
# success/failure with the actual remote response so the user can debug
# auth and connectivity issues directly.
# api_hooks_test -> app_sites.py (Phase 4 multi-block extraction)

# api_hooks_spillover_check -> app_sites.py (Phase 4 multi-block extraction)

# api_login_manual_done -> app_sites.py (Phase 4 multi-block extraction)

# api_login_manual_cancel -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.51: wizard step endpoints ─────────────────────────────────
#
# (The template-apply primitive already exists as
# /api/sites/<sid>/templates/apply — see api_template_apply above.
# The wizard uses that endpoint directly, no new wrapper needed.)


# api_login_verify -> app_sites.py (Phase 4 multi-block extraction)


# api_login_verify_status -> app_sites.py (Phase 4 multi-block extraction)


# ── Phase 5.4: Manual takeover for downloads ──────────────────────────────
# api_take_over_url -> app_sites.py (Phase 4 multi-block extraction)

# api_take_over_done -> app_sites.py (Phase 4 multi-block extraction)

# api_take_over_cancel -> app_sites.py (Phase 4 multi-block extraction)

# ── Phase 10: Teach Mode endpoints ─────────────────────────────────────────
# Called by the in-page TEACH_OVERLAY_JS panel. Same flow as take_over_*
# but with selector picks routed through teach_verify/teach_commit so the
# user's curated picks beat the auto-classifier.
def _teach_cors_response(payload, status=200):
    """Add CORS headers for the takeover browser. The teach overlay
    runs on whatever site the user navigated to (wowgirls.com,
    vimeo.com, etc.) and posts to /api/sites/<sid>/teach_*. Without
    CORS, those POSTs are blocked.

    Phase 26.3: tightened from wildcard `*` to echoing the request's
    Origin only, and only when the request matches the small set of
    teach_* endpoints. Credentials aren't allowed, methods limited to
    POST+OPTIONS, headers limited to Content-Type. The risk we're
    closing is: a malicious site can no longer make a cross-origin
    POST and read the response (browsers block that under the new
    policy because the echoed origin doesn't match wildcard semantics
    when credentials might be involved later)."""
    resp = jsonify(payload)
    resp.status_code = status
    origin = request.headers.get("Origin", "")
    # Only echo back if the request actually had an Origin header (real
    # cross-origin XHRs always do). Bare same-origin requests don't
    # need the header at all.
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    # Explicitly DENY credentials — keeps cookies/auth from accidentally
    # leaking cross-origin even if the teach overlay tries
    resp.headers["Access-Control-Allow-Credentials"] = "false"
    return resp

# api_teach_verify -> app_sites.py (Phase 4 multi-block extraction)

# api_teach_test_download -> app_sites.py (Phase 4 multi-block extraction)

# api_teach_commit -> app_sites.py (Phase 4 multi-block extraction)

# api_teach_cancel -> app_sites.py (Phase 4 multi-block extraction)

# api_teach_save_template -> app_sites.py (Phase 4 multi-block extraction)

# api_reset_learned -> app_sites.py (Phase 4 multi-block extraction)

# ── Phase 13: Visibility endpoints ─────────────────────────────────────────
# api_events -> app_sites.py (Phase 4 multi-block extraction)

# api_timeline -> app_sites.py (Phase 4 multi-block extraction)

# api_selector_stats -> app_sites.py (Phase 4 multi-block extraction)


# ── Phase 23.1: Learning radar — auto-prune dead selectors ────────────
# api_prune_selectors -> app_sites.py (Phase 4 multi-block extraction)


# api_learned_apply_repairs -> app_sites.py (Phase 4 multi-block extraction)


# ── Phase 23.3: Learned profile export / import via web UI ────────────
# bdctl already exposes these, but a UI button is more discoverable for
# casual users.
# api_learned_export -> app_sites.py (Phase 4 multi-block extraction)

# api_learned_import -> app_sites.py (Phase 4 multi-block extraction)


# ── Phase 23.4: Site templates ────────────────────────────────────────
# /api/templates -> app_templates.py (Phase 4 thin-core-shell extraction)
# /api/login_templates -> app_login_templates.py (Phase 4 thin-core-shell extraction)
# api_login_template_apply -> app_sites.py (Phase 4 multi-block extraction)


# /api/jsonapi -> app_jsonapi.py (Phase 4 thin-core-shell extraction)
# api_template_apply -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.16: User-saved templates ────────────────────────────────────
# Templates the user creates after a successful teach session. Stored
# in user_templates.json next to sites_config.json. See
# bulk_downloader/user_templates.py for storage details.

# /api/user_templates -> app_user_templates.py (Phase 4 thin-core-shell extraction)
# /api/storage -> app_storage.py (Phase 4 thin-core-shell extraction)
# ── v3.43.16: Secure password storage ────────────────────────────────
# Endpoints for the secrets-store backend. See secrets_store.py.

# api_secrets_status -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_usage -> app_secrets.py (Phase 4 multi-block extraction)


# /api/integrations -> app_integrations.py (Phase 4 thin-core-shell extraction)
# api_secrets_configure -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_unlock -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_lock -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_change_password -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_migrate -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_import_file -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_import_apply -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_delete -> app_secrets.py (Phase 4 multi-block extraction)


# ── v3.43.16 (extension bridge): browser-extension vault access ──────
#
# The extension uses these endpoints to autofill passwords on websites
# in your normal Chrome. Three-step flow:
#
#   1. User clicks "Pair extension" in the app's vault settings.
#      Server issues a short-lived pairing token, displayed as a
#      QR code (or copyable string).
#   2. User opens the extension's options page and pastes/scans
#      the pairing token. Extension calls /api/secrets/extension/pair
#      and receives a long-lived vault token, stored in
#      chrome.storage.local.
#   3. On every page load, the extension's content script asks
#      /api/secrets/extension/list_for_origin with the page's URL.
#      Server returns matching entries (NO passwords). If the user
#      clicks the autofill suggestion in the floating menu, the
#      extension calls /api/secrets/extension/fetch_one with the
#      entry ID, gets the password ONCE, and fills the form via the
#      background script (NEVER via the content script — content
#      scripts share JS context with the page).
#
# Auth: Bearer token. The vault token is validated on every request.
# Rate-limited: per-entry 5s cooldown, per-token 30/min.

def _require_vault_token():
    """Helper that validates the Authorization: Bearer <vault_token>
    header. Returns (token_str, meta_dict) on success or raises
    a Flask-aborting response."""
    from . import extension_vault as _ev
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, None, (jsonify({"ok": False,
            "error": "Authorization: Bearer <vault_token> required"}), 401)
    vt = auth[7:].strip()
    meta = _ev.validate_vault_token(vt)
    if meta is None:
        return None, None, (jsonify({"ok": False,
            "error": "invalid or expired vault token"}), 401)
    return vt, meta, None


def _reject_if_vault_token():
    """B12 (v3.66.38): management routes (pair_issue / list_paired /
    revoke) must never be operable with a vault token, even if the global
    auth gate is misconfigured or disabled. If the request carries a
    Bearer that validates as a vault token, reject it with 403.

    Returns a Flask response tuple to return, or None to proceed. The
    legitimate caller authenticates with a session cookie + CSRF or the
    BD_AUTH_TOKEN bearer — neither validates as a vault token — so this
    never fires for them."""
    from . import extension_vault as _ev
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        vt = auth[7:].strip()
        if vt and _ev.validate_vault_token(vt) is not None:
            return (jsonify({"ok": False,
                "error": "vault tokens cannot access management routes"}), 403)
    return None


# api_secrets_extension_pair_issue -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_extension_pair -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_extension_list_paired -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_extension_revoke -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_extension_list_for_origin -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_extension_fetch_one -> app_secrets.py (Phase 4 multi-block extraction)


# api_secrets_extension_ping -> app_secrets.py (Phase 4 multi-block extraction)


# ── v3.43.16: Session keep-alive endpoints ────────────────────────────

# /api/session_status -> app_session_status.py (Phase 4 thin-core-shell extraction)
# /api/session_history -> app_session_history.py (Phase 4 thin-core-shell extraction)
# api_site_reconnect -> app_sites.py (Phase 4 multi-block extraction)


# api_site_keep_alive_toggle -> app_sites.py (Phase 4 multi-block extraction)


# api_load_urls -> app_sites.py (Phase 4 multi-block extraction)

# ── Phase 16.41: Quick-add endpoint for iOS Share Sheet ─────────────────
# This is what an iOS Shortcut hits when the user chooses "Send to Bulk
# Downloader" from the share sheet. The request body is just `{"url": "..."}`
# (or as a query param for shell/Shortcuts simplicity). Routes to the site
# whose hostname matches the URL's hostname; falls back to the configured
# default if no match. Idempotent: if the URL is already in any site's
# queue, returns ok without duplicating.
# /api/quick_add -> app_quick_add.py (Phase 4 thin-core-shell extraction)
# ── Phase 18.21: Bulk URL routing — distribute a mixed list across sites ──
# UI sends a big list of URLs, server splits them by hostname/url_pattern,
# each site gets its share via load_urls. Returns a per-site summary so
# the UI can show "23 to wowgirls, 15 to filthykings, 4 unrouted".
# api_scrape_listing (/api/scrape_listing) -> app_scrape_listing.py (Phase 4 thin-core-shell extraction; httpx NameError preserved -- see DEFERRED_FIXES.md)


# ── v3.43.42: storage-tier endpoints ──────────────────────────────────
# api_storage_tier_status -> app_sites.py (Phase 4 multi-block extraction)


# api_storage_tier_run_now -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.44: heuristic fingerprint inspection ─────────────────────
# api_heuristic_fingerprint -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.41: download-window diagnostic ───────────────────────────
# api_window_status -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.43.40: extension URL routing improvements ──────────────────────
# ── v3.43.45: paste-HTML → template extractor ───────────────────────
# /api/template -> app_template.py (Phase 4 thin-core-shell extraction)
# ── v3.45.2 Phase 178: extension companion lookup ─────────────────────
# /api/extension -> app_extension.py (Phase 4 thin-core-shell extraction)
# api_sites_list (/api/sites_list) -> app_sites_list.py (Phase 4 thin-core-shell extraction)


# /api/route_preview -> app_route_preview.py (Phase 4 thin-core-shell extraction)
# api_site_queue_url -> app_sites.py (Phase 4 multi-block extraction)


# /api/route_urls -> app_route_urls.py (Phase 4 thin-core-shell extraction)
# api_load_cookies -> app_sites.py (Phase 4 multi-block extraction)

# api_reorder -> app_sites.py (Phase 4 multi-block extraction)

# api_priority -> app_sites.py (Phase 4 multi-block extraction)

# api_bulk_priority -> app_sites.py (Phase 4 multi-block extraction)

# api_bulk_delete -> app_sites.py (Phase 4 multi-block extraction)

# api_bulk_approve -> app_sites.py (Phase 4 multi-block extraction)

# ── v3.49 (#5/#55): bulk pause / resume / retry / reorder ────────────────
# These are the queue-row toolbar actions exposed to the bulk-select UI.
# Body shape: {"urls": [...]}. Reorder additionally takes a full ordering.

# api_bulk_pause -> app_sites.py (Phase 4 multi-block extraction)

# api_bulk_resume -> app_sites.py (Phase 4 multi-block extraction)

# api_bulk_retry -> app_sites.py (Phase 4 multi-block extraction)

# api_bulk_reorder -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.49 (#71): Server-side queue search ────────────────────────────────
# At ≥2000 rows, client-side filtering starts to stutter on input. This
# endpoint serves the queue tab when the user is filtering a large queue.
# Cursor-paginated; the client appends batches as it scrolls.
# api_queue_search -> app_sites.py (Phase 4 multi-block extraction)


# api_queue_counts -> app_sites.py (Phase 4 multi-block extraction)


# api_queue_grouped -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.49 (#62/#63): queue snapshot — export / import / restore ──────────
# Snapshots are JSON dumps of the current queue (URLs + priority +
# status + force_download flags). Three use cases:
#   1. Backup before a risky operation
#   2. Move queue between machines / migrate from old install
#   3. Save a recurring queue as a template ("nightly check-ins")
#
# Snapshots are operator-readable JSON, not opaque blobs — paste into
# a text editor, modify, re-import. Format versioned for forward compat.
# api_queue_export -> app_sites.py (Phase 4 multi-block extraction)


# api_queue_import -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.49 (#64): Queue templates ─────────────────────────────────────────
# Save / recall named queue snapshots. See queue_templates.py for
# storage details. These endpoints are site-agnostic (templates live in
# their own table) which is why they're not under /api/sites/<sid>.
# /api/queue_templates -> app_queue_templates.py (Phase 4 thin-core-shell extraction)
# api_queue_save_template -> app_sites.py (Phase 4 multi-block extraction)

# ── Phase 18.25: Bulk regex find/replace on URLs ──────────────────────────
# api_bulk_url_transform -> app_sites.py (Phase 4 multi-block extraction)

# api_export -> app_sites.py (Phase 4 multi-block extraction)

# ── History maintenance ──────────────────────────────────────────────────────
# api_prune -> app_history.py (Phase 4 multi-block extraction)

# api_vacuum -> app_history.py (Phase 4 multi-block extraction)

# ── Config import/export ─────────────────────────────────────────────────────
# /api/config -> app_config.py (Phase 4 thin-core-shell extraction)
# ── Phase 26.4 + 26.7: explicit action endpoints with rate limiting ───
# Previously this was a `for action in [...]; exec(...)` block — works
# fine but obscures the actual route surface, defeats static analysis,
# and triggers AST-walker false positives on security scanners. The
# explicit version below is equivalent.
#
# Rate limiting (Phase 26.7): a small per-(IP, action) token bucket
# prevents accidental DoS via UI button-spamming or buggy scripts. The
# limit is generous (10 per 5s) — well above any human's click rate
# but blocks runaway loops cleanly with a 429.

import collections, threading
_rate_buckets = collections.defaultdict(list)  # (ip, action) -> [timestamps]
_rate_lock = threading.Lock()
RATE_LIMIT_MAX    = 10     # actions per window
# Phase 42 (v3.36.10): bound the bucket dict. Every distinct (ip, action) tuple
# adds an entry that never gets removed by the slide logic — only the values
# (lists of timestamps) prune themselves. On a long-running deployment with
# rotating client IPs (LAN with DHCP, mobile devices roaming) the dict grows
# without bound. Sweep removes any bucket whose timestamps are all older than
# the window; runs at most every _RATE_SWEEP_INTERVAL seconds.
_RATE_SWEEP_INTERVAL = 60.0  # seconds between sweeps
_rate_last_sweep = 0.0

def _rate_sweep_locked(now: float):
    """Drop bucket entries with no timestamps newer than the window. Caller
    holds _rate_lock. Cheap: typical install has 1-5 IPs × 6 actions = 30
    buckets, so even O(n) scan is fine."""
    global _rate_last_sweep
    cutoff = now - RATE_LIMIT_WINDOW
    stale = [k for k, ts in _rate_buckets.items()
             if not ts or max(ts) <= cutoff]
    for k in stale:
        del _rate_buckets[k]
    _rate_last_sweep = now


def _is_url_public(url: str) -> bool:
    """AUDIT FIX (v3.43.16): SSRF defence. Resolve the hostname and ensure
    it doesn't land in a private, loopback, link-local, or reserved IP
    range. Returns False on any parse/resolve error (fail closed).

    Used by /api/scrape_listing and the subscription scanner to prevent
    authenticated users from probing internal services."""
    try:
        from urllib.parse import urlparse
        from bulk_downloader.provider_resolve_impl._common import (
            _is_safe_public_host,
        )
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = p.hostname or ""
        if not host:
            return False
        # v3.66.540 (F-APP01-01): delegate host classification to the single
        # canonical predicate (fixed for RFC 6598 CGNAT under VR-P15 @524)
        # instead of a local denylist copy that missed 100.64.0.0/10.
        ok, _reason = _is_safe_public_host(host)
        return bool(ok)
    except Exception:
        return False


def _rate_check(action: str) -> bool:
    """Return True if the request should be allowed, False if rate
    limited. Uses request.remote_addr as the key; behind a proxy you'd
    want X-Forwarded-For but we don't trust proxy headers by default."""
    ip = request.remote_addr or "unknown"
    key = (ip, action)
    now = time.time()
    with _rate_lock:
        # Phase 42: periodic sweep of stale bucket keys to bound memory
        if now - _rate_last_sweep >= _RATE_SWEEP_INTERVAL:
            _rate_sweep_locked(now)
        bucket = _rate_buckets[key]
        # Slide window — drop timestamps older than the window
        cutoff = now - RATE_LIMIT_WINDOW
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= RATE_LIMIT_MAX:
            return False
        bucket.append(now)
    return True

def _do_action(sid, action):
    """Common body for start/pause/resume/stop/clear/retry. Rate-limits
    first, then dispatches to the runner method. Returns Flask response."""
    if not _rate_check(action):
        return jsonify({"ok": False, "error": "rate limited",
                        "retry_after": RATE_LIMIT_WINDOW}), 429
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    runner = runners[sid]
    # Dispatch via getattr — no exec, no eval. Hardcoded list below is
    # the only entry point; nothing user-controlled reaches getattr.
    method = getattr(runner, action, None)
    if method is None or not callable(method):
        return jsonify({"error": f"unknown action: {action}"}), 400
    method()
    extra = {}
    if action == "start":
        if runner.is_rate_limited():
            extra["blocked_by"] = "rate_limited"
        elif runner.state() == "low_disk":
            extra["blocked_by"] = "low_disk"
    return jsonify({"ok": True, **extra})

# api_start -> app_sites.py (Phase 4 multi-block extraction)
# api_pause -> app_sites.py (Phase 4 multi-block extraction)
# api_resume -> app_sites.py (Phase 4 multi-block extraction)
# api_stop -> app_sites.py (Phase 4 multi-block extraction)
# api_clear -> app_sites.py (Phase 4 multi-block extraction)

# v3.43.80 Phase 84: bulk start/pause/resume across all runners. The UI
# wants a one-click "pause everything" toolbar button so the operator can
# stop the world without clicking through each site. Same CSRF + rate
# limit semantics as the per-site versions.
def _do_action_all(action):
    """Apply `action` to every runner. Returns aggregate result.
    Best-effort: per-site failures are collected and reported, the rest
    still run. Rate-limit applies once per call (not per site)."""
    if not _rate_check(f"all_{action}"):
        return jsonify({"ok": False, "error": "rate limited",
                        "retry_after": RATE_LIMIT_WINDOW}), 429
    results = {"ok": 0, "errors": []}
    for sid, runner in list(runners.items()):
        try:
            method = getattr(runner, action, None)
            if method is None or not callable(method):
                results["errors"].append({"sid": sid, "error": f"unknown action: {action}"})
                continue
            method()
            results["ok"] += 1
        except Exception as e:
            results["errors"].append({"sid": sid, "error": str(e)[:200]})
    return jsonify({"ok": True, "applied_to": results["ok"],
                    "total_sites": len(runners), "errors": results["errors"]})

# /api/pause_all -> app_pause_all.py (Phase 4 thin-core-shell extraction)
# /api/resume_all -> app_resume_all.py (Phase 4 thin-core-shell extraction)
# /api/start_all -> app_start_all.py (Phase 4 thin-core-shell extraction)
# ── v3.43.25: one-click backup + restore ──────────────────────────────
# api_backup_create -> app_backup.py (Phase 4 multi-block extraction)


# api_backup_preview -> app_backup.py (Phase 4 multi-block extraction)


# api_backup_restore -> app_backup.py (Phase 4 multi-block extraction)


# ── v3.43.24: self-test diagnostic endpoint ───────────────────────────
# /api/selftest -> app_selftest.py (Phase 4 thin-core-shell extraction)
# api_retry -> app_sites.py (Phase 4 multi-block extraction)

# v3.43.23: per-URL retry — used by the queue context menu. The site-
# wide /retry endpoint moves every failed/stopped URL back to pending;
# this targets one. The job-level "mark" endpoint at /jobs/mark
# overlaps, but this one's friendlier: no caller knowledge of valid
# transition states, just "do the right thing for this URL".
# api_retry_one -> app_sites.py (Phase 4 multi-block extraction)

# api_jobs_mark -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.49 (#55, #56, #71): bulk queue operations ─────────────────────────
# Single-URL endpoint (jobs/mark above) was fine for one-at-a-time UX, but
# the v3.49 queue panel adds checkbox selection — at which point the
# operator wants to act on 200 URLs without 200 round-trips.
#
# All four endpoints share the same shape:
#   Body: {urls: [...], <op-specific fields>}
#   Returns: {ok, affected: N}

def _validate_bulk_urls(body):
    """Common URL list validation. Returns (ok, urls_or_error_dict)."""
    urls = body.get("urls")
    if not isinstance(urls, list) or not urls:
        return False, {"ok": False,
                       "error": "urls must be a non-empty list"}
    if len(urls) > 5000:
        # Defense-in-depth: refuse pathological lists. 5K URLs is already
        # 10x larger than any realistic operator selection.
        return False, {"ok": False,
                       "error": "url list too large (max 5000 per call)"}
    cleaned = [str(u).strip() for u in urls if str(u).strip()]
    if not cleaned:
        return False, {"ok": False, "error": "no valid urls in list"}
    return True, cleaned


# api_jobs_bulk_mark -> app_sites.py (Phase 4 multi-block extraction)


# api_jobs_bulk_delete -> app_sites.py (Phase 4 multi-block extraction)


# api_jobs_bulk_priority -> app_sites.py (Phase 4 multi-block extraction)


# api_jobs_reorder -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.49 (#57): per-job detail surface ──────────────────────────────────
# The Queue tab renders a row per URL with summary info. Clicking a row
# opens a side panel that needs the full job state — every field the
# runner tracks, the latest retry record, the headers used on the last
# attempt, and the history-table row if the URL was previously attempted.
#
# Returns everything from runner.jobs[url] plus a few derived fields:
#   - history: prior attempts of this URL from the history table
#   - last_screenshot: filename of the latest screenshot, if any
#   - elapsed_s: time since job started (running only)
# api_jobs_detail -> app_sites.py (Phase 4 multi-block extraction)


# ── v3.49 (#71): global pause-all toggle ────────────────────────────────
# Single button to pause every site's runner at once. Useful when the
# operator needs to step away briefly without stopping individual jobs.
# Internally just iterates and calls each runner's stop()/start(), but
# returns a unified result so the UI can render "Pausing 5 sites…"
# without N round-trips.
# api_runners_pause_all + api_runners_resume_all (/api/runners/{pause,resume}_all) -> app_runners.py (Phase 4 thin-core-shell extraction)


# ── v3.56 (Phase 8, #125): stuck-job watchdog ───────────────────────────
# The runner already detects stuck jobs internally (the auto-retry
# thread bumps them). This endpoint just *surfaces* them so the UI can
# show an "N jobs stuck" indicator. A job is stuck if it's been in a
# running/active state with no progress for longer than the threshold.
# Read-only — no state mutation.

# api_jobs_stuck (/api/jobs/stuck) -> app_jobs.py (Phase 4 thin-core-shell extraction)
# Read + write operations on the operator's collection.
#
# Read endpoints (GET):
#   /api/library/browse  — paginated, filterable list
#   /api/library/<id>    — single row + tags
#   /api/library/stats   — aggregate disk usage by dimension
#   /api/library/tags    — list all tags
#   /api/library/missing — rows with file_exists=0
#   /api/library/orphans?root=<path>  — files on disk not in library
#   /api/library/scan/status — scanner progress
#
# Write endpoints (POST/PUT/DELETE):
#   /api/library/<id>/watched   — toggle watched flag
#   /api/library/<id>/rating    — 1-5 or null
#   /api/library/<id>/notes     — free-form text
#   /api/library/<id>/tags      — add/remove a tag
#   /api/library/scan/start     — kick off a scan
#   /api/library/scan/cancel    — stop a running scan
#   /api/library/<id>           — DELETE (with optional file removal)

# api_library_browse -> app_library.py (Phase 4 multi-block extraction)


# api_library_get -> app_library.py (Phase 4 multi-block extraction)


# api_library_delete -> app_library.py (Phase 4 multi-block extraction)


# api_library_watched -> app_library.py (Phase 4 multi-block extraction)


# api_library_rating -> app_library.py (Phase 4 multi-block extraction)


# api_library_notes -> app_library.py (Phase 4 multi-block extraction)


# api_library_tag_add -> app_library.py (Phase 4 multi-block extraction)


# api_library_tag_remove -> app_library.py (Phase 4 multi-block extraction)


# api_library_stats -> app_library.py (Phase 4 multi-block extraction)


# api_library_tags_list -> app_library.py (Phase 4 multi-block extraction)


# api_library_tag_delete -> app_library.py (Phase 4 multi-block extraction)


# api_library_missing -> app_library.py (Phase 4 multi-block extraction)


# api_library_orphans_v2 -> app_library.py (Phase 4 multi-block extraction)


# api_library_scan_start -> app_library.py (Phase 4 multi-block extraction)


# api_library_scan_status -> app_library.py (Phase 4 multi-block extraction)


# api_library_scan_cancel -> app_library.py (Phase 4 multi-block extraction)


# ── v3.57 (Phase 9): library backlog ────────────────────────────────────

# api_sites_preview_filename -> app_sites.py (Phase 4 multi-block extraction)


# api_library_integrity -> app_library.py (Phase 4 multi-block extraction)

# api_history -> app_history.py (Phase 4 multi-block extraction)


# /api/hourly_stats -> app_hourly_stats.py (Phase 4 thin-core-shell extraction)
# api_export_watchlist -> app_sites.py (Phase 4 multi-block extraction)

# api_queue -> app_sites.py (Phase 4 multi-block extraction)

# /api/stats -> app_stats.py (Phase 4 thin-core-shell extraction)
@app.route("/screenshots/<path:filename>")
def serve_ss(filename):
    # Reject anything that tries to escape the screenshots dir, then 404
    # cleanly if the requested file isn't there. Without this, missing or
    # malformed paths raise → Flask returns a generic 500 page.
    try:
        target=(SCREENSHOTS_DIR/filename).resolve()
        # v3.47.7: use os.sep boundary in the prefix check (CWE-22).
        # The bare startswith() previously here let an attacker-crafted
        # filename like "../screenshots_evil/secret.png" pass since
        # ".../screenshots_evil" textually starts with ".../screenshots".
        # Same boundary check as api_thumbnails_serve below.
        ss_root = str(SCREENSHOTS_DIR.resolve())
        target_s = str(target)
        if not (target_s == ss_root or target_s.startswith(ss_root + os.sep)):
            return jsonify({"error":"path traversal"}),400
        if not target.is_file():
            return jsonify({"error":"not found","path":str(filename)}),404
        return send_file(target)
    except Exception as e:
        return jsonify({"error":str(e)}),500

# /api/concurrent -> app_concurrent.py (Phase 4 thin-core-shell extraction)
# Phase 44 (v3.37.0): the HTML shell + 800 lines of CSS + 3,900 lines
# of JS were extracted from this file in v3.37.0. See:
#   bulk_downloader/templates/index.html
#   bulk_downloader/static/app.css
#   bulk_downloader/static/app.js
# The index() route above reads templates/index.html on each request
# (cached for production, hot-reloaded when FLASK_DEBUG=1).


# v3.43.60: VPN feature routes — 23 endpoints registered as a Flask
# blueprint in bulk_downloader/app_vpn_api.py. Soft import so app.py
# still starts if the VPN modules fail to load.
try:
    from . import app_vpn_api
    app_vpn_api.register_routes(app)
except Exception as _vpn_routes_err:
    import sys as _sys
    _sys.stderr.write(f"[app] VPN routes not registered: {_vpn_routes_err}\n")

# v3.43.60: Dashboard widget picker routes — 5 endpoints.
try:
    from . import app_widgets_api
    app_widgets_api.register_routes(app)
except Exception as _widgets_routes_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Widget routes not registered: {_widgets_routes_err}\n")

# v3.43.60: Captcha relay routes — 5 endpoints for manual-takeover flow.
try:
    from . import app_captcha_relay
    app_captcha_relay.register_routes(app)
except Exception as _captcha_routes_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Captcha relay routes not registered: {_captcha_routes_err}\n")

# v3.43.62: Live-stream recording routes — 6 endpoints for the live
# recording panel + URL probe. Fail-open: if the module can't import
# or routes can't register, the rest of the app keeps working.
try:
    from . import app_live_recorder, live_recorder as _live_recorder
    app_live_recorder.register_routes(app)
    # Init persistence + hooks. State dir is alongside other persistence
    # under data/. Push notifier reuses the existing web-push module.
    try:
        from pathlib import Path as _Path
        _live_state_dir = str(_Path(DATA_DIR if "DATA_DIR" in globals() else ".") / "live_recordings")
    except Exception:
        _live_state_dir = "./live_recordings"
    _live_push = None
    try:
        from . import push as _push_module
        def _live_push_notifier(title: str, body: str) -> None:
            try:
                _push_module.broadcast({"title": title, "body": body, "tag": "live-recorder"})
            except Exception:
                pass
        _live_push = _live_push_notifier
    except Exception:
        _live_push = None
    _live_disk = None
    try:
        # Reuse the existing disk check helper if present.
        from .detect import disk_free_gb as _disk_free_gb
        _live_disk = _disk_free_gb
    except Exception:
        _live_disk = None
    _live_recorder.init(_live_state_dir, push_notifier=_live_push, disk_check=_live_disk)
    # Don't start the scheduler if keep-alive is disabled (test harness).
    if os.environ.get("BD_DISABLE_KEEPALIVE", "").lower() not in ("1", "true", "yes"):
        _live_recorder.start_scheduler()
except Exception as _live_rec_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Live recorder not wired: {_live_rec_err}\n")

# v3.43.60: Wire captcha_relay → SiteRunner. The relay needs to call into
# whichever SiteRunner owns the affected site_id when the user clicks
# "Solve now" or "Done". We register one dispatcher each for start/end.
try:
    from . import captcha_relay as _captcha_relay

    def _captcha_takeover_starter(site_id: str, url: str) -> dict:
        runner = runners.get(site_id)
        if runner is None:
            return {"ok": False, "error": f"no SiteRunner for {site_id!r}"}
        if not hasattr(runner, "start_captcha_solve_session"):
            return {"ok": False, "error": "runner does not support captcha takeover"}
        return runner.start_captcha_solve_session(url)

    def _captcha_takeover_ender(site_id: str, url: str, resolution: str) -> None:
        runner = runners.get(site_id)
        if runner is None:
            return
        if hasattr(runner, "end_captcha_solve_session"):
            runner.end_captcha_solve_session(url, resolution=resolution)

    _captcha_relay.register_takeover_starter(_captcha_takeover_starter)
    _captcha_relay.register_takeover_ender(_captcha_takeover_ender)

    # MOD-1 A-5b: live-browser census for the no-orphan sweep (A5-R3). The
    # sweep cross-checks every actually-open solve browser against the relay
    # registry; a live browser the registry does not bind is reaped. Without
    # this hook the browser surface is UNVERIFIABLE and the sweep says so.
    def _captcha_session_census() -> list:
        out = []
        for _site_id, _runner in list(runners.items()):
            _sessions = getattr(_runner, "_captcha_solve_sessions", None) or {}
            for _url in list(_sessions.keys()):
                out.append((_site_id, _url))
        return out

    _captcha_relay.register_session_census(_captcha_session_census)
    # The reaper actually runs (idle-timeout + orphan reap need a caller).
    _captcha_relay.start_sweeper()
except Exception as _captcha_wire_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Captcha relay dispatchers not wired: {_captcha_wire_err}\n")

# v3.66.94: read-only framework report dashboard (/framework) + multi-server
# fleet view (/fleet). The blueprints live in tools/ and are strictly read-only
# (no command surface). Fail-open: if tools/ isn't importable or routes can't
# register, the rest of the app keeps working without the dashboard.
try:
    from tools.framework_dashboard import register_routes as _register_framework_dash
    _register_framework_dash(app)
except Exception as _fw_dash_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Framework dashboard routes not registered: {_fw_dash_err}\n")

try:
    from tools.framework_fleet import register_routes as _register_framework_fleet
    _register_framework_fleet(app)
except Exception as _fw_fleet_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Fleet routes not registered: {_fw_fleet_err}\n")

# v3.66.98: operator cockpit console — the day-to-day operator workflow GUI.
# Authorized LOCAL ops only: allowlisted report/capture tools, validated args,
# no shell, no remote control, no replay, human-gated. Fail-open like the others.
try:
    from tools.cockpit_console import register_routes as _register_cockpit
    _register_cockpit(app)
except Exception as _cockpit_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Cockpit console routes not registered: {_cockpit_err}\n")

# v3.66.158.1: read-only backlog dashboards — cockpit home (nav hub), template
# manager, data layer (analytics providers), monitoring, and report center. All
# additive and strictly read-only (no command/mutation surface); monitoring and
# report center consume the data-layer providers. Fail-open like the others.
try:
    from . import (app_cockpit_home, app_template_manager_ui, app_data_layer,
                   app_report_center)
    app_cockpit_home.register_routes(app)
    app_template_manager_ui.register_routes(app)
    app_data_layer.register_routes(app)
    # app_monitoring (empty blueprint, fully unregistered @345) and
    # app_actions_center (empty blueprint, 0 routes since @344) were removed
    # from the tree in v3.66.353 — both were dead surface with no SPA/console
    # caller (the SPA hits /api/sites/*, /api/library/*, /api/backup/*,
    # /api/monitoring/summary was retired). report_center is the surviving
    # read-only backlog dashboard.
    app_report_center.register_routes(app)
except Exception as _backlog_routes_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Backlog dashboards not registered: {_backlog_routes_err}\n")

# GUI Phase 3 (Slice 1): read-only Settings Center. Additive, GET-only, no edit
# surface; mirrors the backlog-dashboard registration. Fail-open like the others.
try:
    from . import app_settings_center
    app_settings_center.register_routes(app)
except Exception as _settings_center_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Settings Center not registered: {_settings_center_err}\n")

# Bucket 2 (GUI-config parity): the `.env` editor for deploy/path/port/host env
# vars. Separate blueprint so the Settings Center stays read-only. Fail-open.
try:
    from . import app_envfile_editor
    app_envfile_editor.register_routes(app)
except Exception as _envfile_editor_err:
    import sys as _sys
    _sys.stderr.write(f"[app] .env editor not registered: {_envfile_editor_err}\n")

# Bucket 3b (GUI-config parity): the raw vpn/widgets store-metadata editor.
# Separate blueprint (same precedent) so the Settings Center stays read-only.
# Fail-open.
try:
    from . import app_store_raw_editor
    app_store_raw_editor.register_routes(app)
except Exception as _store_raw_err:
    import sys as _sys
    _sys.stderr.write(f"[app] store-raw editor not registered: {_store_raw_err}\n")

# Phase 4 cut 1 (v3.66.405): outgoing webhooks API extracted to a blueprint.
# Pure motion; (rule, methods, bare-name) surface unchanged. Fail-open.
try:
    from . import app_webhooks
    app_webhooks.register_routes(app)
except Exception as _webhooks_routes_err:
    import sys as _sys
    _sys.stderr.write(f"[app] Webhooks API not registered: {_webhooks_routes_err}\n")

try:
    from . import app_openapi
    app_openapi.register_routes(app)
except Exception as _reg_openapi_err:
    import sys as _sys
    _sys.stderr.write(f"[app] openapi routes not registered: {_reg_openapi_err}\n")

try:
    from . import app_tools
    app_tools.register_routes(app)
except Exception as _reg_tools_err:
    import sys as _sys
    _sys.stderr.write(f"[app] tools routes not registered: {_reg_tools_err}\n")

try:
    from . import app_sites_list
    app_sites_list.register_routes(app)
except Exception as _reg_sites_list_err:
    import sys as _sys
    _sys.stderr.write(f"[app] sites_list routes not registered: {_reg_sites_list_err}\n")

try:
    from . import app_scrape_listing
    app_scrape_listing.register_routes(app)
except Exception as _reg_scrape_listing_err:
    import sys as _sys
    _sys.stderr.write(f"[app] scrape_listing routes not registered: {_reg_scrape_listing_err}\n")

try:
    from . import app_runners
    app_runners.register_routes(app)
except Exception as _reg_runners_err:
    import sys as _sys
    _sys.stderr.write(f"[app] runners routes not registered: {_reg_runners_err}\n")

try:
    from . import app_jobs
    app_jobs.register_routes(app)
except Exception as _reg_jobs_err:
    import sys as _sys
    _sys.stderr.write(f"[app] jobs routes not registered: {_reg_jobs_err}\n")

try:
    from . import app_i18n
    app_i18n.register_routes(app)
except Exception as _reg_i18n_err:
    import sys as _sys
    _sys.stderr.write(f"[app] i18n routes not registered: {_reg_i18n_err}\n")

try:
    from . import app_health
    app_health.register_routes(app)
except Exception as _reg_health_err:
    import sys as _sys
    _sys.stderr.write(f"[app] health routes not registered: {_reg_health_err}\n")

try:
    from . import app_search
    app_search.register_routes(app)
except Exception as _reg_search_err:
    import sys as _sys
    _sys.stderr.write(f"[app] search routes not registered: {_reg_search_err}\n")

try:
    from . import app_subtitles
    app_subtitles.register_routes(app)
except Exception as _reg_subtitles_err:
    import sys as _sys
    _sys.stderr.write(f"[app] subtitles routes not registered: {_reg_subtitles_err}\n")

try:
    from . import app_stream
    app_stream.register_routes(app)
except Exception as _reg_stream_err:
    import sys as _sys
    _sys.stderr.write(f"[app] stream routes not registered: {_reg_stream_err}\n")

try:
    from . import app_pair
    app_pair.register_routes(app)
except Exception as _reg_pair_err:
    import sys as _sys
    _sys.stderr.write(f"[app] pair routes not registered: {_reg_pair_err}\n")

try:
    from . import app_history
    app_history.register_routes(app)
except Exception as _reg_history_err:
    import sys as _sys
    _sys.stderr.write(f"[app] history routes not registered: {_reg_history_err}\n")

try:
    from . import app_global_config
    app_global_config.register_routes(app)
except Exception as _reg_global_config_err:
    import sys as _sys
    _sys.stderr.write(f"[app] global_config routes not registered: {_reg_global_config_err}\n")

try:
    from . import app_cookie_quality
    app_cookie_quality.register_routes(app)
except Exception as _reg_cookie_quality_err:
    import sys as _sys
    _sys.stderr.write(f"[app] cookie_quality routes not registered: {_reg_cookie_quality_err}\n")

try:
    from . import app_activity
    app_activity.register_routes(app)
except Exception as _reg_activity_err:
    import sys as _sys
    _sys.stderr.write(f"[app] activity routes not registered: {_reg_activity_err}\n")

try:
    from . import app_ytdlp_archive
    app_ytdlp_archive.register_routes(app)
except Exception as _reg_ytdlp_archive_err:
    import sys as _sys
    _sys.stderr.write(f"[app] ytdlp_archive routes not registered: {_reg_ytdlp_archive_err}\n")

try:
    from . import app_thumbnails
    app_thumbnails.register_routes(app)
except Exception as _reg_thumbnails_err:
    import sys as _sys
    _sys.stderr.write(f"[app] thumbnails routes not registered: {_reg_thumbnails_err}\n")

try:
    from . import app_tg
    app_tg.register_routes(app)
except Exception as _reg_tg_err:
    import sys as _sys
    _sys.stderr.write(f"[app] tg routes not registered: {_reg_tg_err}\n")

try:
    from . import app_supervisor
    app_supervisor.register_routes(app)
except Exception as _reg_supervisor_err:
    import sys as _sys
    _sys.stderr.write(f"[app] supervisor routes not registered: {_reg_supervisor_err}\n")

try:
    from . import app_scrapling
    app_scrapling.register_routes(app)
except Exception as _reg_scrapling_err:
    import sys as _sys
    _sys.stderr.write(f"[app] scrapling routes not registered: {_reg_scrapling_err}\n")

try:
    from . import app_phoenix
    app_phoenix.register_routes(app)
except Exception as _reg_phoenix_err:
    import sys as _sys
    _sys.stderr.write(f"[app] phoenix routes not registered: {_reg_phoenix_err}\n")

try:
    from . import app_multi_conn
    app_multi_conn.register_routes(app)
except Exception as _reg_multi_conn_err:
    import sys as _sys
    _sys.stderr.write(f"[app] multi_conn routes not registered: {_reg_multi_conn_err}\n")

try:
    from . import app_flaresolverr
    app_flaresolverr.register_routes(app)
except Exception as _reg_flaresolverr_err:
    import sys as _sys
    _sys.stderr.write(f"[app] flaresolverr routes not registered: {_reg_flaresolverr_err}\n")

try:
    from . import app_apple
    app_apple.register_routes(app)
except Exception as _reg_apple_err:
    import sys as _sys
    _sys.stderr.write(f"[app] apple-touch-icon route not registered: {_reg_apple_err}\n")

try:
    from . import app_weather
    app_weather.register_routes(app)
except Exception as _reg_weather_err:
    import sys as _sys
    _sys.stderr.write(f"[app] weather routes not registered: {_reg_weather_err}\n")

try:
    from . import app_ui_events
    app_ui_events.register_routes(app)
except Exception as _reg_ui_events_err:
    import sys as _sys
    _sys.stderr.write(f"[app] ui_events routes not registered: {_reg_ui_events_err}\n")

try:
    from . import app_tpdb
    app_tpdb.register_routes(app)
except Exception as _reg_tpdb_err:
    import sys as _sys
    _sys.stderr.write(f"[app] tpdb routes not registered: {_reg_tpdb_err}\n")

try:
    from . import app_synthetic_tests
    app_synthetic_tests.register_routes(app)
except Exception as _reg_synthetic_tests_err:
    import sys as _sys
    _sys.stderr.write(f"[app] synthetic_tests routes not registered: {_reg_synthetic_tests_err}\n")

try:
    from . import app_status
    app_status.register_routes(app)
except Exception as _reg_status_err:
    import sys as _sys
    _sys.stderr.write(f"[app] status routes not registered: {_reg_status_err}\n")

try:
    from . import app_session_status
    app_session_status.register_routes(app)
except Exception as _reg_session_status_err:
    import sys as _sys
    _sys.stderr.write(f"[app] session_status routes not registered: {_reg_session_status_err}\n")

try:
    from . import app_selftest
    app_selftest.register_routes(app)
except Exception as _reg_selftest_err:
    import sys as _sys
    _sys.stderr.write(f"[app] selftest routes not registered: {_reg_selftest_err}\n")

try:
    from . import app_schedules
    app_schedules.register_routes(app)
except Exception as _reg_schedules_err:
    import sys as _sys
    _sys.stderr.write(f"[app] schedules routes not registered: {_reg_schedules_err}\n")

try:
    from . import app_scheduled_exports
    app_scheduled_exports.register_routes(app)
except Exception as _reg_scheduled_exports_err:
    import sys as _sys
    _sys.stderr.write(f"[app] scheduled_exports routes not registered: {_reg_scheduled_exports_err}\n")

# v3.66.716: app_sched_exports (the /api/sched_exports family) was REMOVED. It registered
# 4 routes that nothing called. app_scheduled_exports is the family the SPA actually uses
# (useGovernance.ts). The bare string "sched_exports" in that hook is a react-query CACHE
# KEY, not a URL -- which is what fooled the first reachability matcher into calling the
# shadow "live". bg_scheduler is unaffected: it imports the LIBRARY (scheduled_exports),
# never the blueprint.

try:
    from . import app_route_urls
    app_route_urls.register_routes(app)
except Exception as _reg_route_urls_err:
    import sys as _sys
    _sys.stderr.write(f"[app] route_urls routes not registered: {_reg_route_urls_err}\n")

try:
    from . import app_route_preview
    app_route_preview.register_routes(app)
except Exception as _reg_route_preview_err:
    import sys as _sys
    _sys.stderr.write(f"[app] route_preview routes not registered: {_reg_route_preview_err}\n")

try:
    from . import app_retention
    app_retention.register_routes(app)
except Exception as _reg_retention_err:
    import sys as _sys
    _sys.stderr.write(f"[app] retention routes not registered: {_reg_retention_err}\n")

try:
    from . import app_rebalance
    app_rebalance.register_routes(app)
except Exception as _reg_rebalance_err:
    import sys as _sys
    _sys.stderr.write(f"[app] rebalance routes not registered: {_reg_rebalance_err}\n")

try:
    from . import app_ramdisk
    app_ramdisk.register_routes(app)
except Exception as _reg_ramdisk_err:
    import sys as _sys
    _sys.stderr.write(f"[app] ramdisk routes not registered: {_reg_ramdisk_err}\n")

try:
    from . import app_quick_add
    app_quick_add.register_routes(app)
except Exception as _reg_quick_add_err:
    import sys as _sys
    _sys.stderr.write(f"[app] quick_add routes not registered: {_reg_quick_add_err}\n")

try:
    from . import app_queue_templates
    app_queue_templates.register_routes(app)
except Exception as _reg_queue_templates_err:
    import sys as _sys
    _sys.stderr.write(f"[app] queue_templates routes not registered: {_reg_queue_templates_err}\n")

try:
    from . import app_marketplace
    app_marketplace.register_routes(app)
except Exception as _reg_marketplace_err:
    import sys as _sys
    _sys.stderr.write(f"[app] marketplace routes not registered: {_reg_marketplace_err}\n")

try:
    from . import app_integrations
    app_integrations.register_routes(app)
except Exception as _reg_integrations_err:
    import sys as _sys
    _sys.stderr.write(f"[app] integrations routes not registered: {_reg_integrations_err}\n")

try:
    from . import app_fixtures
    app_fixtures.register_routes(app)
except Exception as _reg_fixtures_err:
    import sys as _sys
    _sys.stderr.write(f"[app] fixtures routes not registered: {_reg_fixtures_err}\n")

try:
    from . import app_extension
    app_extension.register_routes(app)
except Exception as _reg_extension_err:
    import sys as _sys
    _sys.stderr.write(f"[app] extension routes not registered: {_reg_extension_err}\n")

try:
    from . import app_events_all
    app_events_all.register_routes(app)
except Exception as _reg_events_all_err:
    import sys as _sys
    _sys.stderr.write(f"[app] events_all routes not registered: {_reg_events_all_err}\n")

try:
    from . import app_eol
    app_eol.register_routes(app)
except Exception as _reg_eol_err:
    import sys as _sys
    _sys.stderr.write(f"[app] eol routes not registered: {_reg_eol_err}\n")

try:
    from . import app_doctor
    app_doctor.register_routes(app)
except Exception as _reg_doctor_err:
    import sys as _sys
    _sys.stderr.write(f"[app] doctor routes not registered: {_reg_doctor_err}\n")

try:
    from . import app_discovery
    app_discovery.register_routes(app)
except Exception as _reg_discovery_err:
    import sys as _sys
    _sys.stderr.write(f"[app] discovery routes not registered: {_reg_discovery_err}\n")

try:
    from . import app_diagnostics_bundle
    app_diagnostics_bundle.register_routes(app)
except Exception as _reg_diagnostics_bundle_err:
    import sys as _sys
    _sys.stderr.write(f"[app] diagnostics_bundle routes not registered: {_reg_diagnostics_bundle_err}\n")

try:
    from . import app_diagnostics
    app_diagnostics.register_routes(app)
except Exception as _reg_diagnostics_err:
    import sys as _sys
    _sys.stderr.write(f"[app] diagnostics routes not registered: {_reg_diagnostics_err}\n")

try:
    from . import app_daily_budget
    app_daily_budget.register_routes(app)
except Exception as _reg_daily_budget_err:
    import sys as _sys
    _sys.stderr.write(f"[app] daily_budget routes not registered: {_reg_daily_budget_err}\n")

try:
    from . import app_csrf
    app_csrf.register_routes(app)
except Exception as _reg_csrf_err:
    import sys as _sys
    _sys.stderr.write(f"[app] csrf routes not registered: {_reg_csrf_err}\n")

try:
    from . import app_cost
    app_cost.register_routes(app)
except Exception as _reg_cost_err:
    import sys as _sys
    _sys.stderr.write(f"[app] cost routes not registered: {_reg_cost_err}\n")

try:
    from . import app_cookie_relogin
    app_cookie_relogin.register_routes(app)
except Exception as _reg_cookie_relogin_err:
    import sys as _sys
    _sys.stderr.write(f"[app] cookie_relogin routes not registered: {_reg_cookie_relogin_err}\n")

try:
    from . import app_cookie_clipboard
    app_cookie_clipboard.register_routes(app)
except Exception as _reg_cookie_clipboard_err:
    import sys as _sys
    _sys.stderr.write(f"[app] cookie_clipboard routes not registered: {_reg_cookie_clipboard_err}\n")

try:
    from . import app_config
    app_config.register_routes(app)
except Exception as _reg_config_err:
    import sys as _sys
    _sys.stderr.write(f"[app] config routes not registered: {_reg_config_err}\n")

try:
    from . import app_concurrent
    app_concurrent.register_routes(app)
except Exception as _reg_concurrent_err:
    import sys as _sys
    _sys.stderr.write(f"[app] concurrent routes not registered: {_reg_concurrent_err}\n")

try:
    from . import app_cleanup
    app_cleanup.register_routes(app)
except Exception as _reg_cleanup_err:
    import sys as _sys
    _sys.stderr.write(f"[app] cleanup routes not registered: {_reg_cleanup_err}\n")

try:
    from . import app_changelog
    app_changelog.register_routes(app)
except Exception as _reg_changelog_err:
    import sys as _sys
    _sys.stderr.write(f"[app] changelog routes not registered: {_reg_changelog_err}\n")

try:
    from . import app_captures
    app_captures.register_routes(app)
except Exception as _reg_captures_err:
    import sys as _sys
    _sys.stderr.write(f"[app] captures routes not registered: {_reg_captures_err}\n")

try:
    from . import app_capacity
    app_capacity.register_routes(app)
except Exception as _reg_capacity_err:
    import sys as _sys
    _sys.stderr.write(f"[app] capacity routes not registered: {_reg_capacity_err}\n")

try:
    from . import app_bulk
    app_bulk.register_routes(app)
except Exception as _reg_bulk_err:
    import sys as _sys
    _sys.stderr.write(f"[app] bulk routes not registered: {_reg_bulk_err}\n")

try:
    from . import app_budget
    app_budget.register_routes(app)
except Exception as _reg_budget_err:
    import sys as _sys
    _sys.stderr.write(f"[app] budget routes not registered: {_reg_budget_err}\n")

try:
    from . import app_auth_health
    app_auth_health.register_routes(app)
except Exception as _reg_auth_health_err:
    import sys as _sys
    _sys.stderr.write(f"[app] auth_health routes not registered: {_reg_auth_health_err}\n")

try:
    from . import app_alerts
    app_alerts.register_routes(app)
except Exception as _reg_alerts_err:
    import sys as _sys
    _sys.stderr.write(f"[app] alerts routes not registered: {_reg_alerts_err}\n")

try:
    from . import app_ytdlp_update
    app_ytdlp_update.register_routes(app)
except Exception as _reg_ytdlp_update_err:
    import sys as _sys
    _sys.stderr.write(f"[app] ytdlp_update routes not registered: {_reg_ytdlp_update_err}\n")

try:
    from . import app_ytdlp_status
    app_ytdlp_status.register_routes(app)
    # C6 (8.4): gallery-dl managed-binary status + update, mirroring yt-dlp.
    from . import app_gallerydl_status
    app_gallerydl_status.register_routes(app)
    from . import app_gallerydl_update
    app_gallerydl_update.register_routes(app)
except Exception as _reg_ytdlp_status_err:
    import sys as _sys
    _sys.stderr.write(f"[app] ytdlp_status routes not registered: {_reg_ytdlp_status_err}\n")

try:
    from . import app_wayback
    app_wayback.register_routes(app)
except Exception as _reg_wayback_err:
    import sys as _sys
    _sys.stderr.write(f"[app] wayback routes not registered: {_reg_wayback_err}\n")

try:
    from . import app_wakeup
    app_wakeup.register_routes(app)
except Exception as _reg_wakeup_err:
    import sys as _sys
    _sys.stderr.write(f"[app] wakeup routes not registered: {_reg_wakeup_err}\n")

try:
    from . import app_vpn
    app_vpn.register_routes(app)
except Exception as _reg_vpn_err:
    import sys as _sys
    _sys.stderr.write(f"[app] vpn routes not registered: {_reg_vpn_err}\n")

try:
    from . import app_thumbs
    app_thumbs.register_routes(app)
except Exception as _reg_thumbs_err:
    import sys as _sys
    _sys.stderr.write(f"[app] thumbs routes not registered: {_reg_thumbs_err}\n")

try:
    from . import app_thumbnail_sheets
    app_thumbnail_sheets.register_routes(app)
except Exception as _reg_thumbnail_sheets_err:
    import sys as _sys
    _sys.stderr.write(f"[app] thumbnail_sheets routes not registered: {_reg_thumbnail_sheets_err}\n")

try:
    from . import app_templates
    app_templates.register_routes(app)
except Exception as _reg_templates_err:
    import sys as _sys
    _sys.stderr.write(f"[app] templates routes not registered: {_reg_templates_err}\n")

try:
    from . import app_template_manager
    app_template_manager.register_routes(app)
except Exception as _reg_template_manager_err:
    import sys as _sys
    _sys.stderr.write(f"[app] template_manager routes not registered: {_reg_template_manager_err}\n")

try:
    from . import app_storage_rebalance
    app_storage_rebalance.register_routes(app)
except Exception as _reg_storage_rebalance_err:
    import sys as _sys
    _sys.stderr.write(f"[app] storage_rebalance routes not registered: {_reg_storage_rebalance_err}\n")

try:
    from . import app_storage
    app_storage.register_routes(app)
except Exception as _reg_storage_err:
    import sys as _sys
    _sys.stderr.write(f"[app] storage routes not registered: {_reg_storage_err}\n")

try:
    from . import app_stats
    app_stats.register_routes(app)
except Exception as _reg_stats_err:
    import sys as _sys
    _sys.stderr.write(f"[app] stats routes not registered: {_reg_stats_err}\n")

try:
    from . import app_start_all
    app_start_all.register_routes(app)
except Exception as _reg_start_all_err:
    import sys as _sys
    _sys.stderr.write(f"[app] start_all routes not registered: {_reg_start_all_err}\n")

try:
    from . import app_sse_status
    app_sse_status.register_routes(app)
except Exception as _reg_sse_status_err:
    import sys as _sys
    _sys.stderr.write(f"[app] sse_status routes not registered: {_reg_sse_status_err}\n")

try:
    from . import app_shortcuts
    app_shortcuts.register_routes(app)
except Exception as _reg_shortcuts_err:
    import sys as _sys
    _sys.stderr.write(f"[app] shortcuts routes not registered: {_reg_shortcuts_err}\n")

try:
    from . import app_shares
    app_shares.register_routes(app)
except Exception as _reg_shares_err:
    import sys as _sys
    _sys.stderr.write(f"[app] shares routes not registered: {_reg_shares_err}\n")

try:
    from . import app_session_history
    app_session_history.register_routes(app)
except Exception as _reg_session_history_err:
    import sys as _sys
    _sys.stderr.write(f"[app] session_history routes not registered: {_reg_session_history_err}\n")

try:
    from . import app_selector_drift
    app_selector_drift.register_routes(app)
except Exception as _reg_selector_drift_err:
    import sys as _sys
    _sys.stderr.write(f"[app] selector_drift routes not registered: {_reg_selector_drift_err}\n")

try:
    from . import app_scene_score
    app_scene_score.register_routes(app)
except Exception as _reg_scene_score_err:
    import sys as _sys
    _sys.stderr.write(f"[app] scene_score routes not registered: {_reg_scene_score_err}\n")

try:
    from . import app_runs
    app_runs.register_routes(app)
except Exception as _reg_runs_err:
    import sys as _sys
    _sys.stderr.write(f"[app] runs routes not registered: {_reg_runs_err}\n")

try:
    from . import app_retry_policy
    app_retry_policy.register_routes(app)
except Exception as _reg_retry_policy_err:
    import sys as _sys
    _sys.stderr.write(f"[app] retry_policy routes not registered: {_reg_retry_policy_err}\n")

try:
    from . import app_resume_all
    app_resume_all.register_routes(app)
except Exception as _reg_resume_all_err:
    import sys as _sys
    _sys.stderr.write(f"[app] resume_all routes not registered: {_reg_resume_all_err}\n")

try:
    from . import app_recommendations
    app_recommendations.register_routes(app)
except Exception as _reg_recommendations_err:
    import sys as _sys
    _sys.stderr.write(f"[app] recommendations routes not registered: {_reg_recommendations_err}\n")

try:
    from . import app_rate_limit
    app_rate_limit.register_routes(app)
except Exception as _reg_rate_limit_err:
    import sys as _sys
    _sys.stderr.write(f"[app] rate_limit routes not registered: {_reg_rate_limit_err}\n")

try:
    from . import app_provenance
    app_provenance.register_routes(app)
except Exception as _reg_provenance_err:
    import sys as _sys
    _sys.stderr.write(f"[app] provenance routes not registered: {_reg_provenance_err}\n")

try:
    from . import app_plugins
    app_plugins.register_routes(app)
except Exception as _reg_plugins_err:
    import sys as _sys
    _sys.stderr.write(f"[app] plugins routes not registered: {_reg_plugins_err}\n")

try:
    from . import app_playground
    app_playground.register_routes(app)
except Exception as _reg_playground_err:
    import sys as _sys
    _sys.stderr.write(f"[app] playground routes not registered: {_reg_playground_err}\n")

try:
    from . import app_pause_all
    app_pause_all.register_routes(app)
except Exception as _reg_pause_all_err:
    import sys as _sys
    _sys.stderr.write(f"[app] pause_all routes not registered: {_reg_pause_all_err}\n")

try:
    from . import app_palette
    app_palette.register_routes(app)
except Exception as _reg_palette_err:
    import sys as _sys
    _sys.stderr.write(f"[app] palette routes not registered: {_reg_palette_err}\n")

try:
    from . import app_logs
    app_logs.register_routes(app)
except Exception as _reg_logs_err:
    import sys as _sys
    _sys.stderr.write(f"[app] logs routes not registered: {_reg_logs_err}\n")

try:
    from . import app_login_templates
    app_login_templates.register_routes(app)
except Exception as _reg_login_templates_err:
    import sys as _sys
    _sys.stderr.write(f"[app] login_templates routes not registered: {_reg_login_templates_err}\n")

try:
    from . import app_jsonapi
    app_jsonapi.register_routes(app)
except Exception as _reg_jsonapi_err:
    import sys as _sys
    _sys.stderr.write(f"[app] jsonapi routes not registered: {_reg_jsonapi_err}\n")

try:
    from . import app_hourly_stats
    app_hourly_stats.register_routes(app)
except Exception as _reg_hourly_stats_err:
    import sys as _sys
    _sys.stderr.write(f"[app] hourly_stats routes not registered: {_reg_hourly_stats_err}\n")

try:
    from . import app_gamification
    app_gamification.register_routes(app)
except Exception as _reg_gamification_err:
    import sys as _sys
    _sys.stderr.write(f"[app] gamification routes not registered: {_reg_gamification_err}\n")

try:
    from . import app_file
    app_file.register_routes(app)
except Exception as _reg_file_err:
    import sys as _sys
    _sys.stderr.write(f"[app] file routes not registered: {_reg_file_err}\n")

try:
    from . import app_export
    app_export.register_routes(app)
except Exception as _reg_export_err:
    import sys as _sys
    _sys.stderr.write(f"[app] export routes not registered: {_reg_export_err}\n")

try:
    from . import app_edge_deploy
    app_edge_deploy.register_routes(app)
except Exception as _reg_edge_deploy_err:
    import sys as _sys
    _sys.stderr.write(f"[app] edge_deploy routes not registered: {_reg_edge_deploy_err}\n")

try:
    from . import app_deploy
    app_deploy.register_routes(app)
except Exception as _reg_deploy_err:
    import sys as _sys
    _sys.stderr.write(f"[app] deploy routes not registered: {_reg_deploy_err}\n")

try:
    from . import app_cluster_rate
    app_cluster_rate.register_routes(app)
except Exception as _reg_cluster_rate_err:
    import sys as _sys
    _sys.stderr.write(f"[app] cluster_rate routes not registered: {_reg_cluster_rate_err}\n")

try:
    from . import app_circuit
    app_circuit.register_routes(app)
except Exception as _reg_circuit_err:
    import sys as _sys
    _sys.stderr.write(f"[app] circuit routes not registered: {_reg_circuit_err}\n")

try:
    from . import app_bw_chart
    app_bw_chart.register_routes(app)
except Exception as _reg_bw_chart_err:
    import sys as _sys
    _sys.stderr.write(f"[app] bw_chart routes not registered: {_reg_bw_chart_err}\n")

try:
    from . import app_bitrot
    app_bitrot.register_routes(app)
except Exception as _reg_bitrot_err:
    import sys as _sys
    _sys.stderr.write(f"[app] bitrot routes not registered: {_reg_bitrot_err}\n")

try:
    from . import app_bg
    app_bg.register_routes(app)
except Exception as _reg_bg_err:
    import sys as _sys
    _sys.stderr.write(f"[app] bg routes not registered: {_reg_bg_err}\n")

try:
    from . import app_batch
    app_batch.register_routes(app)
except Exception as _reg_batch_err:
    import sys as _sys
    _sys.stderr.write(f"[app] batch routes not registered: {_reg_batch_err}\n")

try:
    from . import app_audit
    app_audit.register_routes(app)
except Exception as _reg_audit_err:
    import sys as _sys
    _sys.stderr.write(f"[app] audit routes not registered: {_reg_audit_err}\n")

try:
    from . import app_api_tokens
    app_api_tokens.register_routes(app)
except Exception as _reg_api_tokens_err:
    import sys as _sys
    _sys.stderr.write(f"[app] api_tokens routes not registered: {_reg_api_tokens_err}\n")

try:
    from . import app_accounts
    app_accounts.register_routes(app)
except Exception as _reg_accounts_err:
    import sys as _sys
    _sys.stderr.write(f"[app] accounts routes not registered: {_reg_accounts_err}\n")

try:
    from . import app_account_pool
    app_account_pool.register_routes(app)
except Exception as _reg_account_pool_err:
    import sys as _sys
    _sys.stderr.write(f"[app] account_pool routes not registered: {_reg_account_pool_err}\n")

try:
    from . import app_a11y
    app_a11y.register_routes(app)
except Exception as _reg_a11y_err:
    import sys as _sys
    _sys.stderr.write(f"[app] a11y routes not registered: {_reg_a11y_err}\n")

try:
    from . import app_plex
    app_plex.register_routes(app)
except Exception as _reg_plex_err:
    import sys as _sys
    _sys.stderr.write(f"[app] plex routes not registered: {_reg_plex_err}\n")

try:
    from . import app_ai
    app_ai.register_routes(app)
except Exception as _reg_ai_err:
    import sys as _sys
    _sys.stderr.write(f"[app] ai routes not registered: {_reg_ai_err}\n")

try:
    from . import app_dev
    app_dev.register_routes(app)
except Exception as _reg_dev_err:
    import sys as _sys
    _sys.stderr.write(f"[app] dev routes not registered: {_reg_dev_err}\n")

try:
    from . import app_sites
    app_sites.register_routes(app)
except Exception as _reg_sites_err:
    import sys as _sys
    _sys.stderr.write(f"[app] sites routes not registered: {_reg_sites_err}\n")

try:
    from . import app_library
    app_library.register_routes(app)
except Exception as _reg_library_err:
    import sys as _sys
    _sys.stderr.write(f"[app] library routes not registered: {_reg_library_err}\n")

try:
    from . import app_queue
    app_queue.register_routes(app)
except Exception as _reg_queue_err:
    import sys as _sys
    _sys.stderr.write(f"[app] queue routes not registered: {_reg_queue_err}\n")

try:
    from . import app_auth
    app_auth.register_routes(app)
    from . import app_oidc
    app_oidc.register_routes(app)
except Exception as _reg_auth_err:
    import sys as _sys
    _sys.stderr.write(f"[app] auth routes not registered: {_reg_auth_err}\n")

try:
    from . import app_semantic_search
    app_semantic_search.register_routes(app)
except Exception as _reg_semantic_err:
    import sys as _sys
    _sys.stderr.write(f"[app] semantic-search routes not registered: {_reg_semantic_err}\n")

try:
    from . import app_backup
    app_backup.register_routes(app)
except Exception as _reg_backup_err:
    import sys as _sys
    _sys.stderr.write(f"[app] backup routes not registered: {_reg_backup_err}\n")

# v3.66.635: replication status route -- exposes db_replication.replication_status()
# (C5 durability signal) that was previously unreachable (module was an island).
# Read-only GET; the mutating lifecycle controls stay out (separate cut).
try:
    from . import app_replication
    app_replication.register_routes(app)
except Exception as _reg_replication_err:
    import sys as _sys
    _sys.stderr.write(f"[app] replication routes not registered: {_reg_replication_err}\n")

# INTEROP-GOV-1b (v3.66.639): operator surface over the interop_registry keystone.
try:
    from . import app_interop
    app_interop.register_routes(app)
except Exception as _reg_interop_err:
    import sys as _sys
    _sys.stderr.write(f"[app] interop routes not registered: {_reg_interop_err}\n")

try:
    from . import app_secrets
    app_secrets.register_routes(app)
except Exception as _reg_secrets_err:
    import sys as _sys
    _sys.stderr.write(f"[app] secrets routes not registered: {_reg_secrets_err}\n")

try:
    from . import app_dashboard
    app_dashboard.register_routes(app)
except Exception as _reg_dashboard_err:
    import sys as _sys
    _sys.stderr.write(f"[app] dashboard routes not registered: {_reg_dashboard_err}\n")

try:
    from . import app_notify
    app_notify.register_routes(app)
except Exception as _reg_notify_err:
    import sys as _sys
    _sys.stderr.write(f"[app] notify routes not registered: {_reg_notify_err}\n")

try:
    from . import app_template
    app_template.register_routes(app)
except Exception as _reg_template_err:
    import sys as _sys
    _sys.stderr.write(f"[app] template routes not registered: {_reg_template_err}\n")

try:
    from . import app_dedup
    app_dedup.register_routes(app)
except Exception as _reg_dedup_err:
    import sys as _sys
    _sys.stderr.write(f"[app] dedup routes not registered: {_reg_dedup_err}\n")

try:
    from . import app_import
    app_import.register_routes(app)
except Exception as _reg_import_err:
    import sys as _sys
    _sys.stderr.write(f"[app] import routes not registered: {_reg_import_err}\n")

try:
    from . import app_community_scrapers
    app_community_scrapers.register_routes(app)
except Exception as _reg_community_scrapers_err:
    import sys as _sys
    _sys.stderr.write(f"[app] community_scrapers routes not registered: {_reg_community_scrapers_err}\n")

try:
    from . import app_user_templates
    app_user_templates.register_routes(app)
except Exception as _reg_user_templates_err:
    import sys as _sys
    _sys.stderr.write(f"[app] user_templates routes not registered: {_reg_user_templates_err}\n")

try:
    from . import app_crash_recovery
    app_crash_recovery.register_routes(app)
except Exception as _reg_crash_recovery_err:
    import sys as _sys
    _sys.stderr.write(f"[app] crash_recovery routes not registered: {_reg_crash_recovery_err}\n")

try:
    from . import app_knowledge
    app_knowledge.register_routes(app)
except Exception as _reg_knowledge_err:
    import sys as _sys
    _sys.stderr.write(f"[app] knowledge routes not registered: {_reg_knowledge_err}\n")

try:
    from . import app_analyzer
    app_analyzer.register_routes(app)
except Exception as _reg_analyzer_err:
    import sys as _sys
    _sys.stderr.write(f"[app] analyzer routes not registered: {_reg_analyzer_err}\n")

try:
    from . import app_fed
    app_fed.register_routes(app)
except Exception as _reg_fed_err:
    import sys as _sys
    _sys.stderr.write(f"[app] fed routes not registered: {_reg_fed_err}\n")

try:
    from . import app_push
    app_push.register_routes(app)
except Exception as _reg_push_err:
    import sys as _sys
    _sys.stderr.write(f"[app] push routes not registered: {_reg_push_err}\n")

try:
    from . import app_rights
    app_rights.register_routes(app)
except Exception as _reg_rights_err:
    import sys as _sys
    _sys.stderr.write(f"[app] rights routes not registered: {_reg_rights_err}\n")

try:
    from . import app_saved_searches
    app_saved_searches.register_routes(app)
except Exception as _reg_saved_searches_err:
    import sys as _sys
    _sys.stderr.write(f"[app] saved_searches routes not registered: {_reg_saved_searches_err}\n")

try:
    from . import app_macros
    app_macros.register_routes(app)
except Exception as _reg_macros_err:
    import sys as _sys
    _sys.stderr.write(f"[app] macros routes not registered: {_reg_macros_err}\n")

try:
    from . import app_tags
    app_tags.register_routes(app)
    # v3.66.717 (Cut 7): the exec bridge -- the ONE validated, allowlisted seam that lets
    # a GUI value reach a tool's argument. tools_exec_bridged was 0; this is the seam.
    try:
        from . import app_tool_bridge
        app_tool_bridge.register_routes(app)
    except Exception as _reg_tool_bridge_err:
        import sys as _sys
        _sys.stderr.write(f"[app] tool_bridge routes not registered: {_reg_tool_bridge_err}\n")
    # v3.66.723 (AF5): the automation READOUT. The 706 rehearsal verdict and the 708
    # pipeline halt both fired into the void -- persisted-but-unread, and not persisted
    # at all, respectively. A safety net whose state you cannot see is not a safety net.
    try:
        from . import app_automation_status
        app_automation_status.register_routes(app)
    except Exception as _reg_auto_status_err:
        import sys as _sys
        _sys.stderr.write(
            f"[app] automation_status routes not registered: {_reg_auto_status_err}\n")
except Exception as _reg_tags_err:
    import sys as _sys
    _sys.stderr.write(f"[app] tags routes not registered: {_reg_tags_err}\n")

# v3.43.70: apprise notifications wiring + Flask routes
try:
    from . import notify_apprise as _notify_apprise
    _NOTIFY_APPRISE_AVAILABLE = True
except Exception as _napp_err:
    import sys as _sys
    _sys.stderr.write(f"[app] notify_apprise import failed: {_napp_err}\n")
    _notify_apprise = None
    _NOTIFY_APPRISE_AVAILABLE = False


def _global_notify_settings_path() -> Path:
    """Where the GLOBAL apprise settings live (not per-site).
    Stored separately so they survive site deletions and they're not
    duplicated per-site."""
    return Path("notify_apprise.json")


def _load_global_notify_settings() -> dict:
    """Load global apprise settings from disk. Fail-open."""
    p = _global_notify_settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_global_notify_settings(d: dict) -> bool:
    """Persist global apprise settings. Fail-open. Atomic write so a
    crash mid-write can't corrupt the settings file."""
    try:
        path = _global_notify_settings_path()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f"[app] save_global_notify_settings: {e}\n")
        return False


def _apply_global_notify_config():
    """Push the saved settings into the dispatcher singleton."""
    if not _NOTIFY_APPRISE_AVAILABLE or _notify_apprise is None:
        return
    cfg = _load_global_notify_settings()
    urls = _notify_apprise.parse_urls_text(
        cfg.get("notify_apprise_urls", "") or "")
    enabled = bool(cfg.get("notify_apprise_enabled", False))
    overrides = _notify_apprise.policy_from_config(cfg)
    try:
        d = _notify_apprise.get_dispatcher()
        d.configure(urls=urls, enabled=enabled,
                    policy_overrides=overrides)
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f"[app] apply_global_notify_config: {e}\n")


# Apply on startup (idempotent — does nothing if settings file is empty)
try:
    _apply_global_notify_config()
    # Fire a startup event if enabled (helpful confirmation for the user
    # that notifications are working).
    if _NOTIFY_APPRISE_AVAILABLE and _notify_apprise is not None:
        try:
            _notify_apprise.get_dispatcher().notify(
                _notify_apprise.EVENT_SERVER_START,
                "BulkDownloader started",
                "Server is up and ready to download.",
            )
        except Exception:
            pass
except Exception as _napp_init_err:
    import sys as _sys
    _sys.stderr.write(f"[app] apprise init: {_napp_init_err}\n")


# api_notify_apprise_settings_get -> app_notify.py (Phase 4 multi-block extraction)
# api_notify_apprise_settings_post -> app_notify.py (Phase 4 multi-block extraction)
# api_notify_apprise_validate -> app_notify.py (Phase 4 multi-block extraction)
# ── v3.58 (Phase 10, #91): notification presets ─────────────────────────
# apprise URL syntax is powerful but cryptic — nobody remembers that
# Pushover is `pover://user@token`. These presets give the UI a
# fill-in-the-blanks template for the services operators actually use,
# so adding a notification target is "pick Discord, paste two values"
# rather than "read the apprise wiki".

# _NOTIFY_PRESETS -> app_notify.py (Phase 4 group-owned constant)


# api_notify_presets -> app_notify.py (Phase 4 multi-block extraction)
# api_notify_apprise_test -> app_notify.py (Phase 4 multi-block extraction)
# api_notify_apprise_schemes -> app_notify.py (Phase 4 multi-block extraction)
# v3.43.71: Telegram bot remote-control wiring + Flask routes
try:
    from . import tg_bot as _tg_bot
    _TG_BOT_AVAILABLE = True
except Exception as _tg_err:
    import sys as _sys
    _sys.stderr.write(f"[app] tg_bot import failed: {_tg_err}\n")
    _tg_bot = None
    _TG_BOT_AVAILABLE = False


def _tg_get_status():
    """Callback for /status: build the site overview dict."""
    out = {}
    for sid, cfg in s_cfg.items():
        runner = runners.get(sid)
        # Counts come from the runner's job state if available, else
        # the cfg-stored queue.
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0}
        state = "?"
        if runner is not None:
            try:
                for j in (cfg.get("queue") or []):
                    st = (j.get("status") or "pending").lower()
                    if st in counts:
                        counts[st] += 1
                    elif st == "stopped":
                        counts["pending"] += 1
                state = "running" if getattr(runner, "_running", False) else \
                        ("paused" if getattr(runner, "_paused", False) else "idle")
            except Exception:
                pass
        out[sid] = {
            "name": cfg.get("name", sid),
            "state": state,
            "counts": counts,
        }
    return out


def _tg_get_queue(sid):
    """Callback for /queue: return the queue for a site."""
    cfg = s_cfg.get(sid)
    if cfg is None:
        return []
    return list(cfg.get("queue") or [])


def _tg_add_url(url):
    """Callback for /mirror: auto-route and add."""
    try:
        summary, unrouted = _route_urls_internal([url])
        if unrouted:
            return {"ok": False, "error": "no matching site for this URL"}
        if not summary:
            return {"ok": False, "error": "routing returned no summary"}
        sid = list(summary.keys())[0]
        added = summary[sid].get("added", 0)
        if added > 0:
            return {"ok": True, "site_id": sid}
        return {"ok": False, "error": "URL already in queue (duplicate)"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _tg_cancel_url(url):
    """Callback for /cancel: find the matching pending job and mark it
    cancelled."""
    for sid, cfg in s_cfg.items():
        for j in (cfg.get("queue") or []):
            if j.get("url") == url and j.get("status") == "pending":
                j["status"] = "stopped"
                _persist_cfg()
                return {"ok": True, "site_id": sid}
    return {"ok": False, "error": "URL not found in any pending queue"}


def _tg_retry_site(sid):
    """Callback for /retry: reset failed→pending in one site or all."""
    target_sids = []
    if sid == "all":
        target_sids = list(s_cfg.keys())
    elif sid in s_cfg:
        target_sids = [sid]
    else:
        return {"ok": False, "error": f"unknown site {sid!r}"}
    count = 0
    for sid_t in target_sids:
        cfg = s_cfg.get(sid_t)
        if not cfg:
            continue
        for j in (cfg.get("queue") or []):
            if j.get("status") == "failed":
                j["status"] = "pending"
                count += 1
    if count > 0:
        _persist_cfg()
    return {"ok": True, "count": count}


def _tg_pause_site(sid):
    """Callback for /pause: pause one site or all."""
    target_sids = list(s_cfg.keys()) if sid == "all" else (
        [sid] if sid in s_cfg else [])
    if not target_sids:
        return {"ok": False, "error": f"unknown site {sid!r}"}
    for sid_t in target_sids:
        runner = runners.get(sid_t)
        if runner is not None and hasattr(runner, "pause"):
            try:
                runner.pause()
            except Exception:
                pass
    return {"ok": True, "site_ids": target_sids}


def _tg_resume_site(sid):
    """Callback for /resume: resume one site or all."""
    target_sids = list(s_cfg.keys()) if sid == "all" else (
        [sid] if sid in s_cfg else [])
    if not target_sids:
        return {"ok": False, "error": f"unknown site {sid!r}"}
    for sid_t in target_sids:
        runner = runners.get(sid_t)
        if runner is not None and hasattr(runner, "start"):
            try:
                runner.start()
            except Exception:
                pass
    return {"ok": True, "site_ids": target_sids}


def _persist_cfg():
    """Helper to save s_cfg back to disk.

    v3.47.8 (#43): delegate to the canonical atomic _save_sites_config()
    instead of doing a direct json.dump. The old implementation here
    bypassed the tmp-write-then-replace pattern, leaving a window where
    a crash could truncate sites_config.json (the file that contains
    every site definition + credentials). Same atomicity contract as
    the rest of the codebase now.
    """
    try:
        _save_sites_config()
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f"[app] persist_cfg: {e}\n")


def _apply_tg_bot_config():
    """Push saved settings into the bot singleton."""
    if not _TG_BOT_AVAILABLE or _tg_bot is None:
        return
    cfg = _load_global_notify_settings()
    try:
        bot = _tg_bot.get_bot()
        bot.set_callbacks(
            get_status=_tg_get_status,
            get_queue=_tg_get_queue,
            add_url=_tg_add_url,
            cancel_url=_tg_cancel_url,
            retry_site=_tg_retry_site,
            pause_site=_tg_pause_site,
            resume_site=_tg_resume_site,
        )
        allowlist = _tg_bot.parse_allowlist(
            cfg.get("tg_bot_allowlist", "") or "")
        bot.configure(
            token=str(cfg.get("tg_bot_token", "") or ""),
            allowlist=allowlist,
            enabled=bool(cfg.get("tg_bot_enabled", False)),
        )
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f"[app] apply_tg_bot_config: {e}\n")


# Apply on startup
try:
    if os.environ.get("BD_DISABLE_KEEPALIVE", "").lower() not in (
        "1", "true", "yes"):
        _apply_tg_bot_config()
except Exception as _tg_init_err:
    import sys as _sys
    _sys.stderr.write(f"[app] tg_bot init: {_tg_init_err}\n")


# /api/tg -> app_tg.py (Phase 4 thin-core-shell extraction)
# v3.43.72: perceptual dedup Flask routes
try:
    from . import dedup as _dedup
    _DEDUP_AVAILABLE = True
except Exception as _dd_err:
    import sys as _sys
    _sys.stderr.write(f"[app] dedup import failed: {_dd_err}\n")
    _dedup = None
    _DEDUP_AVAILABLE = False


# Background scan state — global so multiple scan calls don't pile up


def _dedup_get_registry():
    """Get/create the singleton registry. Picks DB path from any site's
    config (they should all share one), defaulting to video_hashes.db."""
    if not (_DEDUP_AVAILABLE and _dedup is not None):
        return None
    db_path = "video_hashes.db"
    for sid, cfg in s_cfg.items():
        v = cfg.get("dedup_db_path")
        if v:
            db_path = v
            break
    try:
        return _dedup.get_default_registry(db_path)
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f"[app] dedup registry init failed: {e}\n")
        return None


# /api/dedup -> app_dedup.py (Phase 4 thin-core-shell extraction)
# v3.43.73: Scrapling adaptive selectors + Turnstile bypass routes
try:
    from . import scrapling_adapter as _scrap_adapter
    _SCRAP_AVAILABLE = True
except Exception as _scrap_err:
    import sys as _sys
    _sys.stderr.write(f"[app] scrapling_adapter import failed: {_scrap_err}\n")
    _scrap_adapter = None
    _SCRAP_AVAILABLE = False


# /api/scrapling -> app_scrapling.py (Phase 4 thin-core-shell extraction)
# v3.43.74: FlareSolverr proxy client routes
try:
    from . import flaresolverr_client as _flare_client
    _FLARE_AVAILABLE = True
except Exception as _fc_err:
    import sys as _sys
    _sys.stderr.write(f"[app] flaresolverr_client import failed: {_fc_err}\n")
    _flare_client = None
    _FLARE_AVAILABLE = False


# /api/flaresolverr -> app_flaresolverr.py (Phase 4 thin-core-shell extraction)
# v3.43.74: Multi-connection downloader routes
try:
    from . import multi_conn as _multi_conn
    _MULTI_CONN_AVAILABLE = True
except Exception as _mc_err:
    import sys as _sys
    _sys.stderr.write(f"[app] multi_conn import failed: {_mc_err}\n")
    _multi_conn = None
    _MULTI_CONN_AVAILABLE = False


# /api/multi_conn -> app_multi_conn.py (Phase 4 thin-core-shell extraction)
# v3.43.75: four-feature Flask routes
try:
    from . import yt_dlp_archive as _ytdlp_archive_module
    _YTDLP_ARCH_AVAILABLE = True
except Exception as _ya_err:
    import sys as _sys
    _sys.stderr.write(f"[app] yt_dlp_archive import failed: {_ya_err}\n")
    _ytdlp_archive_module = None
    _YTDLP_ARCH_AVAILABLE = False

try:
    from . import community_scrapers as _community_scrapers
    _COMMUNITY_SCRAPERS_AVAILABLE = True
except Exception as _cs_err:
    import sys as _sys
    _sys.stderr.write(f"[app] community_scrapers import failed: {_cs_err}\n")
    _community_scrapers = None
    _COMMUNITY_SCRAPERS_AVAILABLE = False


# /api/ytdlp_archive -> app_ytdlp_archive.py (Phase 4 thin-core-shell extraction)
# /api/community_scrapers -> app_community_scrapers.py (Phase 4 thin-core-shell extraction)
# v3.43.76: Phoenix catalog + supervisor + thumbnail routes
try:
    from . import phoenix_catalog as _phoenix_cat
    _PHOENIX_AVAILABLE = True
except Exception as _pcerr:
    import sys as _sys
    _sys.stderr.write(f"[app] phoenix_catalog import failed: {_pcerr}\n")
    _phoenix_cat = None
    _PHOENIX_AVAILABLE = False

try:
    from . import download_supervisor as _supervisor_mod
    _SUPERVISOR_AVAILABLE = True
except Exception as _spvrerr:
    import sys as _sys
    _sys.stderr.write(f"[app] download_supervisor import failed: {_spvrerr}\n")
    _supervisor_mod = None
    _SUPERVISOR_AVAILABLE = False

try:
    from . import thumbnail_gen as _thumb_mod
    _THUMB_AVAILABLE = True
except Exception as _thumberr:
    import sys as _sys
    _sys.stderr.write(f"[app] thumbnail_gen import failed: {_thumberr}\n")
    _thumb_mod = None
    _THUMB_AVAILABLE = False


# /api/phoenix -> app_phoenix.py (Phase 4 thin-core-shell extraction)
# /api/supervisor -> app_supervisor.py (Phase 4 thin-core-shell extraction)
# /api/thumbnails -> app_thumbnails.py (Phase 4 thin-core-shell extraction)
# v3.43.77: Search-and-add by query
try:
    from . import search_extractor as _search_mod
    _SEARCH_AVAILABLE = True
except Exception as _se_err:
    import sys as _sys
    _sys.stderr.write(f"[app] search_extractor import failed: {_se_err}\n")
    _search_mod = None
    _SEARCH_AVAILABLE = False


def _serialize_search_result(r) -> dict:
    """Convert a SearchResult dataclass to a JSON-friendly dict."""
    if r is None:
        return {"ok": False, "error": "no_result"}
    return {
        "ok": r.ok,
        "site_id": r.site_id,
        "query": r.query,
        "search_url": r.search_url,
        "hits": [
            {"url": h.url, "title": h.title,
             "thumbnail_url": h.thumbnail_url,
             "duration_s": h.duration_s}
            for h in r.hits
        ],
        "count": r.count,
        "elapsed_s": round(r.elapsed_s, 2),
        "error": r.error,
    }


# api_search_site -> app_search.py (Phase 4 multi-block extraction)


# api_search_all -> app_search.py (Phase 4 multi-block extraction)


# api_search_sites_available -> app_search.py (Phase 4 multi-block extraction)
