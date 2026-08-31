"""v3.43.63: HLS (.m3u8) and DASH (.mpd) download path.

# Why a dedicated module

The existing worker model is "click a button, capture the file URL,
HTTP-download it." That works for direct MP4/MKV URLs but breaks on
HLS manifests where the "file" is a playlist of N segments served
separately. The old fallback was yt-dlp -- slow (subprocess startup,
serial fragment fetch by default) and opaque to progress reporting.

This module:

  1. Detects HLS / DASH URLs (by extension and Content-Type)
  2. Drives ffmpeg with proper input options (User-Agent, Referer,
     timeout, header rewrites) to download + remux into a single file
  3. Parses ffmpeg's stderr progress (frame=, time=, size=) and reports
     bytes/percent back to the caller via callback
  4. Handles segment-level failures: ffmpeg has a `-reconnect` family
     of options that we enable by default; on persistent failure the
     module returns a structured error the worker can convert into a
     retry decision

# Why ffmpeg, not a pure-Python HLS library

  - ffmpeg handles every quirk of every CDN we've ever seen
  - It's already a recommended install for the live-recorder runtime
    (v3.43.62) so users who set that up don't need a second tool
  - Streamlink isn't a fit -- it's optimized for live streams, not
    static playlists, and adds its own overhead
  - Pure-Python options (m3u8 + httpx) exist but reimplementing the
    full HLS variant resolution + per-segment retry + remux is more
    code than this entire module

# Fail-open

If ffmpeg isn't installed, `is_available()` returns False and the worker
falls back to yt-dlp / Playwright. No errors.
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
# INV-003: this module is one of the documented fail-open sinks.
# `except Exception: pass` clauses in ffmpeg dispatch are intentional —
# subprocess failures must not crash the worker. See DANGER_MAP §INV-003.
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─── Tunables ───────────────────────────────────────────────────────

# Tunables read at CALL TIME (store key > env seed > default) so a Settings
# write takes effect on the next download without a restart (v3.66.503 GUI
# parity). Previously module-level constants bound once at import.

# Default ffmpeg input timeout per microsegment (microseconds). 10s.
def _input_timeout_us() -> int:
    from .runtime_flags import num
    return num("hls_input_timeout_us", "BD_HLS_INPUT_TIMEOUT_US", 10000000, int)


# Soft cap on total download wall time per URL. Some sites serve 8K HEVC
# at 5+GB; on a 100Mbps line that's ~10 minutes. Give 60 minutes.
def _max_runtime_s() -> int:
    from .runtime_flags import num
    return num("hls_max_runtime_s", "BD_HLS_MAX_RUNTIME_S", 3600, int)


# How often to poll the output file size for progress reports (seconds).
def _progress_poll_s() -> float:
    from .runtime_flags import num
    return num("hls_progress_poll_s", "BD_HLS_PROGRESS_POLL_S", 1.0, float)

# ffmpeg reconnect options -- retry on segment fetch failures up to N times
RECONNECT_DELAY_MAX_S = 10


# ─── Backend detection ──────────────────────────────────────────────

_FFMPEG_CACHE: Optional[str] = None
_FFMPEG_LOCK = threading.Lock()


def _find_ffmpeg() -> Optional[str]:
    """Locate ffmpeg on PATH. Cached. Returns absolute path or None."""
    global _FFMPEG_CACHE
    with _FFMPEG_LOCK:
        if _FFMPEG_CACHE is not None:
            return _FFMPEG_CACHE or None
        from . import ffmpeg_bin      # MOD-4
        ff = ffmpeg_bin.ffmpeg() or ""
        _FFMPEG_CACHE = ff
        return ff or None


def _reset_ffmpeg_cache_for_tests() -> None:
    """v3.66.703 (MOD-4): clear BOTH caches. This module caches its resolved ffmpeg,
    and ffmpeg_bin (the one resolver) caches too -- so clearing only this one left a
    WARM resolver cache that served the old path straight through a patched
    shutil.which. Two caches, one reset: the resolver's must go too."""
    global _FFMPEG_CACHE
    with _FFMPEG_LOCK:
        _FFMPEG_CACHE = None
    from . import ffmpeg_bin
    ffmpeg_bin.reset()      # unguarded by design: reset() cannot raise (it clears a
                            # dict), and a broad except here would only add a blind
                            # spot -- plus a new DP-13 the ratchet correctly rejects.


def is_available() -> bool:
    """True iff ffmpeg is on PATH."""
    return _find_ffmpeg() is not None


# ─── URL classification ─────────────────────────────────────────────


