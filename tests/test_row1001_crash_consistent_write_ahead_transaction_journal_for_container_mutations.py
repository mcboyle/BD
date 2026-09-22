"""Unit tests for Row 1001: Crash-Consistent Write-Ahead Transaction Journal for Container Mutations.

Guards:
- Localized transaction journal under bulk_downloader.transaction_journal
- CRC32/checksum protected write-ahead log records
- ACID transaction lifecycle: begin -> mutate -> commit / abort
- Crash recovery: replay valid records, isolate uncommitted mutations
- Torn write detection: recovery ignores corrupted or partial trailing records
- Checkpointing: state snapshot generation and journal compaction
- End-to-end container state machine reconstruction
"""
from __future__ import annotations

from pathlib import Path

import pytest

# H622 anti-orphan convention: scoped module test, collected by bd-test-shard
BD_GATE_SCOPE = "module"


def test_journal_metadata_and_info():
    """Verify journal capability introspection and operational metadata."""
    from bulk_downloader import container_repair

    assert hasattr(
        container_repair, "repair_with_journal"
    ), "Row 1001 capability missing: container_repair lacks repair_with_journal transaction logging"
    assert hasattr(
        container_repair, "TransactionJournal"
    ), "Row 1001 capability missing: container_repair lacks TransactionJournal"

    from bulk_downloader.transaction_journal import (
        MutationOp,
        RecordType,
        get_transaction_journal_info,
    )

    info = get_transaction_journal_info()
    assert isinstance(info, dict)
    assert info["version"] >= 1
    assert "supported_ops" in info
    assert MutationOp.CREATE_CONTAINER.value in info["supported_ops"]
    assert MutationOp.UPDATE_CONFIG.value in info["supported_ops"]
    assert RecordType.COMMIT.value == "COMMIT"


def test_record_checksum_and_validation():
    """Verify record checksum verification and tamper resistance."""
    from bulk_downloader.transaction_journal import (
        JournalRecord,
        MutationOp,
        RecordType,
    )

    rec = JournalRecord(
        lsn=1,
        tx_id="tx-101",
        record_type=RecordType.MUTATE,
        op=MutationOp.CREATE_CONTAINER,
        target_id="container-alpha",
        payload={"image": "test:v1", "memory_mb": 512},
    )
    serialized = rec.serialize()
    assert isinstance(serialized, bytes)
    assert rec.checksum != 0

    # Deserialization and checksum validation
    recovered = JournalRecord.deserialize(serialized)
    assert recovered.lsn == 1
    assert recovered.tx_id == "tx-101"
    assert recovered.target_id == "container-alpha"
    assert recovered.payload == {"image": "test:v1", "memory_mb": 512}

    # Tampered payload fails checksum
    tampered = serialized.replace(b"container-alpha", b"container-omega")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        JournalRecord.deserialize(tampered)


def test_transaction_lifecycle_commit(tmp_path: Path):
    """Verify normal begin -> mutate -> commit transaction lifecycle."""
    from bulk_downloader.transaction_journal import (
        MutationOp,
        TransactionJournal,
    )

    journal = TransactionJournal(tmp_path)
    tx_id = journal.begin_transaction()
    assert tx_id.startswith("tx-")

    lsn1 = journal.log_mutation(
        tx_id,
        MutationOp.CREATE_CONTAINER,
        "c-001",
        {"name": "worker-1", "status": "init"},
    )
    assert lsn1 > 0

    lsn2 = journal.log_mutation(
        tx_id,
        MutationOp.SET_STATE,
        "c-001",
        {"status": "running"},
    )
    assert lsn2 > lsn1

    commit_lsn = journal.commit(tx_id)
    assert commit_lsn > lsn2

    # Verify journal stats
    stats = journal.inspect()
    assert stats["active_transactions"] == 0
    assert stats["total_records"] == 4  # BEGIN, 2x MUTATE, COMMIT


def test_transaction_lifecycle_abort(tmp_path: Path):
    """Verify aborted transactions do not apply upon recovery."""
    from bulk_downloader.transaction_journal import (
        MutationOp,
        TransactionJournal,
    )

    journal = TransactionJournal(tmp_path)
    tx_id = journal.begin_transaction()
    journal.log_mutation(
        tx_id,
        MutationOp.CREATE_CONTAINER,
        "c-aborted",
        {"name": "discarded"},
    )
    abort_lsn = journal.abort(tx_id)
    assert abort_lsn > 0

    # Recovery should yield no active container for aborted tx
    recovery = journal.recover()
    assert "c-aborted" not in recovery.container_state
    assert recovery.aborted_transactions == 1
    assert recovery.committed_transactions == 0


