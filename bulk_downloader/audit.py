"""v3.48 — append-only audit log for config changes.

Tracks every successful mutation of sites_config.json and app_config.json
so the operator can answer "wait, when did that setting change?" Six
months from now without sifting through the main log.

Table schema (created lazily on first write):
    audit_log(id, ts, source, action, target, before, after, actor)
where:
    source  = 'api' | 'cli' | 'boot' | 'migration'
    action  = 'create' | 'update' | 'delete' | 'load' | 'seed'
    target  = 'sites_config:<sid>' | 'app_config' | etc.
    before  = JSON string (or NULL for create)
    after   = JSON string (or NULL for delete)
    actor   = best-effort identifier (session id prefix, ip, or 'system')

Lookup helpers:
    audit_recent(limit) -> list[dict]
    audit_for_target(target_prefix, limit) -> list[dict]
    audit_prune(days) -> int

Storage: SQLite, same database as everything else. Retention is the
operator's call — `audit_prune(days)` exposed for the cleanup pass but
not auto-called. Audit log is intentionally non-rotating because it's
small (~200 bytes/row, maybe 50 events per day in active use).
"""
from __future__ import annotations
import json
import time
from typing import Any
from .db import db_conn
from .capture_artifact_redact import _kv_key_is_secret

# Secret field names redacted before serializing into the audit row.
# Keep in sync with _build_meta() in app.py — the audit log must never
# become a backdoor for exfiltrating credentials.
_SECRET_FIELDS = frozenset({
    "password", "qb_password", "captcha_api_key",
    "stash_api_key", "plex_token", "jellyfin_api_key",
    "ha_token", "tpdb_api_key", "ai_api_key", "auth_token",
})

# B3 (v3.66.40): the explicit set above predates the password manager and
# extension vault, so it missed vault_token / pairing_token / csrf_token /
# cookies / master_password / etc. — all of which could land in the audit
# before/after columns in plaintext. Redact by NAME SUBSTRING as well, so
# any field whose key contains one of these markers is stripped even if a
# future field name isn't enumerated above. Over-redaction is the safe
# direction for a security log (it fails closed).
_SECRET_KEY_MARKERS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "cookie", "credential", "private_key", "privatekey", "vault",
)


def _is_secret_key(key: Any) -> bool:
    """True if a dict key names a sensitive value that must be redacted."""
    if not isinstance(key, str):
        return False
    if key in _SECRET_FIELDS:
        return True
    lk = key.lower()
    # I0008 floor: delegate the bare-name secret-keyword set to the single
    # canonical SoT so the audit redactor can never drift from it
    # (F-COREBD17-03). Over-redaction stays the fail-closed direction.
    return (any(marker in lk for marker in _SECRET_KEY_MARKERS)
            or _kv_key_is_secret(lk))


def _ensure_schema():
    """Idempotent schema init. Called on every audit_log() write to keep
    the cold-start case free (no need to wire into db_init)."""
    with db_conn() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            before TEXT,
            after  TEXT,
            actor  TEXT DEFAULT 'system'
        )""")
        cx.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)")
        cx.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_target "
            "ON audit_log(target, ts DESC)")


def _redact(value: Any) -> Any:
    """Recursive secret redaction. Replaces secret values with
    '[redacted]' (if present) or empty string (if originally empty).
    Returns a copy — never mutates the caller's data."""
    if isinstance(value, dict):
        clean = {}
        for k, v in value.items():
            if _is_secret_key(k):
                clean[k] = "[redacted]" if v else ""
            elif k == "accounts" and isinstance(v, list):
                clean[k] = [_redact(a) for a in v]
            else:
                clean[k] = _redact(v)
        return clean
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _serialize(value: Any) -> str | None:
    """Marshal a value into a JSON string, secret-stripped, defensively."""
    if value is None:
        return None
    redacted = _redact(value)
    try:
        return json.dumps(redacted, ensure_ascii=False, default=str,
                          sort_keys=True)
    except Exception:
        return json.dumps({"__unserializable__": type(value).__name__})


def audit_log(source: str, action: str, target: str,
              *, before: Any = None, after: Any = None,
              actor: str = "system"):
    """Append a single audit event. Best-effort: never raises.

    Returns the row id on success or None on failure. The function MUST
    NOT raise — an audit-log bug should never break an actual config
    write."""
    try:
        _ensure_schema()
        with db_conn() as cx:
            cur = cx.execute(
                "INSERT INTO audit_log(ts, source, action, target,"
                " before, after, actor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), source, action, target,
                 _serialize(before), _serialize(after), actor))
            row_id = cur.lastrowid
    except Exception:
        # Can't safely use the logger here (circular import risk during
        # early boot). Silently swallow.
        return None
    # row1002: mirror the row into the process's signed provenance chain so
    # a later edit or deletion of audit_log rows is detectable against the
    # chain head. Same best-effort contract: never raises, never blocks the
    # write it records.
    try:
        from .log import record_audit_provenance_event
        record_audit_provenance_event(
            f"AUDIT:{action}",
            {"id": row_id, "source": source, "target": target, "actor": actor})
    except Exception:
        pass
    return row_id