_HLS_EXTS = (".m3u8",)
_DASH_EXTS = (".mpd",)
_HLS_CONTENT_TYPES = (
    "application/x-mpegurl",
    "application/vnd.apple.mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
)
_DASH_CONTENT_TYPES = (
    "application/dash+xml",
)


def is_hls_url(url: str) -> bool:
    """Cheap heuristic by URL extension. Robust enough -- callers can
    follow up with `is_hls_content_type(headers)` when the URL has no
    extension (e.g., signed URLs with no path)."""
    if not isinstance(url, str):
        return False
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    return any(path.endswith(ext) for ext in _HLS_EXTS)


def is_dash_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    return any(path.endswith(ext) for ext in _DASH_EXTS)


def is_streaming_url(url: str) -> bool:
    """Either HLS or DASH."""
    return is_hls_url(url) or is_dash_url(url)


def is_hls_content_type(content_type: str) -> bool:
    """True when an HTTP Content-Type response header indicates HLS."""
    if not isinstance(content_type, str):
        return False
    return any(ct in content_type.lower() for ct in _HLS_CONTENT_TYPES)


def is_dash_content_type(content_type: str) -> bool:
    if not isinstance(content_type, str):
        return False
    return any(ct in content_type.lower() for ct in _DASH_CONTENT_TYPES)


# ─── Result type ────────────────────────────────────────────────────


@dataclass
class DownloadResult:
    ok: bool
    output_path: str = ""
    bytes_written: int = 0
    duration_s: float = 0.0
    error: str = ""           # short code
    error_detail: str = ""    # human-readable
    ffmpeg_exit: Optional[int] = None
    ffmpeg_last_lines: list[str] = field(default_factory=list)


# ─── Progress parser ────────────────────────────────────────────────


# ffmpeg progress lines look like:
#   frame= 1234 fps= 30 q=-1.0 size=  123456kB time=00:01:23.45 bitrate=...
_FFMPEG_PROGRESS_RE = re.compile(
    r"size=\s*(\d+)kB.*?time=(\d+):(\d+):([\d.]+)",
    re.IGNORECASE,
)


def _parse_ffmpeg_progress(line: str) -> Optional[dict]:
    """Pull bytes_so_far and seconds_played from one ffmpeg stderr line.
    Returns dict on hit, None on miss."""
    m = _FFMPEG_PROGRESS_RE.search(line)
    if not m:
        return None
    try:
        kb = int(m.group(1))
        h = int(m.group(2))
        mm = int(m.group(3))
        ss = float(m.group(4))
    except Exception:
        return None
    return {
        "bytes": kb * 1024,
        "seconds": h * 3600 + mm * 60 + ss,
    }


# ─── Command builder ────────────────────────────────────────────────


# ─── Egress: proxy carriage + ambient-environment scrub ─────────────
#
# Row 439 (v3.66.1362): this module's Popen passed no ``env=``, so ffmpeg
# inherited the whole ambient environment -- including any ``http_proxy`` set in
# the service environment, which silently rerouted every http:// segment fetch.
# It also had no way to be TOLD a proxy, so BD's configured per-site proxy /
# tunnel SOCKS url never reached a segmented transfer at all. Both halves are
# fixed here, at the boundary that owns the subprocess.
#
# Every case variant is scrubbed: the child's resolver is case-insensitive on
# some platforms and libcurl-style tooling reads both spellings.
_PROXY_ENV_KEYS = frozenset({
    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy",
})

# ffmpeg's HTTP protocol implements an HTTP proxy (``-http_proxy`` /
# ``http_proxy``) and nothing else. It has NO SOCKS support, which is exactly
# what BD's tunnels expose (``vpn_socks`` -> ``socks5://127.0.0.1:PORT``).
#
# CONFINE, DON'T REFUSE (operator ruling, 2026-08-31). A proxy ffmpeg cannot
# carry is NOT by itself a reason to stop the transfer: when the caller also
# hands us a network namespace, the namespace's own route IS the egress (see
# ``netns_isolation.egress_commands`` -- wg0 becomes the ns's only default
# route), so the unusable url is DROPPED and the confined child transfers
# inside the tunnel. Only when there is neither a carriable proxy nor a
# namespace is the egress claim unestablishable, and then -- per CLAUDE.md A7 --
# the transfer refuses rather than fetching the media on the clear interface
# and reporting OK.
_FFMPEG_PROXY_SCHEMES = ("http", "https")


def _proxy_scheme(proxy_url: str) -> str:
    try:
        return (urlparse(proxy_url).scheme or "").lower()
    except Exception:
        return ""


