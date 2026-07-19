"""bulk_downloader.user_accounts -- Cut 625 / C7 sub-wave 11.1a: accounts + roles.

A self-contained multi-user identity + role store. Roles form a total order
``admin > reviewer > operator``; only ``reviewer`` and ``admin`` may promote a
template (the review-only gate the automation program assumes). Passwords are
stored as salted PBKDF2-HMAC-SHA256 (stdlib, no dependency). A ``bd_user`` session
is a signed, expiring token (HMAC over ``username.exp`` with a per-store signing
key) -- deliberately a PARALLEL layer, NOT entangled with the existing
``bd_session`` pairing cookie in app.py's auth hot path, so this cut changes no
existing auth behaviour.

**Default-OFF / backward-compatible.** ``multi_user_enabled`` reads the
``multi_user`` block of ``app_config.json`` and defaults False. With multi-user
off (the single-operator default) the promote gate is a no-op and every current
behaviour is byte-identical. Role enforcement activates only when the operator
creates accounts AND turns it on.

Storage: ``accounts.json`` next to ``sites_config.json``::

    {"users": {"<name>": {"pw_hash", "salt", "iters", "role", "created_ts"}},
     "signing_key": "<hex>"}

All read paths never raise; passwords are never stored or returned in the clear.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time

ACCOUNTS_FILE = "user_accounts.json"
ROLES = ("admin", "reviewer", "operator")
_PROMOTE_ROLES = ("admin", "reviewer")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_PBKDF2_ITERS = 200_000
_SESSION_TTL = 12 * 3600  # 12h default


def _store_path(base_dir: str | os.PathLike | None = None) -> str:
    base = str(base_dir) if base_dir else "."
    return os.path.join(base, ACCOUNTS_FILE)


def _load(base_dir=None) -> dict:
    try:
        with open(_store_path(base_dir), "r") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return {"users": {}, "signing_key": ""}
        doc.setdefault("users", {})
        doc.setdefault("signing_key", "")
        if not isinstance(doc["users"], dict):
            doc["users"] = {}
        return doc
    except (OSError, ValueError):
        return {"users": {}, "signing_key": ""}


def _save(doc: dict, base_dir=None) -> bool:
    path = _store_path(base_dir)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)  # secrets file: owner-only
        except OSError:
            pass
        return True
    except OSError:
        return False


def _signing_key(doc: dict, base_dir=None) -> str:
    """Return the store's HMAC signing key, generating + persisting one on first
    use."""
    key = doc.get("signing_key")
    if not key:
        key = secrets.token_hex(32)
        doc["signing_key"] = key
        _save(doc, base_dir)
    return key


# ── password hashing (PBKDF2) ───────────────────────────────────────────

def _hash_password(password: str, salt: str, iters: int) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), iters)
    return dk.hex()


# ── CRUD ────────────────────────────────────────────────────────────────

def create_user(username: str, password: str, role: str = "operator",
                base_dir: str | os.PathLike | None = None) -> tuple[bool, str]:
    username = (username or "").strip()
    if not _NAME_RE.match(username):
        return False, "username must be 1-64 chars of [A-Za-z0-9_.-], no spaces"
    if not password:
        return False, "password must be non-empty"
    if role not in ROLES:
        return False, f"role must be one of {ROLES}"
    doc = _load(base_dir)
    if username in doc["users"]:
        return False, "user already exists"
    salt = secrets.token_hex(16)
    doc["users"][username] = {
        "pw_hash": _hash_password(password, salt, _PBKDF2_ITERS),
        "salt": salt, "iters": _PBKDF2_ITERS, "role": role,
        "created_ts": int(time.time()),
    }
    _signing_key(doc, base_dir)  # ensure a key exists
    if not _save(doc, base_dir):
        return False, "could not write accounts store"
    return True, "created"


def verify_password(username: str, password: str,
                    base_dir: str | os.PathLike | None = None) -> bool:
    rec = _load(base_dir)["users"].get((username or "").strip())
    if not isinstance(rec, dict):
        return False
    try:
        got = _hash_password(password or "", rec.get("salt", ""),
                             int(rec.get("iters", _PBKDF2_ITERS)))
        return hmac.compare_digest(got, rec.get("pw_hash", ""))
    except Exception:
        return False


def get_user(username: str,
             base_dir: str | os.PathLike | None = None) -> dict | None:
    rec = _load(base_dir)["users"].get((username or "").strip())
    if not isinstance(rec, dict):
        return None
    return {"username": (username or "").strip(), "role": rec.get("role", "operator"),
            "created_ts": rec.get("created_ts")}


def list_users(base_dir: str | os.PathLike | None = None) -> list[dict]:
    out = []
    for name, rec in _load(base_dir)["users"].items():
        if isinstance(rec, dict):
            out.append({"username": name, "role": rec.get("role", "operator"),
                        "created_ts": rec.get("created_ts")})
    return sorted(out, key=lambda r: r["username"])


def set_role(username: str, role: str,
             base_dir: str | os.PathLike | None = None) -> tuple[bool, str]:
    if role not in ROLES:
        return False, f"role must be one of {ROLES}"
    doc = _load(base_dir)
    name = (username or "").strip()
    if name not in doc["users"]:
        return False, "no such user"
    doc["users"][name]["role"] = role
    return (True, "updated") if _save(doc, base_dir) else (False, "write failed")


def set_password(username: str, new_password: str,
                 base_dir: str | os.PathLike | None = None) -> tuple[bool, str]:
    if not new_password:
        return False, "password must be non-empty"
    doc = _load(base_dir)
    name = (username or "").strip()
    if name not in doc["users"]:
        return False, "no such user"
    salt = secrets.token_hex(16)
    doc["users"][name].update({
        "pw_hash": _hash_password(new_password, salt, _PBKDF2_ITERS),
        "salt": salt, "iters": _PBKDF2_ITERS,
    })
    return (True, "updated") if _save(doc, base_dir) else (False, "write failed")


def delete_user(username: str,
                base_dir: str | os.PathLike | None = None) -> bool:
    doc = _load(base_dir)
    name = (username or "").strip()
    if name in doc["users"]:
        del doc["users"][name]
        return _save(doc, base_dir)
    return False


def count(base_dir: str | os.PathLike | None = None) -> int:
    return len(_load(base_dir)["users"])


# ── roles ───────────────────────────────────────────────────────────────

def _can_promote_role(role: str) -> bool:
    return role in _PROMOTE_ROLES


def user_can_promote(username: str,
                     base_dir: str | os.PathLike | None = None) -> bool:
    u = get_user(username, base_dir)
    return bool(u and _can_promote_role(u.get("role", "")))


# ── signed sessions (bd_user cookie) ────────────────────────────────────

def issue_session(username: str, ttl_seconds: int = _SESSION_TTL,
                  base_dir: str | os.PathLike | None = None) -> str:
    """Return a signed session token ``<username>.<exp>.<sig>`` for the browser's
    ``bd_user`` cookie. Never raises."""
    doc = _load(base_dir)
    key = _signing_key(doc, base_dir)
    exp = int(time.time()) + int(ttl_seconds)
    msg = f"{username}.{exp}"
    sig = hmac.new(bytes.fromhex(key), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def verify_session(token: str,
                   base_dir: str | os.PathLike | None = None) -> dict | None:
    """Validate a ``bd_user`` token: signature intact, not expired, and the user
    still exists. Returns ``{username, role}`` or ``None``. Never raises."""
    try:
        parts = (token or "").split(".")
        if len(parts) != 3:
            return None
        username, exp_s, sig = parts
        doc = _load(base_dir)
        key = doc.get("signing_key")
        if not key:
            return None
        expected = hmac.new(bytes.fromhex(key), f"{username}.{exp_s}".encode("utf-8"),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp_s) < int(time.time()):
            return None
        rec = doc["users"].get(username)
        if not isinstance(rec, dict):
            return None
        return {"username": username, "role": rec.get("role", "operator")}
    except Exception:
        return None


# ── enablement ──────────────────────────────────────────────────────────

def multi_user_enabled(base_dir: str | os.PathLike | None = None) -> bool:
    """Read the ``multi_user.enabled`` flag from ``app_config.json``. Defaults
    False -- with multi-user off the promote gate is a no-op and the single-
    operator experience is unchanged. Never raises."""
    base = str(base_dir) if base_dir else "."
    try:
        with open(os.path.join(base, "app_config.json"), "r") as fh:
            doc = json.load(fh)
        mu = doc.get("multi_user") if isinstance(doc, dict) else None
        return bool(mu.get("enabled", False)) if isinstance(mu, dict) else False
    except (OSError, ValueError):
        return False


def current_user_from_cookie(cookie_value: str,
                             base_dir: str | os.PathLike | None = None) -> dict | None:
    """Convenience for request handlers: resolve the ``bd_user`` cookie value to
    ``{username, role}`` or ``None``. Never raises."""
    return verify_session(cookie_value or "", base_dir)
