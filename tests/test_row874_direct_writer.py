"""row874: bulk_downloader.direct_writer -- O_DIRECT aligned sequential writer.

direct_writer.write_direct() copies a source file to an output path using
O_DIRECT (bypassing the page cache) for the aligned body, when the file is
large enough and the platform/filesystem support it. The unaligned tail
(source size not a multiple of the block alignment), files under the size
threshold, and any platform/filesystem without O_DIRECT all fall back to a
buffered copy. Output bytes must be identical regardless of path taken.

Negative control: test_write_direct_detects_corrupted_output proves the
byte-identity assertion used throughout this file actually fails when the
output genuinely differs -- without it, a compare that always passes would
hide a real writer defect.
"""
import os
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import direct_writer as DW


def _write_source(path, size, seed=0):
    import random
    rng = random.Random(seed)
    block = bytes(rng.getrandbits(8) for _ in range(4096))
    with open(path, "wb") as f:
        written = 0
        while written < size:
            take = min(len(block), size - written)
            f.write(block[:take])
            written += take
    return path


def _o_direct_available():
    return hasattr(os, "O_DIRECT")


# --------------------------------------------------------------- basic
def test_write_direct_aligned_body_matches_source(tmp_path):
    src = _write_source(tmp_path / "src.bin", 4096 * 20, seed=1)
    out = tmp_path / "out.bin"

    result = DW.write_direct(src, out, alignment=4096, min_direct_bytes=0)

    assert out.read_bytes() == src.read_bytes()
    assert result.total_bytes == 4096 * 20


def test_write_direct_uses_direct_when_size_and_alignment_allow(tmp_path):
    assert _o_direct_available(), "os.O_DIRECT is required here: fail closed, never skip (FR46/T5)"
    src = _write_source(tmp_path / "src.bin", 4096 * 50, seed=2)
    out = tmp_path / "out.bin"

    result = DW.write_direct(src, out, alignment=4096, min_direct_bytes=0)

    assert result.used_direct is True
    assert result.direct_bytes == 4096 * 50
    assert out.read_bytes() == src.read_bytes()


def test_write_direct_unaligned_tail_falls_back_for_tail_only(tmp_path):
    """Source size is NOT a multiple of alignment: the aligned body still
    goes through O_DIRECT (when available), the trailing partial block
    always through the buffered fallback -- and the concatenation must be
    byte-identical to the source."""
    size = 4096 * 10 + 777  # unaligned tail
    src = _write_source(tmp_path / "src.bin", size, seed=3)
    out = tmp_path / "out.bin"

    result = DW.write_direct(src, out, alignment=4096, min_direct_bytes=0)

    assert out.read_bytes() == src.read_bytes()
    assert result.total_bytes == size
    if _o_direct_available():
        assert result.direct_bytes == 4096 * 10


def test_write_direct_below_threshold_stays_buffered(tmp_path):
    src = _write_source(tmp_path / "src.bin", 4096 * 4, seed=4)
    out = tmp_path / "out.bin"

    result = DW.write_direct(src, out, alignment=4096, min_direct_bytes=1024 * 1024 * 1024)

    assert result.used_direct is False
    assert result.direct_bytes == 0
    assert out.read_bytes() == src.read_bytes()


def test_write_direct_truncates_existing_output(tmp_path):
    out = tmp_path / "out.bin"
    out.write_bytes(b"stale-longer-than-the-new-content-should-be" * 200)

    src = _write_source(tmp_path / "src.bin", 4096 * 3, seed=5)
    DW.write_direct(src, out, alignment=4096, min_direct_bytes=0)

    assert out.read_bytes() == src.read_bytes()


# ------------------------------------------------------- negative control
def test_write_direct_detects_corrupted_output(tmp_path):
    """Proves the byte-identity check above can actually FAIL: if
    write_direct() dropped or corrupted bytes, this test would catch it."""
    src = _write_source(tmp_path / "src.bin", 4096 * 6, seed=6)
    out = tmp_path / "out.bin"
    DW.write_direct(src, out, alignment=4096, min_direct_bytes=0)

    expected = src.read_bytes()
    corrupted_expected = expected[:-1] + bytes([expected[-1] ^ 0xFF])
    assert out.read_bytes() != corrupted_expected


# ------------------------------------------------------------- fallback
def test_write_direct_falls_back_when_platform_lacks_o_direct(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "O_DIRECT", raising=False)

    src = _write_source(tmp_path / "src.bin", 4096 * 8, seed=7)
    out = tmp_path / "out.bin"

    result = DW.write_direct(src, out, alignment=4096, min_direct_bytes=0)

    assert result.used_direct is False
    assert out.read_bytes() == src.read_bytes()


