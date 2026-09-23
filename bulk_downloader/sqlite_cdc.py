"""bulk_downloader.sqlite_cdc -- Row 880: transactional SQLite -> Postgres CDC.

Reads the SQLite WAL file's own frame stream to find committed transaction
boundaries (the same mechanism SQLite itself uses for crash recovery), then
diffs the tracked tables' current row state against the last-known snapshot
and streams the resulting row-level INSERT/UPDATE/DELETE events to Postgres.

Design constraints (mirrors bulk_downloader.pg_backend / db_replication):

  1. **DEFAULT OFF.** No ``SQLITE_CDC_PG_DSN`` -> streaming is a no-op; psycopg
     is never imported. ``poll()`` still runs the WAL/diff logic (so tests can
     exercise it against a ``FakeSink``) but ``PostgresSink`` degrades to a
     recorded no-op without a DSN.
  2. **FAIL-OPEN on the Postgres side.** A dead server or missing driver never
     raises out of ``poll()``; it is counted and observable via ``stats()``.
  3. **WAL frame parsing is byte-exact and self-verifying.** Frames are
     checksummed with SQLite's own algorithm; a torn/partial tail (a
     transaction whose frames exist but were never completed with a valid
     commit frame) is never surfaced -- this is what gives "clean recovery on
     restart" its safety, mirroring SQLite's own WAL-recovery rule of stopping
     at the first frame that fails to validate.
  4. **Full-snapshot diff, not incremental page tracking.** WAL frames only
     prove *when* a transaction committed and *which pages* it touched;
     mapping pages back to individual rows requires decoding SQLite's B-tree
     page format, which this cut does not attempt. Instead, each detected
     commit triggers a full re-read of the tracked tables (through the normal
     ``sqlite3`` connection, which already merges WAL + main file correctly)
     and a diff against the last snapshot. This is what makes "zero data loss
     under concurrent writes" structural rather than timing-dependent: any
     number of transactions between two polls are all captured by the single
     diff on the next poll, and restart recovery is the same diff seeded from
     a persisted snapshot instead of an in-memory one.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import sqlite3
import struct
import tempfile
import threading
from dataclasses import dataclass

from .wal_inspector import inspect_live_wal

# ── WAL frame parsing ────────────────────────────────────────────────────

_WAL_HEADER_SIZE = 32
_WAL_FRAME_HEADER_SIZE = 24
_WAL_MAGIC_LE = 0x377F0682
_WAL_MAGIC_BE = 0x377F0683


@dataclass(frozen=True)
class WalFrame:
    """One validated WAL frame: a single page write plus its transaction
    metadata. ``commit_size`` is nonzero iff this frame ends a transaction."""
    index: int
    page_number: int
    commit_size: int
    offset: int
    page_data: bytes


@dataclass(frozen=True)
class WalTransaction:
    """A run of consecutive frames ending at a commit frame. ``pages`` is the
    ordered, deduplicated (last-write-wins) list of page numbers touched."""
    frames: tuple
    pages: tuple
    commit_size: int


def _checksum_step(data: bytes, s0: int, s1: int, bigendian: bool) -> tuple:
    fmt = ">2I" if bigendian else "<2I"
    for i in range(len(data) // 8):
        x0, x1 = struct.unpack_from(fmt, data, i * 8)
        s0 = (s0 + x0 + s1) & 0xFFFFFFFF
        s1 = (s1 + x1 + s0) & 0xFFFFFFFF
    return s0, s1


def iter_wal_frames(wal_path: str):
    """Yield validated ``WalFrame`` records from a WAL file, in file order.

    Stops (without raising) at the first structurally short read or checksum
    mismatch -- an incomplete trailing write is exactly what SQLite's own
    reader ignores during recovery, so this generator never yields a frame
    from a transaction that was not durably completed.
    """
    try:
        with open(wal_path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return
    if len(data) < _WAL_HEADER_SIZE:
        return
    magic, _ver, page_size, _seq, salt1, salt2, c1, c2 = struct.unpack(
        ">IIIIIIII", data[:_WAL_HEADER_SIZE])
    if magic not in (_WAL_MAGIC_LE, _WAL_MAGIC_BE) or page_size <= 0:
        return
    bigendian = magic == _WAL_MAGIC_BE
    hs0, hs1 = _checksum_step(data[:24], 0, 0, bigendian)
    if (hs0, hs1) != (c1, c2):
        return  # header itself is corrupt; nothing in this WAL is trustworthy

    s0, s1 = c1, c2
    off = _WAL_HEADER_SIZE
    frame_size = _WAL_FRAME_HEADER_SIZE + page_size
    index = 0
    while off + frame_size <= len(data):
        pgno, commit_size, fsalt1, fsalt2, fc1, fc2 = struct.unpack(
            ">IIIIII", data[off:off + _WAL_FRAME_HEADER_SIZE])
        page = data[off + _WAL_FRAME_HEADER_SIZE:off + frame_size]
        if (fsalt1, fsalt2) != (salt1, salt2):
            return  # a checkpoint recycled this WAL mid-file; stop here
        cs0, cs1 = _checksum_step(data[off:off + 8], s0, s1, bigendian)
        cs0, cs1 = _checksum_step(page, cs0, cs1, bigendian)
        if (cs0, cs1) != (fc1, fc2):
            return  # unverified frame -- treat as a torn/in-flight write
        s0, s1 = cs0, cs1
        index += 1
        yield WalFrame(index=index, page_number=pgno, commit_size=commit_size,
                        offset=off, page_data=page)
        off += frame_size


def iter_wal_transactions(wal_path: str):
    """Group validated frames into committed transactions.

    A trailing run of frames with no terminating commit frame (a transaction
    still in flight, or one whose commit frame failed validation) is
    discarded -- there is nothing durable to replay yet.
    """
    pending: list = []
    for frame in iter_wal_frames(wal_path):
        pending.append(frame)
        if frame.commit_size:
            pages: list = []
            seen = set()
            for f in reversed(pending):
                if f.page_number not in seen:
                    seen.add(f.page_number)
                    pages.append(f.page_number)
            pages.reverse()
            yield WalTransaction(frames=tuple(pending), pages=tuple(pages),
                                  commit_size=frame.commit_size)
            pending = []


# ── Postgres sink (fail-open, default-off) ───────────────────────────────

_DSN_ENV = "SQLITE_CDC_PG_DSN"


def _ident(name: str) -> str:
    """row880 fixer (3): quote a SQL identifier, doubling embedded quotes so a
    table/column name can never break out of its quoting."""
    return '"' + str(name).replace('"', '""') + '"'


class PostgresSink:
    """Applies row-level change events to Postgres. Never raises: a dead
    server or missing driver is recorded via ``stats()``, matching
    ``pg_backend``'s fail-open contract for a store that is not authoritative.
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn
        self._lock = threading.Lock()
        self._stats = {"applied": 0, "failed": 0, "degraded_reason": None}

    def _resolve_dsn(self):
        return self._dsn or (os.environ.get(_DSN_ENV) or "").strip() or None

    def enabled(self) -> bool:
        return self._resolve_dsn() is not None

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def _degrade(self, reason: str) -> None:
        with self._lock:
            self._stats["degraded_reason"] = reason

    def _connect(self):
        dsn = self._resolve_dsn()
        if not dsn:
            return None
        try:
            import psycopg
        except Exception as e:
            self._degrade(f"psycopg unavailable ({type(e).__name__})")
            return None
        try:
            return psycopg.connect(dsn, connect_timeout=5)
        except Exception as e:
            self._degrade(f"connect failed ({type(e).__name__})")
            return None

    def ensure_table(self, table: str, pk: str, columns) -> bool:
        cx = self._connect()
        if cx is None:
            return False
        cols_sql = ", ".join(f'{_ident(c)} TEXT' for c in columns if c != pk)
        try:
            with cx:
                cx.execute(
                    f'CREATE TABLE IF NOT EXISTS {_ident(table)} '
                    f'({_ident(pk)} TEXT PRIMARY KEY{", " + cols_sql if cols_sql else ""})')
            return True
        except Exception as e:
            self._degrade(f"ensure_table failed ({type(e).__name__})")
            return False
        finally:
            cx.close()

    def apply_events(self, table: str, pk: str, events) -> bool:
        """Apply a list of ``{"op": "upsert"|"delete", "pk": ..., "row": {...}}``
        events for one table. Best-effort: returns False (never raises) if the
        mirror is unavailable."""
        if not events:
            return True
        cx = self._connect()
        if cx is None:
            with self._lock:
                self._stats["failed"] += len(events)
            return False
        try:
            with cx:
                for ev in events:
                    if ev["op"] == "delete":
                        cx.execute(f'DELETE FROM {_ident(table)} WHERE {_ident(pk)} = %s',
                                   (ev["pk"],))
                    else:
                        row = ev["row"]
                        cols = list(row.keys())
                        placeholders = ", ".join(["%s"] * len(cols))
                        col_sql = ", ".join(_ident(c) for c in cols)
                        update_sql = ", ".join(
                            f'{_ident(c)} = EXCLUDED.{_ident(c)}' for c in cols if c != pk)
                        sql = (f'INSERT INTO {_ident(table)} ({col_sql}) '
                               f'VALUES ({placeholders}) '
                               f'ON CONFLICT ({_ident(pk)}) DO UPDATE SET {update_sql}'
                               if update_sql else
                               f'INSERT INTO {_ident(table)} ({col_sql}) '
                               f'VALUES ({placeholders}) '
                               f'ON CONFLICT ({_ident(pk)}) DO NOTHING')
                        cx.execute(sql, tuple(row[c] for c in cols))
            with self._lock:
                self._stats["applied"] += len(events)
            return True
        except Exception as e:
            self._degrade(f"apply failed ({type(e).__name__})")
            with self._lock:
                self._stats["failed"] += len(events)
            return False
        finally:
            cx.close()


