"""wal_inspector -- SQLite Write-Ahead Log (WAL) Page Header Checksum & Torn-Write Detector.

Provides low-level inspection of SQLite WAL headers, 24-byte frame headers, cumulative
64-bit checksum verification, and torn-write anomaly classification (checksum corruption,
salt mismatch, truncated frames, trailing garbage), with automated recovery truncation.
inspect_live_wal judges the WAL of a database open in another process, or in this one: only the
frames its -shm wal-index says are committed, so a frame a writer is still appending is not a torn
write. It reads the -shm without releasing this process's own SQLite locks on it (_read_shm_head).
"""
from __future__ import annotations

import enum
import logging
import os
import stat
import struct
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WAL_HEADER_SIZE = 32
_WAL_FRAME_HEADER_SIZE = 24
_WAL_MAGIC_LE = 0x377F0682
_WAL_MAGIC_BE = 0x377F0683


class TornWriteType(str, enum.Enum):
    """Classification of WAL corruption or torn-write defects."""
    CORRUPT_WAL_HEADER = "CORRUPT_WAL_HEADER"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    SALT_MISMATCH = "SALT_MISMATCH"
    TRUNCATED_FRAME_HEADER = "TRUNCATED_FRAME_HEADER"
    TRUNCATED_PAGE_DATA = "TRUNCATED_PAGE_DATA"
    INVALID_PAGE_NUMBER = "INVALID_PAGE_NUMBER"
    TRAILING_GARBAGE = "TRAILING_GARBAGE"


@dataclass
class TornWriteAnomaly:
    """Detailed anomaly diagnostic."""
    anomaly_type: TornWriteType
    offset: int
    frame_index: int | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WalHeader:
    """Parsed SQLite WAL 32-byte header."""
    magic: int
    version: int
    page_size: int
    sequence: int
    salt1: int
    salt2: int
    checksum1: int
    checksum2: int
    is_big_endian: bool


@dataclass(frozen=True)
class WalFrameHeader:
    """Parsed SQLite WAL 24-byte frame header."""
    page_number: int
    commit_size: int
    salt1: int
    salt2: int
    checksum1: int
    checksum2: int


@dataclass
class WalFrame:
    """Decoded WAL frame record."""
    index: int
    offset: int
    header: WalFrameHeader
    page_data: bytes
    is_valid: bool
    is_commit: bool


@dataclass
class WalInspectionReport:
    """Full health and diagnostic report for a WAL stream."""
    is_healthy: bool
    header: WalHeader | None = None
    frames: list[WalFrame] = field(default_factory=list)
    anomalies: list[TornWriteAnomaly] = field(default_factory=list)
    valid_frames_count: int = 0
    torn_frames_count: int = 0
    stale_frames_count: int = 0
    in_flight_tail: bool = False
    committed_transactions_count: int = 0
    recoverable_boundary_offset: int = 0


