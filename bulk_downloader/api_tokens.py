"""Scoped programmatic API tokens (v3.66.227, Phase F4.3).

Distinct from the read-only *share* tokens in ``shares.py``. Share tokens
are deliberately read-only and method-restricted to GET/HEAD/OPTIONS — a
"peek, not the keys". This module mints tokens that may carry **write**
capability, so it is kept entirely separate:

  * its own table        (``api_auth_tokens``)
  * its own signing secret (``api_auth_token_secret`` in global_config)
  * its own verify path   (never reachable through the share/vault gates)

Scope hierarchy (a token carries exactly one level; higher includes lower):

  read    (1)  — read-only data endpoints
  enqueue (2)  — read + add URLs to the download queue
  admin   (3)  — enqueue + destructive/management endpoints

All three defined scopes are mintable.  ``admin`` is deliberately bounded by
the same explicit route policy as lower scopes; it reaches the listed
destructive/management endpoints, including token issue/list/revoke, but does
not turn an API token into an unrestricted operator session.

ENFORCEMENT IS FAIL-CLOSED and lives in ``app._check_token``: a valid API
token may reach ONLY the (route, method) pairs explicitly enumerated in
``app._API_TOKEN_ROUTE_POLICY`` for its scope level. Any other route, any
method not listed, or insufficient scope → 403. A newly added route is NOT
reachable by an API token until it is deliberately added to that policy.

Wire format (prefix makes it unmistakable vs the master bearer / share
tokens):  ``bdapi_<token_id>.<nonce>.<sig>``
where ``sig = HMAC-SHA256(secret, "<token_id>.<nonce>")[:24]``.

Tokens are HMAC-signed and stateful (revocable via DB). The full token
value is returned exactly once, at creation; thereafter only metadata is
ever exposed (F2 posture — never echo the secret).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sys
import time
from typing import Optional

PREFIX = "bdapi_"

# Single source of truth for the scope taxonomy + ordering.
SCOPES = {"read": 1, "enqueue": 2, "admin": 3}

# Every named capability can be issued.  Keep the derived reserved set as the
# compatibility surface for callers that inspect it and as an explicit future
# extension point; it is empty while every SCOPES entry is mintable.
MINTABLE_SCOPES = set(SCOPES)
RESERVED_SCOPES = set(SCOPES) - MINTABLE_SCOPES

# Per-token rate limit is intentionally NOT implemented here; the surface is
# a trusted-LAN operator tool and the master bearer / session paths are
# unmetered. Add a token-bucket keyed on token_id if metering is ever needed.


def scope_level(scope: str) -> int:
    """Numeric level for a scope name; 0 for unknown (deny-by-default)."""
    return SCOPES.get(scope, 0)


def _ensure_tables() -> None:
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS api_auth_tokens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT UNIQUE NOT NULL,
                label TEXT DEFAULT '',
                scope TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                revoked_at REAL,
                last_used_at REAL DEFAULT 0,
                use_count INTEGER DEFAULT 0
            )""")
            cx.execute("""CREATE TABLE IF NOT EXISTS api_auth_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                token_id TEXT NOT NULL,
                path TEXT NOT NULL,
                method TEXT DEFAULT '',
                scope TEXT,
                allowed INTEGER NOT NULL,
                client_ip TEXT,
                reason TEXT DEFAULT ''
            )""")
    except Exception as e:  # pragma: no cover - schema init best-effort
        sys.stderr.write(f"[api_tokens] schema init: {e}\n")


def _signing_secret() -> str:
    """Get/create the API-token signing secret (separate from shares)."""
    try:
        from .global_config import get_config, set_config
        cfg = get_config() or {}
        sec = cfg.get("api_auth_token_secret", "")
        if sec:
            return sec
        sec = secrets.token_urlsafe(32)
        try:
            set_config({**cfg, "api_auth_token_secret": sec})
        except Exception:
            pass
        return sec
    except Exception:
        if not hasattr(_signing_secret, "_fb"):
            _signing_secret._fb = secrets.token_urlsafe(32)  # type: ignore
        return _signing_secret._fb  # type: ignore