def audit_chain_status() -> dict:
    """Head + self-verification of the signed audit chain for /api/audit/recent.
    Fail-closed: an unavailable chain reports valid=False, never absent.
    state: VERIFIED (history anchored in both the file store and the DB),
    NEW_CHAIN (genesis only and no DB anchor: a first run -- or a wipe of
    everything; never a bare valid=True), MISMATCH / INVALID otherwise."""
    try:
        from .log import audit_chain_anchor, audit_chain_db_anchor, get_audit_provenance_chain
        chain = get_audit_provenance_chain()
        # row1002 H1: verify against the persisted anchor, so a chain file
        # truncated or rewritten between restarts reads invalid here.
        anchor = audit_chain_anchor()
        res = chain.verify_chain(expected_head=anchor)
        errors = list(res.errors)
        db_anchor = audit_chain_db_anchor()
        if db_anchor is None:
            state = "NEW_CHAIN" if res.block_count == 1 else "UNANCHORED"
            if state == "UNANCHORED":
                errors.append(f"DB_ANCHOR_MISSING: {res.block_count} blocks on disk, "
                              "none mirrored in the history DB")
        elif db_anchor != (res.head_hash, res.block_count):
            # row1002 r3: the file store was wiped or rolled back behind the DB
            state = "MISMATCH"
            errors.append(f"ANCHOR_MISMATCH: DB head {db_anchor[0][:12]} ({db_anchor[1]} blocks) != "
                          f"store head {res.head_hash[:12]} ({res.block_count} blocks)")
        else:
            state = "VERIFIED"
        audit_blocks = sum(1 for b in chain.blocks if b.event_type.startswith("AUDIT:"))
        db_query_ok = False
        try:
            _ensure_schema()
            with db_conn() as cx:
                audit_rows = cx.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            db_query_ok = True
        except Exception as exc:
            audit_rows = -1
            state = "UNVERIFIABLE"
            errors.append(f"AUDIT_COUNT_UNAVAILABLE: {type(exc).__name__}")
        if db_query_ok and audit_blocks != audit_rows and state not in ("MISMATCH", "UNANCHORED"):
            if audit_blocks < audit_rows:
                state = "GAPPED"
                errors.append(f"PROVENANCE_GAP: {audit_blocks} chain blocks for "
                              f"{audit_rows} audit rows")
            else:
                state = "GAPPED"
                errors.append(f"AUDIT_ROWS_MISSING: {audit_blocks} chain blocks but "
                              f"only {audit_rows} audit rows in DB")
        valid = res.valid and not errors
        return {"block_count": res.block_count, "head_hash": res.head_hash,
                "anchor": anchor, "state": state if valid or state in ("MISMATCH", "GAPPED", "UNVERIFIABLE") else "INVALID",
                "valid": valid, "errors": errors[:5]}
    except Exception as e:
        return {"block_count": 0, "head_hash": "", "anchor": "", "state": "UNAVAILABLE",
                "valid": False, "errors": [f"{type(e).__name__}: {e}"[:200]]}



def audit_recent(limit: int = 100) -> list[dict]:
    """Most recent audit events, newest first."""
    try:
        _ensure_schema()
        with db_conn() as cx:
            rows = cx.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def audit_for_target(target_prefix: str, limit: int = 50) -> list[dict]:
    """Events whose `target` starts with the prefix. Useful for
    "show me everything that ever happened to site X" — pass
    'sites_config:<sid>' as the prefix."""
    try:
        _ensure_schema()
        with db_conn() as cx:
            rows = cx.execute(
                "SELECT * FROM audit_log WHERE target LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (target_prefix + "%", int(limit))).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def audit_prune(days: int = 365) -> int:
    """Drop audit rows older than `days`. Returns rows pruned.
    Operator-initiated only; not auto-called."""
    if days < 1:
        return 0
    cutoff = time.time() - (days * 86400)
    try:
        _ensure_schema()
        with db_conn() as cx:
            cur = cx.execute("DELETE FROM audit_log WHERE ts < ?",
                             (cutoff,))
            return cur.rowcount or 0
    except Exception:
        return 0
