"""Read-only sharing tokens (Phase 138, Block Q).

Operator wants to give a partner / collaborator a peek at the
dashboard — but not the keys to the kingdom. This module mints
revocable, scoped tokens that grant read access to a subset of the
API, with optional time and IP limits.

Token grants — pick any combination at creation time:
  • all_stats        — read /api/status_all, /api/capacity, /metrics
  • recent_events    — read /api/events
  • library_browse   — read /api/history (but not file paths)
  • site_view:<sid>  — see one specific site's state

Tokens are HMAC-signed, stateful (revocable via DB), and all access is
logged. There is no per-token request-rate limit -- the surface is a
trusted-LAN operator tool; do not rely on a rate cap here.

Auth flow:
  client → GET /api/something?ro_token=<token>
       OR  Authorization: Bearer <token>
  middleware → verify_share_token(token, requested_scope) → ok/deny

Operator UI:
  • POST /api/shares  → create
  • GET  /api/shares  → list
  • DEL  /api/shares/<id> → revoke
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
import time
from typing import Optional


def _ensure_tables():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS share_tokens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT UNIQUE NOT NULL,
                label TEXT DEFAULT '',
                scopes TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                revoked_at REAL,
                ip_whitelist TEXT DEFAULT '',
                last_used_at REAL DEFAULT 0,
                use_count INTEGER DEFAULT 0
            )""")
            cx.execute("""CREATE TABLE IF NOT EXISTS share_access_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                token_id TEXT NOT NULL,
                path TEXT NOT NULL,
                scope TEXT,
                allowed INTEGER NOT NULL,
                client_ip TEXT,
                reason TEXT DEFAULT ''
            )""")
    except Exception as e:
        sys.stderr.write(f"[shares] schema init: {e}\n")


def _signing_secret() -> str:
    """Get/create the share-token signing secret."""
    try:
        from .global_config import get_config, set_config
        cfg = get_config() or {}
        sec = cfg.get("share_token_secret", "")
        if sec:
            return sec
        sec = secrets.token_urlsafe(32)
        try:
            set_config({**cfg, "share_token_secret": sec})
        except Exception:
            pass
        return sec
    except Exception:
        if not hasattr(_signing_secret, "_fb"):
            _signing_secret._fb = secrets.token_urlsafe(32)  # type: ignore
        return _signing_secret._fb  # type: ignore


def create_token(*, scopes: list, label: str = "",
                ttl_hours: Optional[int] = None,
                ip_whitelist: str = "") -> dict:
    """Mint a new share token. Returns
    {token, token_id, scopes, expires_at}."""
    _ensure_tables()
    if not scopes:
        return {"ok": False, "error": "scopes required"}
    token_id = secrets.token_urlsafe(8)  # short stable ID
    nonce = secrets.token_urlsafe(16)
    expires_at = (time.time() + ttl_hours * 3600) if ttl_hours else None
    secret = _signing_secret()
    # Token format: <token_id>.<nonce>.<sig>
    sig_body = f"{token_id}.{nonce}"
    sig = hmac.new(secret.encode("utf-8"), sig_body.encode("utf-8"),
                   hashlib.sha256).hexdigest()[:24]
    full_token = f"{sig_body}.{sig}"
    try:
        from . import db as _db
        import json
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO share_tokens(
                token_id, label, scopes, created_at, expires_at,
                ip_whitelist
            ) VALUES (?,?,?,?,?,?)""",
                (token_id, label[:200], json.dumps(scopes),
                 time.time(), expires_at, ip_whitelist[:500]))
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {
        "ok": True,
        "token": full_token,
        "token_id": token_id,
        "scopes": scopes,
        "expires_at": expires_at,
    }


def revoke_token(token_id: str) -> bool:
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""UPDATE share_tokens
                                SET revoked_at = ?
                                WHERE token_id = ? AND revoked_at IS NULL""",
                              (time.time(), token_id))
            return cur.rowcount > 0
    except Exception:
        return False


