"""ffmpeg_bin -- the single place BD decides WHICH ffmpeg/ffprobe it runs (MOD-4).

Before this module, SEVEN modules each called bare ``shutil.which("ffmpeg")``
(dedup, enrichment, healthcheck, hls_downloader, live_recorder, thumbnail_gen,
thumbnail_sheets). There was no central resolver and no way to say *which* ffmpeg to
use -- BD simply took whatever was first on PATH.

That is a real, already-observed failure: the static ffmpeg build (johnvansickle
7.0.2) SEGFAULTS on HLS+HTTPS, and the distro build must be used instead.
``healthcheck._ffmpeg_capability`` already PROBES for exactly this class (does the
binary run; does it have the mpegts muxer + https protocol). But a probe only tells
you the binary on PATH is bad -- it gives you no way to point BD at the good one.
This module is that missing half.

Resolution order (first hit wins):

  1. the ``ffmpeg_path`` global-config pin -- a DIRECTORY containing the build;
  2. ``shutil.which`` -- exactly today's behaviour.

An EMPTY pin (the default) therefore leaves every existing deployment byte-identical.
A pin that points at nothing degrades back to ``which`` rather than hard-failing the
app: resolution fails OPEN, because it is the capability probe -- not the resolver --
whose job is to fail closed on a bad binary.

``ffprobe`` is taken from the SAME directory as the pinned ``ffmpeg``. Mixing an
ffmpeg from one build with an ffprobe from another is precisely the inconsistency
the pin exists to remove; they ship together and are resolved together.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

_CACHE: dict = {}
_HWACCEL_OVERRIDE: Optional[bool] = None


def reset() -> None:
    """Drop the resolution cache (a config change or a test must be able to
    re-resolve without a restart)."""
    global _HWACCEL_OVERRIDE
    _CACHE.clear()
    _HWACCEL_OVERRIDE = None


def _pinned_dir() -> str:
    """The operator's ``ffmpeg_path`` pin: a directory holding the ffmpeg build.
    Empty (the default) means 'no pin -- use PATH'. Never raises: a broken or
    absent config store degrades to no pin."""
    try:
        from . import global_config
        return str(global_config.get("ffmpeg_path", "") or "").strip()
    except Exception:
        return ""


def _resolve(name: str) -> Optional[str]:
    if name in _CACHE:
        return _CACHE[name]
    found: Optional[str] = None
    pin = _pinned_dir()
    if pin:
        cand = os.path.join(pin, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            found = cand
    if not found:
        found = shutil.which(name) or None
    _CACHE[name] = found
    return found


def ffmpeg() -> Optional[str]:
    """Path to the ffmpeg BD should run, or None."""
    return _resolve("ffmpeg")


def ffprobe() -> Optional[str]:
    """Path to ffprobe -- from the SAME build as :func:`ffmpeg` when pinned."""
    return _resolve("ffprobe")


def available() -> bool:
    """True when an ffmpeg binary resolves at all (presence only -- capability is
    ``healthcheck._ffmpeg_capability``'s job, and presence has never implied it)."""
    return ffmpeg() is not None


def both_available() -> bool:
    """True when BOTH ffmpeg and ffprobe resolve (what the thumbnail/sheet paths
    actually need)."""
    return ffmpeg() is not None and ffprobe() is not None


def _probe_cuda_support() -> bool:
    """Check if NVIDIA GPU and CUDA acceleration are available and functional."""
    if _HWACCEL_OVERRIDE is not None:
        return _HWACCEL_OVERRIDE
    env_val = os.environ.get("HWACCEL", "").strip().lower()
    if env_val in ("cuda", "1", "true"):
        return True
    if env_val in ("cpu", "none", "0", "false"):
        return False
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "-1":
        return False

    has_dev = os.path.exists("/dev/nvidia0") or os.path.exists("/proc/driver/nvidia")
    smi = shutil.which("nvidia-smi")
    if not (has_dev or smi):
        return False

    if smi:
        try:
            res = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=2)
            if res.returncode != 0 or not res.stdout.strip():
                return False
        except Exception:
            return False
    return True


def is_cuda_available() -> bool:
    """Return True if GPU hardware acceleration with CUDA is available."""
    if "cuda_available" in _CACHE:
        return _CACHE["cuda_available"]
    avail = bool(available() and _probe_cuda_support())
    _CACHE["cuda_available"] = avail
    return avail


def detect_hwaccel() -> Optional[str]:
    """Return 'cuda' if GPU hardware acceleration is available, otherwise None."""
    return "cuda" if is_cuda_available() else None