def proxy_is_carriable(proxy_url: Optional[str]) -> bool:
    """True when ffmpeg can actually be handed ``proxy_url``.

    Public because the caller's egress seam has to make the same judgement
    BEFORE it decides whether the transfer needs a namespace, and this module
    owns the fact about ffmpeg. One definition, two readers -- a second copy of
    the scheme list in the caller is exactly the drift A7 warns about. An empty
    proxy is trivially carriable (there is nothing to carry).
    """
    prox = (proxy_url or "").strip()
    return (not prox) or _proxy_scheme(prox) in _FFMPEG_PROXY_SCHEMES


def _scrubbed_env(proxy_url: str = "") -> dict:
    """The environment the ffmpeg child gets: ambient proxy variables removed,
    and the resolved proxy (if any) injected explicitly.

    Nothing else is altered -- PATH, HOME and the locale are inherited exactly
    as before, because narrowing them is a different change with its own
    failure modes.
    """
    env = dict(os.environ)
    for key in [k for k in env if k.lower() in _PROXY_ENV_KEYS]:
        del env[key]
    if proxy_url:
        env["http_proxy"] = proxy_url
        env["https_proxy"] = proxy_url
    return env


def _build_ffmpeg_cmd(
    ffmpeg_path: str,
    input_url: str,
    output_path: str,
    user_agent: str = "",
    referer: str = "",
    extra_headers: Optional[dict[str, str]] = None,
    threads: int = 1,
    extra_args: Optional[list[str]] = None,
    proxy_url: str = "",
    netns: str = "",
) -> list[str]:
    """Construct an ffmpeg argv that downloads `input_url` (HLS or DASH)
    into `output_path`. Designed to be robust on flaky CDNs:

      - Input timeout per segment (avoids hangs on dead segments)
      - Auto-reconnect on network errors
      - User-Agent + Referer so segment fetches use the same identity
        as the page that served the manifest
      - Stream copy by default (`-c copy`) -- no re-encoding, instant remux
      - Map first video + first audio (or none) only
      - Output container inferred from extension

    Caller MUST validate input_url has http(s) scheme already.
    """
    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "info",
           "-y",  # overwrite output without prompting
           "-nostdin",  # don't try to read from stdin
           ]
    # Input options that go BEFORE -i (per ffmpeg argv ordering rules)
    cmd += [
        "-timeout", str(_input_timeout_us()),
        # Reconnect on transient network errors. Each is a separate flag.
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", str(RECONNECT_DELAY_MAX_S),
    ]
    # Row 439: an INPUT option, so it must precede -i (ffmpeg argv ordering).
    if proxy_url:
        cmd += ["-http_proxy", proxy_url]
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if referer:
        cmd += ["-referer", referer]
    if extra_headers:
        # ffmpeg wants headers as a single \r\n-joined blob via -headers
        hdr_blob = "\r\n".join(f"{k}: {v}" for k, v in extra_headers.items()) + "\r\n"
        cmd += ["-headers", hdr_blob]
    cmd += ["-i", input_url]
    # Output options (after -i)
    cmd += [
        "-c", "copy",       # stream-copy: no re-encoding
        "-map", "0:v:0",    # first video
        "-map", "0:a:0?",   # first audio if present (the '?' = optional)
        "-bsf:a", "aac_adtstoasc",  # HLS audio often needs this for mp4 muxing
        "-threads", str(max(1, threads)),
    ]
    if extra_args:
        cmd += list(extra_args)
    cmd.append(output_path)
    # Row 439: confine the whole subprocess to a per-capture network namespace,
    # exactly as ``_build_ytdlp_cmd`` / ``_build_gallerydl_cmd`` already do
    # (F5, v3.66.689). LAST, so the wrap encloses the fully-built argv; netns
    # None/empty -> byte-identical prior cmd.
    if netns:
        from . import netns_isolation
        cmd = netns_isolation.netns_exec_argv(netns, cmd)
    return cmd


# ─── Download driver ────────────────────────────────────────────────


