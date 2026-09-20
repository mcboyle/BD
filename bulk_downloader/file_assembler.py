"""row859: zero-copy assembly of completed download parts into one file.

# What this is

Given an ordered list of already-downloaded part files, concatenate them
into a single output file. On Linux, each part is copied with
``os.sendfile()`` so the bytes move kernel-side (page cache to page cache)
without a userspace copy; the fallback is a plain buffered read/write loop.
Output bytes are identical either way -- the choice of copy path is a
performance detail, not a content decision.

# Fallback

``os.sendfile()`` is attempted first when available. If it raises
``OSError`` (unsupported source/destination pairing -- e.g. a filesystem
that does not implement it, or a platform without the syscall at all) the
remainder of that part, and every part after it, copies via a buffered
read/write loop instead. A part that sendfile partially transferred before
failing resumes the buffered copy from wherever sendfile's file offsets
were left (sendfile never rewinds on error).

# What this is NOT

Not a merge of overlapping/conflicting byte ranges -- parts are assumed to
already be ordered, complete, non-overlapping regions of the final file
(as multi_conn.py's own pwrite-into-sparse-file path already guarantees
for its own case; this module is for callers that instead produce one
file per part and need them stitched together).
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

_COPY_BLOCK_BYTES = 4 * 1024 * 1024  # 4 MiB buffered-copy block
_SENDFILE_CHUNK_BYTES = 64 * 1024 * 1024  # 64 MiB per sendfile() call

PathLike = Union[str, "os.PathLike[str]"]


class ShortPartError(OSError):
    """A part copied fewer bytes than its own size reported at open time --
    a truncated or racing source part. Distinguishes this from any other
    OSError so a caller does not mistake silent data loss for success."""


@dataclass(frozen=True)
class PartCopyResult:
    path: Path
    bytes_copied: int
    used_sendfile: bool


@dataclass(frozen=True)
class AssembleResult:
    output_path: Path
    total_bytes: int
    parts: tuple = field(default_factory=tuple)

    @property
    def used_sendfile(self) -> bool:
        """True only if every part copied entirely via sendfile (no
        buffered fallback anywhere in the run)."""
        return bool(self.parts) and all(p.used_sendfile for p in self.parts)


def _copy_one_part(part_path: Path, dst_fd: int, try_sendfile: bool) -> PartCopyResult:
    size = part_path.stat().st_size
    if size == 0:
        return PartCopyResult(part_path, 0, used_sendfile=try_sendfile)

    used_sendfile = try_sendfile
    with open(part_path, "rb") as src:
        src_fd = src.fileno()
        remaining = size

        if try_sendfile:
            while remaining > 0:
                chunk = min(_SENDFILE_CHUNK_BYTES, remaining)
                try:
                    sent = os.sendfile(dst_fd, src_fd, None, chunk)
                except OSError:
                    used_sendfile = False
                    break
                if sent <= 0:
                    used_sendfile = False
                    break
                remaining -= sent

        if remaining > 0:
            used_sendfile = False
            while remaining > 0:
                block = os.read(src_fd, min(_COPY_BLOCK_BYTES, remaining))
                if not block:
                    break
                remaining -= _write_all(dst_fd, block)

    return PartCopyResult(part_path, size - remaining, used_sendfile)


def _write_all(dst_fd: int, block: bytes) -> int:
    """Write every byte of ``block`` to ``dst_fd``; os.write() may return a
    short count (pipes, quotas, signals) and a short write left unaccounted
    would be reported as copied. Returns the number of bytes written; a
    zero-length write is treated as an error rather than spun on."""
    view = memoryview(block)
    written = 0
    while written < len(view):
        n = os.write(dst_fd, view[written:])
        if n <= 0:
            raise OSError(f"os.write returned {n} for {len(view) - written} bytes")
        written += n
    return written


def assemble(
    parts: Sequence[PathLike],
    output_path: PathLike,
    *,
    try_sendfile: bool = True,
) -> AssembleResult:
    """Concatenate ``parts`` in order into ``output_path``.

    Prefers ``os.sendfile()`` per part; falls back to a buffered copy for
    any part (and everything after it) where sendfile is unavailable or
    fails. Byte-identical to a straight buffered concatenation.

    Atomic: built in a sibling temp file and ``os.replace()``d into
    ``output_path`` only once every part has copied in full, so a failed or
    interrupted assembly (including a part that turns out to be truncated)
    never leaves a partial file at ``output_path`` -- it either has the
    prior content (if any) or the complete new content, never a mix.
    """
    part_paths = [Path(p) for p in parts]
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sendfile_ok = try_sendfile and hasattr(os, "sendfile")
    results = []
    fd, tmp_name = tempfile.mkstemp(
        prefix=out_path.name + ".", suffix=".assembling", dir=str(out_path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        for part_path in part_paths:
            expected_size = part_path.stat().st_size
            result = _copy_one_part(part_path, fd, sendfile_ok)
            if result.bytes_copied != expected_size:
                raise ShortPartError(
                    f"{part_path}: expected {expected_size} bytes, copied "
                    f"{result.bytes_copied} (truncated or racing source part)"
                )
            results.append(result)
            if sendfile_ok and not result.used_sendfile:
                # sendfile failed once for this destination: stop retrying
                # it for the rest of this run's parts.
                sendfile_ok = False
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp_path, out_path)
    except BaseException:
        # close and publication are inside the owned-temp cleanup scope: a
        # failed os.replace() (destination is a directory, cross-device,
        # permission) must not strand a complete *.assembling file.
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        tmp_path.unlink(missing_ok=True)
        raise

    total = sum(r.bytes_copied for r in results)
    return AssembleResult(out_path, total, tuple(results))