def verify_token(token: str, *, required_scope: str,
                client_ip: str = "", request_path: str = "") -> dict:
    """Validate a token + check the required scope. Returns
    {ok, token_id, reason}."""
    _ensure_tables()
    if not token or token.count(".") != 2:
        return {"ok": False, "reason": "malformed token"}
    try:
        token_id, nonce, sig = token.split(".")
    except ValueError:
        return {"ok": False, "reason": "malformed token"}
    # Signature check first (cheap, no DB)
    secret = _signing_secret()
    expected = hmac.new(secret.encode("utf-8"),
                        f"{token_id}.{nonce}".encode("utf-8"),
                        hashlib.sha256).hexdigest()[:24]
    if not hmac.compare_digest(expected, sig):
        _log_access(token_id, request_path, required_scope, False,
                   client_ip, "bad signature")
        return {"ok": False, "reason": "bad signature"}
    # DB lookup
    try:
        from . import db as _db
        import json
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT * FROM share_tokens
                                 WHERE token_id = ?""",
                              (token_id,)).fetchone()
        if not row:
            _log_access(token_id, request_path, required_scope, False,
                       client_ip, "no such token")
            return {"ok": False, "reason": "no such token"}
        d = dict(row)
        if d.get("revoked_at"):
            _log_access(token_id, request_path, required_scope, False,
                       client_ip, "revoked")
            return {"ok": False, "reason": "revoked"}
        if d.get("expires_at") and d["expires_at"] < time.time():
            _log_access(token_id, request_path, required_scope, False,
                       client_ip, "expired")
            return {"ok": False, "reason": "expired"}
        # IP whitelist -- fail CLOSED: a set whitelist denies a caller whose
        # IP is empty/unknown or not on the list.
        wl = d.get("ip_whitelist") or ""
        if wl:
            allowed_ips = {ip.strip() for ip in wl.split(",") if ip.strip()}
            if not client_ip or client_ip not in allowed_ips:
                _log_access(token_id, request_path, required_scope, False,
                           client_ip, "ip not allowed")
                return {"ok": False, "reason": "ip not allowed"}
        # Scope check
        try:
            scopes = json.loads(d.get("scopes") or "[]")
        except Exception:
            scopes = []
        if required_scope not in scopes:
            # Also allow scope: 'all' as super-scope
            if "all" not in scopes:
                _log_access(token_id, request_path, required_scope, False,
                           client_ip, "scope denied")
                return {"ok": False, "reason": "scope denied"}
        # Bump use stats
        with _db.db_conn() as cx:
            cx.execute("""UPDATE share_tokens
                          SET last_used_at = ?, use_count = use_count + 1
                          WHERE token_id = ?""",
                       (time.time(), token_id))
        _log_access(token_id, request_path, required_scope, True,
                   client_ip, "")
        return {"ok": True, "token_id": token_id,
                "label": d.get("label", "")}
    except Exception as e:
        return {"ok": False, "reason": f"DB error: {e}"}


def _log_access(token_id: str, path: str, scope: str,
               allowed: bool, client_ip: str, reason: str):
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO share_access_log(
                ts, token_id, path, scope, allowed, client_ip, reason
            ) VALUES (?,?,?,?,?,?,?)""",
                (time.time(), token_id, path[:200], scope,
                 1 if allowed else 0, client_ip[:64], reason[:200]))
    except Exception:
        pass


def list_tokens() -> list:
    _ensure_tables()
    try:
        from . import db as _db
        import json
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM share_tokens
                                  ORDER BY created_at DESC""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["scopes"] = json.loads(d.get("scopes") or "[]")
            except Exception:
                d["scopes"] = []
            d["status"] = ("revoked" if d.get("revoked_at")
                           else ("expired"
                                 if d.get("expires_at") and
                                 d["expires_at"] < time.time()
                                 else "active"))
            out.append(d)
        return out
    except Exception:
        return []


def recent_access(*, limit: int = 100) -> list:
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM share_access_log
                                  ORDER BY id DESC LIMIT ?""",
                              (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# Recognized scopes — documented surface for the UI
KNOWN_SCOPES = {
    "all_stats": "Read aggregate stats: /api/status_all, /api/capacity",
    "recent_events": "Read /api/events (last events)",
    "library_browse": "Read /api/history (no file paths exposed)",
    "metrics": "Read /metrics for monitoring",
    "all": "Super-scope — grants all of the above",
}


# Path → required scope mapping. Only the paths listed here are
# share-accessible; everything else returns 401 even with a valid
# token. ORDER MATTERS: prefix matches are tried in order, first wins.
# Add new entries cautiously — anything write-shaped should NOT be here.
SHARE_ROUTES = [
    # (path_prefix, required_scope)
    ("/api/status", "all_stats"),
    ("/api/capacity", "all_stats"),
    ("/api/health/checklist", "all_stats"),
    ("/api/events_all", "recent_events"),
    ("/api/ui_events", "recent_events"),
    ("/api/history", "library_browse"),
    ("/api/gamification/report", "all_stats"),
    ("/metrics", "metrics"),
]


def path_required_scope(path: str) -> Optional[str]:
    """Look up which scope a path needs. Returns None if path is
    not share-accessible (i.e. share tokens never grant it)."""
    if not path:
        return None
    for prefix, scope in SHARE_ROUTES:
        if path == prefix or path.startswith(prefix + "/")\
                or path.startswith(prefix + "?"):
            return scope
    return None


def check_share_access(token: str, path: str, method: str,
                      client_ip: str = "") -> dict:
    """Middleware helper. Returns {ok, reason} for whether a share
    token grants access to (method, path).

    Rules:
      • Only GET / HEAD / OPTIONS are share-accessible (read-only).
      • Path must be in SHARE_ROUTES.
      • Token must verify + have the required scope.
      • IP whitelist enforced if set.
    """
    if method not in ("GET", "HEAD", "OPTIONS"):
        return {"ok": False, "reason": "share tokens are read-only"}
    scope = path_required_scope(path)
    if scope is None:
        return {"ok": False, "reason": "path not share-accessible"}
    return verify_token(token, required_scope=scope,
                       client_ip=client_ip,
                       request_path=path)
