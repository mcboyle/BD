"""container_repair -- automatic bitstream repair for truncated containers (row 868).

Downloads interrupted near completion often leave broken container headers
that standard players refuse to open, triggering unnecessary full redownloads.
Running ffmpeg bitstream copy with error detection ignored recovers the playable
stream before marking jobs failed.

Design principles:
  * ZERO DATA CORRUPTION (Acceptance 3): Never modifies or truncates the source
    input file. Recovery operates on an isolated output file.
  * NON-BLOCKING / FAIL-SOFT (Acceptance 2): Never raises unhandled exceptions.
    Missing binaries, missing inputs, timeouts, and transcoding errors return
    RepairResult(recovered=False, reason=...).
  * PROCESS GROUP ISOLATION (Fleet Rule 45): All subprocess invocations use
    start_new_session=True and enforce os.killpg cleanup on timeout or error
    to prevent leaked child processes.
  * VERIFIED PLAYABILITY (Acceptance 1): Re-verifies recovered output via
    ffprobe duration probe before marking recovered=True.
"""
from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from . import ffmpeg_bin

PathLike = Union[str, Path]
DEFAULT_TIMEOUT = 60

__all__ = [
    "DEFAULT_TIMEOUT",
    "MutationOp",
    "PathLike",
    "RecordType",
    "RepairResult",
    "TransactionJournal",
    "repair",
    "repair_or_fail",
    "repair_with_journal",
]


@dataclass
class RepairResult:
    recovered: bool
    output_path: Path | None
    reason: str


def _run_isolated(
    cmd: list[str],
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    """Execute command with process group isolation and os.killpg cleanup (Rule 45)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    pgid = None
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pass

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise
    except Exception:
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise


def _probe_playable(ffprobe_path: str, path: Path, timeout: int) -> bool:
    """Verify that the repaired container is readable and has valid duration."""
    try:
        rc, stdout, _ = _run_isolated(
            [
                ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            timeout=timeout,
        )
        return rc == 0 and stdout.strip() != ""
    except (OSError, subprocess.TimeoutExpired):
        return False


def repair(
    corrupt_path: PathLike,
    output_path: PathLike | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> RepairResult:
    """Attempt to recover corrupt_path into a playable media container.

    Never modifies corrupt_path. Returns RepairResult -- never raises on missing
    binary, missing input, timeout, or ffmpeg failure.
    """
    src = Path(corrupt_path)
    if not src.is_file():
        return RepairResult(False, None, f"input not found: {src}")

    ffmpeg = ffmpeg_bin.ffmpeg()
    ffprobe = ffmpeg_bin.ffprobe()
    if not ffmpeg or not ffprobe:
        return RepairResult(False, None, "ffmpeg/ffprobe not available")

    out = (
        Path(output_path)
        if output_path
        else src.with_suffix(f".repaired{src.suffix}")
    )
    if out.exists():
        try:
            out.unlink()
        except OSError:
            pass

    try:
        rc, stdout, stderr = _run_isolated(
            [
                ffmpeg, "-y", "-err_detect", "ignore_err",
                "-i", str(src), "-c", "copy", str(out),
            ],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        return RepairResult(False, None, f"ffmpeg invocation failed: {exc}")

    if rc != 0 or not out.is_file() or out.stat().st_size == 0:
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        err_detail = (stderr or stdout or "").strip()[-300:]
        return RepairResult(False, None, f"ffmpeg exit {rc}: {err_detail}")

    if not _probe_playable(ffprobe, out, timeout):
        try:
            out.unlink()
        except OSError:
            pass
        return RepairResult(False, None, "repaired output failed playability probe")

    return RepairResult(True, out, "recovered")


def repair_or_fail(
    corrupt_path: PathLike,
    job_id: str,
    mark_failed: Callable[[str, str], None],
    output_path: PathLike | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> RepairResult:
    """Try repair; on failure call mark_failed(job_id, reason).

    Ensures non-blocking failure handling where unrecoverable files mark the
    job failed without throwing unhandled exceptions.
    """
    result = repair(corrupt_path, output_path=output_path, timeout=timeout)
    if not result.recovered:
        try:
            mark_failed(job_id, result.reason)
        except Exception:
            pass
    return result


def repair_with_journal(
    corrupt_path: PathLike,
    output_path: PathLike | None = None,
    journal: Any | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> RepairResult:
    """Repair truncated media container with crash-consistent write-ahead transaction logging (Row 1001).

    Records mutation intent in WAL, executes isolated stream recovery, and atomically
    commits the mutation record upon verified recovery or aborts on failure.
    """
    if journal is None:
        return repair(corrupt_path, output_path=output_path, timeout=timeout)

    from .transaction_journal import MutationOp

    cid = str(corrupt_path)
    tx_id = journal.begin_transaction()
    journal.log_mutation(
        tx_id,
        MutationOp.CREATE_CONTAINER,
        cid,
        {"source": str(corrupt_path), "status": "repairing"},
    )
    result = repair(corrupt_path, output_path=output_path, timeout=timeout)
    if result.recovered and result.output_path:
        journal.log_mutation(
            tx_id,
            MutationOp.SET_STATE,
            cid,
            {"status": "recovered", "output": str(result.output_path)},
        )
        journal.commit(tx_id)
    else:
        journal.abort(tx_id)
    return result


# Re-export TransactionJournal for container lifecycle callers (Row 1001)
try:
    from .transaction_journal import MutationOp, RecordType, TransactionJournal
except ImportError:
    pass
