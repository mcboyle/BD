"""Row 1035: Append-Only Operator Audit Journal & Modification Provenance.

Validates:
(1) OperatorAuditJournal providing tamper-evident append-only journal entries with cryptographic hash chaining.
(2) ModificationProvenanceTracker tracking change provenance and verification across targets.
(3) Integration with bulk_downloader.audit logging pipeline.

RED on baseline: fails with explicit semantic AssertionError (capability missing), not an unhandled ImportError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import sys
import time
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import audit_journal
except ImportError:
    audit_journal = None


def test_positive_control_audit_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline audit capabilities."""
    from bulk_downloader import audit

    assert hasattr(audit, "audit_log")
    assert callable(audit.audit_log)
    assert hasattr(audit, "audit_recent")
    assert callable(audit.audit_recent)

    audit.audit_log("cli", "test", "test_target", after={"key": "val"}, actor="test_runner")
    entries = audit.audit_recent(limit=5)
    assert isinstance(entries, list)


def test_operator_audit_journal_provenance_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import audit

    assert audit_journal is not None, (
        "Row 1035 capability missing: Append-Only Operator Audit Journal & Modification Provenance "
        "not implemented in bulk_downloader.audit_journal"
    )
    assert hasattr(audit_journal, "OperatorAuditJournal"), (
        "Row 1035 component missing: bulk_downloader.audit_journal.OperatorAuditJournal"
    )
    assert hasattr(audit_journal, "ModificationProvenanceTracker"), (
        "Row 1035 component missing: bulk_downloader.audit_journal.ModificationProvenanceTracker"
    )
    assert hasattr(audit, "verify_audit_provenance"), (
        "Row 1035 caller missing: bulk_downloader.audit.verify_audit_provenance"
    )


def test_operator_audit_journal_tamper_evident_chain():
    """Verify OperatorAuditJournal appends entries with cryptographic Merkle-like hash chaining."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader.audit_journal import OperatorAuditJournal

    journal = OperatorAuditJournal(db_table="test_audit_journal")

    # Record entries
    e1 = journal.record_entry(
        source="cli",
        action="create",
        target="sites_config:site_a",
        before=None,
        after={"url": "https://site-a.com", "threads": 4},
        actor="operator_1",
        reason="Initial setup",
    )
    assert e1["id"] is not None
    assert e1["chain_hash"]

    e2 = journal.record_entry(
        source="api",
        action="update",
        target="sites_config:site_a",
        before={"threads": 4},
        after={"threads": 8},
        actor="operator_2",
        reason="Scale concurrency",
    )
    assert e2["chain_hash"] != e1["chain_hash"]
    assert e2["prev_hash"] == e1["chain_hash"]

    # Verify intact chain
    v_ok = journal.verify_chain()
    assert v_ok["valid"] is True
    assert v_ok["verified_count"] >= 2


def test_modification_provenance_tracker():
    """Verify ModificationProvenanceTracker returns structured provenance history by target."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader.audit_journal import (
        OperatorAuditJournal,
        ModificationProvenanceTracker,
    )

    journal = OperatorAuditJournal(db_table="test_prov_tracker")
    target = "app_config:storage"

    journal.record_entry(
        source="cli",
        action="update",
        target=target,
        before={"retention_days": 30},
        after={"retention_days": 90},
        actor="admin",
        reason="Compliance requirement",
    )
    journal.record_entry(
        source="cli",
        action="update",
        target=target,
        before={"retention_days": 90},
        after={"retention_days": 180},
        actor="compliance_officer",
        reason="Long-term retention",
    )

    tracker = ModificationProvenanceTracker(journal=journal)
    history = tracker.get_target_provenance(target)

    assert len(history) == 2
    assert history[0]["actor"] == "admin"
    assert history[1]["actor"] == "compliance_officer"
    assert history[0]["chain_hash"] == history[1]["prev_hash"]


def test_audit_module_provenance_integration():
    """Verify bulk_downloader.audit.verify_audit_provenance integrates with the global journal."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader import audit

    # Normal audit log call
    audit.audit_log(
        source="api",
        action="update",
        target="sites_config:site_b",
        before={"delay": 1.0},
        after={"delay": 2.0},
        actor="operator_3",
    )

    verif = audit.verify_audit_provenance()
    assert isinstance(verif, dict)
    assert "valid" in verif
    assert verif["valid"] is True
    assert verif["verified_count"] >= 1


def test_empty_journal_fails_closed_with_empty_status():
    """Verify F2/F4 fix: empty journal must not claim valid=True (cannot verify empty)."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader.audit_journal import OperatorAuditJournal

    journal = OperatorAuditJournal(db_table="test_empty_journal_audit")
    res = journal.verify_chain()
    assert res["valid"] is False
    assert res["status"] == "empty"
    assert res["verified_count"] == 0