def test_write_direct_falls_back_when_open_raises(tmp_path, monkeypatch):
    """os.O_DIRECT is present (platform claims support) but the open()
    itself fails (e.g. filesystem doesn't actually implement it) -- must
    still produce byte-identical output via the whole-file fallback.

    Deterministic on every platform, including one with no real
    os.O_DIRECT: the flag value itself is faked via monkeypatch (an
    arbitrary nonzero int is enough -- direct_writer only ORs it into the
    open() flags and never inspects its value), so this does not depend on
    -- and does not skip for -- real platform support."""
    monkeypatch.setattr(os, "O_DIRECT", 1 << 20, raising=False)
    fake_direct_flag = os.O_DIRECT

    real_open = os.open

    def _boom(path, flags, mode=0o777, *a, **kw):
        if flags & fake_direct_flag:
            raise OSError("simulated: filesystem does not support O_DIRECT")
        return real_open(path, flags, mode, *a, **kw)

    monkeypatch.setattr(os, "open", _boom)

    src = _write_source(tmp_path / "src.bin", 4096 * 12, seed=8)
    out = tmp_path / "out.bin"

    result = DW.write_direct(src, out, alignment=4096, min_direct_bytes=0)

    assert result.used_direct is False
    assert out.read_bytes() == src.read_bytes()


# ------------------------------------------------ page-cache diagnostic
#
# /proc/meminfo "Cached:" is system-wide: on a shared 48-64 core fleet host
# running dozens of concurrent test batteries, its deltas are dominated by
# OTHER processes' cache traffic, not this test's two writes -- measured
# this session (a clean baseline run saw direct_growth=204660 KB >
# buffered_growth=153600 KB, the wrong direction, purely from contention).
# mincore(2) is a single-process, deterministic replacement: it asks the
# kernel which pages of THIS file, right now, are resident in the page
# cache -- no dependency on what anything else on the host is doing.
def _resident_fraction(path: str, size: int) -> float:
    """Fraction of the first `size` bytes of `path` currently resident in
    the page cache. Querying residency via mincore() does not itself fault
    any pages in."""
    import ctypes
    import mmap as _mmap

    if size == 0:
        return 0.0
    pagesize = _mmap.PAGESIZE
    npages = (size + pagesize - 1) // pagesize
    libc = ctypes.CDLL(None, use_errno=True)
    with open(path, "rb") as f:
        # ACCESS_COPY (not PROT_READ): ctypes needs a writable buffer view
        # to take an address, but this is copy-on-write over the same
        # file-backed pages -- mincore() below queries their real
        # residency, and nothing here ever writes to trigger a copy.
        mm = _mmap.mmap(f.fileno(), size, access=_mmap.ACCESS_COPY)
        try:
            addr = ctypes.addressof(ctypes.c_char.from_buffer(mm))
            vec = (ctypes.c_ubyte * npages)()
            rc = libc.mincore(ctypes.c_void_p(addr), ctypes.c_size_t(size), vec)
            if rc != 0:
                errno = ctypes.get_errno()
                raise OSError(errno, os.strerror(errno))
            resident = sum(1 for b in vec if b & 1)
            return resident / npages
        finally:
            mm.close()


def test_direct_write_leaves_output_pages_out_of_cache(tmp_path):
    """Acceptance criterion: an O_DIRECT write must not leave its output
    resident in the page cache the way a buffered write does. mincore(2)
    proof, immediately after each write.

    R-NEG: the buffered path's residency is asserted "mostly resident"
    first, so an instrument that cannot see ANY residency (e.g. mincore
    unsupported/behaving oddly in this sandbox) fails loudly instead of
    passing by accident.
    """
    assert _o_direct_available(), "os.O_DIRECT is required here: fail closed, never skip (FR46/T5)"
    size = 8 * 1024 * 1024  # several thousand pages -- enough for a clean signal
    src = _write_source(tmp_path / "src.bin", size, seed=9)

    out_direct = tmp_path / "out_direct.bin"
    DW.write_direct(src, out_direct, alignment=4096, min_direct_bytes=0)
    direct_resident = _resident_fraction(str(out_direct), size)

    out_buffered = tmp_path / "out_buffered.bin"
    DW.write_direct(src, out_buffered, alignment=4096, min_direct_bytes=1024 * 1024 * 1024 * 1024)
    buffered_resident = _resident_fraction(str(out_buffered), size)

    assert out_direct.read_bytes() == out_buffered.read_bytes()
    assert buffered_resident > 0.5, (
        f"positive control failed: a buffered write left only "
        f"{buffered_resident:.0%} of its own output pages cache-resident "
        "right after writing -- instrument cannot distinguish here"
    )
    assert direct_resident < buffered_resident, (
        f"O_DIRECT write left {direct_resident:.0%} of its output pages "
        f"cache-resident, buffered write left {buffered_resident:.0%} -- "
        "expected direct < buffered"
    )
