"""Row 1000: SQLite WAL Page Header Checksum & Torn-Write Detector.

Validates SQLite Write-Ahead Log (WAL) header parsing, 24-byte frame header decoding,
cumulative 64-bit checksum verification, torn-write anomaly classification
(truncated frames, salt mismatch, checksum corruption, trailing garbage),
and durable repair/truncation at safe commit boundaries.

RED on baseline: bulk_downloader.wal_inspector does not exist.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"


def _make_wal_bytes(
    page_size: int = 4096,
    salt1: int = 0x12345678,
    salt2: int = 0x9ABCDEF0,
    frames: list[tuple[int, int, bytes]] | None = None,
    corrupt_header_checksum: bool = False,
) -> bytes:
    """Helper creating raw SQLite WAL bytes with valid checksums."""
    magic = 0x377F0683  # Big-endian checksums

    def checksum_step(data: bytes, s0: int, s1: int) -> tuple[int, int]:
        for i in range(len(data) // 8):
            x0, x1 = struct.unpack_from(">2I", data, i * 8)
            s0 = (s0 + x0 + s1) & 0xFFFFFFFF
            s1 = (s1 + x1 + s0) & 0xFFFFFFFF
        return s0, s1

    # WAL Header (32 bytes)
    hdr_prefix = struct.pack(
        ">IIIIII",
        magic,
        3007000,
        page_size,
        1,  # sequence
        salt1,
        salt2,
    )
    s0, s1 = checksum_step(hdr_prefix, 0, 0)
    if corrupt_header_checksum:
        s0 ^= 0xFFFFFFFF

    wal_data = bytearray(hdr_prefix + struct.pack(">II", s0, s1))

    # Add frames
    if frames:
        for pgno, commit_size, pdata in frames:
            assert len(pdata) == page_size
            frame_hdr_prefix = struct.pack(
                ">IIII",
                pgno,
                commit_size,
                salt1,
                salt2,
            )
            cs0, cs1 = checksum_step(frame_hdr_prefix[:8], s0, s1)
            cs0, cs1 = checksum_step(pdata, cs0, cs1)
            frame_hdr = frame_hdr_prefix + struct.pack(">II", cs0, cs1)
            wal_data.extend(frame_hdr)
            wal_data.extend(pdata)
            s0, s1 = cs0, cs1

    return bytes(wal_data)


def test_module_exports():
    """RED assertion 1: Base must provide SQLite WAL page header checksum and torn-write detector."""
    try:
        from bulk_downloader import wal_inspector
    except ImportError:
        pytest.fail("Base lacks SQLite WAL page header checksum & torn-write detector (bulk_downloader.wal_inspector)")

    assert hasattr(wal_inspector, "WalHeader")
    assert hasattr(wal_inspector, "WalFrameHeader")
    assert hasattr(wal_inspector, "WalFrame")
    assert hasattr(wal_inspector, "TornWriteType")
    assert hasattr(wal_inspector, "TornWriteAnomaly")
    assert hasattr(wal_inspector, "WalInspectionReport")
    assert hasattr(wal_inspector, "WalTornWriteDetector")
    assert hasattr(wal_inspector, "inspect_wal")
    assert hasattr(wal_inspector, "detect_torn_writes")


def test_clean_wal_inspection(tmp_path: Path):
    """Verify clean WAL file passes all checksums and reports healthy."""
    from bulk_downloader.wal_inspector import WalTornWriteDetector

    p1 = b"A" * 4096
    p2 = b"B" * 4096
    raw_wal = _make_wal_bytes(
        page_size=4096,
        frames=[(1, 0, p1), (2, 2, p2)],  # Frame 2 commits transaction of 2 pages
    )
    wal_file = tmp_path / "test.db-wal"
    wal_file.write_bytes(raw_wal)

    detector = WalTornWriteDetector()
    report = detector.inspect_file(wal_file)

    assert report.is_healthy is True
    assert report.valid_frames_count == 2
    assert report.torn_frames_count == 0
    assert report.committed_transactions_count == 1
    assert len(report.anomalies) == 0
    assert report.header is not None
    assert report.header.page_size == 4096


def test_detects_checksum_mismatch(tmp_path: Path):
    """Verify checksum corruption in page data is detected as CHECKSUM_MISMATCH."""
    from bulk_downloader.wal_inspector import TornWriteType, WalTornWriteDetector

    p1 = b"C" * 4096
    raw_wal = bytearray(
        _make_wal_bytes(
            page_size=4096,
            frames=[(1, 1, p1)],
        )
    )
    # Corrupt a byte in page 1
    raw_wal[-100] ^= 0x55
    wal_file = tmp_path / "corrupt_checksum.db-wal"
    wal_file.write_bytes(raw_wal)

    detector = WalTornWriteDetector()
    report = detector.inspect_file(wal_file)

    assert report.is_healthy is False
    assert any(a.anomaly_type == TornWriteType.CHECKSUM_MISMATCH for a in report.anomalies)
    assert report.valid_frames_count == 0
    assert report.torn_frames_count == 1


def test_detects_salt_mismatch(tmp_path: Path):
    """Verify a frame with the previous generation's salts ends the log without an anomaly."""
    from bulk_downloader.wal_inspector import TornWriteType, WalTornWriteDetector

    raw_wal = bytearray(
        _make_wal_bytes(
            page_size=4096,
            salt1=0x11111111,
            salt2=0x22222222,
            frames=[(1, 1, b"X" * 4096)],
        )
    )
    # Tamper frame salt at offset 32 + 8 (after pgno, commit_size)
    raw_wal[40:44] = b"\x00\x00\x00\x00"
    wal_file = tmp_path / "salt_mismatch.db-wal"
    wal_file.write_bytes(raw_wal)

    detector = WalTornWriteDetector()
    report = detector.inspect_file(wal_file)

    # Bounce E1: a frame from an earlier WAL generation (other salt) is where SQLite stops
    # reading -- a normal boundary, not a torn write.
    assert report.is_healthy is True
    assert report.valid_frames_count == 0 and report.stale_frames_count == 1
    assert not any(a.anomaly_type == TornWriteType.SALT_MISMATCH for a in report.anomalies)


