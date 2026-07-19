"""Multi-instance federation (Phase 120, Block Q).

Operators with multiple BD instances (work laptop + home NAS + VPS)
want them to coordinate:

  • Don't both download the same URL
  • Share new URLs between instances (one operator finds a scene,
    another instance picks it up)
  • Aggregate dashboard across instances

This module exposes a minimal sync API:

  • POST /api/fed/announce {instance_id, last_seen_history_id}
       — register or refresh a peer
  • GET  /api/fed/peers
       — list active peers (seen in last hour)
  • POST /api/fed/url_seen {url, source_instance}
       — peer informs us they're handling this URL
  • POST /api/fed/sync_pull {since_history_id}
       — peer asks for our new history rows since their bookmark
       — we return rows that are 'done' or 'failed' (in-flight not shared)

Trust model: shared bearer token in fed_token config. All peers know
the same token. Mutual trust within a single operator's instances.
Multi-tenant federation not in scope.

Conflict resolution: instances opportunistically skip URLs they see
another peer is processing. If two start at the same moment, both
finish — duplication shows up in dedup_scan later, gets cleaned up
by storage_rebalance + batch_delete.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional


def _ensure_tables():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS fed_peers(
                instance_id TEXT PRIMARY KEY,
                base_url TEXT NOT NULL,
                last_seen_ts REAL NOT NULL,
                last_history_id INTEGER DEFAULT 0,
                version TEXT DEFAULT '',
                hostname TEXT DEFAULT '',
                trust_tier TEXT DEFAULT 'observed'
            )""")
            # migrate a pre-11.2 fed_peers table that lacks trust_tier
            cols = {r[1] for r in cx.execute("PRAGMA table_info(fed_peers)")}
            if "trust_tier" not in cols:
                cx.execute("ALTER TABLE fed_peers ADD COLUMN "
                           "trust_tier TEXT DEFAULT 'observed'")
            cx.execute("""CREATE TABLE IF NOT EXISTS fed_url_locks(
                url TEXT PRIMARY KEY,
                source_instance TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_fed_url_exp "
                       "ON fed_url_locks(expires_at)")
    except Exception as e:
        import sys
        sys.stderr.write(f"[federation] schema init failed: {e}\n")


# ─── Trust tiers (C7 11.2) ────────────────────────────────────────────

# Ordered least->most trusted. A peer defaults to "observed" on first announce;
# only an operator (set_peer_trust) can promote to "trusted" or demote to
# "blocked". A "blocked" peer is refused download coordination (claim_url).
TRUST_TIERS = ("blocked", "observed", "trusted")


def set_peer_trust(instance_id: str, tier: str) -> bool:
    """Operator-set a peer's trust tier. Returns False for an unknown tier or
    unknown peer. This is the ONLY way a tier changes -- register_peer (announce)
    never modifies it, so a peer cannot self-elevate."""
    if tier not in TRUST_TIERS:
        return False
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute(
                "UPDATE fed_peers SET trust_tier = ? WHERE instance_id = ?",
                (tier, (instance_id or "").strip()))
            return cur.rowcount > 0
    except Exception:
        return False


def peer_trust(instance_id: str) -> str:
    """Return a peer's trust tier, or 'observed' if unknown."""
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT trust_tier FROM fed_peers WHERE instance_id = ?",
                ((instance_id or "").strip(),)).fetchone()
        if not row:
            return "observed"
        return (row[0] if not hasattr(row, "keys") else row["trust_tier"]) \
            or "observed"
    except Exception:
        return "observed"


# ─── Authentication ───────────────────────────────────────────────────

def _compute_token_signature(token: str, body: bytes) -> str:
    """HMAC over the request body. Peer must include the same."""
    return hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_request(body_bytes: bytes, *, signature: str,
                  token: str) -> bool:
    """Verify a peer request's signature. constant-time compare."""
    if not token or not signature:
        return False
    expected = _compute_token_signature(token, body_bytes)
    return hmac.compare_digest(expected, signature)


# ─── Peer registry ────────────────────────────────────────────────────