class WalTornWriteDetector:
    """Detects torn writes, checksum failures, and corruption in SQLite WAL files."""

    @staticmethod
    def checksum_step(data: bytes, s0: int, s1: int, big_endian: bool) -> tuple[int, int]:
        """Compute one step of SQLite's 64-bit WAL checksum algorithm."""
        fmt = ">2I" if big_endian else "<2I"
        num_pairs = len(data) // 8
        for i in range(num_pairs):
            x0, x1 = struct.unpack_from(fmt, data, i * 8)
            s0 = (s0 + x0 + s1) & 0xFFFFFFFF
            s1 = (s1 + x1 + s0) & 0xFFFFFFFF
        return s0, s1

    def inspect_bytes(self, data: bytes, committed_frames: int | None = None) -> WalInspectionReport:
        """Inspect in-memory raw WAL byte stream.

        committed_frames: the wal-index mxFrame of a live database (see inspect_live_wal).
        When given, exactly that many frames must be valid. An anomaly found only AFTER
        them -- in the header too, when it is 0 -- is in a frame no transaction has
        committed: SQLite ignores it, so it is reported as in_flight_tail, not as a torn
        write. A log that ends, or reaches a frame of another generation, before them
        has lost committed frames: that is a torn write.
        """
        report = self._scan(data)
        if committed_frames is None:
            return report
        if report.valid_frames_count >= committed_frames:
            if report.anomalies:
                report.anomalies, report.torn_frames_count = [], 0
                report.is_healthy = report.in_flight_tail = True
        elif not report.anomalies:
            k = report.valid_frames_count + 1
            stale = report.stale_frames_count > 0
            report.anomalies.append(
                TornWriteAnomaly(
                    anomaly_type=TornWriteType.SALT_MISMATCH if stale else TornWriteType.TRUNCATED_FRAME_HEADER,
                    offset=report.recoverable_boundary_offset,
                    frame_index=k,
                    message=(f"Frame salt mismatch at frame {k}" if stale else f"WAL ends after frame {k - 1}")
                    + f": the wal-index says {committed_frames} frames are committed",
                )
            )
            report.torn_frames_count, report.is_healthy = 1, False
        return report

    def _scan(self, data: bytes) -> WalInspectionReport:
        """Judge every byte of a WAL image: the header, then frames until the log ends."""
        anomalies: list[TornWriteAnomaly] = []
        frames: list[WalFrame] = []
        total_len = len(data)

        # A 0-byte WAL is the empty log that wal_checkpoint(TRUNCATE) leaves behind.
        if total_len == 0:
            return WalInspectionReport(is_healthy=True, recoverable_boundary_offset=0)

        if total_len < _WAL_HEADER_SIZE:
            anomalies.append(
                TornWriteAnomaly(
                    anomaly_type=TornWriteType.CORRUPT_WAL_HEADER,
                    offset=0,
                    message=f"File smaller than minimum WAL header size (got {total_len} bytes, expected >= 32)",
                )
            )
            return WalInspectionReport(
                is_healthy=False,
                anomalies=anomalies,
                recoverable_boundary_offset=0,
            )

        # Parse 32-byte header
        magic, ver, page_size, seq, salt1, salt2, c1, c2 = struct.unpack(
            ">IIIIIIII", data[:_WAL_HEADER_SIZE]
        )

        if magic not in (_WAL_MAGIC_LE, _WAL_MAGIC_BE) or page_size <= 0:
            anomalies.append(
                TornWriteAnomaly(
                    anomaly_type=TornWriteType.CORRUPT_WAL_HEADER,
                    offset=0,
                    message=f"Invalid WAL magic (0x{magic:08X}) or invalid page size ({page_size})",
                )
            )
            return WalInspectionReport(
                is_healthy=False,
                anomalies=anomalies,
                recoverable_boundary_offset=0,
            )

        big_endian = (magic == _WAL_MAGIC_BE)
        hs0, hs1 = self.checksum_step(data[:24], 0, 0, big_endian)
        if (hs0, hs1) != (c1, c2):
            anomalies.append(
                TornWriteAnomaly(
                    anomaly_type=TornWriteType.CORRUPT_WAL_HEADER,
                    offset=24,
                    message=f"WAL header checksum mismatch (computed 0x{hs0:08X}:0x{hs1:08X}, expected 0x{c1:08X}:0x{c2:08X})",
                )
            )
            return WalInspectionReport(
                is_healthy=False,
                anomalies=anomalies,
                recoverable_boundary_offset=0,
            )

        header = WalHeader(
            magic=magic,
            version=ver,
            page_size=page_size,
            sequence=seq,
            salt1=salt1,
            salt2=salt2,
            checksum1=c1,
            checksum2=c2,
            is_big_endian=big_endian,
        )

        s0, s1 = c1, c2
        off = _WAL_HEADER_SIZE
        frame_size = _WAL_FRAME_HEADER_SIZE + page_size
        frame_index = 0
        valid_frames = 0
        torn_frames = 0
        committed_tx = 0
        stale_frames = 0
        last_valid_offset = _WAL_HEADER_SIZE

        while off < total_len:
            # Check for partial frame header
            if off + _WAL_FRAME_HEADER_SIZE > total_len:
                torn_frames += 1
                anomalies.append(
                    TornWriteAnomaly(
                        anomaly_type=TornWriteType.TRUNCATED_FRAME_HEADER,
                        offset=off,
                        frame_index=frame_index + 1,
                        message=(
                            f"Truncated frame header at offset {off}: "
                            f"{total_len - off} bytes available, expected {_WAL_FRAME_HEADER_SIZE}"
                        ),
                    )
                )
                break

            pgno, commit_size, fsalt1, fsalt2, fc1, fc2 = struct.unpack(
                ">IIIIII", data[off:off + _WAL_FRAME_HEADER_SIZE]
            )

            frame_hdr = WalFrameHeader(
                page_number=pgno,
                commit_size=commit_size,
                salt1=fsalt1,
                salt2=fsalt2,
                checksum1=fc1,
                checksum2=fc2,
            )

            # Check page number sanity
            if pgno == 0:
                torn_frames += 1
                anomalies.append(
                    TornWriteAnomaly(
                        anomaly_type=TornWriteType.INVALID_PAGE_NUMBER,
                        offset=off,
                        frame_index=frame_index + 1,
                        message="Invalid database page number 0 in frame header",
                    )
                )
                break

            # Check for partial page data or trailing garbage
            if off + frame_size > total_len:
                torn_frames += 1
                if (fsalt1, fsalt2) == (salt1, salt2) and pgno > 0:
                    anomalies.append(
                        TornWriteAnomaly(
                            anomaly_type=TornWriteType.TRUNCATED_PAGE_DATA,
                            offset=off + _WAL_FRAME_HEADER_SIZE,
                            frame_index=frame_index + 1,
                            message=(
                                f"Truncated page data at frame {frame_index + 1}: "
                                f"{total_len - (off + _WAL_FRAME_HEADER_SIZE)} bytes available, expected {page_size}"
                            ),
                        )
                    )
                else:
                    anomalies.append(
                        TornWriteAnomaly(
                            anomaly_type=TornWriteType.TRAILING_GARBAGE,
                            offset=off,
                            frame_index=frame_index + 1,
                            message=f"Trailing unaligned garbage at offset {off} ({total_len - off} bytes)",
                        )
                    )
                break

            page_data = data[off + _WAL_FRAME_HEADER_SIZE:off + frame_size]

            # Salts that differ from the header's belong to an earlier WAL generation: a
            # checkpoint that restarts the log rewrites the header and overwrites from the
            # start, leaving the old frames behind. SQLite stops reading here by design, so
            # this is the normal end of the log, not a torn write.
            if (fsalt1, fsalt2) != (salt1, salt2):
                stale_frames = (total_len - off) // frame_size
                off = total_len
                break

            # Verify frame checksum (first 8 bytes of header + page data)
            cs0, cs1 = self.checksum_step(data[off:off + 8], s0, s1, big_endian)
            cs0, cs1 = self.checksum_step(page_data, cs0, cs1, big_endian)
            if (cs0, cs1) != (fc1, fc2):
                torn_frames += 1
                anomalies.append(
                    TornWriteAnomaly(
                        anomaly_type=TornWriteType.CHECKSUM_MISMATCH,
                        offset=off + 16,
                        frame_index=frame_index + 1,
                        message=(
                            f"Frame checksum mismatch at frame {frame_index + 1}: "
                            f"computed 0x{cs0:08X}:0x{cs1:08X}, expected 0x{fc1:08X}:0x{fc2:08X}"
                        ),
                    )
                )
                break

            # Frame is valid
            frame_index += 1
            valid_frames += 1
            s0, s1 = cs0, cs1
            is_commit = commit_size > 0
            if is_commit:
                committed_tx += 1

            frames.append(
                WalFrame(
                    index=frame_index,
                    offset=off,
                    header=frame_hdr,
                    page_data=page_data,
                    is_valid=True,
                    is_commit=is_commit,
                )
            )

            off += frame_size
            last_valid_offset = off

        # Trailing unaligned garbage check
        if off < total_len and not anomalies:
            anomalies.append(
                TornWriteAnomaly(
                    anomaly_type=TornWriteType.TRAILING_GARBAGE,
                    offset=off,
                    message=f"Trailing unparsed garbage after frame {frame_index} ({total_len - off} bytes)",
                )
            )

        is_healthy = (len(anomalies) == 0) and (torn_frames == 0)

        return WalInspectionReport(
            is_healthy=is_healthy,
            header=header,
            frames=frames,
            anomalies=anomalies,
            valid_frames_count=valid_frames,
            torn_frames_count=torn_frames,
            stale_frames_count=stale_frames,
            committed_transactions_count=committed_tx,
            recoverable_boundary_offset=last_valid_offset,
        )

    def inspect_file(self, path: str | Path) -> WalInspectionReport:
        """Inspect a WAL file on disk."""
        p = Path(path)
        if not p.is_file():
            return WalInspectionReport(
                is_healthy=False,
                anomalies=[
                    TornWriteAnomaly(
                        anomaly_type=TornWriteType.CORRUPT_WAL_HEADER,
                        offset=0,
                        message=f"WAL file does not exist: {p}",
                    )
                ],
                recoverable_boundary_offset=0,
            )
        try:
            data = p.read_bytes()
            return self.inspect_bytes(data)
        except OSError as exc:
            return WalInspectionReport(
                is_healthy=False,
                anomalies=[
                    TornWriteAnomaly(
                        anomaly_type=TornWriteType.CORRUPT_WAL_HEADER,
                        offset=0,
                        message=f"Failed reading WAL file: {exc}",
                    )
                ],
                recoverable_boundary_offset=0,
            )

    def repair_wal(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
    ) -> int:
        """Write a copy of a torn WAL file truncated to its last recoverable frame boundary.

        Never rewrites the input: on a live database the -shm index still references
        the frames a truncation would remove, so the input path is refused.
        Returns the size of the repaired WAL in bytes.
        """
        inp = Path(input_path)
        if output_path is None:
            raise ValueError("repair_wal needs an output path; the WAL is never rewritten in place")
        out = Path(output_path)
        if out.resolve() == inp.resolve():
            raise ValueError(f"repair_wal refuses to rewrite the WAL in place: {inp}")

        report = self.inspect_file(inp)
        valid_bytes = report.recoverable_boundary_offset

        data = inp.read_bytes()
        repaired_data = data[:valid_bytes]

        tmp = out.with_name(out.name + ".tmp")
        tmp.write_bytes(repaired_data)
        os.replace(tmp, out)
        logger.info(
            "Repaired WAL %s: truncated from %d to %d bytes (retained %d valid frames)",
            inp,
            len(data),
            len(repaired_data),
            report.valid_frames_count,
        )
        return len(repaired_data)