def test_detects_truncated_page_torn_write(tmp_path: Path):
    """Verify partially written frame ending prematurely is detected as TRUNCATED_PAGE_DATA."""
    from bulk_downloader.wal_inspector import TornWriteType, WalTornWriteDetector

    raw_wal = _make_wal_bytes(
        page_size=4096,
        frames=[(1, 0, b"1" * 4096), (2, 2, b"2" * 4096)],
    )
    # Cut off 1500 bytes from the end of frame 2
    torn_wal = raw_wal[:-1500]
    wal_file = tmp_path / "torn.db-wal"
    wal_file.write_bytes(torn_wal)

    detector = WalTornWriteDetector()
    report = detector.inspect_file(wal_file)

    assert report.is_healthy is False
    assert report.valid_frames_count == 1
    assert report.torn_frames_count == 1
    assert any(a.anomaly_type == TornWriteType.TRUNCATED_PAGE_DATA for a in report.anomalies)


def test_detects_corrupt_wal_header(tmp_path: Path):
    """Verify corrupted WAL header magic or checksum is flagged immediately."""
    from bulk_downloader.wal_inspector import TornWriteType, WalTornWriteDetector

    raw_wal = _make_wal_bytes(
        page_size=4096,
        corrupt_header_checksum=True,
    )
    wal_file = tmp_path / "corrupt_hdr.db-wal"
    wal_file.write_bytes(raw_wal)

    detector = WalTornWriteDetector()
    report = detector.inspect_file(wal_file)

    assert report.is_healthy is False
    assert any(a.anomaly_type == TornWriteType.CORRUPT_WAL_HEADER for a in report.anomalies)


def test_detects_trailing_garbage_and_repairs(tmp_path: Path):
    """Verify trailing garbage after clean transactions is detected and repaired cleanly."""
    from bulk_downloader.wal_inspector import TornWriteType, WalTornWriteDetector

    raw_wal = _make_wal_bytes(
        page_size=4096,
        frames=[(1, 1, b"Z" * 4096)],
    )
    dirty_wal = raw_wal + b"\xFF\xEE\xDD\xCC\xBB\xAA" * 16  # trailing garbage
    wal_file = tmp_path / "trailing_garbage.db-wal"
    wal_file.write_bytes(dirty_wal)

    detector = WalTornWriteDetector()
    report = detector.inspect_file(wal_file)

    assert report.is_healthy is False
    assert report.valid_frames_count == 1
    assert any(a.anomaly_type == TornWriteType.TRAILING_GARBAGE for a in report.anomalies)
    assert report.recoverable_boundary_offset == len(raw_wal)

    # Perform repair
    repaired_path = tmp_path / "repaired.db-wal"
    detector.repair_wal(wal_file, repaired_path)

    repaired_report = detector.inspect_file(repaired_path)
    assert repaired_report.is_healthy is True
    assert repaired_report.valid_frames_count == 1
    assert len(repaired_report.anomalies) == 0


def test_detects_invalid_page_zero_and_truncated_frame_header(tmp_path: Path):
    """Verify page 0 is flagged as INVALID_PAGE_NUMBER and incomplete frame headers are caught."""
    from bulk_downloader.wal_inspector import TornWriteType, WalTornWriteDetector

    # Case 1: Page 0 in frame header
    raw_wal = _make_wal_bytes(
        page_size=4096,
        frames=[(0, 0, b"0" * 4096)],
    )
    detector = WalTornWriteDetector()
    report1 = detector.inspect_bytes(raw_wal)
    assert report1.is_healthy is False
    assert any(a.anomaly_type == TornWriteType.INVALID_PAGE_NUMBER for a in report1.anomalies)

    # Case 2: Incomplete frame header (12 bytes instead of 24)
    clean_wal = _make_wal_bytes(page_size=4096, frames=[(1, 1, b"X" * 4096)])
    truncated_header_wal = clean_wal + b"\x00" * 12
    report2 = detector.inspect_bytes(truncated_header_wal)
    assert report2.is_healthy is False
    assert any(a.anomaly_type == TornWriteType.TRUNCATED_FRAME_HEADER for a in report2.anomalies)


def test_convenience_functions_and_missing_file(tmp_path: Path):
    """Verify inspect_wal, detect_torn_writes, and missing file error reporting."""
    from bulk_downloader.wal_inspector import (
        TornWriteType,
        detect_torn_writes,
        inspect_wal,
    )

    raw_wal = _make_wal_bytes(page_size=4096, frames=[(1, 1, b"Q" * 4096)])
    report = inspect_wal(raw_wal)
    assert report.is_healthy is True

    # Corrupt a byte
    corrupt_wal = bytearray(raw_wal)
    corrupt_wal[-1] ^= 0xFF
    anomalies = detect_torn_writes(bytes(corrupt_wal))
    assert len(anomalies) > 0
    assert anomalies[0].anomaly_type == TornWriteType.CHECKSUM_MISMATCH

    # Missing file check
    missing_report = inspect_wal(tmp_path / "does_not_exist.db-wal")
    assert missing_report.is_healthy is False
    assert any(a.anomaly_type == TornWriteType.CORRUPT_WAL_HEADER for a in missing_report.anomalies)



# ---------------------------------------------------------------- bounce (N6-A E1-E3)

def _live_wal_after_restart(tmp_path: Path):
    """A real SQLite WAL after a RESTART checkpoint: new frames, then stale frames of the old salt."""
    import sqlite3
    db = tmp_path / "live.db"
    cx = sqlite3.connect(db)
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA wal_autocheckpoint=0")
    cx.execute("PRAGMA synchronous=OFF")   # no fsync: the WAL layout is the same, the test is fast
    cx.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(400):
        cx.execute("INSERT INTO t(v) VALUES (?)", ("x" * 200,))
        cx.commit()
    busy, _log, _ckpt = cx.execute("PRAGMA wal_checkpoint(RESTART)").fetchone()
    assert busy == 0, "fixture: the RESTART checkpoint did not complete"
    for i in range(20):
        cx.execute("INSERT INTO t(v) VALUES (?)", ("y" * 200,))
        cx.commit()
    assert cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return cx, db


