"""stream_verifier -- media stream chunk integrity and packet continuity verifier (row 952).

Verifies bitstream packet continuity and container integrity of live/interrupted
stream downloads before marking download jobs complete, and automates missing
chunk re-acquisition.

Design principles:
  * PACKET CONTINUITY CHECK: Runs ffprobe packet inspection over stream containers,
    detecting DTS/PTS discontinuities, timestamp jumps, and dropped packets.
  * AUTOMATED RE-ACQUISITION: Detects missing or corrupted stream chunks and
    automates targeted re-fetching to assemble healthy, continuous media.
  * PROCESS GROUP ISOLATION (Fleet Rule 45): All subprocesses execute under
    start_new_session=True with os.killpg cleanup on timeout.
  * NON-BLOCKING / FAIL-SOFT: Missing binaries, timeouts, or corrupt inputs
    return structured verification results rather than throwing unhandled exceptions.
"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from . import ffmpeg_bin

PathLike = Union[str, Path]
DEFAULT_TIMEOUT = 60


@dataclass
class Discontinuity:
    stream_index: int
    pts_prev: float
    pts_current: float
    gap_seconds: float


@dataclass
class StreamVerificationResult:
    valid: bool
    total_packets: int = 0
    dropped_packets: int = 0
    discontinuities: list[Discontinuity] = field(default_factory=list)
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    # checked=False: the probe could not run (no ffprobe, timeout, unreadable output) -- the
    # verdict is UNKNOWN, not a stream defect; a completion path fails open on it but reports it.
    checked: bool = True
    container_duration: float = 0.0
    truncated: bool = False


# Only these stream kinds carry a continuous packet cadence. Subtitle/data tracks are sparse by
# nature (cues at 0 s and 5 s) and gap-checking them refutes every healthy subtitled file (E2).
CONTINUOUS_CODEC_TYPES = ("video", "audio")


def truncation_tolerance(duration: float) -> float:
    """Seconds of tail a stream may be short of its declared duration: the larger of 1.0 s and 5%.
    Wide enough for an audio track that stops a few frames before the video, far short of a
    stall that lost whole segments."""
    return max(1.0, 0.05 * duration)


def _run_isolated(
    cmd: list[str],
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    """Execute command with process group isolation and os.killpg cleanup (Fleet Rule 45)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except BaseException:
        # timeout or any other failure: the whole process group dies, then the original error
        # propagates (the caller turns TimeoutExpired into a checked=False result)
        _kill_process_group(proc)
        raise


def _kill_process_group(proc: subprocess.Popen) -> list[str]:
    """SIGKILL proc's session (start_new_session=True made it the group leader) and reap it.
    Returns the cleanup steps that failed (already-exited processes make these ordinary), so a
    caller that cares can log them; nothing here raises."""
    failed: list[str] = []
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError as exc:
        failed.append(f"killpg: {exc}")
    try:
        proc.kill()
    except OSError as exc:
        failed.append(f"kill: {exc}")
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError) as exc:
        failed.append(f"wait: {exc}")
    return failed


