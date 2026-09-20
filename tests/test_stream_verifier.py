"""Row 952 -- MEDIA-STREAM-CHUNK-INTEGRITY-AND-PACKET-CONTINUITY-VERIFIER.

Live stream downloads interrupted by network stalls can produce truncated
containers or missing audio/video packets. stream_verifier runs ffprobe packet
continuity checks before marking download jobs complete, and automates
missing chunk re-acquisition.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

try:
    from bulk_downloader import stream_verifier
except ImportError:
    stream_verifier = None

BD_GATE_SCOPE = "module"

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_tools = pytest.mark.skipif(
    not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not on PATH"
)


def _generate_stream_chunks(dir_path: Path, count: int = 3) -> list[Path]:
    """Generate continuous MPEG-TS stream chunks with synchronized timestamps."""
    dir_path.mkdir(parents=True, exist_ok=True)
    pattern = str(dir_path / "chunk_%d.ts")
    subprocess.run(
        [
            FFMPEG, "-y", "-f", "lavfi",
            "-i", "testsrc=size=160x120:rate=10",
            "-t", str(count),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-g", "10",
            "-f", "segment", "-segment_time", "1",
            "-reset_timestamps", "0",
            pattern,
        ],
        check=True, capture_output=True,
    )
    return [dir_path / f"chunk_{i}.ts" for i in range(count)]


def _concat_ts_files(paths: list[Path], out_path: Path) -> None:
    """Concatenate MPEG-TS segments into a single file."""
    with open(out_path, "wb") as outfile:
        for p in paths:
            outfile.write(p.read_bytes())


@requires_tools
def test_detects_truncated_media_stream_and_dropped_packets(tmp_path):
    """Acceptance (1): detection of truncated media streams and dropped packets."""
    assert stream_verifier is not None, "stream_verifier module not implemented"

    chunks = _generate_stream_chunks(tmp_path / "chunks", count=3)

    # Missing chunk 1: dropped chunk creates an unhandled packet continuity gap
    gap_stream = tmp_path / "gap_stream.ts"
    _concat_ts_files([chunks[0], chunks[2]], gap_stream)

    result = stream_verifier.check_packet_continuity(gap_stream, max_gap_seconds=0.3)
    assert result.valid is False, "failed to detect packet continuity gap in stream with dropped chunk"
    assert result.dropped_packets > 0 or len(result.discontinuities) > 0
    assert any(d.gap_seconds >= 0.5 for d in result.discontinuities)

    # Truncated stream: incomplete file with truncated bytes
    truncated_stream = tmp_path / "truncated.ts"
    full_bytes = chunks[0].read_bytes()
    truncated_stream.write_bytes(full_bytes[:500])
    trunc_result = stream_verifier.check_packet_continuity(truncated_stream, min_packets=5)
    assert trunc_result.valid is False
    assert len(trunc_result.errors) > 0


@requires_tools
def test_validation_of_healthy_streams(tmp_path):
    """Acceptance (2): validation of healthy streams with continuous packets."""
    assert stream_verifier is not None, "stream_verifier module not implemented"

    chunks = _generate_stream_chunks(tmp_path / "healthy_chunks", count=3)

    healthy_stream = tmp_path / "healthy_stream.ts"
    _concat_ts_files(chunks, healthy_stream)

    result = stream_verifier.verify_stream(healthy_stream, max_gap_seconds=0.3)
    assert result.valid is True
    assert result.dropped_packets == 0
    assert len(result.discontinuities) == 0
    assert result.total_packets >= 25
    assert len(result.errors) == 0


@requires_tools
def test_automated_missing_chunk_reacquisition(tmp_path):
    """Acceptance (3): automated missing chunk re-acquisition restores continuity."""
    assert stream_verifier is not None, "stream_verifier module not implemented"

    # Pre-generate server segments
    server_dir = tmp_path / "server"
    server_chunks = _generate_stream_chunks(server_dir, count=3)

    # Client download folder with missing chunk_1
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    shutil.copy(server_chunks[0], download_dir / "chunk_0.ts")
    # chunk_1 is missing
    shutil.copy(server_chunks[2], download_dir / "chunk_2.ts")

    reacquired_chunks = []

    def fetch_chunk(chunk_id: int) -> Path:
        reacquired_chunks.append(chunk_id)
        src = server_dir / f"chunk_{chunk_id}.ts"
        dest = download_dir / f"chunk_{chunk_id}.ts"
        shutil.copy(src, dest)
        return dest

    out_file = tmp_path / "reconstructed.ts"
    recovery_result = stream_verifier.reacquire_and_assemble(
        chunk_indices=[0, 1, 2],
        chunk_dir=download_dir,
        fetch_chunk_fn=fetch_chunk,
        output_path=out_file,
        max_gap_seconds=0.3,
    )

    assert recovery_result.valid is True
    assert 1 in reacquired_chunks, "automated recovery did not reacquire missing chunk 1"
    assert out_file.is_file()
    assert recovery_result.total_packets >= 25
    assert recovery_result.dropped_packets == 0


def test_verify_or_fail_marks_job_failed_on_unrecoverable(tmp_path):
    """Non-blocking failure handling: unrecoverable corrupted stream marks job failed."""
    assert stream_verifier is not None, "stream_verifier module not implemented"
    corrupt = tmp_path / "garbage.ts"
    corrupt.write_bytes(b"non-media random garbage bytes" * 32)

    failed_records = []
    res = stream_verifier.verify_or_fail(
        corrupt,
        job_id="job_stream_952",
        mark_failed=lambda jid, reason: failed_records.append((jid, reason)),
    )

    assert res.valid is False
    assert len(failed_records) == 1
    assert failed_records[0][0] == "job_stream_952"


def test_process_group_isolation_on_timeout(monkeypatch):
    """Fleet Rule 45: subprocess must use start_new_session=True and killpg on timeout."""
    assert stream_verifier is not None, "stream_verifier module not implemented"
    killed_pgids = []
    real_killpg = os.killpg

    def mock_killpg(pgid, sig):
        killed_pgids.append((pgid, sig))
        try:
            real_killpg(pgid, sig)
        except OSError:
            pass

    monkeypatch.setattr(os, "killpg", mock_killpg)

    with pytest.raises(subprocess.TimeoutExpired):
        stream_verifier._run_isolated(["sleep", "10"], timeout=1)

    assert len(killed_pgids) > 0
    assert any(sig == signal.SIGKILL for _, sig in killed_pgids)


# ── correctness REFUTE E1-E3 (2026-09-20): tail truncation, sparse tracks, completion wiring ──

def _lavfi_media(out: Path, seconds: float, extra: list[str] | None = None) -> Path:
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "10", "-c:a", "aac",
         *(extra or []), str(out)],
        check=True, capture_output=True,
    )
    return out


@requires_tools
def test_tail_truncated_mpegts_is_detected_against_the_expected_duration(tmp_path):
    """E1: an MPEG-TS cut at 50% still has a clean packet cadence (its ffprobe duration is just its
    last timestamp); the job's expected duration is the evidence, and HALF the stream is not it."""
    full = _lavfi_media(tmp_path / "full.ts", 6.0)
    data = full.read_bytes()
    half = tmp_path / "half.ts"
    half.write_bytes(data[: len(data) // 2])

    ok = stream_verifier.verify_stream(full, expected_duration=6.0)
    assert ok.valid is True and ok.truncated is False, ok.errors
    cut = stream_verifier.verify_stream(half, expected_duration=6.0)
    assert cut.valid is False and cut.truncated is True, cut
    assert any("truncated media stream" in e for e in cut.errors), cut.errors
    # control: with no expected duration the cut file's own cadence is clean (the cut is only visible
    # against a declared length), so the seam MUST pass the job's known duration
    assert stream_verifier.verify_stream(half).truncated is False


@requires_tools
def test_tail_truncated_faststart_mp4_is_detected_from_its_own_header(tmp_path):
    """E1: an mp4 with +faststart declares the full duration in its header; the truncated tail is
    caught with no external knowledge."""
    full = _lavfi_media(tmp_path / "full.mp4", 6.0, ["-movflags", "+faststart"])
    data = full.read_bytes()
    half = tmp_path / "half.mp4"
    half.write_bytes(data[: len(data) // 2])
    res = stream_verifier.verify_stream(half)
    assert res.container_duration >= 5.5, res
    assert res.valid is False and res.truncated is True, res


@requires_tools
def test_healthy_file_with_a_sparse_subtitle_track_is_valid(tmp_path):
    """E2: a sparse subtitle/data track (cues at 0 s and 5 s) is not a dropped-packet gap."""
    srt = tmp_path / "cues.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nfirst\n\n2\n00:00:05,000 --> 00:00:06,000\nsecond\n")
    out = tmp_path / "subtitled.mkv"
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-i", str(srt),
         "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "10", "-c:s", "srt", str(out)],
        check=True, capture_output=True,
    )
    res = stream_verifier.verify_stream(out)
    assert res.valid is True, res
    assert res.discontinuities == [] and res.dropped_packets == 0
    # denominator: the sparse track IS in the probe (2 cues, 4 s apart > max_gap 0.5 s) and is
    # counted among total_packets -- the codec_type filter is what spares it, not a missing track
    probe = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_packets", "-show_streams", "-of", "json", str(out)],
        check=True, capture_output=True, text=True).stdout)
    sub_idx = {s["index"] for s in probe["streams"] if s["codec_type"] == "subtitle"}
    cues = [float(p["pts_time"]) for p in probe["packets"] if p["stream_index"] in sub_idx]
    assert len(sub_idx) == 1 and len(cues) == 2 and cues[1] - cues[0] > 0.5, (sub_idx, cues)
    assert res.total_packets == len(probe["packets"]) > len(cues)


