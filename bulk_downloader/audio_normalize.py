"""Two-pass EBU R128 loudnorm command planning and background execution."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Mapping, Sequence


_REQUIRED_MEASUREMENTS = (
    "input_i", "input_lra", "input_tp", "input_thresh", "target_offset",
)
_LOUDNORM_JSON = re.compile(r"\{.*?\}", re.DOTALL)
_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"


def build_analysis_argv(source_path: str, *, ffmpeg: str, audio_stream: int = 0) -> list[str]:
    """Build pass-one ffmpeg argv for ONE audio stream (``0:a:<n>``); execution
    remains outside this seam. Every retained audio stream is measured on its own."""
    return [ffmpeg, "-hide_banner", "-i", source_path, "-map", f"0:a:{audio_stream}", "-af",
            f"{_FILTER}:print_format=json", "-f", "null", "-"]


def build_probe_argv(source_path: str, *, ffprobe: str) -> list[str]:
    """ffprobe argv listing the audio stream indexes of ``source_path`` (one per line)."""
    return [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
            "-of", "csv=p=0", source_path]


def count_audio_streams(probe_stdout: str) -> int:
    return sum(1 for line in (probe_stdout or "").splitlines() if line.strip())


def ffprobe_for(ffmpeg: str) -> str:
    """The ffprobe beside a given ffmpeg binary (same directory, same suffix)."""
    directory, name = os.path.split(ffmpeg)
    probe = name.replace("ffmpeg", "ffprobe", 1) if "ffmpeg" in name else "ffprobe"
    return os.path.join(directory, probe) if directory else probe


def parse_measurements(stderr: str) -> dict[str, str]:
    """Extract the complete pass-one loudnorm report from ffmpeg stderr."""
    match = _LOUDNORM_JSON.search(stderr or "")
    if match is None:
        raise ValueError("loudnorm measurements missing JSON report")
    try:
        report = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("loudnorm measurements invalid JSON report") from exc
    missing = [key for key in _REQUIRED_MEASUREMENTS if not str(report.get(key, ""))]
    if missing:
        raise ValueError("missing loudnorm measurements: " + ", ".join(missing))
    return {key: str(report[key]) for key in _REQUIRED_MEASUREMENTS}


def _loudnorm_filter(measurements: Mapping[str, str]) -> str:
    missing = [key for key in _REQUIRED_MEASUREMENTS if not measurements.get(key)]
    if missing:
        raise ValueError("missing loudnorm measurements: " + ", ".join(missing))
    return (
        f"{_FILTER}:measured_I={measurements['input_i']}:"
        f"measured_LRA={measurements['input_lra']}:"
        f"measured_TP={measurements['input_tp']}:"
        f"measured_thresh={measurements['input_thresh']}:"
        f"offset={measurements['target_offset']}:linear=true:print_format=summary"
    )


def build_apply_argv(source_path: str, output_path: str,
                     measurements: Mapping[str, str] | Sequence[Mapping[str, str]], *,
                     ffmpeg: str) -> list[str]:
    """Build pass-two argv using the exact measurements emitted by pass one.

    Every stream of the source is kept (``-map 0``): video, subtitle, data and
    attachment streams are stream-copied untouched; each audio stream ``n`` gets
    its own loudnorm filter from ``measurements[n]`` (a single mapping means one
    audio stream). An audio-only operation never transcodes video."""
    per_stream = [measurements] if isinstance(measurements, Mapping) else list(measurements)
    if not per_stream:
        raise ValueError("no audio stream measurements")
    argv = [ffmpeg, "-y", "-i", source_path, "-map", "0",
            "-c:v", "copy", "-c:s", "copy", "-c:d", "copy", "-c:t", "copy"]
    for index, m in enumerate(per_stream):
        argv += [f"-filter:a:{index}", _loudnorm_filter(m)]
    argv.append(output_path)
    return argv


def normalize_in_background(source_path: str, *, ffmpeg: str) -> None:
    """Start the optional fail-open two-pass rewrite without blocking downloads."""
    threading.Thread(target=_normalize_file, args=(source_path, ffmpeg),
                     daemon=True, name="audio-normalize").start()


def _normalize_file(source_path: str, ffmpeg: str) -> None:
    source = Path(source_path)
    # The temporary output is EXCLUSIVELY OURS: a fresh unique file beside the
    # source (never a predictable name that may already belong to someone), so
    # cleanup can only ever remove what this invocation created.
    temporary: Path | None = None
    try:
        probe = subprocess.run(build_probe_argv(str(source), ffprobe=ffprobe_for(ffmpeg)),
                               capture_output=True, text=True, check=False)
        streams = count_audio_streams(probe.stdout) if probe.returncode == 0 else 1
        if streams < 1:
            return
        measurements = []
        for index in range(streams):
            analysis = subprocess.run(build_analysis_argv(str(source), ffmpeg=ffmpeg, audio_stream=index),
                                      capture_output=True, text=True, check=False)
            if analysis.returncode:
                return
            measurements.append(parse_measurements(analysis.stderr))
        fd, tmp_name = tempfile.mkstemp(prefix=f".{source.stem}.loudnorm-", suffix=source.suffix,
                                        dir=str(source.parent))
        os.close(fd)
        temporary = Path(tmp_name)
        applied = subprocess.run(
            build_apply_argv(str(source), str(temporary), measurements, ffmpeg=ffmpeg),
            capture_output=True, text=True, check=False,
        )
        if applied.returncode == 0 and temporary.is_file() and temporary.stat().st_size > 0:
            temporary.replace(source)
            temporary = None
    except (OSError, ValueError):
        return
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
