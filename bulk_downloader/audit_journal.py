"""Append-Only Operator Audit Journal & Modification Provenance.

Provides tamper-evident cryptographic hash chaining for configuration and
administrative mutations, enabling verifiable modification provenance and audit
history forensics.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional
from .db import db_conn
from .audit import _redact


def _content_hash(payload: Dict[str, Any]) -> str:
    """Stable hash of an audit event's content."""
    serialized = json.dumps(
        {k: payload[k] for k in sorted(payload) if k not in ("id", "prev_hash", "chain_hash")},
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _chain_hash(prev_hash: str, content_hash: str) -> str:
    """Merkle-like hash chaining = SHA256(prev_hash || content_hash)."""
    return hashlib.sha256(((prev_hash or "") + content_hash).encode("utf-8")).hexdigest()


class OperatorAuditJournal:
    """Append-only audit journal securing administrative mutations with cryptographic chaining."""

    def __init__(self, db_table: str = "operator_audit_journal") -> None:
        self.db_table = "".join(c for c in db_table if c.isalnum() or c == "_")
        self._ensure_table()

    def _ensure_table(self) -> None:
        with db_conn() as cx:
            cx.execute(f"""CREATE TABLE IF NOT EXISTS {self.db_table}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                source TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                before TEXT,
                after TEXT,
                actor TEXT DEFAULT 'system',
                reason TEXT DEFAULT '',
                prev_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                chain_hash TEXT NOT NULL
            )""")
            cx.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.db_table}_ts ON {self.db_table}(ts DESC)")
            cx.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.db_table}_target ON {self.db_table}(target, ts DESC)")
            cx.execute(f"""CREATE TABLE IF NOT EXISTS {self.db_table}_state(
                key TEXT PRIMARY KEY,
                val TEXT
            )""")

    def _get_state(self, cx: Any, key: str, default: str = "") -> str:
        row = cx.execute(f"SELECT val FROM {self.db_table}_state WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row and row[0] is not None else default

    def _set_state(self, cx: Any, key: str, val: str) -> None:
        cx.execute(
            f"INSERT INTO {self.db_table}_state(key, val) VALUES (?, ?) "
            f"ON CONFLICT(key) DO UPDATE SET val = excluded.val",
            (key, str(val)),
        )

    def record_drop(self, reason: str = "") -> int:
        """Record a dropped journal write into persistent state."""
        self._ensure_table()
        with db_conn() as cx:
            cur = int(self._get_state(cx, "dropped_writes", "0"))
            new_val = cur + 1
            self._set_state(cx, "dropped_writes", str(new_val))
            return new_val

    def get_dropped_writes(self) -> int:
        """Get persisted count of dropped journal writes."""
        self._ensure_table()
        with db_conn() as cx:
            return int(self._get_state(cx, "dropped_writes", "0"))

    def _latest_chain_hash(self, cx: Any) -> str:
        row = cx.execute(f"SELECT chain_hash FROM {self.db_table} ORDER BY id DESC LIMIT 1").fetchone()
        return str(row[0]) if row and row[0] else ""

    def record_entry(
        self,
        source: str,
        action: str,
        target: str,
        before: Any = None,
        after: Any = None,
        actor: str = "system",
        reason: str = "",
    ) -> Dict[str, Any]:
        """Record an append-only modification into the cryptographic audit journal."""
        self._ensure_table()
        clean_before = _redact(before) if before is not None else None
        clean_after = _redact(after) if after is not None else None
        ts = time.time()

        content_dict = {
            "ts": ts,
            "source": source,
            "action": action,
            "target": target,
            "before": clean_before,
            "after": clean_after,
            "actor": actor or "system",
            "reason": reason or "",
        }
        c_hash = _content_hash(content_dict)

        before_json = json.dumps(clean_before, default=str) if clean_before is not None else None
        after_json = json.dumps(clean_after, default=str) if clean_after is not None else None

        with db_conn() as cx:
            prev_h = self._latest_chain_hash(cx)
            ch_hash = _chain_hash(prev_h, c_hash)

            cur = cx.execute(
                f"""INSERT INTO {self.db_table}(
                    ts, source, action, target, before, after, actor, reason, prev_hash, content_hash, chain_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    source,
                    action,
                    target,
                    before_json,
                    after_json,
                    actor or "system",
                    reason or "",
                    prev_h,
                    c_hash,
                    ch_hash,
                ),
            )
            row_id = cur.lastrowid
            self._set_state(cx, "head_id", str(row_id))
            self._set_state(cx, "head_chain_hash", ch_hash)

        return {
            "id": row_id,
            "ts": ts,
            "source": source,
            "action": action,
            "target": target,
            "actor": actor or "system",
            "reason": reason or "",
            "prev_hash": prev_h,
            "content_hash": c_hash,
            "chain_hash": ch_hash,
        }

    def verify_chain(self) -> Dict[str, Any]:
        """Verify complete cryptographic chain integrity across all journal entries."""
        self._ensure_table()
        with db_conn() as cx:
            rows = cx.execute(
                f"""SELECT id, ts, source, action, target, before, after, actor, reason,
                           prev_hash, content_hash, chain_hash
                    FROM {self.db_table} ORDER BY id ASC"""
            ).fetchall()
            persisted_head_id_str = self._get_state(cx, "head_id", "")
            persisted_head_chain_hash = self._get_state(cx, "head_chain_hash", "")
            dropped_writes = int(self._get_state(cx, "dropped_writes", "0"))
            audit_log_exists = cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
            ).fetchone()
            audit_count = (
                cx.execute("SELECT count(*) FROM audit_log").fetchone()[0]
                if audit_log_exists
                else 0
            )

        max_id = rows[-1][0] if rows else 0
        latest_chain_h = rows[-1][11] if rows else ""

        if dropped_writes > 0:
            return {
                "valid": False,
                "status": "diverged",
                "verified_count": len(rows),
                "broken_at": None,
                "latest_hash": latest_chain_h,
                "dropped_journal_writes": dropped_writes,
                "reason": f"{dropped_writes} audit entries failed to record to journal; audit trail and provenance diverged",
            }

        if not rows:
            if persisted_head_id_str or (self.db_table == "operator_audit_journal" and audit_count > 0):
                return {
                    "valid": False,
                    "status": "truncated",
                    "verified_count": 0,
                    "broken_at": 1,
                    "latest_hash": "",
                    "dropped_journal_writes": dropped_writes,
                    "reason": "journal is empty but expected entries; tail truncated",
                }
            return {
                "valid": False,
                "status": "empty",
                "verified_count": 0,
                "broken_at": None,
                "latest_hash": "",
                "dropped_journal_writes": dropped_writes,
                "reason": "journal is empty; no provenance to verify",
            }

        # Completeness checks against persisted head
        if persisted_head_id_str:
            persisted_head_id = int(persisted_head_id_str)
            if max_id < persisted_head_id:
                return {
                    "valid": False,
                    "status": "truncated",
                    "verified_count": len(rows),
                    "broken_at": max_id + 1,
                    "latest_hash": latest_chain_h,
                    "dropped_journal_writes": dropped_writes,
                    "reason": f"Journal tail truncated: persisted head id {persisted_head_id} exceeds current max id {max_id}",
                }
            if persisted_head_chain_hash and latest_chain_h != persisted_head_chain_hash:
                return {
                    "valid": False,
                    "status": "truncated",
                    "verified_count": len(rows),
                    "broken_at": max_id,
                    "latest_hash": latest_chain_h,
                    "dropped_journal_writes": dropped_writes,
                    "reason": f"Journal tail hash mismatch: persisted head hash {persisted_head_chain_hash[:8]} does not match latest entry hash {latest_chain_h[:8]}",
                }

        # Cross-check against audit_log table for operator_audit_journal
        if self.db_table == "operator_audit_journal" and audit_count > len(rows):
            return {
                "valid": False,
                "status": "truncated",
                "verified_count": len(rows),
                "broken_at": len(rows) + 1,
                "latest_hash": latest_chain_h,
                "dropped_journal_writes": dropped_writes,
                "reason": f"Journal has {len(rows)} entries but audit_log has {audit_count} entries; tail truncated",
            }

        expected_prev = ""
        verified_count = 0

        for r in rows:
            (
                r_id,
                r_ts,
                r_source,
                r_action,
                r_target,
                r_before,
                r_after,
                r_actor,
                r_reason,
                r_prev_h,
                r_content_h,
                r_chain_h,
            ) = r

            if r_prev_h != expected_prev:
                return {
                    "valid": False,
                    "status": "broken",
                    "verified_count": verified_count,
                    "broken_at": r_id,
                    "latest_hash": expected_prev,
                    "dropped_journal_writes": dropped_writes,
                    "reason": f"Previous hash mismatch at id {r_id}",
                }

            # Recompute content hash
            before_val = json.loads(r_before) if r_before else None
            after_val = json.loads(r_after) if r_after else None
            computed_content_h = _content_hash({
                "ts": r_ts,
                "source": r_source,
                "action": r_action,
                "target": r_target,
                "before": before_val,
                "after": after_val,
                "actor": r_actor,
                "reason": r_reason or "",
            })

            if computed_content_h != r_content_h:
                return {
                    "valid": False,
                    "status": "broken",
                    "verified_count": verified_count,
                    "broken_at": r_id,
                    "latest_hash": expected_prev,
                    "dropped_journal_writes": dropped_writes,
                    "reason": f"Content hash mismatch at id {r_id}",
                }

            expected_chain_h = _chain_hash(expected_prev, computed_content_h)
            if r_chain_h != expected_chain_h:
                return {
                    "valid": False,
                    "status": "broken",
                    "verified_count": verified_count,
                    "broken_at": r_id,
                    "latest_hash": expected_prev,
                    "dropped_journal_writes": dropped_writes,
                    "reason": f"Chain hash mismatch at id {r_id}",
                }

            expected_prev = r_chain_h
            verified_count += 1

        return {
            "valid": True,
            "status": "intact",
            "verified_count": verified_count,
            "broken_at": None,
            "latest_hash": expected_prev,
            "dropped_journal_writes": 0,
        }

    def get_entries_for_target(self, target: str, limit: int = 100) -> List[Dict[str, Any]]:
        self._ensure_table()
        with db_conn() as cx:
            rows = cx.execute(
                f"""SELECT id, ts, source, action, target, before, after, actor, reason,
                           prev_hash, content_hash, chain_hash
                    FROM {self.db_table} WHERE target = ? ORDER BY id ASC LIMIT ?""",
                (target, limit),
            ).fetchall()

        out = []
        for r in rows:
            out.append({
                "id": r[0],
                "ts": r[1],
                "source": r[2],
                "action": r[3],
                "target": r[4],
                "before": json.loads(r[5]) if r[5] else None,
                "after": json.loads(r[6]) if r[6] else None,
                "actor": r[7],
                "reason": r[8],
                "prev_hash": r[9],
                "content_hash": r[10],
                "chain_hash": r[11],
            })
        return out


class ModificationProvenanceTracker:
    """Tracks modification provenance and revision ancestry for configuration entities."""

    def __init__(self, journal: Optional[OperatorAuditJournal] = None) -> None:
        self.journal = journal or get_operator_audit_journal()

    def get_target_provenance(self, target: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.journal.get_entries_for_target(target, limit=limit)


_GLOBAL_JOURNAL: Optional[OperatorAuditJournal] = None


def get_operator_audit_journal() -> OperatorAuditJournal:
    global _GLOBAL_JOURNAL
    if _GLOBAL_JOURNAL is None:
        _GLOBAL_JOURNAL = OperatorAuditJournal()
    return _GLOBAL_JOURNAL
