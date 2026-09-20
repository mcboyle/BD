"""bulk_downloader.vmaf_scorer -- Hardware-accelerated VMAF video quality assessment.

Provides perceptual quality scoring using ffmpeg libvmaf with optional CUDA
hardware acceleration. Designed as a non-blocking post-download verification step
to detect low-bitrate and fake upscaled video rips without impeding download
completion.

Key properties:
- CUDA acceleration on supported cluster hardware (Tesla T4 / NVDEC) with CPU fallback.
- Non-blocking execution: bounded timeout and background thread dispatch.
- Process group isolation (isolated_popen_kwargs / kill_process_tree) to avoid orphan leaks.
- Zero external egress: operates strictly locally with no network calls.
- Enriches media_metadata dictionary with assessment metrics.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import importlib
import signal
import sys


def _resolve_ffmpeg() -> str:
    """Resolve ffmpeg binary via ffmpeg_bin if available, fallback to PATH."""
    try:
        mod = importlib.import_module("bulk_downloader.ffmpeg_bin")
        p = getattr(mod, "ffmpeg", lambda: None)()
        if p:
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def _isolated_popen_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"preexec_fn": os.setpgrp}


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        proc.terminate()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()


@dataclass
class VmafScoreResult:
    """Result of a VMAF quality assessment run."""
    ok: bool = False
    vmaf_score: Optional[float] = None
    is_low_quality: bool = False
    is_upscale: bool = False
    hardware_accelerated: bool = False
    cuda_fallback: bool = False    # CUDA run failed, the score is from the CPU retry
    timed_out: bool = False
    error: Optional[str] = None
    media_metadata: Dict[str, Any] = field(default_factory=dict)


def is_cuda_available() -> bool:
    """Check if CUDA hardware acceleration is usable for ffmpeg on this node.

    Fails soft and returns False if drivers or devices are unavailable.
    """
    # Check if nvidia-smi exists and succeeds
    nvsmi = shutil.which("nvidia-smi")
    if not nvsmi:
        return False
    try:
        res = subprocess.run(
            [nvsmi, "-L"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode != 0:
            return False
        # Verify ffmpeg supports cuda hwaccel
        ffmpeg_cmd = _resolve_ffmpeg()
        res_hw = subprocess.run(
            [ffmpeg_cmd, "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        return "cuda" in res_hw.stdout.lower()
    except Exception:
        return False


LOCAL_PROTOCOLS = "file,pipe,crypto,data"
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def build_vmaf_command(
    distorted_path: str | Path,
    reference_path: Optional[str | Path] = None,
    use_cuda: bool = False,
    model_path: Optional[str] = None,
    log_path: Optional[str] = None,
) -> List[str]:
    """Construct ffmpeg command for VMAF computation.

    If reference_path is omitted, evaluates upscale distortion by comparing
    the stream against a downscaled-and-interpolated baseline filter chain.
    """
    for path in (distorted_path, reference_path):
        if path is not None and _URL_SCHEME_RE.match(str(path)):
            raise ValueError(f"vmaf scores local files only, not a URL: {path}")
    ffmpeg_exec = _resolve_ffmpeg()
    # -protocol_whitelist is enforced by ffmpeg itself: with it, the scoring
    # process cannot open http/https/rtmp/... inputs (a playlist or a
    # sidecar reference that points at the network is refused by the
    # demuxer), so the scorer is network-isolated at the process level.
    cmd: List[str] = [ffmpeg_exec, "-nostdin", "-hide_banner", "-protocol_whitelist", LOCAL_PROTOCOLS]

    if use_cuda:
        cmd.extend(["-hwaccel", "cuda"])

    cmd.extend(["-i", str(distorted_path)])

    vmaf_opts = []
    if log_path:
        vmaf_opts.append(f"log_path={log_path}:log_fmt=json")
    if model_path:
        vmaf_opts.append(f"model_path={model_path}")
    vmaf_filter_str = f"libvmaf={':'.join(vmaf_opts)}" if vmaf_opts else "libvmaf"

    if reference_path:
        cmd.extend(["-i", str(reference_path)])
        filter_complex = f"[0:v][1:v]{vmaf_filter_str}"
    else:
        # Reference-free / Self-upscale check: downscale 50% and upscale back
        filter_complex = (
            f"[0:v]split=2[orig][ref];"
            f"[ref]scale=iw/2:ih/2,scale=iw*2:ih*2[scaled];"
            f"[orig][scaled]{vmaf_filter_str}"
        )

    cmd.extend(["-filter_complex", filter_complex, "-f", "null", "-"])
    return cmd


def parse_vmaf_output(output_text: str, log_content: Optional[str] = None) -> Optional[float]:
    """Extract VMAF numerical score from ffmpeg terminal output or JSON log."""
    # 1. Parse JSON log if provided
    if log_content:
        try:
            data = json.loads(log_content)
            if "pooled_metrics" in data and "vmaf" in data["pooled_metrics"]:
                vmaf_obj = data["pooled_metrics"]["vmaf"]
                if "mean" in vmaf_obj:
                    return float(vmaf_obj["mean"])
                if "harmonic_mean" in vmaf_obj:
                    return float(vmaf_obj["harmonic_mean"])
            if "vmaf" in data and isinstance(data["vmaf"], (int, float)):
                return float(data["vmaf"])
        except Exception:
            pass

    # 2. Parse standard ffmpeg output strings
    # Match: "VMAF score: 48.321456"
    m = re.search(r"VMAF\s+score:\s*([0-9]+(?:\.[0-9]+)?)", output_text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # Match: "VMAF score = 65.4"
    m = re.search(r"VMAF\s+score\s*=\s*([0-9]+(?:\.[0-9]+)?)", output_text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # Match: "vmaf: 82.5"
    m = re.search(r"\bvmaf:\s*([0-9]+(?:\.[0-9]+)?)", output_text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    return None


def _execute_vmaf_subprocess(cmd: List[str], timeout: float) -> Tuple[int, str, str]:
    """Execute ffmpeg in an isolated process group with strict timeout enforcement."""
    kwargs = {**_isolated_popen_kwargs(), "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True}
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=1.0)
        except Exception:
            pass
        raise TimeoutError(f"VMAF scoring timed out after {timeout} seconds")


def _run_ffmpeg_vmaf(
    distorted_path: str | Path,
    reference_path: Optional[str | Path] = None,
    use_cuda: bool = False,
    timeout: float = 30.0,
    log_path: Optional[str] = None,
) -> Tuple[int, str, str]:
    """Helper to build and execute the ffmpeg command."""
    cmd = build_vmaf_command(
        distorted_path=distorted_path,
        reference_path=reference_path,
        use_cuda=use_cuda,
        log_path=log_path,
    )
    return _execute_vmaf_subprocess(cmd, timeout=timeout)


def flag_low_quality_upscale(
    vmaf_score: float,
    threshold: float = 70.0,
    claimed_resolution: Optional[str] = None,
) -> bool:
    """Determine if a video is a low-quality or fake upscale.

    A score below the threshold (default 70.0) indicates significant
    perceptual distortion or low high-frequency content characteristic of upscaling.
    """
    return vmaf_score < threshold


def score_video(
    video_path: str | Path,
    reference_path: Optional[str | Path] = None,
    quality_threshold: float = 70.0,
    timeout: float = 30.0,
    use_cuda: Optional[bool] = None,
    media_metadata: Optional[Dict[str, Any]] = None,
) -> VmafScoreResult:
    """Perform synchronous VMAF scoring with non-blocking timeout safety.

    If media_metadata dictionary is supplied, it is mutated in-place with
    the resulting assessment so post-download workflows have immediate access.
    """
    if use_cuda is None:
        use_cuda = is_cuda_available()

    claimed_res = None
    if media_metadata and "resolution" in media_metadata:
        claimed_res = str(media_metadata["resolution"])

    with tempfile.TemporaryDirectory(prefix="vmaf_") as tmp_dir:
        json_log = os.path.join(tmp_dir, "vmaf.json")
        try:
            rc, stdout, stderr = _run_ffmpeg_vmaf(
                distorted_path=video_path,
                reference_path=reference_path,
                use_cuda=use_cuda,
                timeout=timeout,
                log_path=json_log,
            )
            cuda_fallback = False
            if rc != 0 and use_cuda:
                # the CUDA path failed (driver/decoder/unsupported codec):
                # score on the CPU instead of failing the assessment
                use_cuda, cuda_fallback = False, True
                rc, stdout, stderr = _run_ffmpeg_vmaf(
                    distorted_path=video_path,
                    reference_path=reference_path,
                    use_cuda=False,
                    timeout=timeout,
                    log_path=json_log,
                )
            if rc != 0:
                res = VmafScoreResult(
                    ok=False,
                    error=f"ffmpeg exited with exit {rc}: {stderr.strip()}",
                    hardware_accelerated=use_cuda,
                )
                raise RuntimeError(res.error)
            combined_output = f"{stdout}\n{stderr}"
            log_content = None
            if os.path.exists(json_log):
                try:
                    with open(json_log, "r", encoding="utf-8") as f:
                        log_content = f.read()
                except Exception:
                    pass

            score = parse_vmaf_output(combined_output, log_content=log_content)
            if score is None:
                err = "No VMAF score could be extracted from ffmpeg output"
                res = VmafScoreResult(ok=False, error=err, hardware_accelerated=use_cuda)
            else:
                is_low = flag_low_quality_upscale(score, threshold=quality_threshold, claimed_resolution=claimed_res)
                res = VmafScoreResult(
                    ok=True,
                    vmaf_score=score,
                    is_low_quality=is_low,
                    is_upscale=is_low,
                    hardware_accelerated=use_cuda,
                    cuda_fallback=cuda_fallback,
                )
        except TimeoutError as te:
            res = VmafScoreResult(
                ok=False,
                timed_out=True,
                hardware_accelerated=use_cuda,
                error=str(te),
            )
        except RuntimeError:
            pass
        except Exception as exc:
            res = VmafScoreResult(ok=False, error=str(exc), hardware_accelerated=use_cuda)

    # Attach assessment to media_metadata if provided
    if media_metadata is not None:
        vmaf_dict: Dict[str, Any] = {
            "score": res.vmaf_score,
            "is_low_quality": res.is_low_quality,
            "is_upscale": res.is_upscale,
            "threshold": quality_threshold,
            "hardware_accelerated": res.hardware_accelerated,
            "timed_out": res.timed_out,
            "evaluated_at": time.time(),
        }
        if res.error:
            vmaf_dict["error"] = res.error
        media_metadata["vmaf"] = vmaf_dict
        res.media_metadata = media_metadata

    return res


def score_video_async(
    video_path: str | Path,
    reference_path: Optional[str | Path] = None,
    quality_threshold: float = 70.0,
    timeout: float = 30.0,
    use_cuda: Optional[bool] = None,
    media_metadata: Optional[Dict[str, Any]] = None,
    callback: Optional[Callable[[VmafScoreResult], None]] = None,
) -> threading.Thread:
    """Non-blocking asynchronous quality assessment.

    Dispatches score_video to a background daemon thread so download completion
    never blocks on media quality evaluation. Updates media_metadata on completion.
    """
    def _worker():
        res = score_video(
            video_path=video_path,
            reference_path=reference_path,
            quality_threshold=quality_threshold,
            timeout=timeout,
            use_cuda=use_cuda,
            media_metadata=media_metadata,
        )
        if media_metadata is not None and "vmaf" not in media_metadata:
            if res.media_metadata and "vmaf" in res.media_metadata:
                media_metadata["vmaf"] = res.media_metadata["vmaf"]
            elif res.vmaf_score is not None:
                media_metadata["vmaf"] = {
                    "score": res.vmaf_score,
                    "is_low_quality": res.is_low_quality,
                    "is_upscale": res.is_upscale,
                }
        if callback is not None:
            try:
                callback(res)
            except Exception:
                pass

    t = threading.Thread(target=_worker, name="vmaf-scorer-worker", daemon=True)
    t.start()
    return t