def download(
    manifest_url: str,
    output_path: str,
    *,
    user_agent: str = "",
    referer: str = "",
    extra_headers: Optional[dict[str, str]] = None,
    threads: int = 1,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    max_runtime_s: Optional[int] = None,
    extra_ffmpeg_args: Optional[list[str]] = None,
    proxy_url: Optional[str] = None,
    netns: Optional[str] = None,
) -> DownloadResult:
    """Download an HLS/DASH stream at `manifest_url` to `output_path`.

    `progress_callback` is called on each parsed progress line with a
    dict containing keys: bytes (int), seconds (float), elapsed (float).
    Safe to be None.

    `cancel_check` is called between progress polls; if it returns True
    the ffmpeg subprocess is terminated and DownloadResult(ok=False,
    error='cancelled') is returned.

    `max_runtime_s` overrides the hls_max_runtime_s default for this call.

    `proxy_url` (row 439) is the egress proxy the caller resolved fail-closed.
    An http(s):// proxy is carried into BOTH the argv (``-http_proxy``) and the
    child environment. ``None``/empty means "no proxy resolved"; the child's
    environment is scrubbed of ambient proxy variables either way, so an
    ambient ``http_proxy`` can no longer reroute segment fetches unnoticed.

    `netns` (row 439) is the per-capture network namespace the ffmpeg child
    must be confined to -- the same ``netns_isolation`` confinement the yt-dlp
    and gallery-dl subprocesses have carried since v3.66.689. The caller owns
    the ``capture_netns`` bracket (create/teardown); this function only wraps
    the argv.

    The two interact, and CONFINEMENT WINS. A proxy ffmpeg cannot honour --
    notably the ``socks5://`` url a BD tunnel exposes, which ffmpeg's HTTP
    protocol has no support for -- is DROPPED when a namespace is given, since
    the namespace's own route is then the egress and the transfer stays inside
    the tunnel. With no namespace to fall back on, the same proxy returns
    ``proxy_unsupported`` WITHOUT spawning anything: an egress claim that
    cannot be established is UNKNOWN, never OK.

    Returns a DownloadResult. Never raises.
    """
    ff = _find_ffmpeg()
    if not ff:
        return DownloadResult(ok=False, error="ffmpeg_not_installed",
                              error_detail="ffmpeg not found on PATH")
    if not isinstance(manifest_url, str) or not manifest_url.startswith(("http://", "https://")):
        return DownloadResult(ok=False, error="bad_url",
                              error_detail=f"manifest URL not http(s): {manifest_url!r}")
    if not output_path:
        return DownloadResult(ok=False, error="bad_output_path",
                              error_detail="output_path is empty")
    # Row 439: reconcile the two egress mechanisms BEFORE any filesystem or
    # process side effect. A proxy ffmpeg has no support for is dropped when a
    # namespace confines the child (the ns route is the egress), and refuses
    # when there is no namespace to fall back on.
    prox = (proxy_url or "").strip()
    ns = (netns or "").strip()
    if not proxy_is_carriable(prox):
        if not ns:
            return DownloadResult(
                ok=False, error="proxy_unsupported",
                error_detail=(
                    f"ffmpeg cannot honour a "
                    f"{_proxy_scheme(prox) or '(schemeless)'}:// egress proxy "
                    f"({prox}) and no netns confines it; refusing the "
                    "segmented transfer rather than fetching the media "
                    "outside it"))
        log.info("hls: dropping %s:// proxy ffmpeg cannot carry -- the "
                 "transfer is confined to netns %s instead",
                 _proxy_scheme(prox) or "(schemeless)", ns)
        prox = ""
    # Make sure the output dir exists.
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return DownloadResult(ok=False, error="output_dir_failed",
                              error_detail=f"could not mkdir parent: {e}")
    # Remove partial file from a previous run.
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
    except Exception:
        pass

    cmd = _build_ffmpeg_cmd(
        ff, manifest_url, output_path,
        user_agent=user_agent, referer=referer,
        extra_headers=extra_headers, threads=threads,
        extra_args=extra_ffmpeg_args, proxy_url=prox, netns=ns,
    )

    # Launch in its own process group so we can kill the whole tree on
    # cancel. Cross-platform Popen kwargs.
    popen_kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "bufsize": 1,           # line-buffered
        # Row 439: an EXPLICIT environment. Inheriting the ambient one let a
        # service-level http_proxy reroute every segment fetch with no signal.
        "env": _scrubbed_env(prox),
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    started = time.time()
    deadline = started + (max_runtime_s or _max_runtime_s())
    last_lines: list[str] = []     # keep tail of stderr for diagnostics
    LAST_LINES_KEEP = 25
    last_progress_emit = 0.0

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except FileNotFoundError:
        return DownloadResult(ok=False, error="ffmpeg_not_installed",
                              error_detail="ffmpeg disappeared between detect and exec")
    except Exception as e:
        return DownloadResult(ok=False, error="ffmpeg_spawn_failed",
                              error_detail=f"{type(e).__name__}: {e}")

    # Read stderr line-by-line in a background thread so we can also
    # poll for cancellation + runtime deadline from the main loop.
    stderr_q: list[str] = []
    stderr_done = threading.Event()

    def _reader():
        try:
            for line in proc.stderr:
                stderr_q.append(line.rstrip("\r\n"))
        except Exception:
            pass
        finally:
            stderr_done.set()

    rt = threading.Thread(target=_reader, daemon=True, name="hls-stderr-reader")
    rt.start()

    cancelled = False
    timed_out = False

    try:
        while True:
            # Drain whatever's accumulated.
            while stderr_q:
                line = stderr_q.pop(0)
                last_lines.append(line)
                if len(last_lines) > LAST_LINES_KEEP:
                    last_lines = last_lines[-LAST_LINES_KEEP:]
                # Try to parse progress
                p = _parse_ffmpeg_progress(line)
                if p and progress_callback:
                    now = time.time()
                    # Throttle callback frequency to avoid flooding SSE
                    if now - last_progress_emit >= _progress_poll_s():
                        last_progress_emit = now
                        try:
                            progress_callback({
                                "bytes": p["bytes"],
                                "seconds": p["seconds"],
                                "elapsed": now - started,
                            })
                        except Exception as cb_e:
                            log.warning("hls progress_callback raised: %s", cb_e)

            # Check process status
            rc = proc.poll()
            if rc is not None:
                # Drain remaining stderr lines after process exit.
                stderr_done.wait(timeout=2.0)
                while stderr_q:
                    line = stderr_q.pop(0)
                    last_lines.append(line)
                    if len(last_lines) > LAST_LINES_KEEP:
                        last_lines = last_lines[-LAST_LINES_KEEP:]
                break

            # Check cancellation
            if cancel_check is not None:
                try:
                    if cancel_check():
                        cancelled = True
                        _terminate(proc)
                        break
                except Exception as e:
                    log.warning("hls cancel_check raised: %s", e)

            # Check deadline
            if time.time() > deadline:
                timed_out = True
                _terminate(proc)
                break

            time.sleep(_progress_poll_s())
    finally:
        # Ensure subprocess is gone before we return.
        if proc.poll() is None:
            _terminate(proc)

    duration = time.time() - started
    rc = proc.returncode if proc.returncode is not None else -1

    if cancelled:
        return DownloadResult(
            ok=False, error="cancelled", error_detail="cancel_check returned True",
            duration_s=duration, ffmpeg_exit=rc, ffmpeg_last_lines=last_lines,
        )
    if timed_out:
        return DownloadResult(
            ok=False, error="timeout",
            error_detail=f"exceeded max_runtime_s={max_runtime_s or _max_runtime_s()}",
            duration_s=duration, ffmpeg_exit=rc, ffmpeg_last_lines=last_lines,
        )
    if rc != 0:
        return DownloadResult(
            ok=False, error="ffmpeg_failed",
            error_detail=f"ffmpeg exited {rc}; last stderr: "
                         + "\n".join(last_lines[-5:]),
            duration_s=duration, ffmpeg_exit=rc, ffmpeg_last_lines=last_lines,
        )
    # Verify the output file actually got written.
    try:
        size = os.path.getsize(output_path)
    except OSError:
        size = 0
    if size == 0:
        return DownloadResult(
            ok=False, error="empty_output",
            error_detail="ffmpeg exit 0 but output is empty",
            duration_s=duration, ffmpeg_exit=rc, ffmpeg_last_lines=last_lines,
        )
    return DownloadResult(
        ok=True, output_path=output_path, bytes_written=size,
        duration_s=duration, ffmpeg_exit=rc, ffmpeg_last_lines=last_lines,
    )


def _terminate(proc: subprocess.Popen) -> None:
    """Best-effort process kill. Tries graceful first, then SIGKILL.

    v3.47.8 (#unrud-port): on POSIX, signal the whole process group
    (which we made child of via start_new_session=True at spawn).
    Plain proc.terminate() only signals the immediate ffmpeg PID;
    ffmpeg's internal threads + any sub-decoders are missed.
    """
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            # CTRL_BREAK_EVENT works because we used CREATE_NEW_PROCESS_GROUP
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            # killpg(pgid, SIGTERM) — pgid == pid because we used
            # start_new_session=True at spawn.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                proc.terminate()  # fallback if pgid lookup fails
    except Exception:
        pass
    try:
        proc.wait(timeout=5.0)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2.0)
    except Exception:
        pass


__all__ = [
    "DownloadResult",
    "download",
    "is_available",
    "is_hls_url",
    "is_dash_url",
    "is_streaming_url",
    "is_hls_content_type",
    "is_dash_content_type",
]
