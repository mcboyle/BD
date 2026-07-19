"""Phase 16.43 — Web push notifications.

Self-contained module that wraps VAPID key generation, subscription
storage, and push sending via pywebpush. Optional: the rest of the app
must work even if pywebpush isn't installed. Public API:

  vapid_public_key()   -> str          # base64url-encoded server pubkey
  add_subscription(sub_dict, ua)       # store from JS PushSubscription.toJSON()
  remove_subscription(endpoint)        # tombstone
  list_subscriptions() -> list[dict]   # for /api/push/subscriptions
  send_push(title, body, url=None, tag=None) -> dict  # broadcast helper

iOS specifics: web push works on iOS 16.4+ but ONLY when the site is
added to the home screen. The browser's PushSubscription endpoint
points at Apple's APNs gateway which proxies to the device. The
subscription is bound to the home-screen instance, not Safari.
"""
import base64
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import db as _db

# v3.66.795 (MOD-3 cut 1): this module used to keep its own connection to
# downloader_history.db, with the note "kept independent so this module doesn't
# pull in the rest of the app at import time". Importing db does NOT do that --
# db.py is a leaf (stdlib + .constants), so `import db` adds only constants and
# _envfile. Independence is preserved; the parallel connection is not, because
# MOD-3 needs ONE interception point for the SQLite->Postgres migration.
#
# _DB_REL is kept: it is the same filename db.py resolves (constants.DB_PATH),
# and _db_path()/_resolve() still serve the VAPID key file and existing tests.
#
# v3.66.11: relative-path captures at module load time bound the path
# to whatever cwd happened to be at import. Tests that set
# BD_INSTALL_DIR (via the isolated_bd_home fixture) without also doing
# chdir saw push.py write to the test runner's cwd, not the isolated
# install dir. Resolution is now deferred to call time, mirroring
# db.py's _resolve_db_path() pattern.
_DB_REL = "downloader_history.db"
_VAPID_KEY_REL = "vapid_keys.json"


def _resolve(rel):
    """Resolve a bd-home-relative filename to an absolute Path.

    Order matches db.py: BD_INSTALL_DIR wins if set, else cwd-relative.
    Caller's filename is taken verbatim if it's already absolute (for
    tests that monkeypatch the module attributes directly)."""
    p = Path(rel)
    if p.is_absolute():
        return p
    install_dir = os.environ.get("BD_INSTALL_DIR")
    if install_dir:
        return Path(install_dir).resolve() / p
    return p


def _db_path():
    return _resolve(_DB_REL)


def _vapid_key_path():
    return _resolve(_VAPID_KEY_REL)


@contextmanager
def _conn():
    """v3.66.795 (MOD-3 cut 1): delegate to the history-DB seam.

    This used to open its own `sqlite3.connect(_db_path())` against the SAME
    file the rest of the app reaches through `db.db_conn()` (`_DB_REL` ==
    `constants.DB_PATH` == "downloader_history.db"), which meant push's
    `push_subscriptions` writes bypassed the seam: no WAL/busy_timeout pragmas,
    no slow-query trace, and -- the reason this cut exists -- invisible to the
    dual-write/shadow-read interception MOD-3's later cuts install.

    Path resolution is unchanged: `db._resolve_db_path()` honours BD_INSTALL_DIR
    exactly as `_db_path()` did (and additionally an absolute monkeypatched
    DB_PATH, a superset). `_db_path()` is kept -- tests and the VAPID key file
    still resolve against it.
    """
    with _db.db_conn() as cx:
        yield cx


def _ensure_vapid_keys():
    """Generate a VAPID keypair on first run; return (priv_pem, pub_b64url).
    Stored in vapid_keys.json next to the DB. Idempotent — reads existing
    file if present. Falls back to (None, None) if cryptography isn't
    available (in which case push is silently disabled)."""
    vapid_path = _vapid_key_path()
    if vapid_path.exists():
        try:
            d = json.loads(vapid_path.read_text(encoding="utf-8"))
            if d.get("private_pem") and d.get("public_b64url"):
                return d["private_pem"], d["public_b64url"]
        except Exception:
            pass  # fall through to regenerate
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return None, None
    # Generate P-256 keypair (Web Push spec)
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    # Public key as uncompressed point (0x04 || X || Y), base64url-encoded
    pub_numbers = priv.public_key().public_numbers()
    x = pub_numbers.x.to_bytes(32, "big"); y = pub_numbers.y.to_bytes(32, "big")
    pub_raw = b"\x04" + x + y
    pub_b64url = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode("ascii")
    try:
        # v3.43.19: atomic write. This only runs once per install (first
        # push request triggers VAPID-key generation; afterward the file
        # is read-only), but a crash mid-write would leave a malformed
        # vapid_keys.json that the user has to manually delete before
        # push would work again. .tmp + replace is cheap insurance.
        tmp = vapid_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "private_pem": priv_pem, "public_b64url": pub_b64url,
            "created_at": time.time(),
        }), encoding="utf-8")
        tmp.replace(vapid_path)
        # Best-effort: 0o600 on POSIX, no-op on Windows
        try: os.chmod(vapid_path, 0o600)
        except Exception: pass
    except Exception as e:
        sys.stderr.write(f"[push] couldn't write vapid_keys.json: {e}\n")
    return priv_pem, pub_b64url