def test_wal_reused_after_checkpoint_restart_is_healthy(tmp_path: Path):
    """Bounce E1: ordinary WAL reuse after a checkpoint is healthy; a flipped byte in a live frame is not."""
    from bulk_downloader.wal_inspector import TornWriteType, WalTornWriteDetector
    cx, _ = _live_wal_after_restart(tmp_path)
    try:
        raw = (tmp_path / "live.db-wal").read_bytes()
    finally:
        cx.close()
    report = WalTornWriteDetector().inspect_bytes(raw)
    assert report.is_healthy is True, [a.message for a in report.anomalies]
    assert report.valid_frames_count > 0 and report.stale_frames_count > 0
    assert report.anomalies == []
    # positive control: the same file with one page byte flipped in frame 1 is a checksum mismatch
    bad = bytearray(raw)
    bad[32 + 24 + 100] ^= 0xFF
    bad_report = WalTornWriteDetector().inspect_bytes(bytes(bad))
    assert bad_report.is_healthy is False
    assert bad_report.anomalies[0].anomaly_type == TornWriteType.CHECKSUM_MISMATCH


def test_repair_never_rewrites_the_wal_in_place(tmp_path: Path):
    """Bounce E3: repair writes a separate copy; the live WAL path is refused and left byte-identical."""
    from bulk_downloader.wal_inspector import WalTornWriteDetector
    raw = _make_wal_bytes(page_size=4096, frames=[(1, 1, b"Z" * 4096)]) + b"\xFF" * 50
    wal_file = tmp_path / "inplace.db-wal"
    wal_file.write_bytes(raw)
    det = WalTornWriteDetector()
    with pytest.raises(ValueError):
        det.repair_wal(wal_file)
    with pytest.raises(ValueError):
        det.repair_wal(wal_file, tmp_path / "." / "inplace.db-wal")
    assert wal_file.read_bytes() == raw
    # control: a distinct output path is written and is healthy
    out = tmp_path / "copy.db-wal"
    assert det.repair_wal(wal_file, out) == len(raw) - 50
    assert det.inspect_file(out).is_healthy is True


def _doctor_wal_check(monkeypatch, db_path: Path):
    from bulk_downloader import db as _db
    from bulk_downloader import doctor
    monkeypatch.setattr(_db, "DB_PATH", str(db_path))
    checks = [c for c in doctor.run_diagnostics({})["checks"] if c["test"] == "sqlite_wal"]
    assert len(checks) == 1, "row1000: the doctor runs no WAL torn-write check"
    return checks[0]


def test_doctor_reports_wal_health_through_the_cdc_seam(monkeypatch, tmp_path: Path):
    """Bounce E2: the doctor inspects the history DB's WAL via sqlite_cdc.inspect_wal_health."""
    from bulk_downloader import sqlite_cdc
    cx, db = _live_wal_after_restart(tmp_path)
    try:
        wal = Path(str(db) + "-wal")
        ok = _doctor_wal_check(monkeypatch, db)       # the doctor runs the check, and it is ok
        assert ok["status"] == "ok", ok
        assert sqlite_cdc.inspect_wal_health(str(wal))["is_healthy"] is True
        raw = bytearray(wal.read_bytes())
    finally:
        cx.close()
    # a torn copy of that database: flipped byte inside frame 1
    raw[32 + 24 + 100] ^= 0xFF
    torn_db = tmp_path / "torn.db"
    torn_db.write_bytes(b"")
    Path(str(torn_db) + "-wal").write_bytes(bytes(raw))
    health = sqlite_cdc.inspect_wal_health(str(torn_db) + "-wal")
    assert health["is_healthy"] is False and health["torn_frames"] == 1
    warn = _doctor_wal_check(monkeypatch, torn_db)
    assert warn["status"] == "warn" and "checksum mismatch" in warn["message"].lower(), warn
    # no WAL at all (rollback journal / checkpointed): nothing to report
    plain = tmp_path / "plain.db"
    plain.write_bytes(b"")
    assert _doctor_wal_check(monkeypatch, plain)["status"] == "ok"


# ---------------------------------------------------------------- bounce 2 (A F1, N4-A E1a/E1b)

def _wal_db(tmp_path: Path, name: str):
    import sqlite3
    db = tmp_path / name
    cx = sqlite3.connect(db, check_same_thread=False)
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA wal_autocheckpoint=0")
    cx.execute("PRAGMA synchronous=OFF")
    cx.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    cx.commit()
    return cx, db


def _shm_header(db: Path) -> tuple[int, bytes]:
    """(mxFrame, salts) read straight from <db>-shm, independently of the code under test.

    The -shm starts with two copies of SQLite's 48-byte WalIndexHdr (native byte order):
    isInit at 12, mxFrame (the last committed frame) at 16, the WAL header's salts at 32.
    """
    raw = Path(str(db) + "-shm").read_bytes()[:96]
    assert len(raw) == 96 and raw[:48] == raw[48:] and raw[12] == 1, "fixture: no initialised wal-index"
    return struct.unpack_from("=I", raw, 16)[0], raw[32:40]


def test_truncate_checkpointed_empty_wal_is_healthy(monkeypatch, tmp_path: Path):
    """Bounce 2 E1(a): a 0-byte WAL left by wal_checkpoint(TRUNCATE) is the normal empty log, not a torn write."""
    from bulk_downloader import sqlite_cdc
    from bulk_downloader.wal_inspector import TornWriteType, inspect_wal
    cx, db = _wal_db(tmp_path, "trunc.db")
    try:
        for _ in range(30):
            cx.execute("INSERT INTO t(v) VALUES (?)", ("x" * 300,))
            cx.commit()
        assert cx.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        wal = Path(str(db) + "-wal")
        assert wal.is_file() and wal.stat().st_size == 0     # the fixture built the shape
        health = sqlite_cdc.inspect_wal_health(str(wal))
        assert health["anomalies"] == [], health
        assert (health["is_healthy"], health["valid_frames"], health["torn_frames"]) == (True, 0, 0), health
        check = _doctor_wal_check(monkeypatch, db)
        assert check["status"] == "ok" and check["message"] == "WAL healthy (0 frames)", check
    finally:
        cx.close()
    # the same empty log with no database open on it (no -shm): still the empty log
    empty = inspect_wal(b"")
    assert (empty.is_healthy, empty.header, empty.valid_frames_count, empty.anomalies) == (True, None, 0, [])
    # A F1 "(or header-only)": a WAL holding only its 32-byte header is an empty log too
    header_only = inspect_wal(_make_wal_bytes(page_size=4096))
    assert (header_only.is_healthy, header_only.valid_frames_count, header_only.anomalies) == (True, 0, [])
    # control: a non-empty file shorter than the header is still a corrupt header
    stub = tmp_path / "stub.db-wal"
    stub.write_bytes(b"\x37\x7f\x06")
    assert [a.anomaly_type for a in inspect_wal(stub).anomalies] == [TornWriteType.CORRUPT_WAL_HEADER]
    assert sqlite_cdc.inspect_wal_health(str(stub))["is_healthy"] is False