def test_journal_tamper_detection():
    """Verify F3 fix: tampering with chain linkage or payload is detected and fails validation."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader.audit_journal import OperatorAuditJournal
    from bulk_downloader.db import db_conn
    import json

    journal = OperatorAuditJournal(db_table="test_tamper_journal_audit")
    e1 = journal.record_entry(source="cli", action="add", target="t1", after={"v": 1}, actor="a1")
    e2 = journal.record_entry(source="cli", action="add", target="t2", after={"v": 2}, actor="a2")

    assert journal.verify_chain()["valid"] is True

    # Tamper with stored after payload of e1 directly in the DB
    with db_conn() as cx:
        cx.execute(f"UPDATE {journal.db_table} SET after = ? WHERE id = ?", (json.dumps({"v": 999}), e1["id"]))

    res = journal.verify_chain()
    assert res["valid"] is False
    assert res["status"] == "broken"
    assert res["broken_at"] == e1["id"]


def test_audit_log_surfaces_journal_drop_divergence():
    """Verify F1 fix: dropped journal writes are detected and report provenance divergence."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader import audit

    # Monkeypatch record_entry to simulate journal write failure
    journal = audit_journal.get_operator_audit_journal()
    orig_record = journal.record_entry

    def failing_record(*args, **kwargs):
        raise RuntimeError("Disk full / DB lock on journal write")

    journal.record_entry = failing_record
    try:
        row_id = audit.audit_log(source="api", action="drop_test", target="test_target", after={"x": 1}, actor="tester")
        assert row_id is not None
        assert audit.get_audit_journal_write_failures() > 0

        verif = audit.verify_audit_provenance()
        assert verif["valid"] is False
        assert verif["status"] == "diverged"
        assert verif["dropped_journal_writes"] > 0
    finally:
        journal.record_entry = orig_record
        audit._JOURNAL_WRITE_FAILURES = 0
        with audit_journal.db_conn() as cx:
            journal._set_state(cx, "dropped_writes", "0")


def test_prev_hash_splice_detected():
    """Verify N3/M1 kill: tampering specifically with a row's prev_hash pointer is caught by linkage check."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader.audit_journal import OperatorAuditJournal
    from bulk_downloader.db import db_conn

    journal = OperatorAuditJournal(db_table="test_splice_prev_hash_audit")
    e1 = journal.record_entry(source="cli", action="add", target="t1", after={"v": 1}, actor="a1")
    e2 = journal.record_entry(source="cli", action="add", target="t2", after={"v": 2}, actor="a2")
    e3 = journal.record_entry(source="cli", action="add", target="t3", after={"v": 3}, actor="a3")

    assert journal.verify_chain()["valid"] is True

    # Mutate ONLY prev_hash pointer of row e3
    with db_conn() as cx:
        cx.execute(f"UPDATE {journal.db_table} SET prev_hash = 'tampered_prev_ptr' WHERE id = ?", (e3["id"],))

    res = journal.verify_chain()
    assert res["valid"] is False
    assert res["status"] == "broken"
    assert res["broken_at"] == e3["id"]
    assert "Previous hash mismatch" in res["reason"]


def test_journal_tail_truncation_detected_via_persisted_head():
    """Verify N1 fix: deleting the newest entries is caught by persisted head tracking."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader.audit_journal import OperatorAuditJournal
    from bulk_downloader.db import db_conn

    journal = OperatorAuditJournal(db_table="test_tail_truncation_audit")
    e1 = journal.record_entry(source="cli", action="add", target="t1", after={"v": 1}, actor="a1")
    e2 = journal.record_entry(source="cli", action="add", target="t2", after={"v": 2}, actor="a2")
    e3 = journal.record_entry(source="cli", action="add", target="t3", after={"v": 3}, actor="a3")
    e4 = journal.record_entry(source="cli", action="add", target="t4", after={"v": 4}, actor="a4")

    assert journal.verify_chain()["valid"] is True

    # Delete tail entries e3 and e4
    with db_conn() as cx:
        cx.execute(f"DELETE FROM {journal.db_table} WHERE id >= ?", (e3["id"],))

    # Verify chain refuses with status="truncated"
    res = journal.verify_chain()
    assert res["valid"] is False
    assert res["status"] == "truncated"
    assert res["broken_at"] == e3["id"]
    assert "Journal tail truncated" in res["reason"]


def test_journal_tail_truncation_detected_via_audit_log_crosscheck(clean_workdir):
    """Verify N1 fix: deleting journal entries while audit_log persists reports truncation across processes."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader import audit
    from bulk_downloader.db import db_conn

    # Log 4 events through audit_log
    for i in range(4):
        audit.audit_log(source="gui", action=f"action_{i}", target=f"target_{i}", after={"i": i})

    assert audit.verify_audit_provenance()["valid"] is True

    # Truncate last entry from operator_audit_journal
    with db_conn() as cx:
        max_id_row = cx.execute("SELECT max(id) FROM operator_audit_journal").fetchone()
        if max_id_row and max_id_row[0]:
            cx.execute("DELETE FROM operator_audit_journal WHERE id = ?", (max_id_row[0],))

    verif = audit.verify_audit_provenance()
    assert verif["valid"] is False
    assert verif["status"] == "truncated"
    assert "tail truncated" in verif["reason"]


def test_dropped_writes_survives_new_instances():
    """Verify N2 fix: dropped journal writes are persisted in database state across process boundaries."""
    assert audit_journal is not None, "audit_journal capability missing"
    from bulk_downloader.audit_journal import OperatorAuditJournal

    journal1 = OperatorAuditJournal(db_table="test_persisted_drops_audit")
    journal1.record_entry(source="cli", action="init", target="t", after={"init": True})
    assert journal1.verify_chain()["valid"] is True

    # Record drop
    journal1.record_drop(reason="Disk pressure")

    # Fresh instance simulating another process / CLI / daemon inspection
    journal2 = OperatorAuditJournal(db_table="test_persisted_drops_audit")
    assert journal2.get_dropped_writes() == 1

    res = journal2.verify_chain()
    assert res["valid"] is False
    assert res["status"] == "diverged"
    assert res["dropped_journal_writes"] == 1


