"""Content-Addressed Blob Storage (CAS) Ledger & De-duplication Engine (Row 1062).

Provides cryptographic content-addressed storage for media blobs, parts,
and assets with deduplication tracking, fanout directory pathing, atomic writes,
hardlink materialization, and unreferenced artifact garbage collection.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PathLike = str | Path


@dataclass(frozen=True)
class BlobAddress:
    """Cryptographic address of a content-addressed blob."""
    algorithm: str
    digest: str
    size_bytes: int

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.digest}"


@dataclass(frozen=True)
class PutResult:
    """Outcome of storing or deduplicating a payload into the CAS engine."""
    address: BlobAddress
    path: Path
    is_duplicate: bool
    bytes_saved: int


@dataclass(frozen=True)
class CASStats:
    """Telemetry metrics for the CAS repository."""
    unique_blobs: int
    total_references: int
    stored_bytes: int
    virtual_bytes: int
    bytes_saved: int
    dedup_ratio: float


def compute_digest(
    data: bytes | bytearray | memoryview,
    algorithm: str = "sha256",
) -> BlobAddress:
    """Compute cryptographic digest for arbitrary in-memory buffer."""
    h = hashlib.new(algorithm)
    h.update(data)
    return BlobAddress(
        algorithm=algorithm,
        digest=h.hexdigest(),
        size_bytes=len(data),
    )


def compute_file_digest(
    file_path: PathLike,
    algorithm: str = "sha256",
    chunk_size: int = 64 * 1024,
) -> BlobAddress:
    """Stream and compute cryptographic digest for a file."""
    h = hashlib.new(algorithm)
    total_size = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
            total_size += len(chunk)
    return BlobAddress(
        algorithm=algorithm,
        digest=h.hexdigest(),
        size_bytes=total_size,
    )


class CASLedger:
    """SQLite-backed metadata ledger recording CAS blobs and virtual references."""

    def __init__(self, db_path: PathLike) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """One transaction on a connection that is always closed afterwards."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cas_blobs (
                    digest TEXT PRIMARY KEY,
                    algorithm TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    rel_path TEXT NOT NULL,
                    refcount INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cas_references (
                    ref_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    virtual_path TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cas_refs_digest ON cas_references(digest);
            """)

    def record_put(
        self,
        address: BlobAddress,
        rel_path: str,
        owner: str = "",
        virtual_path: str = "",
    ) -> bool:
        """Record or increment reference to a blob. Returns True if newly stored, False if duplicate."""
        now = time.time()
        ref_id = f"ref-{uuid.uuid4().hex[:12]}"
        with self._lock, self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT refcount FROM cas_blobs WHERE digest = ?", (address.digest,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """INSERT INTO cas_blobs
                       (digest, algorithm, size_bytes, rel_path, refcount, created_at, last_accessed)
                       VALUES (?, ?, ?, ?, 1, ?, ?)""",
                    (address.digest, address.algorithm, address.size_bytes, rel_path, now, now),
                )
                cur.execute(
                    """INSERT INTO cas_references (ref_id, digest, owner, virtual_path, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ref_id, address.digest, owner, virtual_path, now),
                )
                return True
            else:
                cur.execute(
                    "UPDATE cas_blobs SET refcount = refcount + 1, last_accessed = ? WHERE digest = ?",
                    (now, address.digest),
                )
                cur.execute(
                    """INSERT INTO cas_references (ref_id, digest, owner, virtual_path, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ref_id, address.digest, owner, virtual_path, now),
                )
                return False

    def remove_reference(self, digest: str, owner: str = "") -> bool:
        """Drop one reference row; False when no matching reference exists.

        refcount is re-derived from cas_references, the single source of truth,
        so a release that matches no row can never make a live blob prunable.
        """
        with self._lock, self._get_conn() as conn:
            cur = conn.cursor()
            # Owner-scoped even for owner "" (anonymous puts record ""), so a
            # release can only drop a reference its caller actually holds.
            cur.execute(
                "DELETE FROM cas_references WHERE rowid IN (SELECT rowid FROM cas_references WHERE digest = ? AND owner = ? LIMIT 1)",
                (digest, owner),
            )
            if cur.rowcount == 0:
                return False
            cur.execute(
                "UPDATE cas_blobs SET refcount = "
                "(SELECT COUNT(*) FROM cas_references WHERE digest = ?) WHERE digest = ?",
                (digest, digest),
            )
            return True

    def get_unreferenced(self) -> list[dict]:
        """Fetch all blobs with zero active references."""
        with self._lock, self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT digest, rel_path, size_bytes FROM cas_blobs b WHERE NOT EXISTS "
                "(SELECT 1 FROM cas_references r WHERE r.digest = b.digest)"
            )
            return [{"digest": r[0], "rel_path": r[1], "size_bytes": r[2]} for r in cur.fetchall()]

    def delete_blob_record(self, digest: str) -> None:
        """Remove a pruned blob record completely."""
        with self._lock, self._get_conn() as conn:
            conn.execute("DELETE FROM cas_blobs WHERE digest = ?", (digest,))

    def stats(self) -> CASStats:
        """Compute aggregate repository statistics."""
        with self._lock, self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM cas_blobs")
            ublobs, stored_b = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM cas_references")
            total_refs = cur.fetchone()[0]

            cur.execute("""
                SELECT COALESCE(SUM(b.size_bytes), 0)
                FROM cas_references r
                JOIN cas_blobs b ON r.digest = b.digest
            """)
            virtual_b = cur.fetchone()[0]

            bytes_saved = max(0, virtual_b - stored_b)
            ratio = (virtual_b / stored_b) if stored_b > 0 else 1.0
            return CASStats(
                unique_blobs=ublobs,
                total_references=total_refs,
                stored_bytes=stored_b,
                virtual_bytes=virtual_b,
                bytes_saved=bytes_saved,
                dedup_ratio=round(ratio, 2),
            )


class CASDeDuplicationEngine:
    """Content-Addressed Storage engine with automatic deduplication and fanout."""

    def __init__(self, root_dir: PathLike) -> None:
        self.root_dir = Path(root_dir)
        self.blobs_dir = self.root_dir / "blobs"
        self.stage_dir = self.root_dir / "staging"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.stage_dir.mkdir(parents=True, exist_ok=True)

        self.ledger = CASLedger(self.root_dir / "ledger.db")

    def _blob_path(self, address: BlobAddress) -> Path:
        # Two-level 2-character fanout: ab/cd/<hash>
        d = address.digest
        return self.blobs_dir / d[:2] / d[2:4] / d

    def put_bytes(
        self,
        data: bytes,
        owner: str = "",
        virtual_path: str = "",
    ) -> PutResult:
        """Store bytes into CAS, deduplicating if existing."""
        addr = compute_digest(data)
        blob_p = self._blob_path(addr)

        if blob_p.exists():
            # Duplicate
            self.ledger.record_put(addr, str(blob_p.relative_to(self.root_dir)), owner, virtual_path)
            return PutResult(
                address=addr,
                path=blob_p,
                is_duplicate=True,
                bytes_saved=addr.size_bytes,
            )

        # Write to temporary file, then atomic rename
        blob_p.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.stage_dir, delete=False) as tf:
            tf.write(data)
            tmp_name = tf.name

        os.replace(tmp_name, blob_p)
        self.ledger.record_put(addr, str(blob_p.relative_to(self.root_dir)), owner, virtual_path)

        return PutResult(
            address=addr,
            path=blob_p,
            is_duplicate=False,
            bytes_saved=0,
        )

    def put_file(
        self,
        source_path: PathLike,
        owner: str = "",
        virtual_path: str = "",
    ) -> PutResult:
        """Store contents of an existing file into CAS, deduplicating if existing."""
        sp = Path(source_path)
        addr = compute_file_digest(sp)
        blob_p = self._blob_path(addr)

        if blob_p.exists():
            self.ledger.record_put(addr, str(blob_p.relative_to(self.root_dir)), owner, virtual_path)
            return PutResult(
                address=addr,
                path=blob_p,
                is_duplicate=True,
                bytes_saved=addr.size_bytes,
            )

        blob_p.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.stage_dir, delete=False) as tf:
            shutil.copyfile(sp, tf.name)
            tmp_name = tf.name

        os.replace(tmp_name, blob_p)
        self.ledger.record_put(addr, str(blob_p.relative_to(self.root_dir)), owner, virtual_path)

        return PutResult(
            address=addr,
            path=blob_p,
            is_duplicate=False,
            bytes_saved=0,
        )

    def link_blob(self, address: BlobAddress, destination_path: PathLike) -> bool:
        """Materialize the blob at destination_path (hardlink, else copy).

        An existing file at destination_path is replaced.
        """
        bp = self._blob_path(address)
        if not bp.exists():
            return False

        dp = Path(destination_path)
        dp.parent.mkdir(parents=True, exist_ok=True)
        if dp.exists():
            dp.unlink()

        try:
            os.link(bp, dp)
        except OSError:
            shutil.copyfile(bp, dp)
        return True

    def release_reference(self, address: BlobAddress, owner: str = "") -> bool:
        """Release a reference held by an owner."""
        return self.ledger.remove_reference(address.digest, owner)

    def prune_unreferenced(self) -> dict[str, int]:
        """Garbage-collect blobs whose reference count has reached 0.

        A blob whose file cannot be removed keeps its ledger record, so the next
        prune retries it instead of leaving an untracked file on disk; it is
        reported in ``failed_blobs``, not counted as freed.
        """
        unref = self.ledger.get_unreferenced()
        pruned_count = 0
        pruned_bytes = 0
        failed_count = 0

        for item in unref:
            p = self.root_dir / item["rel_path"]
            try:
                p.unlink(missing_ok=True)
            except OSError:
                failed_count += 1
                continue
            self.ledger.delete_blob_record(item["digest"])
            pruned_count += 1
            pruned_bytes += item["size_bytes"]

        return {"pruned_blobs": pruned_count, "pruned_bytes": pruned_bytes, "failed_blobs": failed_count}

    def stats(self) -> CASStats:
        """Return repository statistics."""
        return self.ledger.stats()


_GLOBAL_CAS_ENGINE: CASDeDuplicationEngine | None = None
_GLOBAL_CAS_LOCK = threading.Lock()


def get_global_cas_engine(root_dir: PathLike | None = None) -> CASDeDuplicationEngine:
    """Return the global default CASDeDuplicationEngine singleton."""
    global _GLOBAL_CAS_ENGINE
    with _GLOBAL_CAS_LOCK:
        if _GLOBAL_CAS_ENGINE is None:
            if root_dir is None:
                # Beside the history DB: the app's data root (BD_INSTALL_DIR aware).
                from .db import _resolve_db_path
                root_dir = Path(_resolve_db_path()).resolve().parent / "cas"
            r = root_dir
            _GLOBAL_CAS_ENGINE = CASDeDuplicationEngine(root_dir=r)
        return _GLOBAL_CAS_ENGINE


def cas_deduplicate_bytes(
    data: bytes,
    owner: str = "",
    engine: CASDeDuplicationEngine | None = None,
) -> PutResult:
    """Helper function to store and deduplicate bytes in CAS."""
    e = engine or get_global_cas_engine()
    return e.put_bytes(data, owner=owner)