# ── row-level diff + stream ──────────────────────────────────────────────

def _read_table_snapshot(db_path: str, table: str, pk: str) -> dict:
    """Current {pk_value: row_dict} for one table, read through a normal
    connection (which merges WAL + main file transparently)."""
    cx = sqlite3.connect(db_path)
    cx.row_factory = sqlite3.Row
    try:
        rows = cx.execute(f'SELECT * FROM {_ident(table)}').fetchall()
    finally:
        cx.close()
    out = {}
    for r in rows:
        d = {k: _cell(v) for k, v in dict(r).items()}
        out[str(d[pk])] = d
    return out


_BLOB_TAG = "base64:"


def _cell(value):
    """SQLite cell -> mirror/state value. Every mirror column is TEXT and the
    snapshot is persisted as JSON, so a BLOB travels as a tagged base64
    string (round-trippable; equal blobs compare equal in the diff)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _BLOB_TAG + base64.b64encode(bytes(value)).decode("ascii")
    return value


def _table_columns(db_path: str, table: str) -> list:
    cx = sqlite3.connect(db_path)
    try:
        return [r[1] for r in cx.execute(f'PRAGMA table_info({_ident(table)})').fetchall()]
    finally:
        cx.close()


def _diff_snapshots(old: dict, new: dict) -> list:
    events = []
    for pk_val, row in new.items():
        if pk_val not in old or old[pk_val] != row:
            events.append({"op": "upsert", "pk": pk_val, "row": row})
    for pk_val in old:
        if pk_val not in new:
            events.append({"op": "delete", "pk": pk_val, "row": None})
    return events


def _write_json_atomic(path: str, obj) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".sqlite-cdc.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


class SqliteCDCStream:
    """Polls a SQLite WAL file for committed transactions and streams
    row-level changes for the tracked tables to ``sink``.

    ``state_path`` persists the last-applied snapshot per table so a fresh
    instance (a process restart) resumes from exactly where the previous one
    left off: it neither replays already-mirrored rows nor loses ones written
    while no process was polling.
    """

    def __init__(self, db_path: str, tables, sink: PostgresSink | None = None,
                 state_path: str | None = None):
        self.db_path = db_path
        self.wal_path = db_path + "-wal"
        self.tables = list(tables)  # [(table, pk), ...]
        self.sink = sink if sink is not None else PostgresSink()
        self.state_path = state_path or (db_path + ".cdc-state.json")
        self._snapshots = self._load_state()
        self._last_frame_index = 0
        self._ensured = set()  # tables whose mirror exists (row880 fixer E1)

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            pass
        else:
            return doc["snapshots"] if (isinstance(doc, dict)
                                         and isinstance(doc.get("snapshots"), dict)) else {}
        return {}

    def _save_state(self) -> None:
        _write_json_atomic(self.state_path, {"schema": "sqlite-cdc/1",
                                              "snapshots": self._snapshots})

    def poll(self) -> dict:
        """Detect newly-committed WAL transactions and stream row-level
        diffs for the tracked tables. Returns a summary dict. Safe to call
        with an empty/absent WAL (yields zero transactions, zero events)."""
        transactions = list(iter_wal_transactions(self.wal_path))
        events_total = 0
        for table, pk in self.tables:
            old = self._snapshots.get(table, {})
            new = _read_table_snapshot(self.db_path, table, pk)
            events = _diff_snapshots(old, new)
            if events:
                # row880 fixer (1): the snapshot -- and the persisted state --
                # advance ONLY when the sink accepted the events. A sink that
                # is down (apply_events False) leaves the old snapshot in
                # place so the same diff is re-derived and re-attempted on
                # the next poll; nothing is dropped.
                # row880 fixer (E1): the stream bootstraps its own mirror --
                # a fresh Postgres has no table until ensure_table ran; once
                # per table per process, retried on the next poll if it fails.
                if table not in self._ensured:
                    if not self.sink.ensure_table(table, pk, _table_columns(self.db_path, table)):
                        continue
                    self._ensured.add(table)
                if not self.sink.apply_events(table, pk, events):
                    continue
                events_total += len(events)
            self._snapshots[table] = new
        self._save_state()
        return {"transactions": len(transactions), "events": events_total,
                "tables": [t for t, _ in self.tables]}


def inspect_wal_health(wal_path: str) -> dict:
    """Inspect SQLite WAL health and detect torn writes via wal_inspector.

    The WAL may belong to a running database: only frames its wal-index says are
    committed are judged (wal_inspector.inspect_live_wal), so a frame a writer is still
    appending reports in_flight_tail, not a torn write. Raises wal_inspector.WalIndexBusy
    when the log restarted under every read attempt (no consistent read was possible).
    """
    report = inspect_live_wal(wal_path)
    return {
        "is_healthy": report.is_healthy,
        "valid_frames": report.valid_frames_count,
        "torn_frames": report.torn_frames_count,
        "anomalies": [a.message for a in report.anomalies],
        "in_flight_tail": report.in_flight_tail,
    }
