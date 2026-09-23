"""Capture numbered diagnostic frames from a local virtual display."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from . import ffmpeg_bin


def record_frames(
    output_dir: Path,
    *,
    display: str = ":0",
    frame_count: int = 8,
    fps: int = 4,
    timeout_s: float | None = None,
    ffmpeg_path: str | None = None,
    spawn: Callable[[list[str]], object] | None = None,
) -> list[Path]:
    """Record a bounded PNG sequence, returning no frames when capture fails."""
    if frame_count < 1 or fps < 1:
        raise ValueError("frame_count and fps must be positive")
    binary = ffmpeg_path or ffmpeg_bin.ffmpeg()
    if not binary:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "frame-%06d.png"
    command = [
        binary, "-f", "x11grab", "-i", display,
        "-frames:v", str(frame_count), "-r", str(fps), str(pattern),
    ]
    process = None
    try:
        process = spawn(command) if spawn else subprocess.Popen(command, stdin=subprocess.DEVNULL)
        if process.wait(timeout=timeout_s) != 0:
            return []
        return sorted(output_dir.glob("frame-*.png"))
    except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
        return []
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except (OSError, TimeoutError, subprocess.TimeoutExpired):
                return []