def vapid_public_key():
    """Return the base64url-encoded server public key, or '' if push is
    disabled (cryptography missing). The JS uses this with
    PushManager.subscribe({applicationServerKey})."""
    _, pub = _ensure_vapid_keys()
    return pub or ""


def is_available():
    """True iff push is fully usable (keys generated AND pywebpush
    importable). The UI hides the subscribe button when this is False."""
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        return False
    return bool(vapid_public_key())


def add_subscription(sub_dict, user_agent=""):
    """Store a PushSubscription. sub_dict is what JS sends — typically
    {endpoint, keys: {p256dh, auth}}. Idempotent on endpoint.

    Audit 2026-05 (Phase 4 fuzzing): the browser-supplied sub_dict has
    been observed (in fuzzing) to contain non-string values for the key
    material — e.g. p256dh=[1,2,3]. SQLite refuses to bind non-text
    types to TEXT columns, raising sqlite3.ProgrammingError. Validate
    types here so a buggy or malicious browser extension can't crash
    the endpoint handler — return False (the same signal already used
    for missing fields).
    """
    if not isinstance(sub_dict, dict):
        return False
    endpoint = sub_dict.get("endpoint")
    keys = sub_dict.get("keys") or {}
    if not isinstance(keys, dict):
        return False
    p256dh = keys.get("p256dh"); auth = keys.get("auth")
    # Required + type-checked: all three must be non-empty strings.
    for v in (endpoint, p256dh, auth):
        if not isinstance(v, str) or not v:
            return False
    with _conn() as cx:
        cx.execute("""INSERT OR REPLACE INTO push_subscriptions
            (endpoint, p256dh, auth, user_agent, created_at, last_sent_at)
            VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S','now'), 0)""",
            (endpoint, p256dh, auth, (user_agent or "")[:200]))
    return True


def remove_subscription(endpoint):
    if not endpoint: return False
    with _conn() as cx:
        cx.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    return True


def list_subscriptions():
    """List subscriptions for the UI. We don't return the keys (they're
    secrets, sort of) — just metadata."""
    with _conn() as cx:
        return [{
            "endpoint": r["endpoint"][:60] + "…" if len(r["endpoint"]) > 60 else r["endpoint"],
            "user_agent": r["user_agent"],
            "created_at": r["created_at"],
            "last_sent_at": r["last_sent_at"],
        } for r in cx.execute("SELECT endpoint, user_agent, created_at, last_sent_at "
                              "FROM push_subscriptions ORDER BY created_at DESC")]


def _admin_email():
    """VAPID requires an admin contact in the JWT. We use a generic value
    that doesn't leak any user info — most push services don't actually
    deliver to it, it's just a contractual requirement."""
    return "mailto:bulk-downloader@example.local"


def send_push(title, body, url=None, tag=None, throttle_seconds=60):
    """Send a notification to all subscribers. Returns
    {sent, failed, throttled} counts. Failures are logged and the bad
    subscription is auto-removed if it returned 410 Gone (the standard
    "this device unsubscribed" response).

    Throttling: if a subscription's last_sent_at is within
    throttle_seconds, we skip it. Prevents notification spam when many
    URLs complete in quick succession.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return {"sent": 0, "failed": 0, "throttled": 0, "error": "pywebpush not installed"}
    priv, pub = _ensure_vapid_keys()
    if not priv:
        return {"sent": 0, "failed": 0, "throttled": 0, "error": "cryptography not installed"}
    payload = json.dumps({"title": title, "body": body,
                          "url": url or "/", "tag": tag or "bulkdl"})
    sent = failed = throttled = 0
    now = time.time()
    dead_endpoints = []
    with _conn() as cx:
        rows = list(cx.execute("SELECT endpoint, p256dh, auth, last_sent_at "
                               "FROM push_subscriptions"))
    for r in rows:
        if (now - (r["last_sent_at"] or 0)) < throttle_seconds:
            throttled += 1; continue
        try:
            webpush(
                subscription_info={"endpoint": r["endpoint"],
                                   "keys": {"p256dh": r["p256dh"], "auth": r["auth"]}},
                data=payload,
                vapid_private_key=priv,
                vapid_claims={"sub": _admin_email()},
                ttl=600,  # 10 minutes — drop if device offline that long
            )
            sent += 1
            # Update last_sent_at so subsequent calls within 60s skip
            with _conn() as cx:
                cx.execute("UPDATE push_subscriptions SET last_sent_at = ? "
                           "WHERE endpoint = ?", (now, r["endpoint"]))
        except WebPushException as e:
            failed += 1
            # 410 Gone = subscription was dropped (user uninstalled / cleared site data)
            status_code = getattr(e.response, "status_code", None) if e.response else None
            if status_code in (404, 410):
                dead_endpoints.append(r["endpoint"])
                sys.stderr.write(f"[push] removing dead endpoint ({status_code})\n")
            else:
                sys.stderr.write(f"[push] send failed ({status_code}): {str(e)[:100]}\n")
        except Exception as e:
            failed += 1
            sys.stderr.write(f"[push] unexpected error: {str(e)[:100]}\n")
    # Cleanup dead subscriptions
    for ep in dead_endpoints:
        remove_subscription(ep)
    return {"sent": sent, "failed": failed, "throttled": throttled}