def register_peer(instance_id: str, base_url: str,
                 *, last_history_id: int = 0,
                 version: str = "", hostname: str = "") -> bool:
    """Insert or refresh a peer's last_seen_ts. Called when a peer
    POSTs /api/fed/announce."""
    if not instance_id or not base_url:
        return False
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO fed_peers(
                instance_id, base_url, last_seen_ts, last_history_id,
                version, hostname
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(instance_id) DO UPDATE SET
                base_url = excluded.base_url,
                last_seen_ts = excluded.last_seen_ts,
                last_history_id = excluded.last_history_id,
                version = excluded.version,
                hostname = excluded.hostname""",
                (instance_id, base_url, time.time(),
                 int(last_history_id), version, hostname))
        return True
    except Exception:
        return False


def active_peers(*, since_seconds: int = 3600) -> list:
    """Peers seen in the last `since_seconds`."""
    _ensure_tables()
    cutoff = time.time() - since_seconds
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM fed_peers
                                  WHERE last_seen_ts >= ?
                                  ORDER BY last_seen_ts DESC""",
                              (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ─── URL claim coordination ───────────────────────────────────────────

def claim_url(url: str, instance_id: str,
             *, ttl_seconds: int = 1800) -> bool:
    """A peer announces they're processing `url`. Returns False if
    another peer already has an unexpired claim on it.

    TTL prevents stuck claims: if a peer crashes mid-download, the
    claim expires and another peer can pick it up."""
    if not url or not instance_id:
        return False
    _ensure_tables()
    # C7 11.2: a blocked peer is refused download coordination.
    if peer_trust(instance_id) == "blocked":
        return False
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            # Check existing claim
            row = cx.execute("""SELECT source_instance, expires_at
                                 FROM fed_url_locks WHERE url = ?""",
                              (url,)).fetchone()
            if row:
                existing_inst = row[0] if not hasattr(row, "keys") else row["source_instance"]
                expires = float(row[1] if not hasattr(row, "keys") else row["expires_at"])
                if expires > now and existing_inst != instance_id:
                    return False
            cx.execute("""INSERT INTO fed_url_locks(
                url, source_instance, claimed_at, expires_at
            ) VALUES (?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
                source_instance = excluded.source_instance,
                claimed_at = excluded.claimed_at,
                expires_at = excluded.expires_at""",
                (url, instance_id, now, now + ttl_seconds))
        return True
    except Exception:
        return False


def release_url(url: str, instance_id: str) -> bool:
    """Release a claim. Only the claiming instance can release."""
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""DELETE FROM fed_url_locks
                                WHERE url = ? AND source_instance = ?""",
                              (url, instance_id))
            return cur.rowcount > 0
    except Exception:
        return False


def is_claimed(url: str) -> Optional[dict]:
    """Return the active claim (instance + when it expires) or None."""
    _ensure_tables()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT * FROM fed_url_locks
                                 WHERE url = ? AND expires_at > ?""",
                              (url, now)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def expire_old_claims():
    """Sweep expired claims. Run periodically (bg_scheduler)."""
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("DELETE FROM fed_url_locks WHERE expires_at < ?",
                             (time.time(),))
            return cur.rowcount
    except Exception:
        return 0


# ─── Sync pull ────────────────────────────────────────────────────────

def history_since(since_id: int, *, limit: int = 500) -> list:
    """Return history rows with id > since_id where status terminal.
    Used when a peer asks 'what's new on your end since I last
    checked?'"""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT id, site_id, url, status,
                                       filename, file_size, ts
                                  FROM history
                                  WHERE id > ?
                                    AND status IN ('done','failed','needs_review')
                                  ORDER BY id ASC LIMIT ?""",
                              (int(since_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _local_max_history_id() -> int:
    """The highest local history row id (0 if none / no table)."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("SELECT MAX(id) FROM history").fetchone()
        v = row[0] if row else None
        return int(v or 0)
    except Exception:
        return 0


def peer_drift() -> list:
    """Per-peer replication lag (C7 11.2): how far each peer's reported
    last_history_id trails this instance's local max history id. A positive
    ``behind`` means the peer has not yet pulled that many of our newer rows."""
    _ensure_tables()
    local_max = _local_max_history_id()
    out = []
    for p in active_peers():
        peer_last = int(p.get("last_history_id", 0) or 0)
        out.append({
            "instance_id": p.get("instance_id"),
            "trust_tier": p.get("trust_tier", "observed"),
            "local_max": local_max,
            "peer_last_id": peer_last,
            "behind": max(0, local_max - peer_last),
        })
    return out


def status() -> dict:
    """Diagnostic snapshot."""
    _ensure_tables()
    out = {"peers_active": 0, "active_claims": 0, "last_expire_run_ts": 0,
           "peers_behind": 0}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cutoff = time.time() - 3600
            r = cx.execute("SELECT COUNT(*) FROM fed_peers WHERE last_seen_ts >= ?",
                          (cutoff,)).fetchone()
            out["peers_active"] = int(r[0] or 0)
            r = cx.execute("SELECT COUNT(*) FROM fed_url_locks WHERE expires_at > ?",
                          (time.time(),)).fetchone()
            out["active_claims"] = int(r[0] or 0)
        # C7 11.2: how many active peers are behind our local history head.
        out["peers_behind"] = sum(1 for d in peer_drift() if d["behind"] > 0)
    except Exception:
        pass
    return out


# ─── Template federation (C7-11.2, v3.66.681) ──────────────────────────
#
# Peers exchange site templates as signed, redacted bundles (reusing the
# marketplace bundle format + HMAC signing keyed on the shared fed_token).
# Received templates DO NOT auto-apply: they land in a review-on-receive
# queue (fed_pending_templates). On operator approval the template is written
# NON-DESTRUCTIVELY into the template store under a fed_<peer>_<host> filename
# (never overwriting the operator's own <host>.template.json) and is visibly
# marked with its peer provenance. Rejection just marks the row.
#
# marketplace-reuse: sign_bundle / verify_signature / validate_bundle / _redact.
# All heavy imports are function-local -> no new module-level import edge.


def _fed_token() -> str:
    try:
        from .global_config import get_config
        return (get_config() or {}).get("fed_token") or ""
    except Exception:
        return ""


def _ensure_pending_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS fed_pending_templates(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_instance TEXT NOT NULL,
                site_id TEXT NOT NULL,
                bundle_json TEXT NOT NULL,
                received_ts REAL NOT NULL,
                status TEXT DEFAULT 'pending'
            )""")
    except Exception:
        pass


def list_shareable_templates() -> list:
    """Descriptors of enabled templates this instance can share. Read-only."""
    out = []
    try:
        from . import template_registry as _tr
        for t in _tr.load_templates():
            host = t.get("host") or ""
            if not host:
                continue
            out.append({
                "site_id": host,
                "host": host,
                "selectors": sorted((t.get("selectors") or {}).keys()),
                "status": t.get("status", ""),
            })
    except Exception:
        pass
    return out


def build_template_bundle(host: str) -> Optional[dict]:
    """Build a signed, redacted bundle for one template (by host). Returns
    None if no enabled template matches. Signature is keyed on fed_token
    (empty token -> unsigned bundle, receiver decides whether to accept)."""
    if not host:
        return None
    try:
        from . import template_registry as _tr
        from . import marketplace as _mk
    except Exception:
        return None
    tmpl = None
    for t in _tr.load_templates():
        if (t.get("host") or "") == host:
            tmpl = t
            break
    if tmpl is None:
        return None
    clean = {k: v for k, v in tmpl.items() if not str(k).startswith("_")}
    try:
        clean = _mk._redact(clean)
    except Exception:
        pass
    bundle = {
        "schema": _mk.SCHEMA_VERSION,
        "site_id": host,
        "template": clean,
        "metadata": {"kind": "federated_template", "host": host},
    }
    token = _fed_token()
    if token:
        try:
            bundle["signature"] = _mk.sign_bundle(bundle, secret=token)
        except Exception:
            pass
    return bundle


def receive_template(from_instance: str, bundle: dict) -> dict:
    """Verify + validate a peer's template bundle and queue it for operator
    review. Returns {ok, pending_id} or {ok: False, error}."""
    if not isinstance(bundle, dict):
        return {"ok": False, "error": "bundle not a dict"}
    try:
        from . import marketplace as _mk
    except Exception:
        return {"ok": False, "error": "marketplace unavailable"}
    v = _mk.validate_bundle(bundle)
    if not v.get("ok"):
        return {"ok": False, "error": "; ".join(v.get("errors", [])) or "invalid bundle"}
    token = _fed_token()
    # If we have a shared token, a signed bundle must verify. An unsigned
    # bundle is allowed only when no token is configured.
    sig = _mk.verify_signature(bundle, secret=token) if token else {"ok": True, "signed": False}
    if token and not sig.get("ok"):
        return {"ok": False, "error": "signature verification failed"}
    _ensure_pending_table()
    import json as _json
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute(
                "INSERT INTO fed_pending_templates(from_instance, site_id, "
                "bundle_json, received_ts, status) VALUES (?,?,?,?, 'pending')",
                (str(from_instance or "")[:128], v.get("site_id", ""),
                 _json.dumps(bundle), time.time()))
            return {"ok": True, "pending_id": cur.lastrowid}
    except Exception as e:
        return {"ok": False, "error": f"queue write failed: {type(e).__name__}"}


def list_pending_templates() -> list:
    """Pending peer templates awaiting operator review. Read-only."""
    _ensure_pending_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute(
                "SELECT id, from_instance, site_id, received_ts, status "
                "FROM fed_pending_templates WHERE status = 'pending' "
                "ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def review_pending_template(pending_id: int, action: str) -> dict:
    """Operator approves or rejects a pending peer template. Approve writes
    the template NON-DESTRUCTIVELY into the store (fed_<peer>_<host>). Returns
    {ok, applied?} or {ok: False, error}."""
    if action not in ("approve", "reject"):
        return {"ok": False, "error": "action must be 'approve' or 'reject'"}
    _ensure_pending_table()
    import json as _json
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT id, from_instance, site_id, bundle_json, status "
                "FROM fed_pending_templates WHERE id = ?", (int(pending_id),)
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "pending template not found"}
            r = dict(row)
            if r["status"] != "pending":
                return {"ok": False, "error": f"already {r['status']}"}
            if action == "reject":
                cx.execute("UPDATE fed_pending_templates SET status='rejected' WHERE id=?",
                           (int(pending_id),))
                return {"ok": True, "applied": False}
            # approve
            try:
                bundle = _json.loads(r["bundle_json"])
            except Exception:
                return {"ok": False, "error": "corrupt bundle_json"}
            applied = _apply_template_bundle(r["from_instance"], bundle)
            if not applied.get("ok"):
                return {"ok": False, "error": applied.get("error", "apply failed")}
            cx.execute("UPDATE fed_pending_templates SET status='approved' WHERE id=?",
                       (int(pending_id),))
            return {"ok": True, "applied": True, "path": applied.get("path", "")}
    except Exception as e:
        return {"ok": False, "error": f"review failed: {type(e).__name__}"}


def _apply_template_bundle(from_instance: str, bundle: dict) -> dict:
    """Write an approved template into the store non-destructively. The file
    is named fed_<peer>_<host>.template.json so it never collides with the
    operator's own <host>.template.json, and carries provenance fields."""
    import json as _json
    import re as _re
    from pathlib import Path as _Path
    try:
        from . import template_registry as _tr
    except Exception:
        return {"ok": False, "error": "template_registry unavailable"}
    tmpl = bundle.get("template")
    host = bundle.get("site_id") or (tmpl.get("host") if isinstance(tmpl, dict) else "")
    if not isinstance(tmpl, dict) or not host:
        return {"ok": False, "error": "bundle has no usable template"}
    dirs = getattr(_tr, "DEFAULT_TEMPLATE_DIRS", None) or []
    if not dirs:
        return {"ok": False, "error": "no template dir configured"}
    target_dir = _Path(dirs[0])
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    safe_peer = _re.sub(r"[^A-Za-z0-9_.-]", "_", str(from_instance or "peer"))[:48]
    safe_host = _re.sub(r"[^A-Za-z0-9_.-]", "_", str(host))[:80]
    fname = f"fed_{safe_peer}_{safe_host}.template.json"
    out = dict(tmpl)
    out["host"] = host
    out.setdefault("status", "enabled")
    out["_federated_from"] = str(from_instance or "")
    out["_federated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = target_dir / fname
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(out, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic
        return {"ok": True, "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": f"write failed: {type(e).__name__}"}