def inspect_wal(path_or_bytes: str | Path | bytes) -> WalInspectionReport:
    """Convenience inspector returning a full WalInspectionReport."""
    detector = WalTornWriteDetector()
    if isinstance(path_or_bytes, bytes):
        return detector.inspect_bytes(path_or_bytes)
    return detector.inspect_file(path_or_bytes)


_WAL_INDEX_HDR_SIZE = 48          # SQLite WalIndexHdr; the -shm starts with two copies of it
_WAL_INDEX_VERSION = 3007000
_INDEX_READ_TRIES = 64            # re-reads while a writer is updating the two copies
_LIVE_READ_ATTEMPTS = 16          # bracketed WAL reads before giving up on a restarting log


class WalIndexBusy(RuntimeError):
    """The log restarted during every attempt to read it: no consistent read was possible."""


# POSIX fcntl() locks belong to a process and a file: close() of ANY descriptor the process has on
# a file releases every lock the process holds on it (sqlite.org/howtocorrupt.html s2.2). The doctor
# runs in the server, whose own SQLite connections hold locks on <db>-shm -- DMS (the wal-index is in
# use) and READ(i) (an open reader's snapshot); with them gone another process could truncate the WAL
# under those readers, or re-initialise the -shm they have mapped. So on POSIX a -shm is read through
# one descriptor that is kept, and closed only once SQLite has deleted that file (the last connection
# closed, so no lock is left on it). Windows locks belong to a handle: our own handle may be closed.
_KEEP_SHM_OPEN = os.name == "posix"
_kept_shm_fds: dict[int, tuple[int, int]] = {}   # kept fd -> (st_dev, st_ino) of the -shm it reads
_kept_shm_lock = threading.Lock()