def test_frame_in_flight_past_the_committed_mxframe_is_not_torn(monkeypatch, tmp_path: Path):
    """Bounce 2 E1(b), deterministic: the bytes a reader of a live WAL sees while a writer is mid-frame.

    SQLite writes a frame's 24-byte header, then its page, and publishes the frame in the -shm
    wal-index (mxFrame) only when its transaction commits; SQLite ignores everything past
    mxFrame. Only frames up to mxFrame can be torn. Positive control: a damaged committed frame.
    """
    from bulk_downloader import doctor, sqlite_cdc
    cx, db = _wal_db(tmp_path, "inflight.db")
    try:
        for _ in range(5):
            cx.execute("INSERT INTO t(v) VALUES (?)", ("c" * 3000,))
            cx.commit()
        wal = Path(str(db) + "-wal")
        committed = wal.read_bytes()
        page_size = struct.unpack(">I", committed[8:12])[0]
        frame = 24 + page_size
        n = (len(committed) - 32) // frame
        mx, salts = _shm_header(db)
        # preconditions: n whole frames, every one committed, and the wal-index describes this log
        assert n > 1 and len(committed) == 32 + n * frame and mx == n and salts == committed[16:24]
        in_flight = {"is_healthy": True, "valid_frames": n, "torn_frames": 0, "anomalies": [],
                     "in_flight_tail": True}

        # (1) frame n+1 has its header on disk and half of its page
        with open(wal, "ab") as f:
            f.write(struct.pack(">II", 2, 0) + salts + b"\x00" * 8 + b"\xab" * (page_size // 2))
        health = sqlite_cdc.inspect_wal_health(str(wal))
        assert health["anomalies"] == [], health
        assert health == in_flight
        check = _doctor_wal_check(monkeypatch, db)
        assert check["status"] == "ok" and check["message"] == f"WAL healthy ({n} frames)", check

        # (2) the whole page is down, the frame is still uncommitted and its checksum does not hold yet
        with open(wal, "ab") as f:
            f.write(b"\xab" * (page_size - page_size // 2))
        assert wal.stat().st_size == 32 + (n + 1) * frame
        health = sqlite_cdc.inspect_wal_health(str(wal))
        assert health["anomalies"] == [], health
        assert health == in_flight
        assert doctor.wal_checks()[0]["status"] == "ok"
        assert _shm_header(db) == (n, salts)                  # nothing was committed meanwhile

        # positive controls: a byte flipped inside a COMMITTED frame is a torn write, and is reported:
        # the last committed frame (frame mxFrame itself) and the first
        pristine = wal.read_bytes()
        for k in (n, 1):
            raw = bytearray(pristine)
            raw[32 + (k - 1) * frame + 24 + 100] ^= 0xFF
            wal.write_bytes(bytes(raw))
            bad = sqlite_cdc.inspect_wal_health(str(wal))
            assert (bad["is_healthy"], bad["valid_frames"], bad["torn_frames"], bad["in_flight_tail"]) == (
                False, k - 1, 1, False), (k, bad)
            assert len(bad["anomalies"]) == 1, bad
            assert bad["anomalies"][0].startswith(f"Frame checksum mismatch at frame {k}:"), bad
        warn = doctor.wal_checks()[0]
        assert warn["status"] == "warn" and warn["detail"]["torn_frames"] == 1, warn
        assert warn["message"].startswith("WAL torn write: Frame checksum mismatch at frame 1:"), warn
    finally:
        cx.close()


def _index_hdr(salts: bytes, mx: int, *, version: int = 3007000, init: int = 1, cksum_delta: int = 0) -> bytes:
    """A 48-byte SQLite WalIndexHdr in native byte order, checksummed as walIndexWriteHdr does."""
    body = struct.pack("=IIIBBHII8s8s", version, 0, 1, init, 0, 512, mx, 3, b"\0" * 8, salts)
    s0 = s1 = 0
    for x0, x1 in struct.iter_unpack("=2I", body):
        s0 = (s0 + x0 + s1) & 0xFFFFFFFF
        s1 = (s1 + x1 + s0) & 0xFFFFFFFF
    return body + struct.pack("=2I", s0, (s1 + cksum_delta) & 0xFFFFFFFF)


def test_only_a_usable_wal_index_of_this_log_bounds_the_scan(tmp_path: Path):
    """Each rule deciding whether the -shm wal-index is used, on one crafted WAL: 2 committed
    frames, then half of frame 3 (header written, half its page). A usable index of this log
    bounds the scan at mxFrame; any other index is ignored and every byte is judged."""
    from bulk_downloader import sqlite_cdc
    page = 512
    good = _make_wal_bytes(page_size=page, frames=[(1, 0, b"a" * page), (2, 2, b"b" * page)])
    salts = good[16:24]
    wal_bytes = good + struct.pack(">II", 3, 0) + salts + b"\0" * 8 + b"c" * (page // 2)
    in_flight = {"is_healthy": True, "valid_frames": 2, "torn_frames": 0, "anomalies": [],
                 "in_flight_tail": True}
    judged = {"is_healthy": False, "valid_frames": 2, "torn_frames": 1,
              "anomalies": ["Truncated page data at frame 3: 256 bytes available, expected 512"],
              "in_flight_tail": False}
    other = b"\x01" * 8
    cases = [
        ("usable index of this log, mxFrame 2", _index_hdr(salts, 2) * 2, in_flight),
        ("restarted log, nothing committed yet", _index_hdr(other, 0) * 2, in_flight),
        ("no -shm", None, judged),
        ("short -shm", _index_hdr(salts, 2) + _index_hdr(salts, 2)[:40], judged),
        ("copies never agree", _index_hdr(salts, 2) + _index_hdr(salts, 3), judged),
        ("not initialised", _index_hdr(salts, 2, init=0) * 2, judged),
        ("unknown version", _index_hdr(salts, 2, version=3007001) * 2, judged),
        ("bad index checksum", _index_hdr(salts, 2, cksum_delta=1) * 2, judged),
        ("index of another log", _index_hdr(other, 2) * 2, judged),
    ]
    got = {}
    for i, (name, shm, _) in enumerate(cases):
        wal = tmp_path / f"c{i}.db-wal"
        wal.write_bytes(wal_bytes)
        if shm is not None:
            (tmp_path / f"c{i}.db-shm").write_bytes(shm)
        got[name] = sqlite_cdc.inspect_wal_health(str(wal))
    assert got == {name: want for name, _, want in cases}
    # a file not named <db>-wal has no wal-index to consult, even if "<stem>-shm" is usable
    odd = tmp_path / "odd.wal"
    odd.write_bytes(wal_bytes)
    (tmp_path / "odd-shm").write_bytes(_index_hdr(salts, 2) * 2)
    assert sqlite_cdc.inspect_wal_health(str(odd)) == judged
    # a WAL path that cannot be read is reported, never passed as healthy
    unreadable = tmp_path / "dir.db-wal"
    unreadable.mkdir()
    bad = sqlite_cdc.inspect_wal_health(str(unreadable))
    assert (bad["is_healthy"], bad["valid_frames"], bad["torn_frames"], len(bad["anomalies"])) == (False, 0, 0, 1)
    assert bad["anomalies"][0] == f"WAL file does not exist: {unreadable}", bad


def test_live_read_retries_when_the_log_restarts_under_it(monkeypatch, tmp_path: Path):
    """A read is judged against mxFrame only when the log did not restart while it was read (a
    restart changes the salts). Restart seen -> read again. A restart under every read -> the
    check reports that it could not run, never a torn write."""
    import itertools

    from bulk_downloader import db as _db
    from bulk_downloader import doctor, sqlite_cdc, wal_inspector
    cx, db = _wal_db(tmp_path, "restart.db")
    try:
        cx.execute("INSERT INTO t(v) VALUES (?)", ("r" * 3000,))
        cx.commit()
        wal = Path(str(db) + "-wal")
        n, salts = _shm_header(db)
        assert n > 0 and wal.read_bytes()[16:24] == salts
        with open(wal, "ab") as f:                             # a frame in flight past mxFrame
            f.write(struct.pack(">II", 2, 0) + salts + b"\x00" * 8 + b"\xab" * 100)
        assert wal_inspector._wal_index_state(Path(str(db) + "-shm")) == (salts, n)

        real = wal_inspector._wal_index_state
        restarted = (b"\x00" * 8, 0)                           # the index of a log that restarted
        script = ["real", restarted]                           # attempt 1: restart between the two reads
        calls = []

        def scripted(shm):
            calls.append(shm)
            step = script.pop(0) if script else "real"
            return real(shm) if step == "real" else step

        monkeypatch.setattr(wal_inspector, "_wal_index_state", scripted)
        health = sqlite_cdc.inspect_wal_health(str(wal))
        assert len(calls) == 4                                 # attempt 1 discarded, attempt 2 judged
        assert health == {"is_healthy": True, "valid_frames": n, "torn_frames": 0, "anomalies": [],
                          "in_flight_tail": True}

        flip = itertools.cycle([(salts, n), restarted])
        calls.clear()
        monkeypatch.setattr(wal_inspector, "_wal_index_state", lambda shm: calls.append(shm) or next(flip))
        with pytest.raises(wal_inspector.WalIndexBusy):
            sqlite_cdc.inspect_wal_health(str(wal))
        assert len(calls) == 2 * wal_inspector._LIVE_READ_ATTEMPTS
        monkeypatch.setattr(_db, "DB_PATH", str(db))
        check = doctor.wal_checks()[0]
        assert check["status"] == "warn" and check["message"].startswith("WAL check could not run:"), check
    finally:
        cx.close()


def test_wal_removed_by_the_last_close_is_not_a_torn_write(tmp_path: Path):
    """SQLite deletes <db>-wal when the last connection closes, so the doctor's isfile() can race
    it. A log that is gone holds no torn write. (The byte-level inspect_wal still reports a
    missing file: its contract is unchanged.)"""
    from bulk_downloader import sqlite_cdc
    from bulk_downloader.wal_inspector import inspect_wal
    cx, db = _wal_db(tmp_path, "closing.db")
    wal = Path(str(db) + "-wal")
    assert wal.is_file()
    cx.close()                                                 # last connection: checkpoint, delete
    assert not wal.exists()                                    # the fixture built the shape
    health = sqlite_cdc.inspect_wal_health(str(wal))
    assert health["anomalies"] == [], health
    assert health == {"is_healthy": True, "valid_frames": 0, "torn_frames": 0, "anomalies": [],
                      "in_flight_tail": False}
    assert inspect_wal(wal).is_healthy is False


def test_doctor_never_reports_a_frame_in_flight_as_torn(monkeypatch, tmp_path: Path):
    """Bounce 2 E1(b), live: 60 doctor reads under a writer thread committing 3 KiB rows: 0 false warns.
    Positive control: a corrupted COMMITTED frame is still reported."""
    import threading
    cx, db = _wal_db(tmp_path, "live2.db")
    stop = threading.Event()
    commits = []

    def writer():
        while not stop.is_set():
            cx.execute("INSERT INTO t(v) VALUES (?)", ("w" * 3072,))
            cx.commit()
            commits.append(1)

    from bulk_downloader import db as _db
    from bulk_downloader import doctor
    monkeypatch.setattr(_db, "DB_PATH", str(db))
    th = threading.Thread(target=writer)
    th.start()
    try:
        # the doctor's own WAL check (run_diagnostics is pinned to include it above)
        results = [doctor.wal_checks()[0] for _ in range(60)]
    finally:
        stop.set()
        th.join(10)
    try:
        assert not th.is_alive() and len(commits) > 0 and len(results) == 60   # the writer ran, then stopped
        warns = [r["message"] for r in results if r["status"] != "ok"]
        assert warns == [], f"row1000: {len(warns)}/60 live reads reported a torn write: {warns[:3]}"
        wal = Path(str(db) + "-wal")
        raw = bytearray(wal.read_bytes())
        raw[32 + 24 + 100] ^= 0xFF                      # frame 1 is committed
        wal.write_bytes(bytes(raw))
        bad = doctor.wal_checks()[0]
        assert bad["status"] == "warn" and "checksum mismatch at frame 1" in bad["message"].lower(), bad
    finally:
        cx.close()


# ---------------------------------------------------------------- fixer self-lens (bd-fixer-B)

_PAGE = 512
_TWO = _make_wal_bytes(page_size=_PAGE, frames=[(1, 0, b"a" * _PAGE), (2, 2, b"b" * _PAGE)])


def _live_health(tmp_path: Path, name: str, wal_bytes: bytes, shm: bytes | None):
    """(inspect_wal_health dict, anomaly types, report) of <name>.db-wal, with <name>.db-shm if given."""
    from bulk_downloader import sqlite_cdc
    from bulk_downloader.wal_inspector import inspect_live_wal
    wal = tmp_path / f"{name}.db-wal"
    wal.write_bytes(wal_bytes)
    if shm is not None:
        (tmp_path / f"{name}.db-shm").write_bytes(shm)
    report = inspect_live_wal(wal)
    return sqlite_cdc.inspect_wal_health(str(wal)), [a.anomaly_type for a in report.anomalies], report


def test_the_wal_index_says_which_frames_must_be_valid(tmp_path: Path):
    """A usable wal-index of this log says which frames are committed: every frame up to mxFrame
    must be there and of this generation. Recovery drops the log from the first frame that is
    not, losing committed transactions -- a torn write, even where the byte-level inspector (no
    index) has to read a foreign salt as the normal end of the log."""
    from bulk_downloader import sqlite_cdc
    from bulk_downloader.wal_inspector import TornWriteType, inspect_wal
    page, two = _PAGE, _TWO
    salts = two[16:24]
    # a whole frame 3 of an earlier generation (other salts): what a restart leaves past the new frames
    older = _make_wal_bytes(page_size=page, salt1=0x0BADF00D, salt2=0x0DDBA11,
                            frames=[(1, 0, b"c" * page), (2, 0, b"d" * page), (3, 3, b"e" * page)])
    with_stale = two + older[32 + 2 * (24 + page):]

    def health(name, wal_bytes, shm):
        return _live_health(tmp_path, name, wal_bytes, shm)

    ok2 = {"is_healthy": True, "valid_frames": 2, "torn_frames": 0, "anomalies": [], "in_flight_tail": False}

    # frame 3 is committed (mxFrame 3) but carries another generation's salts
    h, types, _ = health("stale3", with_stale, _index_hdr(salts, 3) * 2)
    assert h == {"is_healthy": False, "valid_frames": 2, "torn_frames": 1, "in_flight_tail": False,
                 "anomalies": ["Frame salt mismatch at frame 3: the wal-index says 3 frames are committed"]}
    assert types == [TornWriteType.SALT_MISMATCH]
    # controls: past mxFrame the same frame is the normal end of the log; with no index it cannot be told apart
    for name, shm in (("stale2", _index_hdr(salts, 2) * 2), ("stale-noshm", None)):
        h, types, report = health(name, with_stale, shm)
        assert (h, types, report.stale_frames_count) == (ok2, [], 1), name
    # frame 3 is committed but the log ends after frame 2
    h, types, _ = health("short", two, _index_hdr(salts, 3) * 2)
    assert h == {"is_healthy": False, "valid_frames": 2, "torn_frames": 1, "in_flight_tail": False,
                 "anomalies": ["WAL ends after frame 2: the wal-index says 3 frames are committed"]}
    assert types == [TornWriteType.TRUNCATED_FRAME_HEADER]
    assert health("whole", two, _index_hdr(salts, 2) * 2)[0] == ok2

    # the same rule on a real database: one salt byte of COMMITTED frame 2 damaged
    cx, db = _wal_db(tmp_path, "salt.db")
    try:
        for _ in range(4):
            cx.execute("INSERT INTO t(v) VALUES (?)", ("s" * 3000,))
            cx.commit()
        wal = Path(str(db) + "-wal")
        raw = bytearray(wal.read_bytes())
        n, live_salts = _shm_header(db)
        frame = 24 + struct.unpack(">I", raw[8:12])[0]
        assert n >= 3 and len(raw) == 32 + n * frame and live_salts == raw[16:24]   # the fixture built the shape
        raw[32 + frame + 8] ^= 0x01                            # frame 2's salt-1
        wal.write_bytes(bytes(raw))
        assert sqlite_cdc.inspect_wal_health(str(wal)) == {
            "is_healthy": False, "valid_frames": 1, "torn_frames": 1, "in_flight_tail": False,
            "anomalies": [f"Frame salt mismatch at frame 2: the wal-index says {n} frames are committed"]}
        # the byte-level inspector has no wal-index: it cannot tell that frame from an earlier generation
        blind = inspect_wal(bytes(raw))
        assert (blind.is_healthy, blind.valid_frames_count, blind.stale_frames_count) == (True, 1, n - 1)
    finally:
        cx.close()


def test_with_nothing_committed_a_torn_header_is_in_flight(monkeypatch, tmp_path: Path):
    """A log that just restarted (wal-index: new salts, mxFrame 0) has nothing committed, so nothing in
    the WAL can be torn -- not even its header, which the next writer rewrites in place. With committed
    frames behind it, or with no index at all, a header that fails its checksum is a corrupt header."""
    from bulk_downloader.wal_inspector import TornWriteType, inspect_wal
    salts, other = _TWO[16:24], b"\x01" * 8
    torn_hdr = bytearray(_TWO)
    torn_hdr[28] ^= 0xFF                                       # the header's checksum does not hold
    torn_hdr = bytes(torn_hdr)
    h, types, _ = _live_health(tmp_path, "hdr-restarted", torn_hdr, _index_hdr(other, 0) * 2)
    assert (h, types) == ({"is_healthy": True, "valid_frames": 0, "torn_frames": 0, "anomalies": [],
                           "in_flight_tail": True}, [])
    for name, shm in (("hdr-committed", _index_hdr(salts, 2) * 2), ("hdr-noshm", None)):
        h, types, _ = _live_health(tmp_path, name, torn_hdr, shm)
        assert (h["is_healthy"], h["valid_frames"], h["in_flight_tail"], types) == (
            False, 0, False, [TornWriteType.CORRUPT_WAL_HEADER]), (name, h)
        assert h["anomalies"][0].startswith("WAL header checksum mismatch"), (name, h)

    # a real database: wal_checkpoint(TRUNCATE) restarts the wal-index (new salts, mxFrame 0), the state a
    # writer restarting the log sets before it rewrites the header over the old log; that old log, read
    # while its header is half rewritten (the index's new salts, the old checksum), through the doctor
    cx, db = _wal_db(tmp_path, "rewrite.db")
    try:
        for _ in range(3):
            cx.execute("INSERT INTO t(v) VALUES (?)", ("h" * 300,))
            cx.commit()
        wal = Path(str(db) + "-wal")
        old_log = wal.read_bytes()
        assert cx.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        mx, new_salts = _shm_header(db)
        assert (wal.stat().st_size, mx) == (0, 0) and new_salts != old_log[16:24]  # the fixture built the shape
        wal.write_bytes(old_log[:16] + new_salts + old_log[24:])
        check = _doctor_wal_check(monkeypatch, db)
        assert check["status"] == "ok" and check["message"] == "WAL healthy (0 frames)", check
        # control: read blind, these very bytes are a corrupt header
        assert [a.anomaly_type for a in inspect_wal(wal).anomalies] == [TornWriteType.CORRUPT_WAL_HEADER]
    finally:
        cx.close()


def test_doctor_never_reports_a_torn_write_while_a_writer_restarts_the_log(monkeypatch, tmp_path: Path):
    """Bounce 2 E1(b) in N4-A's probe shape: the writer has its OWN connection with autocheckpoint on,
    so under the doctor's reads the log keeps restarting (new salts in the wal-index, the WAL header
    rewritten, frames overwritten from the start, older frames left behind them). 0 warns.
    Positive control: in that post-restart shape a damaged committed frame is still reported."""
    import sqlite3
    import threading

    from bulk_downloader import db as _db
    from bulk_downloader import doctor
    cx, db = _wal_db(tmp_path, "restarts.db")                 # stays open and idle: -wal/-shm persist
    _, salts0 = _shm_header(db)
    stop = threading.Event()
    commits = []

    def writer():
        w = sqlite3.connect(db)
        w.execute("PRAGMA synchronous=OFF")
        w.execute("PRAGMA wal_autocheckpoint=20")             # restart every ~20 frames (product: 1000)
        try:
            while not stop.is_set():
                w.execute("INSERT INTO t(v) VALUES (?)", ("r" * 3072,))
                w.commit()
                commits.append(1)
        finally:
            w.close()

    monkeypatch.setattr(_db, "DB_PATH", str(db))
    th = threading.Thread(target=writer)
    th.start()
    results = []
    try:
        while (len(results) < 40 or len(commits) < 100) and len(results) < 2000:
            results.append(doctor.wal_checks()[0])
    finally:
        stop.set()
        th.join(10)
    try:
        assert not th.is_alive() and len(commits) >= 100 and len(results) >= 40  # the writer ran, then stopped
        n, salts1 = _shm_header(db)
        assert salts1 != salts0, "fixture: the log never restarted under the reads"
        warns = [r["message"] for r in results if r["status"] != "ok"]
        assert warns == [], f"row1000: {len(warns)}/{len(results)} reads under restarts warned: {warns[:3]}"
        wal = Path(str(db) + "-wal")
        raw = bytearray(wal.read_bytes())
        assert n > 0 and raw[16:24] == salts1                  # a committed log of the current generation
        assert doctor.wal_checks()[0]["status"] == "ok"
        raw[32 + 24 + 100] ^= 0xFF                             # frame 1 is committed
        wal.write_bytes(bytes(raw))
        bad = doctor.wal_checks()[0]
        assert bad["status"] == "warn" and bad["message"].startswith(
            "WAL torn write: Frame checksum mismatch at frame 1:"), bad
    finally:
        cx.close()


# ---------------------------------------------------------------- verify-r1 (bd-fixer-B): the -shm locks

_LOCKS_HELD_ELSEWHERE = r"""
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
held = []
for name, start, length in (("DMS", 128, 1), ("READ", 124, 4)):
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, length, start)
    except OSError:
        held.append(name)
    else:
        fcntl.lockf(fd, fcntl.LOCK_UN, length, start)
print(" ".join(held) or "none")
"""

_CHECKPOINT_TRUNCATE = r"""
import sqlite3, sys
cx = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
print(*cx.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
cx.close()
"""


def _second_process(code: str, arg: Path) -> str:
    """stdout of `code` run by another Python process, which holds none of this process's locks."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-c", code, str(arg)],
                       capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.mark.skipif(os.name != "posix", reason="POSIX fcntl() locks (Windows locks belong to a handle)")
def test_the_doctors_wal_check_keeps_the_sqlite_locks_of_its_own_process(monkeypatch, tmp_path: Path):
    """verify-r1: the doctor runs in the server, whose own SQLite connections hold POSIX locks on
    <db>-shm: DMS (the wal-index is in use) and READ(i) (an open reader's snapshot). close() of ANY
    descriptor this process has on that file releases them all (sqlite.org/howtocorrupt.html s2.2);
    another process could then truncate the WAL under the open reader. Seen from a second process after
    the doctor's WAL checks: the same locks are held, its wal_checkpoint(TRUNCATE) stays BUSY with the
    WAL whole, and the open read transaction still reads every row."""
    import sqlite3

    from bulk_downloader import db as _db
    from bulk_downloader import doctor, sqlite_cdc
    db = tmp_path / "server.db"
    shm, wal = Path(str(db) + "-shm"), Path(str(db) + "-wal")
    w = sqlite3.connect(db, isolation_level=None)                  # the server's pooled writer, idle
    w.execute("PRAGMA journal_mode=WAL")
    w.execute("PRAGMA wal_autocheckpoint=0")
    w.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    w.execute("BEGIN")
    w.executemany("INSERT INTO t(v) VALUES (?)", [("x" * 200,)] * 500)
    w.execute("COMMIT")
    rd = sqlite3.connect(db, isolation_level=None)                 # a request thread, mid-read
    try:
        rd.execute("BEGIN")
        assert rd.execute("SELECT count(*) FROM t").fetchone() == (500,)
        assert _second_process(_LOCKS_HELD_ELSEWHERE, shm) == "DMS READ"   # the fixture built the shape
        monkeypatch.setattr(_db, "DB_PATH", str(db))
        for _ in range(3):
            check = doctor.wal_checks()[0]
            assert check["status"] == "ok", check
        assert sqlite_cdc.inspect_wal_health(str(wal))["is_healthy"] is True
        held = _second_process(_LOCKS_HELD_ELSEWHERE, shm)
        assert held == "DMS READ", (
            f"row1000: the doctor's WAL check released this process's SQLite locks on {shm.name}: "
            f"a second process sees {held!r} held, want 'DMS READ'")
        size = wal.stat().st_size
        busy = _second_process(_CHECKPOINT_TRUNCATE, db).split()[0]
        assert (busy, wal.stat().st_size) == ("1", size), "a second process truncated the WAL under a reader"
        assert rd.execute("SELECT count(*), sum(length(v)) FROM t").fetchone() == (500, 100000)
    finally:
        rd.close()
        w.close()


def _descriptors_on(path: Path) -> tuple[int, int]:
    """(this process's descriptors on the file at `path`, on a deleted file that was at `path`): /proc."""
    real = os.path.realpath(path)
    live = deleted = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        live += target == real
        deleted += target == f"{real} (deleted)"
    return live, deleted


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="counts descriptors through Linux /proc")
def test_the_wal_check_keeps_one_descriptor_per_live_shm(tmp_path: Path):
    """verify-r1, the fix's own bound: a -shm is read through ONE kept descriptor, reused by every check
    (never one per read), and closed once SQLite has deleted that -shm (its last connection closed, so
    no lock is left on it). A kept number closed, or reused for another file, behind the check's back
    is forgotten -- never closed by the check -- and a fresh descriptor is opened."""
    import sqlite3

    from bulk_downloader import sqlite_cdc, wal_inspector
    cx, db = _wal_db(tmp_path, "kept.db")
    shm, wal = Path(str(db) + "-shm"), Path(str(db) + "-wal")

    def check():
        assert sqlite_cdc.inspect_wal_health(str(wal))["is_healthy"] is True

    def kept():
        st = os.stat(shm)
        return [fd for fd, key in wal_inspector._kept_shm_fds.items() if key == (st.st_dev, st.st_ino)]

    try:
        cx.execute("INSERT INTO t(v) VALUES ('k')")
        cx.commit()
        sqlite_own = _descriptors_on(shm)[0]                   # SQLite's own descriptor on the -shm
        for _ in range(5):
            check()
        assert _descriptors_on(shm) == (sqlite_own + 1, 0) and len(kept()) == 1
        n, other = kept()[0], tmp_path / "other"
        other.write_bytes(b"")
        src = os.open(other, os.O_RDONLY)
        os.dup2(src, n)                                        # the kept number now names another file
        os.close(src)
        check()
        assert os.readlink(f"/proc/self/fd/{n}") == os.path.realpath(other)   # not closed by the check
        assert _descriptors_on(shm) == (sqlite_own + 1, 0) and n not in kept()
        os.close(n)
        os.close(kept()[0])                                    # the kept number closed behind its back
        check()
        assert _descriptors_on(shm) == (sqlite_own + 1, 0) and len(kept()) == 1
    finally:
        cx.close()                                             # last connection: -wal and -shm deleted
    assert not shm.exists()
    assert sqlite_cdc.inspect_wal_health(str(wal))["valid_frames"] == 0
    assert _descriptors_on(shm) == (0, 0)                      # the deleted -shm's descriptor was closed
    cx = sqlite3.connect(db)                                   # a new -shm at the same path
    try:
        cx.execute("INSERT INTO t(v) VALUES ('k2')")
        cx.commit()
        check()
        assert _descriptors_on(shm) == (sqlite_own + 1, 0) and len(kept()) == 1
    finally:
        cx.close()


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="counts descriptors through Linux /proc")
def test_an_empty_or_unreadable_shm_is_no_index_and_windows_reads_and_closes(monkeypatch, tmp_path: Path):
    """verify-r1, the fix's other branches. A -shm that is empty (SQLite creates it so, then sizes it), no
    regular file, or cannot be opened, is no usable wal-index: every byte of the WAL is judged. Where a
    lock belongs to a handle (Windows: _KEEP_SHM_OPEN False) the -shm is read and closed each time,
    with the same judgments."""
    from bulk_downloader import sqlite_cdc, wal_inspector
    salts = _TWO[16:24]
    wal_bytes = _TWO + struct.pack(">II", 3, 0) + salts + b"\0" * 8 + b"c" * (_PAGE // 2)   # half of frame 3
    in_flight = {"is_healthy": True, "valid_frames": 2, "torn_frames": 0, "anomalies": [], "in_flight_tail": True}
    judged = {"is_healthy": False, "valid_frames": 2, "torn_frames": 1, "in_flight_tail": False,
              "anomalies": ["Truncated page data at frame 3: 256 bytes available, expected 512"]}

    def health(name):
        wal = tmp_path / f"{name}.db-wal"
        wal.write_bytes(wal_bytes)
        return sqlite_cdc.inspect_wal_health(str(wal))

    (tmp_path / "empty.db-shm").write_bytes(b"")
    assert health("empty") == judged
    (tmp_path / "dir.db-shm").mkdir()
    assert health("dir") == judged
    if os.geteuid() != 0:                                      # root opens a mode-000 file anyway
        locked = tmp_path / "locked.db-shm"
        locked.write_bytes(_index_hdr(salts, 2) * 2)
        locked.chmod(0)
        try:
            assert health("locked") == judged
        finally:
            locked.chmod(0o600)
    monkeypatch.setattr(wal_inspector, "_KEEP_SHM_OPEN", False)
    shm = tmp_path / "handle.db-shm"
    shm.write_bytes(_index_hdr(salts, 2) * 2)
    assert health("handle") == in_flight
    assert _descriptors_on(shm) == (0, 0)
    shm.unlink()
    assert health("handle") == judged
