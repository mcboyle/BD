"""Cut 846: tests/test_row846_mp4_faststart.py -- Acceptance tests for Row 846:
ASYNC-MP4-FASTSTART-MOOV-ATOM-REMUXER-PIPELINE.

Verifies:
(1) moov atom precedes mdat atom in remuxed MP4 header,
(2) remuxing preserves lossless stream integrity,
(3) non-blocking error handling preserves original file on failure.
"""
from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

BD_GATE_SCOPE = "module"

faststart = importlib.import_module("bulk_downloader.faststart")


def _create_test_mp4(path: Path, *, faststart: bool = False) -> None:
    """Generate a valid minimal test MP4 file using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=duration=1:frequency=440",
        "-c:v", "libx264", "-c:a", "aac",
    ]
    if faststart:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)


@pytest.fixture
def temp_mp4(tmp_path: Path) -> Path:
    """Fixture providing a valid MP4 with moov atom at the end."""
    mp4_path = tmp_path / "test_video.mp4"
    _create_test_mp4(mp4_path, faststart=False)
    return mp4_path


def test_moov_atom_precedes_mdat_after_faststart(temp_mp4: Path) -> None:
    """Acceptance (1): moov atom precedes mdat atom in remuxed MP4 header."""
    # Before faststart remux: moov is located after mdat
    atoms_before = faststart.parse_mp4_atoms(temp_mp4)
    atom_dict_before = {tag: offset for tag, offset, _ in atoms_before}
    assert "moov" in atom_dict_before, "Input MP4 must contain moov atom"
    assert "mdat" in atom_dict_before, "Input MP4 must contain mdat atom"
    assert atom_dict_before["moov"] > atom_dict_before["mdat"], (
        f"Initial file must have moov after mdat: {atom_dict_before}"
    )
    assert not faststart.is_faststart(temp_mp4)

    # Perform synchronous faststart remux
    result = faststart.remux_faststart(temp_mp4)
    assert result.ok is True
    assert result.modified is True
    assert Path(result.path).exists()

    # After faststart remux: moov is relocated before mdat in header
    atoms_after = faststart.parse_mp4_atoms(temp_mp4)
    atom_dict_after = {tag: offset for tag, offset, _ in atoms_after}
    assert "moov" in atom_dict_after
    assert "mdat" in atom_dict_after
    assert atom_dict_after["moov"] < atom_dict_after["mdat"], (
        f"Faststart file must have moov preceding mdat: {atom_dict_after}"
    )
    assert faststart.is_faststart(temp_mp4)


def test_remuxing_preserves_lossless_stream_integrity(temp_mp4: Path) -> None:
    """Acceptance (2): remuxing preserves lossless stream integrity (-c copy)."""
    # Read mdat payload size before remuxing
    atoms_before = faststart.parse_mp4_atoms(temp_mp4)
    mdat_before = next((size for tag, _, size in atoms_before if tag == "mdat"), None)
    assert mdat_before is not None

    # Run remuxer
    result = faststart.remux_faststart(temp_mp4)
    assert result.ok is True

    # Read mdat payload size after remuxing
    atoms_after = faststart.parse_mp4_atoms(temp_mp4)
    mdat_after = next((size for tag, _, size in atoms_after if tag == "mdat"), None)
    assert mdat_after is not None

    # Exact payload length preserved under -c copy
    assert mdat_before == mdat_after, (
        f"Lossless stream integrity violated: mdat size changed from {mdat_before} to {mdat_after}"
    )

    # Verify stream decodability with ffmpeg null muxer
    probe_cmd = ["ffmpeg", "-v", "error", "-i", str(temp_mp4), "-f", "null", "-"]
    check = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
    assert check.returncode == 0, f"Remuxed stream decoding error: {check.stderr}"


def test_non_blocking_error_handling_preserves_original_file(tmp_path: Path) -> None:
    """Acceptance (3): non-blocking error handling preserves original file on failure."""
    corrupt_file = tmp_path / "corrupt.mp4"
    original_bytes = b"CORRUPTED_NOT_A_VALID_MP4_HEADER_DATA_1234567890"
    corrupt_file.write_bytes(original_bytes)

    # Calling remux_faststart on a corrupt file fails gracefully without raising
    result = faststart.remux_faststart(corrupt_file)
    assert result.ok is False
    assert corrupt_file.exists()
    assert corrupt_file.read_bytes() == original_bytes, "Original file must be preserved untouched on failure"

    # Test non-blocking asynchronous remuxing
    valid_file = tmp_path / "async_test.mp4"
    _create_test_mp4(valid_file, faststart=False)

    callback_called = []

    def on_done(res: faststart.FaststartResult) -> None:
        callback_called.append(res)

    # Launch asynchronous remux
    future = faststart.async_remux_faststart(valid_file, on_complete=on_done)
    # The async call returns immediately (non-blocking)
    assert not future.done() or len(callback_called) <= 1

    # Wait for completion
    res = future.result(timeout=30)
    assert res.ok is True
    assert len(callback_called) == 1
    assert callback_called[0].ok is True
    assert faststart.is_faststart(valid_file)


def test_failure_due_to_missing_ffmpeg_preserves_original(temp_mp4: Path) -> None:
    """When ffmpeg binary cannot be found, fail safely and leave file untouched."""
    original_bytes = temp_mp4.read_bytes()

    with patch("bulk_downloader.faststart._get_ffmpeg", return_value=None):
        result = faststart.remux_faststart(temp_mp4)
        assert result.ok is False
        assert "ffmpeg" in result.error.lower()
        assert temp_mp4.read_bytes() == original_bytes


def test_already_faststart_file_is_noop(tmp_path: Path) -> None:
    """If file is already faststart, remuxer reports success without unnecessary rewriting."""
    fast_file = tmp_path / "already_fast.mp4"
    _create_test_mp4(fast_file, faststart=True)
    assert faststart.is_faststart(fast_file)

    orig_mtime = os.path.getmtime(fast_file)
    result = faststart.remux_faststart(fast_file)
    assert result.ok is True
    assert result.modified is False
    assert os.path.getmtime(fast_file) == orig_mtime
    assert faststart.is_faststart(fast_file)


# ---- fixer (O928) controls for the correctness REFUTE E1-E4 (HIGH) ---------

def _stream_types(path: Path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_e1_two_audio_tracks_survive_the_remux(tmp_path: Path) -> None:
    src = tmp_path / "two_audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
                    "-f", "lavfi", "-i", "sine=duration=1:frequency=440",
                    "-f", "lavfi", "-i", "sine=duration=1:frequency=880",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a",
                    "-c:v", "libx264", "-c:a", "aac", str(src)], capture_output=True, check=True)
    assert _stream_types(src) == ["video", "audio", "audio"]
    assert not faststart.is_faststart(src)
    res = faststart.remux_faststart(src)
    assert res.ok is True and res.modified is True, res
    assert faststart.is_faststart(src)
    assert _stream_types(src) == ["video", "audio", "audio"]
    assert [p.name for p in tmp_path.iterdir()] == ["two_audio.mp4"]


def test_e2_overlapping_calls_own_distinct_temporaries_and_never_publish_each_other(tmp_path: Path, monkeypatch) -> None:
    """Two calls on the same source at once: each gets its own mkstemp output;
    a call whose ffmpeg fails never publishes the other's bytes nor deletes
    anything it did not create."""
    src = tmp_path / "shared.mp4"
    _create_test_mp4(src, faststart=False)
    original = src.read_bytes()
    seen_outputs: list[Path] = []
    real_run = faststart.subprocess.run

    def run(cmd, **kw):
        seen_outputs.append(Path(cmd[-1]))
        if len(seen_outputs) == 1:
            # first call: simulate a timeout after ffmpeg started writing partial bytes
            Path(cmd[-1]).write_bytes(b"partial")
            raise subprocess.TimeoutExpired(cmd, 0.01)
        return real_run(cmd, **kw)

    monkeypatch.setattr(faststart.subprocess, "run", run)
    first = faststart.remux_faststart(src)
    assert first.ok is False and first.modified is False
    assert src.read_bytes() == original, "a failed call must not touch the source"
    second = faststart.remux_faststart(src)
    assert second.ok is True and faststart.is_faststart(src)
    assert len(seen_outputs) == 2 and seen_outputs[0] != seen_outputs[1]
    assert all(p.name.startswith(".shared.faststart-") and p.suffix == ".mp4" for p in seen_outputs)
    assert not seen_outputs[0].exists() and not seen_outputs[1].exists()
    assert [p.name for p in tmp_path.iterdir()] == ["shared.mp4"]


def test_e2_failure_does_not_delete_a_foreign_file_at_a_predictable_name(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"CORRUPTED_NOT_A_VALID_MP4_HEADER_DATA_1234567890")
    foreign = tmp_path / f".corrupt.faststart.{os.getpid()}.tmp.mp4"   # the old PID-derived name
    foreign.write_bytes(b"not ours")
    res = faststart.remux_faststart(corrupt)
    assert res.ok is False
    assert foreign.read_bytes() == b"not ours"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["corrupt.mp4", foreign.name])


def test_e3_directory_at_temp_path_is_contained(tmp_path: Path, monkeypatch) -> None:
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"CORRUPTED_NOT_A_VALID_MP4_HEADER_DATA_1234567890")
    real_run = faststart.subprocess.run

    def run(cmd, **kw):
        out = Path(cmd[-1])
        out.unlink(missing_ok=True)
        out.mkdir()          # something else now occupies our temp path
        return real_run(cmd, **kw)

    monkeypatch.setattr(faststart.subprocess, "run", run)
    res = faststart.remux_faststart(corrupt)   # ffmpeg fails on the corrupt input; cleanup meets a directory
    assert res.ok is False and isinstance(res.error, str)
    assert corrupt.read_bytes() == b"CORRUPTED_NOT_A_VALID_MP4_HEADER_DATA_1234567890"


def test_e4_truncated_atoms_are_rejected(tmp_path: Path) -> None:
    import struct
    bogus = tmp_path / "bogus.mp4"
    bogus.write_bytes(struct.pack(">I4s", 4096, b"moov"))   # 8 bytes claiming a 4096-byte moov
    assert faststart.parse_mp4_atoms(bogus) == []
    assert faststart.is_faststart(bogus) is False
    res = faststart.remux_faststart(bogus)
    assert res.ok is False and res.modified is False
    assert bogus.read_bytes() == struct.pack(">I4s", 4096, b"moov")
    # a real file still parses to a complete atom list ending exactly at EOF
    good = tmp_path / "good.mp4"
    _create_test_mp4(good, faststart=True)
    atoms = faststart.parse_mp4_atoms(good)
    assert atoms and atoms[-1][1] + atoms[-1][2] == good.stat().st_size
    assert faststart.is_faststart(good)