def check_packet_continuity(
    media_path: PathLike,
    max_gap_seconds: float = 0.5,
    min_packets: int = 5,
    timeout: int = DEFAULT_TIMEOUT,
    expected_duration: Optional[float] = None,
) -> StreamVerificationResult:
    """Analyze packet timestamps in media_path using ffprobe to detect discontinuities and
    tail truncation.

    Truncation (E1): each video/audio stream's last packet end is compared with the container's
    declared format.duration (mp4/mkv carry the full length in the header even when the tail is
    missing) and, when the caller knows it, `expected_duration` (an HLS playlist total, a job's
    known length -- the only evidence for a cut-short MPEG-TS, whose duration is just its last
    timestamp). Continuity is judged on video/audio streams only (E2), each on one clock in
    presentation order (pts; see _presentation_timeline).

    Returns StreamVerificationResult; fail-soft: a probe that cannot run returns checked=False.
    """
    path = Path(media_path)
    if not path.is_file():
        return StreamVerificationResult(
            valid=False,
            errors=[f"media file not found: {path}"],
        )

    if path.stat().st_size == 0:
        return StreamVerificationResult(
            valid=False,
            errors=["media file is empty (0 bytes)"],
        )

    ffprobe = ffmpeg_bin.ffprobe()
    if not ffprobe:
        # no probe ran: UNKNOWN (checked=False), the same fail-open contract verify_media_integrity
        # keeps for a host without ffmpeg -- not a stream defect that quarantines every download
        return StreamVerificationResult(
            valid=False, checked=False,
            errors=["ffprobe not available on system PATH"],
        )

    cmd = [
        ffprobe,
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-show_packets",
        "-of", "json",
        str(path),
    ]

    try:
        rc, stdout, stderr = _run_isolated(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return StreamVerificationResult(
            valid=False, checked=False,
            errors=["ffprobe packet inspection timed out"],
        )
    except Exception as exc:
        return StreamVerificationResult(
            valid=False, checked=False,
            errors=[f"ffprobe invocation failed: {exc}"],
        )

    errors = []
    if rc != 0:
        err_msg = (stderr or stdout or "").strip()[-200:]
        errors.append(f"ffprobe reported error (exit {rc}): {err_msg}")

    try:
        data = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        errors.append(f"unparseable ffprobe output: {exc}")
        return StreamVerificationResult(valid=False, checked=False, errors=errors)

    packets = data.get("packets", [])
    total_packets = len(packets)
    if total_packets == 0:
        errors.append("no media packets found in stream")
        return StreamVerificationResult(valid=False, errors=errors)

    container_duration = _as_float((data.get("format") or {}).get("duration"))
    expected_duration = _as_float(expected_duration)
    codec_types: dict[int, str] = {}
    for st in data.get("streams", []) or []:
        idx = st.get("index")
        if isinstance(idx, int):
            codec_types[idx] = str(st.get("codec_type") or "")

    if total_packets < min_packets and not _is_still_picture(
        total_packets, codec_types, container_duration, expected_duration
    ):
        errors.append(
            f"truncated media stream: packet count {total_packets} < minimum expected {min_packets}"
        )

    # Group packets by stream_index (continuous a/v streams only); each stream is then judged
    # on ONE clock in presentation order (_presentation_timeline)
    streams_packets: dict[int, list[dict]] = {}
    for pkt in packets:
        s_idx = pkt.get("stream_index", 0)
        if codec_types and codec_types.get(s_idx, "") not in CONTINUOUS_CODEC_TYPES:
            continue
        streams_packets.setdefault(s_idx, []).append(pkt)

    discontinuities: list[Discontinuity] = []
    total_dropped = 0
    max_duration = 0.0
    # per stream: (first timestamp, last packet end). The SPAN between them is what is compared
    # with the declared length -- a live MPEG-TS starts at an arbitrary PTS (tens of thousands of
    # seconds), so an absolute end time says nothing about how much of the stream was captured.
    stream_spans: dict[int, tuple[float, float]] = {}

    for s_idx, stream_pkts in streams_packets.items():
        prev_ts: Optional[float] = None
        prev_dur: float = 0.0

        for ts, dur in _presentation_timeline(stream_pkts):
            if ts > max_duration:
                max_duration = ts
            first, end = stream_spans.get(s_idx, (ts, ts + dur))
            stream_spans[s_idx] = (min(first, ts), max(end, ts + dur))

            if prev_ts is not None:
                expected_ts = prev_ts + (prev_dur if prev_dur > 0 else 0.0)
                gap = ts - expected_ts
                if gap > max_gap_seconds:
                    discontinuities.append(
                        Discontinuity(
                            stream_index=s_idx,
                            pts_prev=prev_ts,
                            pts_current=ts,
                            gap_seconds=round(gap, 4),
                        )
                    )
                    est_dropped = max(1, int(round(gap / (prev_dur if prev_dur > 0 else 0.1))))
                    total_dropped += est_dropped

            prev_ts = ts
            prev_dur = dur

    # Tail truncation: every continuous stream's captured span must reach the declared length
    # (the container header's format.duration and/or the caller's expected_duration)
    truncated = False
    declared = [d for d in (container_duration, expected_duration) if d > 0]
    for s_idx, (first, end) in stream_spans.items():
        span = end - first
        for target in declared:
            if target - span > truncation_tolerance(target):
                truncated = True
                errors.append(
                    f"truncated media stream: stream {s_idx} covers {span:.2f}s of {target:.2f}s declared"
                )
                break

    valid = (len(errors) == 0) and (len(discontinuities) == 0)

    return StreamVerificationResult(
        valid=valid,
        total_packets=total_packets,
        dropped_packets=total_dropped,
        discontinuities=discontinuities,
        duration_seconds=round(max_duration, 4),
        errors=errors,
        container_duration=container_duration,
        truncated=truncated,
    )


def _presentation_timeline(stream_pkts: list[dict]) -> list[tuple[float, float]]:
    """One stream's packets as (timestamp, duration) on a SINGLE clock, sorted into presentation
    order. The clock is pts_time; dts_time is the fallback only for a packet that carries no pts.

    Mixing clocks across packets (dts when present, else pts) is not a timeline: ffprobe reports
    dts_time=N/A on the B-frame lead-in packets of an H.264 Matroska stream, so their pts (0 s,
    4 s at 1 fps) met the next packet's dts (0 s) as a 3-4 s "gap", and the dts tail ended one
    B-frame lookahead short of format.duration -- a healthy 1-2 fps camera/timelapse file was
    judged gappy AND truncated. Presentation order is the order a viewer sees, so a dropped
    segment is still a hole in it and the last presented end is still the captured span."""
    timeline: list[tuple[float, float]] = []
    for pkt in stream_pkts:
        ts_str = pkt.get("pts_time")
        if ts_str is None or ts_str == "N/A":
            ts_str = pkt.get("dts_time")
        if ts_str is None:
            continue
        try:
            ts = float(ts_str)
        except ValueError:
            continue
        if not math.isfinite(ts):
            continue
        try:
            dur = float(pkt.get("duration_time") or "0.0")
        except ValueError:
            dur = 0.0
        if not math.isfinite(dur) or dur < 0:
            dur = 0.0
        timeline.append((ts, dur))
    timeline.sort(key=lambda item: item[0])
    return timeline


def _is_still_picture(total_packets: int, codec_types: dict[int, str],
                      container_duration: float, expected_duration: float) -> bool:
    """One video packet, no audio, no declared length: a still picture that reached the ffprobe
    route (bmp/tiff/...), not a stream cut short. Any declared duration makes it a stream again."""
    return (
        total_packets == 1
        and bool(codec_types)
        and set(codec_types.values()) <= {"video"}
        and container_duration <= 0
        and expected_duration <= 0
    )


def _as_float(value) -> float:
    """A JSON number/string as a finite non-negative float, else 0.0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return number if number > 0 else 0.0


def verify_stream(
    media_path: PathLike,
    max_gap_seconds: float = 0.5,
    min_packets: int = 5,
    timeout: int = DEFAULT_TIMEOUT,
    expected_duration: Optional[float] = None,
) -> StreamVerificationResult:
    """Validate healthy stream integrity, packet continuity and tail completeness."""
    return check_packet_continuity(
        media_path=media_path,
        max_gap_seconds=max_gap_seconds,
        min_packets=min_packets,
        timeout=timeout,
        expected_duration=expected_duration,
    )


def reacquire_and_assemble(
    chunk_indices: list[int],
    chunk_dir: PathLike,
    fetch_chunk_fn: Callable[[int], Path],
    output_path: PathLike,
    max_gap_seconds: float = 0.5,
    min_packets: int = 5,
    timeout: int = DEFAULT_TIMEOUT,
) -> StreamVerificationResult:
    """Automate re-acquisition of missing chunks and assemble full stream.

    Scans chunk_dir for chunks specified in chunk_indices. Missing chunks are
    reacquired using fetch_chunk_fn. The complete set is concatenated and verified.
    """
    directory = Path(chunk_dir)
    directory.mkdir(parents=True, exist_ok=True)
    out = Path(output_path)

    collected_paths: list[Path] = []
    for idx in sorted(chunk_indices):
        chunk_file = directory / f"chunk_{idx}.ts"
        if not chunk_file.is_file() or chunk_file.stat().st_size == 0:
            reacquired = fetch_chunk_fn(idx)
            chunk_file = Path(reacquired)
        collected_paths.append(chunk_file)

    # Concatenate MPEG-TS segments sequentially
    with open(out, "wb") as outfile:
        for p in collected_paths:
            outfile.write(p.read_bytes())

    return check_packet_continuity(
        media_path=out,
        max_gap_seconds=max_gap_seconds,
        min_packets=min_packets,
        timeout=timeout,
    )


def verify_or_fail(
    media_path: PathLike,
    job_id: str,
    mark_failed: Callable[[str, str], None],
    max_gap_seconds: float = 0.5,
    min_packets: int = 5,
    timeout: int = DEFAULT_TIMEOUT,
    expected_duration: Optional[float] = None,
) -> StreamVerificationResult:
    """Run continuity verification; on failure call mark_failed(job_id, reason).

    Non-blocking: catches internal errors and marks job failed without raising.
    """
    result = check_packet_continuity(
        media_path=media_path,
        max_gap_seconds=max_gap_seconds,
        min_packets=min_packets,
        timeout=timeout,
        expected_duration=expected_duration,
    )

    if not result.valid:
        reason = (
            result.errors[0]
            if result.errors
            else f"packet continuity check failed ({result.dropped_packets} dropped packets)"
        )
        try:
            mark_failed(job_id, reason)
        except Exception as exc:
            # the verdict stands; the failed callback is recorded on the result, not lost
            result.errors.append(f"mark_failed raised: {type(exc).__name__}: {str(exc)[:100]}")

    return result