def test_crash_recovery_committed_vs_uncommitted(tmp_path: Path):
    """Simulate crash where one transaction committed and another was mid-flight."""
    from bulk_downloader.transaction_journal import (
        MutationOp,
        TransactionJournal,
    )

    # First session
    j1 = TransactionJournal(tmp_path)
    tx_good = j1.begin_transaction()
    j1.log_mutation(tx_good, MutationOp.CREATE_CONTAINER, "c-good", {"v": 1})
    j1.commit(tx_good)

    tx_uncommitted = j1.begin_transaction()
    j1.log_mutation(tx_uncommitted, MutationOp.CREATE_CONTAINER, "c-bad", {"v": 2})
    # Crash occurs before commit/abort!
    del j1

    # Second session recovers state
    j2 = TransactionJournal(tmp_path)
    recovery = j2.recover()

    assert "c-good" in recovery.container_state
    assert recovery.container_state["c-good"]["v"] == 1
    assert "c-bad" not in recovery.container_state
    assert recovery.committed_transactions == 1
    assert recovery.uncommitted_transactions == 1


def test_torn_write_and_corruption_detection(tmp_path: Path):
    """Simulate torn write by appending corrupt trailing garbage."""
    from bulk_downloader.transaction_journal import (
        MutationOp,
        TransactionJournal,
    )

    journal = TransactionJournal(tmp_path)
    tx_id = journal.begin_transaction()
    journal.log_mutation(tx_id, MutationOp.CREATE_CONTAINER, "c-stable", {"status": "ok"})
    journal.commit(tx_id)

    # Append trailing truncated/torn bytes to active journal file
    log_file = journal.active_journal_path
    with open(log_file, "ab") as f:
        f.write(b"\x00\x01\x02CORRUPTED_TORN_BYTES_PARTIAL_WRITE\xff")

    # Recovery must safely ignore torn trailing records and restore valid prefix
    recovered = journal.recover()
    assert "c-stable" in recovered.container_state
    assert recovered.torn_records_discarded >= 1


def test_checkpointing_and_log_compaction(tmp_path: Path):
    """Verify checkpointing serializes state snapshot and trims journal log."""
    from bulk_downloader.transaction_journal import (
        MutationOp,
        TransactionJournal,
    )

    journal = TransactionJournal(tmp_path)
    tx1 = journal.begin_transaction()
    journal.log_mutation(tx1, MutationOp.CREATE_CONTAINER, "c-100", {"val": 100})
    journal.commit(tx1)

    # Checkpoint
    chk_lsn = journal.checkpoint()
    assert chk_lsn > 0

    # New transaction post-checkpoint
    tx2 = journal.begin_transaction()
    journal.log_mutation(tx2, MutationOp.UPDATE_CONFIG, "c-100", {"val": 200})
    journal.commit(tx2)

    # Recover should apply checkpoint snapshot + post-checkpoint WAL records
    j_new = TransactionJournal(tmp_path)
    recovery = j_new.recover()
    assert recovery.container_state["c-100"]["val"] == 200


def test_container_state_reconstruction(tmp_path: Path):
    """Verify complete CRUD lifecycle replay against container state store."""
    from bulk_downloader.transaction_journal import (
        MutationOp,
        TransactionJournal,
    )

    journal = TransactionJournal(tmp_path)
    tx = journal.begin_transaction()
    journal.log_mutation(tx, MutationOp.CREATE_CONTAINER, "pod-1", {"name": "app", "status": "pending"})
    journal.log_mutation(tx, MutationOp.UPDATE_CONFIG, "pod-1", {"cpu": 4, "mem": 8192})
    journal.log_mutation(tx, MutationOp.SET_STATE, "pod-1", {"status": "running"})
    journal.log_mutation(tx, MutationOp.MUTATE_PAYLOAD, "pod-1", {"ports": [80, 443]})
    journal.commit(tx)

    # Delete in second transaction
    tx2 = journal.begin_transaction()
    journal.log_mutation(tx2, MutationOp.DELETE_CONTAINER, "pod-1", {})
    journal.commit(tx2)

    rec = journal.recover()
    assert "pod-1" not in rec.container_state


def test_container_repair_caller_integration(tmp_path: Path):
    """Verify concrete caller bulk_downloader/container_repair.py wires transaction journal."""
    from unittest.mock import patch

    from bulk_downloader.container_repair import RepairResult, repair_with_journal
    from bulk_downloader.transaction_journal import TransactionJournal

    journal = TransactionJournal(tmp_path)
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.touch()
    out = tmp_path / "repaired.mp4"
    out.touch()

    # Success case: commits mutation
    with patch("bulk_downloader.container_repair.repair") as mock_repair:
        mock_repair.return_value = RepairResult(
            recovered=True, output_path=out, reason="recovered"
        )
        res = repair_with_journal(corrupt, output_path=out, journal=journal)
        assert res.recovered is True

    report = journal.recover()
    assert str(corrupt) in report.container_state
    assert report.container_state[str(corrupt)]["status"] == "recovered"
    assert report.committed_transactions == 1

    # Failure case: aborts mutation
    with patch("bulk_downloader.container_repair.repair") as mock_repair:
        mock_repair.return_value = RepairResult(
            recovered=False, output_path=None, reason="ffmpeg error"
        )
        res = repair_with_journal(tmp_path / "fatal.mp4", journal=journal)
        assert res.recovered is False

    report2 = journal.recover()
    assert str(tmp_path / "fatal.mp4") not in report2.container_state
    assert report2.aborted_transactions == 1