def _kept_shm_fd(shm: Path) -> int | None:
    """The kept read-only descriptor on the -shm now at `shm` (opened on first use; OSError if it
    cannot be), None if there is no such regular file. Caller holds _kept_shm_lock. Also closes each
    kept descriptor whose -shm SQLite has deleted (st_nlink 0)."""
    try:
        st = os.stat(shm)
    except OSError:
        st = None
    want = (st.st_dev, st.st_ino) if st is not None and stat.S_ISREG(st.st_mode) else None
    found = None
    for fd, key in list(_kept_shm_fds.items()):
        try:
            now = os.fstat(fd)
        except OSError:                                  # closed elsewhere: no longer ours
            del _kept_shm_fds[fd]
            continue
        if (now.st_dev, now.st_ino) != key:              # the number now names another file
            del _kept_shm_fds[fd]
        elif key == want:
            found = fd
        elif now.st_nlink == 0:
            del _kept_shm_fds[fd]
            os.close(fd)
    if want is None or found is not None:
        return found
    fd = os.open(shm, os.O_RDONLY)                       # non-inheritable (PEP 446)
    now = os.fstat(fd)
    _kept_shm_fds[fd] = (now.st_dev, now.st_ino)
    return fd


def _read_shm_head(shm: Path) -> bytes | None:
    """The first two WalIndexHdr copies of the -shm, or None when it cannot be read."""
    try:
        if not _KEEP_SHM_OPEN:
            with open(shm, "rb") as f:
                return f.read(2 * _WAL_INDEX_HDR_SIZE)
        with _kept_shm_lock:
            fd = _kept_shm_fd(shm)
            return None if fd is None else os.pread(fd, 2 * _WAL_INDEX_HDR_SIZE, 0)
    except OSError:
        return None


