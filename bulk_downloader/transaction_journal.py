"""Crash-Consistent Write-Ahead Transaction Journal for Container Mutations.

Provides ACID-compliant write-ahead logging (WAL), CRC32-checksummed serialization,
atomic transactions, crash recovery with torn-write detection, and checkpoint compaction
for container mutations in BulkDownloader.
"""
from __future__ import annotations

import json
import os
import time
import zlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RecordType(str, Enum):
    """Transaction log record types."""
    BEGIN = "BEGIN"
    MUTATE = "MUTATE"
    COMMIT = "COMMIT"
    ABORT = "ABORT"
    CHECKPOINT = "CHECKPOINT"


class MutationOp(str, Enum):
    """Supported container mutation operations."""
    CREATE_CONTAINER = "CREATE_CONTAINER"
    UPDATE_CONFIG = "UPDATE_CONFIG"
    SET_STATE = "SET_STATE"
    DELETE_CONTAINER = "DELETE_CONTAINER"
    MUTATE_PAYLOAD = "MUTATE_PAYLOAD"


@dataclass
class JournalRecord:
    """Individual write-ahead log record with CRC32 integrity verification."""
    lsn: int
    tx_id: str
    record_type: RecordType
    op: MutationOp = MutationOp.CREATE_CONTAINER
    target_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    checksum: int = 0

    def compute_checksum(self) -> int:
        """Compute CRC32 checksum over the record's payload and headers."""
        data = {
            "lsn": self.lsn,
            "tx_id": self.tx_id,
            "record_type": self.record_type.value,
            "op": self.op.value,
            "target_id": self.target_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }
        encoded = json.dumps(data, sort_keys=True).encode("utf-8")
        return zlib.crc32(encoded) & 0xFFFFFFFF

    def serialize(self) -> bytes:
        """Serialize record to framed binary format: 4-byte length + JSON payload."""
        if self.checksum == 0:
            self.checksum = self.compute_checksum()

        record_dict = {
            "lsn": self.lsn,
            "tx_id": self.tx_id,
            "record_type": self.record_type.value,
            "op": self.op.value,
            "target_id": self.target_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "checksum": self.checksum,
        }
        body = json.dumps(record_dict, sort_keys=True).encode("utf-8")
        length_header = len(body).to_bytes(4, byteorder="big")
        return length_header + body

    @classmethod
    def deserialize(cls, data: bytes) -> JournalRecord:
        """Deserialize and validate framed binary record with checksum verification."""
        if len(data) < 4:
            raise ValueError("Data too short for framed record")

        length = int.from_bytes(data[:4], byteorder="big")
        if len(data) < 4 + length:
            raise ValueError(f"Truncated frame: expected {4 + length} bytes, got {len(data)}")

        body = data[4 : 4 + length]
        record_dict = json.loads(body.decode("utf-8"))

        stored_checksum = record_dict.pop("checksum", 0)
        rec = cls(
            lsn=record_dict["lsn"],
            tx_id=record_dict["tx_id"],
            record_type=RecordType(record_dict["record_type"]),
            op=MutationOp(record_dict["op"]),
            target_id=record_dict.get("target_id", ""),
            payload=record_dict.get("payload", {}),
            timestamp=record_dict.get("timestamp", 0.0),
            checksum=stored_checksum,
        )

        expected_checksum = rec.compute_checksum()
        if stored_checksum != expected_checksum:
            raise ValueError(
                f"Checksum mismatch: stored {stored_checksum} != expected {expected_checksum}"
            )
        return rec


@dataclass
class RecoveryReport:
    """Detailed summary of journal replay and state reconstruction."""
    container_state: dict[str, Any] = field(default_factory=dict)
    committed_transactions: int = 0
    uncommitted_transactions: int = 0
    aborted_transactions: int = 0
    torn_records_discarded: int = 0
    recovered_records: int = 0


def get_transaction_journal_info() -> dict[str, Any]:
    """Capability introspection for the container transaction journal."""
    return {
        "version": 1,
        "supported_ops": [op.value for op in MutationOp],
        "supported_records": [rt.value for rt in RecordType],
        "checksum_algorithm": "CRC32",
        "sync_policy": "fsync_on_commit",
    }