def build_ffmpeg_command(
    args: list[str],
    *,
    use_hwaccel: Optional[bool] = None,
) -> list[str]:
    """Build an ffmpeg invocation command, injecting -hwaccel cuda when available.

    Args:
        args: Command arguments. If args[0] is not ffmpeg, the resolved ffmpeg
              binary path is prepended.
        use_hwaccel: Explicit override. If None, auto-detects via is_cuda_available().

    Returns:
        List of command tokens with -hwaccel cuda placed before the input (-i).
    """
    cmd = list(args)
    ff = ffmpeg() or "ffmpeg"
    # row835 fixer: args[0] naming the binary in ANY spelling ("ffmpeg", a
    # path ending in /ffmpeg, or the resolved path) is the binary and is
    # replaced by the resolved one -- never doubled into ['/usr/bin/ffmpeg',
    # 'ffmpeg', ...]. Anything else is an argument list and ff is prepended.
    if cmd and (cmd[0] == ff or cmd[0] == "ffmpeg" or os.path.basename(cmd[0]) == "ffmpeg"):
        cmd[0] = ff
    else:
        cmd.insert(0, ff)

    should_accel = is_cuda_available() if use_hwaccel is None else bool(use_hwaccel)
    if should_accel and "-hwaccel" not in cmd:
        if "-i" in cmd:
            idx = cmd.index("-i")
            cmd[idx:idx] = ["-hwaccel", "cuda"]
        else:
            cmd.extend(["-hwaccel", "cuda"])

    return cmd


build_ffmpeg_cmd = build_ffmpeg_command


_CUDA_ERRORS = (
    "cuda",
    "cannot load libcuda",
    "cannot load nvcuda",
    "device creation failed",
    "no nvdec capable devices",
    "failed to setup cuda",
    "out of memory",
    "unknown error",
)


def _is_cuda_failure(stderr: str) -> bool:
    """Check if stderr contains signatures of CUDA/NVDEC hardware decode failure."""
    if not stderr:
        return False
    low = stderr.lower()
    return any(err in low for err in _CUDA_ERRORS)


def _stderr_text(stderr) -> str:
    """row835 fixer: subprocess.run(capture_output=True) without text=True
    yields BYTES stderr -- the default for every caller here; decode it so the
    CUDA-failure signatures are seen instead of an empty string."""
    if not stderr:
        return ""
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", "replace")
    return str(stderr)


def _strip_hwaccel(cmd: list[str]) -> list[str]:
    """Remove -hwaccel and its parameter from command tokens."""
    res = []
    skip = False
    for token in cmd:
        if skip:
            skip = False
            continue
        if token == "-hwaccel":
            skip = True
            continue
        res.append(token)
    return res


def run_ffmpeg(
    cmd: list[str],
    *args,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Execute ffmpeg with automatic fail-soft fallback to CPU decode if CUDA fails.

    If the command was built with -hwaccel and encounters CUDA driver or memory
    errors, automatically retries with CPU decode (-hwaccel stripped).
    """
    has_hwaccel = "-hwaccel" in cmd
    try:
        proc = subprocess.run(cmd, *args, **kwargs)
        if has_hwaccel and proc.returncode != 0:
            if _is_cuda_failure(_stderr_text(proc.stderr)):
                cpu_cmd = _strip_hwaccel(cmd)
                return subprocess.run(cpu_cmd, *args, **kwargs)
        return proc
    except Exception:
        if has_hwaccel:
            cpu_cmd = _strip_hwaccel(cmd)
            return subprocess.run(cpu_cmd, *args, **kwargs)
        raise


run_with_fallback = run_ffmpeg


def check_call_ffmpeg(cmd: list[str], **kwargs) -> None:
    """``subprocess.check_call`` for ffmpeg with the CUDA -> CPU fallback.

    Keeps the ``subprocess.check_call`` boundary the thumbnail_sheets callers
    always had (row 440 pins argv[0] at that boundary; stdout/stderr are the
    caller's, typically DEVNULL, so a CUDA signature cannot be read here):
    a failed ``-hwaccel`` run is retried once with the CPU decode; a CPU
    failure raises ``CalledProcessError`` exactly as before.
    """
    try:
        subprocess.check_call(cmd, **kwargs)
    except subprocess.CalledProcessError:
        if "-hwaccel" not in cmd:
            raise
        subprocess.check_call(_strip_hwaccel(cmd), **kwargs)