def _segmented_ts(dir_path: Path, seconds: int, offset: float = 0.0) -> list[Path]:
    """`seconds` one-second MPEG-TS segments (video+audio); `offset` shifts every timestamp the
    way a live HLS origin does (PTS starts at an arbitrary value, not 0)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "10", "-c:a", "aac",
         "-output_ts_offset", str(offset), "-f", "segment", "-segment_time", "1",
         "-reset_timestamps", "0", str(dir_path / "seg_%d.ts")],
        check=True, capture_output=True,
    )
    segs = sorted(dir_path.glob("seg_*.ts"), key=lambda p: int(p.stem.split("_")[1]))
    assert len(segs) == seconds, [p.name for p in segs]
    return segs


@requires_tools
def test_hls_stall_that_loses_the_last_segments_is_detected_against_the_playlist_total(tmp_path):
    """E1 (the row's own "network stall" case): a TS assembled from the first 3 of 5 segments has
    a clean cadence and an ffprobe duration of ~3 s; only the playlist total says 2 s are missing."""
    segs = _segmented_ts(tmp_path / "segs", 5)
    whole, stalled = tmp_path / "whole.ts", tmp_path / "stalled.ts"
    _concat_ts_files(segs, whole)
    _concat_ts_files(segs[:3], stalled)
    ok = stream_verifier.verify_stream(whole, expected_duration=5.0)
    assert ok.valid is True and ok.truncated is False and ok.total_packets > 0, ok
    cut = stream_verifier.verify_stream(stalled, expected_duration=5.0)
    assert cut.total_packets > 0 and cut.discontinuities == []  # the cadence really is clean
    assert cut.valid is False and cut.truncated is True, cut
    assert any("truncated media stream" in e for e in cut.errors), cut.errors


@requires_tools
def test_live_ts_with_an_arbitrary_start_pts_is_judged_on_its_span_not_its_last_timestamp(tmp_path):
    """E1: a live origin's PTS starts at e.g. 90000 s; a stream cut at 50% still ENDS far beyond
    any expected duration, so the captured span (last end - first ts) is what must be compared."""
    segs = _segmented_ts(tmp_path / "segs", 4, offset=90000.0)
    whole, half = tmp_path / "whole.ts", tmp_path / "half.ts"
    _concat_ts_files(segs, whole)
    _concat_ts_files(segs[:2], half)
    ok = stream_verifier.verify_stream(whole, expected_duration=4.0)
    assert ok.duration_seconds > 89000, ok  # the offset really is in the timestamps
    assert ok.valid is True and ok.truncated is False, ok
    cut = stream_verifier.verify_stream(half, expected_duration=4.0)
    assert cut.duration_seconds > 89000 and cut.valid is False and cut.truncated is True, cut


@requires_tools
def test_a_still_picture_on_the_ffprobe_route_is_not_a_truncated_stream(tmp_path):
    """A one-packet, video-only, duration-less file (bmp/tiff reach verify_media_integrity's
    ffprobe route) is a picture; the min_packets floor must not quarantine it. Any declared
    length makes it a stream again (control)."""
    still = tmp_path / "still.bmp"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-frames:v", "1", str(still)], check=True, capture_output=True)
    res = stream_verifier.verify_stream(still)
    assert res.total_packets == 1 and res.container_duration == 0.0, res
    assert res.valid is True and res.errors == [], res
    as_stream = stream_verifier.verify_stream(still, expected_duration=6.0)
    assert as_stream.valid is False and as_stream.truncated is True, as_stream


@requires_tools
def test_low_fps_h264_mkv_with_dts_less_lead_in_packets_is_one_healthy_timeline(monkeypatch, tmp_path):
    """Skeptic (A3-A re-judge): default x264 emits B-frames; in Matroska ffprobe reports dts_time
    N/A on the lead-in packets, and a verifier that takes dts when present else pts mixes two
    clocks -- at 1-2 fps the pts of the lead-in (0 s, 4 s) against the next packet's dts (0 s) is
    a 3-4 s "gap" and the dts tail ends 2 s short of format.duration, so a healthy timelapse /
    camera file was gappy + truncated and the completion path quarantined it (BASE kept it).
    One clock (pts, dts only as fallback) in presentation order judges it valid; the SAME file
    cut at 50% is still truncated (negative control)."""
    import threading
    from bulk_downloader import runner_integrity

    cam = tmp_path / "cam_1fps.mkv"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=1", "-t", "20",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cam)], check=True, capture_output=True)
    # denominator: the fixture really has the shape -- the first packets carry pts but NO dts_time
    # (ffprobe's N/A is an absent key in -of json), later packets carry both, and decode order
    # differs from presentation order (B-frames), with the last dts ending short of the duration
    probe = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_packets", "-of", "json", str(cam)],
        check=True, capture_output=True, text=True).stdout)
    pkts = probe["packets"]
    assert float(probe["format"]["duration"]) == 20.0 and len(pkts) == 20, probe["format"]
    dts_less = [p for p in pkts if p.get("dts_time") in (None, "N/A")]
    assert dts_less and all("pts_time" in p for p in dts_less), pkts[:3]
    assert pkts[0].get("dts_time") in (None, "N/A"), pkts[0]
    pts = [float(p["pts_time"]) for p in pkts]
    assert pts != sorted(pts), "expected B-frame reordering (pts not in file order)"
    last_dts_end = max(float(p["dts_time"]) + float(p["duration_time"]) for p in pkts if "dts_time" in p)
    assert last_dts_end < 20.0 - 1.0, last_dts_end  # the dts clock alone under-reports the span

    res = stream_verifier.verify_stream(cam)
    assert res.valid is True, res
    assert res.discontinuities == [] and res.dropped_packets == 0 and res.truncated is False, res
    assert res.total_packets == 20 and res.duration_seconds == 19.0, res

    # negative control: the same file cut at 50% is truncated against its own header
    half = tmp_path / "half.mkv"
    data = cam.read_bytes()
    half.write_bytes(data[: len(data) // 2])
    cut = stream_verifier.verify_stream(half)
    assert cut.container_duration == 20.0 and cut.total_packets > 0, cut
    assert cut.valid is False and cut.truncated is True, cut

    # the E3 wiring keeps the healthy file where BASE kept it (no quarantine) and still fails the cut
    monkeypatch.setattr(runner_integrity, "db_log", lambda *a, **k: None)

    class Runner(runner_integrity.IntegrityMixin):
        config = {"retry_on_corruption": False}
        site_id = "t"
        _lock = threading.Lock()
        jobs = {"u": {}}
        def _update_job(self, *a, **k): pass
        def log_event(self, *a, **k): pass
    ok, retry, reason = Runner()._verify_integrity_or_quarantine("u", cam, cam.name, 1)
    assert (ok, retry) == (True, False) and cam.exists() and not (tmp_path / "_failed" / cam.name).exists(), reason
    ok, retry, reason = Runner()._verify_integrity_or_quarantine("u", half, half.name, 1)
    assert ok is False and (tmp_path / "_failed" / half.name).exists(), reason
    assert "truncated media stream" in reason, reason


def test_no_ffprobe_is_an_unchecked_verdict_not_a_stream_defect(monkeypatch, tmp_path):
    """Fail-soft contract the completion path relies on: without ffprobe the result is UNKNOWN
    (checked=False), the same fail-open verify_media_integrity keeps; the file is still not valid."""
    f = tmp_path / "f.ts"; f.write_bytes(b"\x47" * 188 * 4)
    monkeypatch.setattr(stream_verifier.ffmpeg_bin, "ffprobe", lambda: None)
    res = stream_verifier.verify_stream(f)
    assert res.checked is False and res.valid is False
    assert res.errors == ["ffprobe not available on system PATH"]


def test_completion_path_runs_the_stream_verifier_and_fails_a_truncated_job(monkeypatch, tmp_path):
    """E3: _verify_integrity_or_quarantine (runner_integrity) runs verify_stream after the container
    check and a truncated/gappy verdict fails the job; an UNCHECKED probe fails open but is reported."""
    import threading
    from bulk_downloader import runner_integrity
    from bulk_downloader.stream_verifier import StreamVerificationResult

    calls = []
    monkeypatch.setattr(runner_integrity, "verify_media_integrity", lambda p: (True, ""))

    class Runner(runner_integrity.IntegrityMixin):
        config = {"retry_on_corruption": False}
        site_id = "t"
        _lock = threading.Lock()
        jobs = {"u": {"duration_sec": 6.0}}
        updates = []
        def _update_job(self, *a, **k): self.updates.append((a, k))
        def log_event(self, *a, **k): pass

    final = tmp_path / "f.mp4"; final.write_bytes(b"x")
    monkeypatch.setattr(runner_integrity, "db_log", lambda *a, **k: None)

    def fake_verify(path, expected_duration=None, timeout=60):
        calls.append((Path(path), expected_duration))
        return StreamVerificationResult(valid=False, truncated=True, errors=["truncated media stream: stream 0 ends at 3.00s of 6.00s declared"])
    monkeypatch.setattr(runner_integrity.stream_verifier, "verify_stream", fake_verify)
    r = Runner()
    ok, retry, reason = r._verify_integrity_or_quarantine("u", final, "f.mp4", 1)
    assert calls == [(final, 6.0)]  # the job's known duration reaches the verifier
    assert (ok, retry) == (False, False) and "truncated media stream" in reason
    assert not final.exists() and (tmp_path / "_failed" / "f.mp4").exists()  # quarantined

    # unchecked probe (no ffprobe): fails OPEN, and the OK reason says the continuity check did not run
    final.write_bytes(b"x")
    monkeypatch.setattr(runner_integrity.stream_verifier, "verify_stream",
                        lambda path, expected_duration=None, timeout=60: StreamVerificationResult(valid=False, checked=False, errors=["ffprobe not available on system PATH"]))
    ok, retry, reason = Runner()._verify_integrity_or_quarantine("u", final, "f.mp4", 1)
    assert ok is True and "stream continuity unverified" in reason

    # zips and images never went down verify_media_integrity's ffprobe route: the stream verifier
    # (which would fail them for having no packets) is not run on them
    calls.clear()
    monkeypatch.setattr(runner_integrity.stream_verifier, "verify_stream", fake_verify)
    for name in ("a.zip", "p.jpg", "p.png", "p.gif", "p.webp"):
        other = tmp_path / name; other.write_bytes(b"x")
        assert Runner()._verify_integrity_or_quarantine("u", other, name, 1) == (True, False, "")
        assert other.exists()
    assert calls == []
    assert runner_integrity._is_stream_container(tmp_path / "v.mkv") is True