class TransactionJournal:
    """Crash-consistent write-ahead transaction journal for container mutations."""

    def __init__(self, journal_dir: str | Path, sync_on_write: bool = True) -> None:
        self.journal_dir = Path(journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.sync_on_write = sync_on_write
        self.active_journal_path = self.journal_dir / "wal.log"
        self.checkpoint_path = self.journal_dir / "checkpoint.json"

        self._current_lsn = 0
        self._active_txs: set[str] = set()
        self._total_records = 0

        # Scan existing log to determine last LSN if file exists
        if self.active_journal_path.exists():
            self._scan_header_lsn()

    def _scan_header_lsn(self) -> None:
        """Quickly scan log file to restore max LSN and record count."""
        try:
            with open(self.active_journal_path, "rb") as f:
                content = f.read()
            offset = 0
            while offset + 4 <= len(content):
                length = int.from_bytes(content[offset : offset + 4], byteorder="big")
                if offset + 4 + length > len(content):
                    break
                try:
                    rec = JournalRecord.deserialize(content[offset : offset + 4 + length])
                    self._current_lsn = max(self._current_lsn, rec.lsn)
                    self._total_records += 1
                except (ValueError, OSError, json.JSONDecodeError):
                    break
                offset += 4 + length
        except OSError:
            pass

    def _next_lsn(self) -> int:
        self._current_lsn += 1
        return self._current_lsn

    def _append_record(self, record: JournalRecord) -> int:
        """Atomically append serialized record to WAL file."""
        data = record.serialize()
        with open(self.active_journal_path, "ab") as f:
            f.write(data)
            if self.sync_on_write:
                f.flush()
                os.fsync(f.fileno())
        self._total_records += 1
        return record.lsn

    def begin_transaction(self, tx_id: str | None = None) -> str:
        """Start a new transaction and log BEGIN record."""
        t_id = tx_id or f"tx-{int(time.time() * 1000)}-{os.urandom(3).hex()}"
        rec = JournalRecord(
            lsn=self._next_lsn(),
            tx_id=t_id,
            record_type=RecordType.BEGIN,
        )
        self._append_record(rec)
        self._active_txs.add(t_id)
        return t_id

    def log_mutation(
        self,
        tx_id: str,
        op: MutationOp,
        target_id: str,
        payload: dict[str, Any],
    ) -> int:
        """Append a container mutation record under the active transaction."""
        rec = JournalRecord(
            lsn=self._next_lsn(),
            tx_id=tx_id,
            record_type=RecordType.MUTATE,
            op=op,
            target_id=target_id,
            payload=payload,
        )
        return self._append_record(rec)

    def commit(self, tx_id: str) -> int:
        """Commit active transaction with durable sync barrier."""
        rec = JournalRecord(
            lsn=self._next_lsn(),
            tx_id=tx_id,
            record_type=RecordType.COMMIT,
        )
        lsn = self._append_record(rec)
        self._active_txs.discard(tx_id)
        return lsn

    def abort(self, tx_id: str) -> int:
        """Abort active transaction."""
        rec = JournalRecord(
            lsn=self._next_lsn(),
            tx_id=tx_id,
            record_type=RecordType.ABORT,
        )
        lsn = self._append_record(rec)
        self._active_txs.discard(tx_id)
        return lsn

    def checkpoint(self, snapshot_data: dict[str, Any] | None = None) -> int:
        """Persist snapshot to checkpoint.json and truncate replayed WAL."""
        report = self.recover()
        state_to_save = snapshot_data if snapshot_data is not None else report.container_state

        chk_rec = {
            "checkpoint_lsn": self._current_lsn,
            "timestamp": time.time(),
            "container_state": state_to_save,
        }
        tmp_chk = self.journal_dir / "checkpoint.tmp"
        with open(tmp_chk, "w", encoding="utf-8") as f:
            json.dump(chk_rec, f)
            f.flush()
            os.fsync(f.fileno())
        tmp_chk.replace(self.checkpoint_path)

        # Clear WAL log after snapshot persistence
        with open(self.active_journal_path, "wb") as f:
            f.flush()
            os.fsync(f.fileno())
        self._total_records = 0
        return self._current_lsn

    def recover(self) -> RecoveryReport:
        """Replay journal from checkpoint and log, ignoring uncommitted and torn writes."""
        container_state: dict[str, Any] = {}
        if self.checkpoint_path.exists():
            try:
                with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                    chk = json.load(f)
                    container_state = chk.get("container_state", {})
            except (OSError, json.JSONDecodeError):
                pass

        if not self.active_journal_path.exists():
            return RecoveryReport(container_state=container_state)

        with open(self.active_journal_path, "rb") as f:
            content = f.read()

        valid_records: list[JournalRecord] = []
        torn_count = 0
        offset = 0
        while offset < len(content):
            if offset + 4 > len(content):
                torn_count += 1
                break
            length = int.from_bytes(content[offset : offset + 4], byteorder="big")
            if offset + 4 + length > len(content):
                torn_count += 1
                break
            chunk = content[offset : offset + 4 + length]
            try:
                rec = JournalRecord.deserialize(chunk)
                valid_records.append(rec)
            except (ValueError, OSError, json.JSONDecodeError):
                torn_count += 1
                break
            offset += 4 + length

        # Group records by tx_id to determine outcomes
        tx_status: dict[str, str] = {}
        tx_mutations: dict[str, list[JournalRecord]] = {}

        for rec in valid_records:
            t = rec.tx_id
            if t not in tx_mutations:
                tx_mutations[t] = []

            if rec.record_type == RecordType.COMMIT:
                tx_status[t] = "COMMIT"
            elif rec.record_type == RecordType.ABORT:
                tx_status[t] = "ABORT"
            elif rec.record_type == RecordType.MUTATE:
                tx_mutations[t].append(rec)

        committed_count = 0
        aborted_count = 0
        uncommitted_count = 0

        for t, status in tx_status.items():
            if status == "COMMIT":
                committed_count += 1
                # Apply mutations in order
                for m in tx_mutations.get(t, []):
                    cid = m.target_id
                    if m.op == MutationOp.CREATE_CONTAINER:
                        container_state[cid] = dict(m.payload)
                    elif m.op == MutationOp.DELETE_CONTAINER:
                        container_state.pop(cid, None)
                    elif m.op in (MutationOp.UPDATE_CONFIG, MutationOp.SET_STATE, MutationOp.MUTATE_PAYLOAD):
                        if cid not in container_state:
                            container_state[cid] = {}
                        container_state[cid].update(m.payload)
            elif status == "ABORT":
                aborted_count += 1

        for t in tx_mutations:
            if t not in tx_status:
                uncommitted_count += 1

        return RecoveryReport(
            container_state=container_state,
            committed_transactions=committed_count,
            uncommitted_transactions=uncommitted_count,
            aborted_transactions=aborted_count,
            torn_records_discarded=torn_count,
            recovered_records=len(valid_records),
        )

    def inspect(self) -> dict[str, Any]:
        """Inspect current memory and storage stats for journal."""
        return {
            "active_transactions": len(self._active_txs),
            "total_records": self._total_records,
            "current_lsn": self._current_lsn,
            "wal_size_bytes": self.active_journal_path.stat().st_size
            if self.active_journal_path.exists()
            else 0,
        }