def _log(token_id: str, path: str, method: str, scope: Optional[str],
         allowed: bool, client_ip: str, reason: str) -> None:
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute(
                """INSERT INTO api_auth_log(
                    ts, token_id, path, method, scope, allowed,
                    client_ip, reason
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (time.time(), token_id, path[:300], method[:10],
                 scope, 1 if allowed else 0, client_ip[:64], reason[:120]),
            )
    except Exception:
        pass


def create_token(*, scope: str, label: str = "",
                 ttl_hours: Optional[int] = None) -> dict:
    """Mint a scoped API token.

    Returns {ok, token, token_id, scope, expires_at} on success. The
    ``token`` field is the only time the full secret is exposed.
    """
    _ensure_tables()
    if scope not in SCOPES:
        return {"ok": False,
                "error": "scope must be one of: read, enqueue, admin"}
    if scope in RESERVED_SCOPES:
        return {"ok": False,
                "error": f"scope '{scope}' is reserved and cannot be minted"}
    try:
        ttl = int(ttl_hours) if ttl_hours not in (None, "") else None
        if ttl is not None and ttl <= 0:
            ttl = None
    except (TypeError, ValueError):
        return {"ok": False, "error": "ttl_hours must be an integer"}

    token_id = secrets.token_urlsafe(8)
    nonce = secrets.token_urlsafe(16)
    expires_at = (time.time() + ttl * 3600) if ttl else None
    secret = _signing_secret()
    sig_body = f"{token_id}.{nonce}"
    sig = hmac.new(secret.encode("utf-8"), sig_body.encode("utf-8"),
                   hashlib.sha256).hexdigest()[:24]
    full_token = f"{PREFIX}{sig_body}.{sig}"
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute(
                """INSERT INTO api_auth_tokens(
                    token_id, label, scope, created_at, expires_at
                ) VALUES (?,?,?,?,?)""",
                (token_id, label[:200], scope, time.time(), expires_at),
            )
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {
        "ok": True,
        "token": full_token,
        "token_id": token_id,
        "scope": scope,
        "expires_at": expires_at,
    }


def revoke_token(token_id: str) -> bool:
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute(
                """UPDATE api_auth_tokens
                   SET revoked_at = ?
                   WHERE token_id = ? AND revoked_at IS NULL""",
                (time.time(), token_id),
            )
            return cur.rowcount > 0
    except Exception:
        return False


def list_tokens() -> list:
    """Metadata only — never the token secret."""
    _ensure_tables()
    out = []
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute(
                """SELECT token_id, label, scope, created_at, expires_at,
                          revoked_at, last_used_at, use_count
                   FROM api_auth_tokens
                   ORDER BY created_at DESC"""
            ).fetchall()
        now = time.time()
        for r in rows:
            (tid, label, scope, created_at, expires_at,
             revoked_at, last_used_at, use_count) = r
            if revoked_at is not None:
                status = "revoked"
            elif expires_at is not None and expires_at < now:
                status = "expired"
            else:
                status = "active"
            out.append({
                "token_id": tid,
                "label": label or "",
                "scope": scope,
                "created_at": created_at,
                "expires_at": expires_at,
                "revoked_at": revoked_at,
                "last_used_at": last_used_at or 0,
                "use_count": use_count or 0,
                "status": status,
            })
    except Exception:
        pass
    return out


def verify_token(token: str, *, client_ip: str = "",
                 request_path: str = "", method: str = "") -> dict:
    """Validate a token (signature + DB state). Returns
    {ok, token_id, scope, scope_level, reason}.

    On success, bumps last_used_at / use_count. Does NOT check route
    authorization — that is the caller's policy decision (app gate).
    """
    _ensure_tables()
    if not token or not token.startswith(PREFIX):
        return {"ok": False, "reason": "not an api token"}
    body = token[len(PREFIX):]
    if body.count(".") != 2:
        return {"ok": False, "reason": "malformed token"}
    try:
        token_id, nonce, sig = body.split(".")
    except ValueError:
        return {"ok": False, "reason": "malformed token"}
    # Signature check first (cheap, no DB), constant-time.
    secret = _signing_secret()
    expected = hmac.new(secret.encode("utf-8"),
                        f"{token_id}.{nonce}".encode("utf-8"),
                        hashlib.sha256).hexdigest()[:24]
    if not hmac.compare_digest(expected, sig):
        _log(token_id, request_path, method, None, False, client_ip,
             "bad signature")
        return {"ok": False, "reason": "bad signature"}
    # DB state.
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                """SELECT scope, expires_at, revoked_at
                   FROM api_auth_tokens WHERE token_id = ?""",
                (token_id,),
            ).fetchone()
    except Exception as e:
        return {"ok": False, "reason": f"db error: {str(e)[:80]}"}
    if not row:
        _log(token_id, request_path, method, None, False, client_ip,
             "unknown token")
        return {"ok": False, "reason": "unknown token"}
    scope, expires_at, revoked_at = row
    if revoked_at is not None:
        _log(token_id, request_path, method, scope, False, client_ip,
             "revoked")
        return {"ok": False, "reason": "revoked"}
    if expires_at is not None and expires_at < time.time():
        _log(token_id, request_path, method, scope, False, client_ip,
             "expired")
        return {"ok": False, "reason": "expired"}
    # Valid. Bump usage (best-effort).
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute(
                """UPDATE api_auth_tokens
                   SET last_used_at = ?, use_count = use_count + 1
                   WHERE token_id = ?""",
                (time.time(), token_id),
            )
    except Exception:
        pass
    return {
        "ok": True,
        "token_id": token_id,
        "scope": scope,
        "scope_level": scope_level(scope),
        "reason": "",
    }


def record_decision(token_id: str, path: str, method: str, scope: str,
                    allowed: bool, client_ip: str, reason: str) -> None:
    """Public hook for the app gate to log an authorization decision
    (separate from verify so a scope-denial is also auditable)."""
    _log(token_id, path, method, scope, allowed, client_ip, reason)
