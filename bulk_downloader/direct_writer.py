"""row874: aligned O_DIRECT sequential writer for large media downloads.

# What this is

Given a completed source file (or byte stream), write it to an output path
using O_DIRECT where the platform, file size and block alignment allow --
bypassing the kernel page cache so a multi-gigabyte sequential video write
does not evict hot pages / trigger dirty-page writeback stalls elsewhere on
the box. Below the size threshold, or wherever O_DIRECT is unavailable or
the alignment cannot be satisfied, the write falls back to an ordinary
buffered write. Output bytes are identical either way.

# Strategy

  1. Below `min_direct_bytes` (default 500 MiB): buffered write, no O_DIRECT
     attempt at all -- the aligned-buffer overhead is not worth it for a
     small file.
  2. `os.O_DIRECT` missing on this platform, or the open() itself raises
     OSError (filesystem does not support it -- e.g. tmpfs, some network
     mounts): buffered write, whole file.
  3. Otherwise: the aligned body (every full `alignment`-byte block,
     `alignment`-aligned offsets, an `alignment`-aligned scratch buffer)
     writes via O_DIRECT; any final partial block (source size is not an
     exact multiple of `alignment`) is appended with an ordinary buffered
     write, since O_DIRECT requires block-aligned length and offset for
     every write.

# What this is NOT

Not a replacement for multi_conn.py's in-flight pwrite-into-sparse-file
downloader -- this is for writing an already-assembled source (e.g. the
output of file_assembler.assemble()) to its final destination with the
page cache bypassed, a separate concern from how the bytes were gathered.
"""
from __future__ import annotations

import mmap
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union

DEFAULT_ALIGNMENT = 4096  # standard page size; a multiple of every real block/sector size in use
DEFAULT_MIN_DIRECT_BYTES = 500 * 1024 * 1024  # below this, O_DIRECT's alignment tax isn't worth it
_READ_BATCH_BLOCKS = 256  # 1 MiB reads (at the default 4 KiB alignment) per aligned write batch

PathLike = Union[str, "os.PathLike[str]"]


@dataclass(frozen=True)
class WriteResult:
    output_path: Path
    total_bytes: int
    used_direct: bool
    direct_bytes: int


def _o_direct_flag():
    return getattr(os, "O_DIRECT", None)


class _AlignedBuffer:
    """A scratch buffer whose start address is page-aligned (POSIX
    guarantees mmap() starts on a page boundary), sized to `alignment`."""

    def __init__(self, alignment: int):
        self._mm = mmap.mmap(-1, alignment)

    def filled_view(self, data: bytes) -> memoryview:
        n = len(data)
        self._mm[:n] = data
        return memoryview(self._mm)[:n]

    def close(self) -> None:
        self._mm.close()


def _buffered_copy(src_path: Path, out_path: Path, *, append: bool = False, skip: int = 0) -> int:
    mode = "r+b" if append else "wb"
    written = 0
    with open(src_path, "rb") as src, open(out_path, mode) as dst:
        if skip:
            src.seek(skip)
            dst.seek(skip)
        while True:
            block = src.read(4 * 1024 * 1024)
            if not block:
                break
            dst.write(block)
            written += len(block)
    return written


def _direct_copy_aligned_body(
    src_path: Path, out_path: Path, aligned_len: int, alignment: int, o_direct_flag: int
) -> bool:
    """Write the first `aligned_len` bytes of src_path to out_path with
    O_DIRECT. Returns True on success, False if O_DIRECT is unsupported for
    this destination (caller falls back to a buffered copy for those
    bytes instead)."""
    if aligned_len == 0:
        # Still create/truncate the output so a buffered tail has somewhere
        # to land, and so used_direct semantics are unambiguous below.
        open(out_path, "wb").close()
        return True

    try:
        out_fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | o_direct_flag, 0o644)
    except OSError:
        return False

    buf = _AlignedBuffer(alignment)
    try:
        with open(src_path, "rb") as src:
            written = 0
            batch_bytes = alignment * _READ_BATCH_BLOCKS
            while written < aligned_len:
                to_read = min(batch_bytes, aligned_len - written)
                chunk = src.read(to_read)
                if not chunk:
                    break
                offset = 0
                while offset < len(chunk):
                    block = chunk[offset: offset + alignment]
                    view = buf.filled_view(block)
                    try:
                        os.write(out_fd, view)
                    finally:
                        view.release()
                    offset += alignment
                    written += alignment
    except OSError:
        os.close(out_fd)
        buf.close()
        return False
    buf.close()
    os.close(out_fd)
    return True


def write_direct(
    source: PathLike,
    output_path: PathLike,
    *,
    alignment: int = DEFAULT_ALIGNMENT,
    min_direct_bytes: int = DEFAULT_MIN_DIRECT_BYTES,
) -> WriteResult:
    """Copy `source` to `output_path`, using O_DIRECT for the aligned body
    when the file is large enough and the platform/filesystem support it.
    Byte-identical to a straight buffered copy regardless of which path is
    taken."""
    src_path = Path(source)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    size = src_path.stat().st_size

    o_direct_flag = _o_direct_flag()
    if o_direct_flag is None or size < min_direct_bytes:
        _buffered_copy(src_path, out_path)
        return WriteResult(out_path, size, used_direct=False, direct_bytes=0)

    aligned_len = (size // alignment) * alignment
    tail_len = size - aligned_len

    used_direct = _direct_copy_aligned_body(src_path, out_path, aligned_len, alignment, o_direct_flag)
    if not used_direct:
        _buffered_copy(src_path, out_path)
        return WriteResult(out_path, size, used_direct=False, direct_bytes=0)

    if tail_len > 0:
        _buffered_copy(src_path, out_path, append=True, skip=aligned_len)

    return WriteResult(out_path, size, used_direct=True, direct_bytes=aligned_len)
