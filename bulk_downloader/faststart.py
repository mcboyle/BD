"""bulk_downloader.faststart -- Non-blocking MP4 faststart moov-atom remuxer.

Relocates the MP4 moov atom from the end of the file to the file header
using `ffmpeg -i input.mp4 -c copy -movflags +faststart output.mp4`.
Ensures immediate streaming capability for media players and web clients.

Guarantees:
1. Moov atom precedes mdat atom in remuxed MP4 header.
2. Stream copy (-c copy) preserves lossless stream integrity byte-for-byte.
3. Fail-open error handling preserves original file intact on failure.
"""
from __future__ import annotations

import concurrent.futures
import importlib
import logging
import os
import struct
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)


def _get_ffmpeg() -> str | None:
    try:
        ffmpeg_bin = importlib.import_module("bulk_downloader.ffmpeg_bin")
        return ffmpeg_bin.ffmpeg()
    except Exception:  # noqa: BLE001
        return None

_GLOBAL_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _GLOBAL_EXECUTOR
    if _GLOBAL_EXECUTOR is None:
        _GLOBAL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="FaststartRemux",
        )
    return _GLOBAL_EXECUTOR


@dataclass
class FaststartResult:
    """Result of an MP4 faststart remuxing operation."""
    ok: bool
    modified: bool = False
    path: str = ""
    error: str = ""


def parse_mp4_atoms(file_path: str | Path) -> list[tuple[str, int, int]]:
    """Parse top-level MP4 atoms (boxes) from file header.

    Returns a list of (atom_tag, byte_offset, atom_size) tuples.
    """
    path = Path(file_path)
    if not path.is_file():
        return []

    atoms: list[tuple[str, int, int]] = []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            f.seek(0)

            pos = 0
            while pos + 8 <= file_size:
                f.seek(pos)
                header = f.read(8)
                if len(header) < 8:
                    break

                size_32, tag_bytes = struct.unpack(">I4s", header)
                tag = tag_bytes.decode("latin1", errors="replace")

                if size_32 == 0:
                    # Box extends to end of file
                    box_size = file_size - pos
                elif size_32 == 1:
                    # 64-bit extended box size
                    ext_bytes = f.read(8)
                    if len(ext_bytes) < 8:
                        break
                    box_size = struct.unpack(">Q", ext_bytes)[0]
                else:
                    box_size = size_32

                if box_size < 8:
                    break
                if pos + box_size > file_size:
                    # A box claiming bytes past EOF is a truncated file, not a
                    # top-level atom list: the parse is INVALID (an 8-byte file
                    # "containing" a 4096-byte moov must not pass as faststart).
                    return []

                atoms.append((tag, pos, box_size))
                pos += box_size
            if pos != file_size:
                return []
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Failed parsing MP4 atoms from %s: %s", path, exc)

    return atoms


def is_faststart(file_path: str | Path) -> bool:
    """True if the MP4 file has its moov atom positioned before the mdat atom."""
    atoms = parse_mp4_atoms(file_path)
    moov_pos = next((pos for tag, pos, _ in atoms if tag == "moov"), None)
    mdat_pos = next((pos for tag, pos, _ in atoms if tag == "mdat"), None)

    if moov_pos is None:
        return False
    if mdat_pos is None:
        return True
    return moov_pos < mdat_pos


def remux_faststart(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    timeout: float = 120.0,
) -> FaststartResult:
    """Remux MP4 to place moov atom at beginning of file (+faststart).

    Uses lossless stream copy (-c copy) so video/audio streams are untouched.
    On failure or error, original input file is preserved intact.
    """
    src = Path(input_path)
    if not src.is_file():
        return FaststartResult(ok=False, error=f"Input file not found: {src}", path=str(src))

    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        return FaststartResult(ok=False, error="ffmpeg binary is unavailable", path=str(src))

    # Fast check: already faststart?
    if is_faststart(src) and (output_path is None or Path(output_path).resolve() == src.resolve()):
        return FaststartResult(ok=True, modified=False, path=str(src))

    in_place = (output_path is None or Path(output_path).resolve() == src.resolve())
    dest = src if in_place else Path(output_path)
    # The remux output is an EXCLUSIVELY OWNED unique temp beside the destination
    # (mkstemp, never a PID-derived name two overlapping calls can share); only
    # this invocation's file is ever published or removed.
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.stem}.faststart-", suffix=dest.suffix, dir=str(dest.parent))
        os.close(fd)
    except OSError as exc:
        return FaststartResult(ok=False, error=f"cannot create temporary output: {exc}", path=str(src))
    tmp_out = Path(tmp_name)

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(src),
        "-map", "0",          # every stream (a second audio track, subtitles, data) -- -c copy alone keeps one per type
        "-c", "copy",
        "-movflags", "+faststart",
        str(tmp_out),
    ]

    def _discard() -> None:
        try:
            if tmp_out.is_dir():
                _logger.warning("faststart temp path %s is a directory; leaving it", tmp_out)
                return
            tmp_out.unlink(missing_ok=True)
        except OSError as exc:  # cleanup never turns a failure into an exception
            _logger.warning("could not remove faststart temp %s: %s", tmp_out, exc)

    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
        if proc.returncode == 0 and tmp_out.is_file() and tmp_out.stat().st_size > 0 and is_faststart(tmp_out):
            os.replace(tmp_out, dest)
            return FaststartResult(ok=True, modified=True, path=str(dest))
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            err = err or "ffmpeg produced no faststart output"
        _discard()
        _logger.warning("ffmpeg faststart remux failed for %s (rc=%d): %s", src, proc.returncode, err[:200])
        return FaststartResult(ok=False, error=err, path=str(src))
    except Exception as exc:  # noqa: BLE001
        _discard()
        _logger.warning("Exception during faststart remux for %s: %s", src, exc)
        return FaststartResult(ok=False, error=str(exc), path=str(src))


def async_remux_faststart(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    timeout: float = 120.0,
    on_complete: Callable[[FaststartResult], None] | None = None,
    executor: concurrent.futures.Executor | None = None,
) -> concurrent.futures.Future:
    """Asynchronously execute remux_faststart in a background thread.

    Returns a concurrent.futures.Future immediately without blocking the caller.
    If `on_complete` is provided, it is invoked with the FaststartResult when finished.
    """
    exec_pool = executor or _get_executor()

    def _worker() -> FaststartResult:
        res = remux_faststart(input_path, output_path, timeout=timeout)
        if on_complete is not None:
            try:
                on_complete(res)
            except Exception as e:  # noqa: BLE001
                _logger.warning("on_complete callback raised error in faststart: %s", e)
        return res

    return exec_pool.submit(_worker)