def _wal_index_state(shm: Path) -> tuple[bytes, int] | None:
    """(salts, mxFrame) from the -shm wal-index header, or None when there is no usable index.

    WalIndexHdr, native byte order: iVersion u32 @0, isInit u8 @12, mxFrame u32 @16 (the last
    committed frame), aSalt @32 (the WAL header's salt bytes), aCksum u32[2] @40 over bytes
    0..40. SQLite writes copy 1 then copy 0; a reader whose two copies differ caught a writer
    mid-update and reads again. None (the index says nothing, every byte is judged): no -shm,
    copies that never agree (a short file never does), uninitialised, unknown version, or a
    bad checksum.
    """
    for _ in range(_INDEX_READ_TRIES):
        raw = _read_shm_head(shm)
        if raw is None:
            return None
        first, second = raw[:_WAL_INDEX_HDR_SIZE], raw[_WAL_INDEX_HDR_SIZE:]
        if len(second) != _WAL_INDEX_HDR_SIZE or first != second:   # a short (even empty) file, or torn
            continue
        version, = struct.unpack_from("=I", first, 0)
        if version != _WAL_INDEX_VERSION or first[12] != 1:
            return None
        cksum = WalTornWriteDetector.checksum_step(first[:40], 0, 0, sys.byteorder == "big")
        if cksum != struct.unpack_from("=2I", first, 40):
            return None
        return first[32:40], struct.unpack_from("=I", first, 16)[0]
    return None


def inspect_live_wal(wal_path: str | Path) -> WalInspectionReport:
    """Inspect the WAL of a database that may be open, and written, in another process or this one
    (the -shm is read without releasing this process's SQLite locks on it: _read_shm_head).

    SQLite publishes a frame in the -shm wal-index (mxFrame) only when its transaction
    commits, and ignores every frame past mxFrame. When a usable wal-index describes this
    WAL, exactly the frames up to mxFrame are judged: an anomaly after them is a frame in
    flight (or an uncommitted tail left by a crash), reported as in_flight_tail, not as a
    torn write; one of them missing, or carrying another generation's salts, is a torn write.

    A read is judged only if the wal-index shows the same log generation (salts) before and
    after it: a checkpoint that restarts the log changes the salts before it rewrites or
    truncates the WAL, and committed frames are never rewritten within a generation. The
    read is retried otherwise; WalIndexBusy if the log restarted under every attempt.
    With no usable wal-index (no -shm, exclusive locking mode) every byte is judged, as by
    inspect_wal. A WAL that is gone (the last connection closed) holds no torn write.
    """
    wal = Path(wal_path)
    detector = WalTornWriteDetector()
    if not wal.name.endswith("-wal"):
        return detector.inspect_file(wal)
    shm = wal.with_name(wal.name[: -len("-wal")] + "-shm")
    for _ in range(_LIVE_READ_ATTEMPTS):
        before = _wal_index_state(shm)
        try:
            data = wal.read_bytes()
        except FileNotFoundError:
            return WalInspectionReport(is_healthy=True)
        except OSError:
            return detector.inspect_file(wal)
        after = _wal_index_state(shm)
        if before is None and after is None:
            return detector.inspect_bytes(data)
        if before is not None and after is not None and before[0] == after[0]:
            salts, committed = before
            # the index describes this log unless the WAL header carries other salts; with
            # nothing committed yet (a restart) every frame on disk is stale or uncommitted
            if committed == 0 or data[16:24] == salts:
                return detector.inspect_bytes(data, committed_frames=committed)
            return detector.inspect_bytes(data)
    raise WalIndexBusy(f"{wal}: the log restarted during each of {_LIVE_READ_ATTEMPTS} reads")


def detect_torn_writes(path_or_bytes: str | Path | bytes) -> list[TornWriteAnomaly]:
    """Convenience helper returning all detected torn-write anomalies."""
    report = inspect_wal(path_or_bytes)
    return report.anomalies
