"""Row 868 -- CORRUPT-CONTAINER-AUTOMATIC-BITSTREAM-REPAIR-FILTER.

Interrupted downloads leave truncated MP4/MKV containers that standard
players refuse to open. automated container recovery in container_repair.py
runs ffmpeg -err_detect ignore_err -i corrupt -c copy to recover the media
before marking jobs failed.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

import pytest

from bulk_downloader import runner_integrity
from bulk_downloader.runner_integrity import IntegrityMixin

try:
    from bulk_downloader import container_repair
except ImportError:
    container_repair = None

BD_GATE_SCOPE = "module"

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(
    not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not on PATH"
)


class _DummyRunner(IntegrityMixin):
    """Test harness for IntegrityMixin download failure path."""

    def __init__(self, tmp_dir: Path):
        self.site_id = "test_site"
        self.config = {
            "name": "test",
            "verify_integrity": True,
            "download_dir": str(tmp_dir),
            "retry_on_corruption": False,
        }
        self.jobs = {}
        self._lock = threading.Lock()
        self.events = []

    def _update_job(self, url, status, message, **extra):
        self.jobs[url] = {"status": status, "message": message, **extra}

    def log_event(self, event_type, msg, url=None):
        self.events.append((event_type, msg, url))


def _make_valid_mp4(path: Path) -> None:
    """Generate a valid minimal MP4 file with video stream via ffmpeg."""
    subprocess.run(
        [
            FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(path),
        ],
        check=True, capture_output=True,
    )


def _probe_duration(path: Path) -> float:
    """Read media duration via ffprobe."""
    proc = subprocess.run(
        [
            FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


@requires_ffmpeg
def test_pipeline_recovers_corrupt_container_before_marking_failed(tmp_path, monkeypatch):
    """Behavioral acceptance: interrupted download is repaired before marking job failed."""
    from bulk_downloader.db import db_init
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    db_init()
    runner = _DummyRunner(tmp_path)
    good = tmp_path / "stream.mp4"
    _make_valid_mp4(good)
    full = good.read_bytes()

    # Simulate truncated container: cut trailing 100 bytes
    corrupt = tmp_path / "corrupt_stream.mp4"
    corrupt.write_bytes(full[:-100])
    url = "https://example.com/stream.mp4"

    # Simulate initial integrity check failure on corrupt container
    real_verify = runner_integrity.verify_media_integrity
    attempts = [0]

    def mock_verify(path):
        attempts[0] += 1
        if attempts[0] == 1:
            return False, "ffprobe rc=1: truncated container"
        return real_verify(path)

    monkeypatch.setattr(runner_integrity, "verify_media_integrity", mock_verify)

    ok, retry, reason = runner._verify_integrity_or_quarantine(
        url, corrupt, "corrupt_stream.mp4", len(full) - 100,
    )

    assert ok is True, (
        f"expected ok=True (container repaired before failure), got ok=False "
        f"(quarantined, job status={runner.jobs.get(url, {}).get('status')})"
    )
    assert runner.jobs.get(url, {}).get("status") != "failed"
    assert corrupt.is_file()
    assert _probe_duration(corrupt) > 0
    assert any("container_repair" in ev[0] for ev in runner.events)


@requires_ffmpeg
def test_container_repair_recovers_truncated_into_playable_media(tmp_path):
    """Acceptance (1): recovery of simulated truncated container into playable media file."""
    assert container_repair is not None, "container_repair module must be implemented"
    good = tmp_path / "good.mp4"
    _make_valid_mp4(good)
    full = good.read_bytes()

    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(full[:-100])

    result = container_repair.repair(corrupt)
    assert result.recovered is True
    assert result.output_path is not None
    assert result.output_path.is_file()
    assert _probe_duration(result.output_path) > 0


@requires_ffmpeg
def test_container_repair_non_blocking_failure_marks_job_failed(tmp_path):
    """Acceptance (2): non-blocking failure handling marks job failed if unrecoverable."""
    assert container_repair is not None, "container_repair module must be implemented"
    garbage = tmp_path / "unrecoverable.mp4"
    garbage.write_bytes(b"corrupted raw random bytes that cannot be remuxed" * 16)

    failed_jobs = []
    result = container_repair.repair_or_fail(
        garbage,
        job_id="job_868",
        mark_failed=lambda jid, reason: failed_jobs.append((jid, reason)),
    )

    assert result.recovered is False
    assert result.output_path is None
    assert failed_jobs == [("job_868", result.reason)]
    assert "ffmpeg exit" in result.reason or "failed" in result.reason


@requires_ffmpeg
def test_container_repair_zero_data_corruption(tmp_path):
    """Acceptance (3): zero data corruption -- input files are never modified."""
    assert container_repair is not None, "container_repair module must be implemented"
    good = tmp_path / "good.mp4"
    _make_valid_mp4(good)
    full = good.read_bytes()

    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(full[:-100])
    corrupt_bytes_before = corrupt.read_bytes()

    container_repair.repair(corrupt)
    assert corrupt.read_bytes() == corrupt_bytes_before, "source corrupt file was mutated"

    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"non-container garbage bytes" * 10)
    garbage_bytes_before = garbage.read_bytes()

    container_repair.repair(garbage)
    assert garbage.read_bytes() == garbage_bytes_before, "garbage file was mutated"


def test_container_repair_missing_file_is_non_blocking(tmp_path):
    """Non-blocking handling when source file does not exist."""
    assert container_repair is not None, "container_repair module must be implemented"
    missing = tmp_path / "absent.mp4"
    result = container_repair.repair(missing)
    assert result.recovered is False
    assert "not found" in result.reason


def test_process_group_isolation_terminates_on_timeout(monkeypatch):
    """Fleet Rule 45: subprocess must use start_new_session=True and killpg on timeout."""
    assert container_repair is not None, "container_repair module must be implemented"
    killed_pgids = []

    real_killpg = os.killpg

    def mock_killpg(pgid, sig):
        killed_pgids.append((pgid, sig))
        try:
            real_killpg(pgid, sig)
        except OSError:
            pass

    monkeypatch.setattr(os, "killpg", mock_killpg)

    # Run command that will definitely time out
    with pytest.raises(subprocess.TimeoutExpired):
        container_repair._run_isolated(["sleep", "10"], timeout=1)

    assert len(killed_pgids) > 0
    assert any(sig == signal.SIGKILL for _, sig in killed_pgids)


@requires_ffmpeg
def test_pipeline_quarantines_the_original_bytes_when_the_remux_fails_reverify(tmp_path, monkeypatch):
    """HIGH (acceptance 3, zero data corruption): when the repaired output does
    NOT pass re-verification the download itself is what gets quarantined --
    byte-for-byte the original -- and the failed remux is removed; nothing is
    ever moved over the original before it has passed."""
    import hashlib
    from bulk_downloader.db import db_init
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    db_init()
    runner = _DummyRunner(tmp_path)
    good = tmp_path / "stream.mp4"
    _make_valid_mp4(good)
    corrupt = tmp_path / "corrupt_stream.mp4"
    corrupt.write_bytes(good.read_bytes()[:-100])
    original_sha = hashlib.sha256(corrupt.read_bytes()).hexdigest()
    verified = []

    def always_failing_verify(path):
        verified.append(Path(path))
        return False, "ffprobe rc=1: still broken"
    monkeypatch.setattr(runner_integrity, "verify_media_integrity", always_failing_verify)
    url = "https://example.com/stream.mp4"

    ok, retry, reason = runner._verify_integrity_or_quarantine(url, corrupt, "corrupt_stream.mp4", corrupt.stat().st_size)

    assert ok is False and retry is False
    quarantined = tmp_path / "_failed" / "corrupt_stream.mp4"
    assert quarantined.is_file() and not corrupt.exists()
    assert hashlib.sha256(quarantined.read_bytes()).hexdigest() == original_sha, "quarantine must hold the ORIGINAL bytes"
    # the remux was verified at its own path (never over the original) and removed
    assert verified[0] == corrupt and verified[1] != corrupt and verified[1].suffix == ".mp4"
    assert not verified[1].exists() and not list(tmp_path.glob("*.repaired*"))
    assert runner.jobs[url]["status"] == "failed"
    assert not any("container_repair" in ev[0] for ev in runner.events)
