"""Row 1062: Content-Addressed Blob Storage (CAS) Ledger & De-duplication Engine.

Verifies:
1. Cryptographic content-addressed storage (BlobAddress, SHA256 fanout pathing).
2. CAS Ledger tracking: blob storage, refcounts, virtual references, and idempotency.
3. Zero-copy de-duplication: storing identical payloads returns is_duplicate=True and increments refcount.
4. Materialization via hardlinks (reflink/hardlink to destination path).
5. Garbage collection / pruning of unreferenced blobs.
6. Telemetry and de-duplication ratio statistics accounting.
7. Integration and caller wiring in bulk_downloader.dedup runtime.
"""
from __future__ import annotations

from pathlib import Path

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader.cas_ledger import (
        BlobAddress,
        CASDeDuplicationEngine,
        CASLedger,
        CASStats,
        PutResult,
        compute_digest,
        get_global_cas_engine,
    )
except ImportError:
    BlobAddress = None  # type: ignore
    CASDeDuplicationEngine = None  # type: ignore
    CASLedger = None  # type: ignore
    CASStats = None  # type: ignore
    PutResult = None  # type: ignore
    compute_digest = None  # type: ignore
    get_global_cas_engine = None  # type: ignore


def test_metadata_and_capabilities():
    """Verify CAS ledger and de-duplication engine capability."""
    from bulk_downloader import dedup

    assert hasattr(
        dedup, "CASDeDuplicationEngine"
    ), "Row 1062 capability missing: CASDeDuplicationEngine not exposed on dedup"
    assert hasattr(
        dedup, "CASLedger"
    ), "Row 1062 capability missing: CASLedger not exposed on dedup"
    assert CASDeDuplicationEngine is not None, "Row 1062 capability missing: CASDeDuplicationEngine not implemented"


def test_compute_digest():
    payload = b"ContentAddressedStoragePayload"
    addr = compute_digest(payload)
    assert isinstance(addr, BlobAddress)
    assert addr.algorithm == "sha256"
    assert len(addr.digest) == 64
    assert addr.size_bytes == len(payload)
    assert str(addr).startswith("sha256:")


def test_cas_put_bytes_and_deduplication(tmp_path: Path):
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")

    payload = b"ReusableMediaBlock_12345"

    # First put: new blob
    res1 = engine.put_bytes(payload, owner="stream_1", virtual_path="/tmp/part1.bin")
    assert isinstance(res1, PutResult)
    assert not res1.is_duplicate
    assert res1.bytes_saved == 0
    assert res1.address.size_bytes == len(payload)
    assert res1.path.exists()
    assert res1.path.read_bytes() == payload

    # Second put of identical bytes: de-duplicated
    res2 = engine.put_bytes(payload, owner="stream_2", virtual_path="/tmp/part2.bin")
    assert res2.is_duplicate
    assert res2.bytes_saved == len(payload)
    assert res2.address == res1.address
    assert res2.path == res1.path

    # Verify stats
    stats = engine.stats()
    assert isinstance(stats, CASStats)
    assert stats.unique_blobs == 1
    assert stats.total_references == 2
    assert stats.stored_bytes == len(payload)
    assert stats.virtual_bytes == len(payload) * 2
    assert stats.bytes_saved == len(payload)
    assert stats.dedup_ratio == 2.0


def test_cas_put_file_and_hardlink(tmp_path: Path):
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")

    source_file = tmp_path / "source_media.dat"
    payload = b"A" * 1024 + b"B" * 1024
    source_file.write_bytes(payload)

    res = engine.put_file(source_file, owner="downloader_task")
    assert res.address.size_bytes == 2048

    # Link to target path
    target_link = tmp_path / "dest" / "linked_media.dat"
    success = engine.link_blob(res.address, target_link)
    assert success
    assert target_link.exists()
    assert target_link.read_bytes() == payload

    # Verify inode match (hardlink)
    assert target_link.stat().st_ino == res.path.stat().st_ino


def test_cas_prune_unreferenced(tmp_path: Path):
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")

    b1 = engine.put_bytes(b"TransientData_1", owner="transient")
    b2 = engine.put_bytes(b"PersistentData_2", owner="persistent")

    # Release reference to b1
    engine.release_reference(b1.address, owner="transient")

    prune_res = engine.prune_unreferenced()
    assert prune_res["pruned_blobs"] == 1
    assert prune_res["pruned_bytes"] == b1.address.size_bytes
    assert not b1.path.exists()
    assert b2.path.exists()


def test_dedup_module_integration(tmp_path: Path):
    """Verify integration through bulk_downloader.dedup."""
    from bulk_downloader.dedup import (
        CASDeDuplicationEngine,
        CASLedger,
        cas_dedup_file,
        cas_deduplicate_bytes,
    )

    assert CASLedger is not None
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")
    res1 = cas_deduplicate_bytes(b"IntegrationTestPayload", engine=engine)
    assert not res1.is_duplicate

    res2 = cas_deduplicate_bytes(b"IntegrationTestPayload", engine=engine)
    assert res2.is_duplicate
    assert res2.bytes_saved == len(b"IntegrationTestPayload")

    test_file = tmp_path / "file_payload.bin"
    test_file.write_bytes(b"FilePayloadData")
    f_res1 = cas_dedup_file(str(test_file), engine=engine)
    assert not f_res1.is_duplicate

    f_res2 = cas_dedup_file(str(test_file), engine=engine)
    assert f_res2.is_duplicate
    assert f_res2.bytes_saved == len(b"FilePayloadData")


def _pruned(engine) -> tuple[int, int]:
    result = engine.prune_unreferenced()
    return result["pruned_blobs"], result["pruned_bytes"]


def test_release_by_non_owner_cannot_delete_blob_with_live_references(tmp_path: Path):
    """E1 data loss: a release matching no reference must not make a referenced blob prunable."""
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")
    payload = b"z" * 8000
    addr = engine.put_bytes(payload, owner="jobA").address
    engine.put_bytes(payload, owner="jobB")

    assert engine.release_reference(addr, owner="nobody") is False
    assert engine.release_reference(addr, owner="nobody") is False
    assert engine.release_reference(addr) is False  # anonymous caller holds no reference either
    assert _pruned(engine) == (0, 0)
    out = tmp_path / "jobA.bin"
    assert engine.link_blob(addr, out) is True
    assert out.read_bytes() == payload

    assert engine.release_reference(addr, owner="jobA") is True
    assert _pruned(engine) == (0, 0)
    assert engine.release_reference(addr, owner="jobB") is True
    assert engine.release_reference(addr, owner="jobB") is False
    assert _pruned(engine) == (1, 8000)
    assert engine.stats().unique_blobs == 0


def test_ledger_closes_every_connection(tmp_path: Path, monkeypatch):
    """E3: each ledger operation closes the sqlite connection it opened."""
    import sqlite3

    from bulk_downloader import cas_ledger

    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(cas_ledger.sqlite3, "connect", tracking_connect)
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")
    addr = engine.put_bytes(b"conn", owner="o").address
    engine.stats()
    engine.release_reference(addr, owner="o")
    engine.prune_unreferenced()

    assert len(opened) >= 5
    still_open = []
    for conn in opened:
        try:
            conn.execute("SELECT 1")
            still_open.append(conn)
        except sqlite3.ProgrammingError:
            pass
    assert still_open == []


def test_global_engine_defaults_to_app_data_root(tmp_path: Path, monkeypatch):
    """E3: the default CAS root sits beside the history DB, not under the user's HOME."""
    from bulk_downloader import cas_ledger, db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "data" / "downloader_history.db"))
    monkeypatch.setattr(cas_ledger, "_GLOBAL_CAS_ENGINE", None)
    engine = cas_ledger.get_global_cas_engine()
    assert engine.root_dir == (tmp_path / "data" / "cas").resolve()
    assert (tmp_path / "data" / "cas" / "ledger.db").is_file()


def test_put_file_records_references_and_dedups(tmp_path: Path):
    """E4: put_file records every owner in the ledger and stores the bytes once."""
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")
    source = tmp_path / "src.bin"
    source.write_bytes(b"P" * 4096)

    first = engine.put_file(source, owner="a")
    second = engine.put_file(source, owner="b")

    assert (first.is_duplicate, second.is_duplicate) == (False, True)
    stats = engine.stats()
    assert (stats.unique_blobs, stats.total_references, stats.stored_bytes, stats.bytes_saved) == (1, 2, 4096, 4096)


def test_link_blob_falls_back_to_copy_when_hardlink_fails(tmp_path: Path, monkeypatch):
    """E4: a cross-device (OSError) hardlink still materializes the bytes by copy."""
    from bulk_downloader import cas_ledger

    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")
    res = engine.put_bytes(b"copy-fallback", owner="o")

    def refuse_link(src, dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(cas_ledger.os, "link", refuse_link)
    dest = tmp_path / "dest" / "out.bin"
    dest.parent.mkdir()
    dest.write_bytes(b"old")
    assert engine.link_blob(res.address, dest) is True
    assert dest.read_bytes() == b"copy-fallback"
    assert dest.stat().st_ino != res.path.stat().st_ino


def test_a_blob_that_cannot_be_removed_keeps_its_record_and_is_retried(tmp_path: Path, monkeypatch):
    """DP-13 (prune_unreferenced): an unlink failure was swallowed, the ledger record deleted and the
    bytes counted as freed -- the file stayed on disk, untracked, forever."""
    engine = CASDeDuplicationEngine(root_dir=tmp_path / "cas")
    blob = engine.put_bytes(b"StuckData_3", owner="transient")
    engine.release_reference(blob.address, owner="transient")

    real_unlink = Path.unlink

    def refuse(self, *a, **k):
        if self == blob.path:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", refuse)
    first = engine.prune_unreferenced()
    assert (first["pruned_blobs"], first["pruned_bytes"], first.get("failed_blobs")) == (0, 0, 1), (
        f"DP-13: a blob whose file could not be removed was reported as pruned: {first}")
    assert blob.path.exists()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    second = engine.prune_unreferenced()
    assert (second["pruned_blobs"], second["pruned_bytes"], second["failed_blobs"]) == (
        1, blob.address.size_bytes, 0), f"DP-13: the stuck blob was not retried: {second}"
    assert not blob.path.exists()

