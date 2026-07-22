"""v3.43.74: multi-connection chunked HTTP downloader.

# What this is

For large files (5-7GB 8K HEVC scenes), a single TCP connection
often hits per-connection rate limits at the CDN. Opening N parallel
connections to different byte ranges and merging on completion can
substantially improve throughput.

# Strategy

  1. HEAD the URL: get Content-Length, verify Accept-Ranges: bytes
  2. Decide if multi-conn is viable (file size > min threshold,
     server supports Range)
  3. Split the byte range into N chunks
  4. Write to a single sparse file (no temp-files-then-concat — that
     doubles disk usage on the merge step). Use os.pwrite() on POSIX,
     seek+write on Windows.
  5. Spawn N worker threads. Each fetches its byte range with
     `Range: bytes=START-END` and writes its chunk at the right
     offset.
  6. Progress: each worker bumps a shared counter under a lock. A
     progress callback receives (downloaded_bytes, total_bytes).
  7. Resume: per-chunk progress survives in-memory. If a worker dies,
     other workers continue; the failed chunk re-spawns once.

# Fallbacks

  - Server returns 200 to HEAD instead of supporting Range → fall
    back to single-connection direct download
  - File too small (< min_size_mb) → single-conn (overhead not worth)
  - Worker thread fatal error → retry that chunk once; if it fails
    again, abort the multi-conn attempt and let the caller retry
    with single-conn

# What this is NOT

  - Not a torrent client (single source URL, not peer-to-peer)
  - Not concurrent file downloads (BD's worker pool handles that;
    this is parallel ranges of ONE file)
  - Not a permanent replacement for the single-conn path; it's an
    enhancement that opt-in per site activates

# Fail-open contract

The caller's expected pattern is:

    if multi_conn.can_use(url, headers) and size >= threshold:
        result = multi_conn.download(url, output_path, ...)
        if result.ok:
            return  # done
        # else fall through to single-conn

`can_use()` may HEAD the URL but doesn't block on errors. `download()`
returns DownloadResult(ok=False) on every failure mode without
raising.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable

log = logging.getLogger(__name__)


# Defaults
DEFAULT_CHUNK_COUNT = 4
DEFAULT_MIN_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_CONNECT_TIMEOUT_S = 10.0
DEFAULT_CHUNK_RETRIES = 1
DEFAULT_BUFFER_SIZE = 64 * 1024  # 64 KiB


# ─── Availability ──────────────────────────────────────────────────


def is_available() -> bool:
    """True if httpx is importable. Multi-conn requires httpx;
    without it, the caller should fall through to its standard
    download path."""
    try:
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


# ─── SSRF host guard (F-RUN03-02) ──────────────────────────────────
#
# A download/media URL is never a legitimate internal target, so this
# module blocks ALL non-public hosts (no LAN exception -- unlike the
# plex/jellyfin/HA/stash hook integrations). Both the initial host and
# every redirect hop are validated with the canonical resolver in
# provider_resolve_impl._common, which resolves DNS and refuses if ANY
# resolved address is loopback/link-local/private/reserved/multicast.


class _SSRFBlocked(Exception):
    """Raised when a host (initial or redirect hop) is non-public."""


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or ""
    except (ValueError, TypeError):
        return ""


def _guard_url(url: str):
    """(ok, reason) for the *initial* request host. Fail closed."""
    from .provider_resolve_impl._common import _is_safe_public_host
    host = _host_of(url)
    if not host:
        return False, "no host in url"
    return _is_safe_public_host(host)


def _redirect_guard_hook(response) -> None:
    """httpx response event-hook: on a redirect, re-validate the hop's
    Location host before the client follows it. Raise ``_SSRFBlocked``
    to abort. Non-redirect responses and public hops pass through."""
    if not getattr(response, "is_redirect", False):
        return
    loc = response.headers.get("location")
    if not loc:
        return
    from urllib.parse import urljoin
    from .provider_resolve_impl._common import _is_safe_public_host
    try:
        base = str(response.request.url)
    except Exception:
        base = ""
    target = urljoin(base, loc) if base else loc
    host = _host_of(target)
    ok, reason = _is_safe_public_host(host)
    if not ok:
        raise _SSRFBlocked(f"redirect to non-public host {host!r}: {reason}")


# ─── Probe (can_use) ───────────────────────────────────────────────


@dataclass
class ProbeResult:
    """Outcome of probing whether multi-connection is viable."""
    ok: bool = False                # safe to multi-conn this URL?
    content_length: int = 0
    accept_ranges: bool = False
    final_url: str = ""             # post-redirect URL
    server: str = ""                # Server: header
    error: str = ""


def probe(
    url: str,
    *, headers: Optional[dict] = None,
    cookies: Optional[dict] = None,
    timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    proxy: Optional[str] = None,
) -> ProbeResult:
    """HEAD the URL and inspect the response headers.

    Returns ProbeResult(ok=True) only when the server reports a
    positive Content-Length AND Accept-Ranges: bytes (or at least
    doesn't say Accept-Ranges: none).

    Some servers respond 405 to HEAD; we then try a Range GET for
    the first byte to check support indirectly.
    """
    if not url or not isinstance(url, str):
        return ProbeResult(ok=False, error="invalid_url")
    if not is_available():
        return ProbeResult(ok=False, error="httpx_not_installed")
    ok, reason = _guard_url(url)
    if not ok:
        return ProbeResult(ok=False, error=f"ssrf_blocked:{reason}"[:200])
    try:
        import httpx
        with httpx.Client(timeout=timeout_s, follow_redirects=True,
                          cookies=cookies or None, proxy=proxy,
                          event_hooks={"response": [_redirect_guard_hook]}
                          ) as client:
            # First try HEAD
            try:
                r = client.head(url, headers=headers or {})
                status = r.status_code
            except Exception:
                r = None
                status = 0
            if r is not None and status == 200:
                cl = int(r.headers.get("content-length") or 0)
                ar = (r.headers.get("accept-ranges") or "").lower()
                accept_ok = (ar == "bytes")
                # Some servers don't advertise but actually support
                # Range. We still set accept_ranges based on the header
                # though — the caller can decide.
                return ProbeResult(
                    ok=(cl > 0 and accept_ok),
                    content_length=cl,
                    accept_ranges=accept_ok,
                    final_url=str(r.url),
                    server=r.headers.get("server", ""),
                )
            # HEAD failed or returned non-200 — try a 1-byte Range GET
            try:
                rh = dict(headers or {})
                rh["Range"] = "bytes=0-0"
                rg = client.get(url, headers=rh)
            except Exception as e:
                return ProbeResult(ok=False,
                                   error=f"range_probe_failed:{e}"[:200])
            if rg.status_code == 206:
                # Got a partial response — Range is supported
                cr = rg.headers.get("content-range") or ""
                # Format: "bytes 0-0/12345" — extract the total
                total = 0
                if "/" in cr:
                    try:
                        total = int(cr.split("/")[-1])
                    except ValueError:
                        pass
                return ProbeResult(
                    ok=(total > 0),
                    content_length=total,
                    accept_ranges=True,
                    final_url=str(rg.url),
                    server=rg.headers.get("server", ""),
                )
            return ProbeResult(
                ok=False, error=f"head_{status}_get_{rg.status_code}",
            )
    except Exception as e:
        return ProbeResult(ok=False,
                           error=f"{type(e).__name__}:{str(e)[:120]}")


# ─── Chunk planning ────────────────────────────────────────────────


@dataclass
class ChunkPlan:
    """One chunk's byte range."""
    index: int      # 0-based
    start: int      # inclusive
    end: int        # inclusive (HTTP Range semantics)

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def plan_chunks(content_length: int, chunk_count: int) -> list:
    """Split [0, content_length-1] into roughly equal chunks.

    Last chunk gets any remainder. Returns a list of ChunkPlan.

    chunk_count is clamped to [1, 16] (more than 16 is rarely useful
    and stresses some servers).
    """
    if content_length <= 0:
        return []
    n = max(1, min(16, int(chunk_count)))
    if n == 1:
        return [ChunkPlan(0, 0, content_length - 1)]
    chunk_size = content_length // n
    chunks: list = []
    for i in range(n):
        start = i * chunk_size
        end = (start + chunk_size - 1) if i < n - 1 else content_length - 1
        chunks.append(ChunkPlan(i, start, end))
    return chunks


# ─── Download ──────────────────────────────────────────────────────


@dataclass
class DownloadResult:
    """Outcome of a multi-connection download."""
    ok: bool = False
    bytes_written: int = 0
    chunks_completed: int = 0
    chunks_failed: int = 0
    elapsed_s: float = 0.0
    avg_speed_bps: float = 0.0
    chunk_count: int = 0
    error: str = ""


def _ensure_parent_dir(path: str) -> None:
    """Create the parent directory of `path` if needed. Best effort."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except Exception:
        pass


def _allocate_sparse_file(path: str, size: int) -> bool:
    """Pre-allocate a sparse file of `size` bytes. Returns True on
    success. Used so all workers can write to their offsets without
    races on file growth.

    On Windows, opening with mode 'wb' and seeking + writing extends
    the file with implicit zeros (sparse on NTFS). On POSIX,
    ftruncate is the canonical way.
    """
    try:
        _ensure_parent_dir(path)
        with open(path, "wb") as f:
            f.truncate(size)
        return True
    except Exception as e:
        log.info("multi_conn: allocate_sparse_file failed: %s", e)
        return False


def _download_chunk(
    client, url: str, chunk: ChunkPlan, output_path: str,
    *, headers: dict, on_progress: Callable,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
    chunk_retries: int = DEFAULT_CHUNK_RETRIES,
) -> tuple:
    """Download one chunk. Returns (success: bool, bytes_written: int,
    error: str)."""
    import httpx
    attempt = 0
    while attempt <= chunk_retries:
        attempt += 1
        bytes_written = 0
        local_headers = dict(headers)
        local_headers["Range"] = f"bytes={chunk.start}-{chunk.end}"
        try:
            with client.stream("GET", url, headers=local_headers) as r:
                if r.status_code not in (200, 206):
                    err = f"chunk_{chunk.index}_http_{r.status_code}"
                    if attempt > chunk_retries:
                        return False, bytes_written, err
                    log.info("multi_conn: %s on attempt %d; retrying",
                             err, attempt)
                    continue
                # Open the output file once, seek to chunk.start, stream
                # bytes in. Using r+b so we don't truncate the
                # pre-allocated sparse file.
                with open(output_path, "r+b") as out_f:
                    out_f.seek(chunk.start)
                    for buf in r.iter_bytes(buffer_size):
                        if not buf:
                            continue
                        out_f.write(buf)
                        bytes_written += len(buf)
                        try:
                            on_progress(chunk.index, bytes_written, len(buf))
                        except Exception:
                            pass
                if bytes_written != chunk.length:
                    err = (f"chunk_{chunk.index}_short_"
                           f"{bytes_written}_of_{chunk.length}")
                    if attempt > chunk_retries:
                        return False, bytes_written, err
                    log.info("multi_conn: %s; retrying", err)
                    continue
                return True, bytes_written, ""
        except Exception as e:
            err = f"chunk_{chunk.index}_{type(e).__name__}:{str(e)[:80]}"
            if attempt > chunk_retries:
                return False, bytes_written, err
            log.info("multi_conn: chunk %d %s on attempt %d; retrying",
                     chunk.index, e, attempt)
    return False, 0, f"chunk_{chunk.index}_unreachable"


def download(
    url: str,
    output_path: str,
    *,
    content_length: int = 0,
    chunk_count: int = DEFAULT_CHUNK_COUNT,
    headers: Optional[dict] = None,
    cookies: Optional[dict] = None,
    progress_cb: Optional[Callable] = None,
    bytes_callback: Optional[Callable] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    cancel_check: Optional[Callable[[], bool]] = None,
    chunk_retries: int = DEFAULT_CHUNK_RETRIES,
    proxy: Optional[str] = None,
) -> DownloadResult:
    """Multi-connection chunked download.

    `content_length`: if known (e.g. from a prior probe), pass it in.
    If 0, we probe first.
    `progress_cb(downloaded, total)`: called with cumulative unique logical
    output bytes. Retried writes to the same chunk offsets do not advance it.
    `bytes_callback(delta_bytes)`: called with the per-write delta on
    every chunk write. Used for bandwidth supervision (v3.43.76);
    callers can pass `supervisor.acquire`-style hooks here. Optional.
    `cancel_check()`: returns True to abort. Workers exit ASAP.

    Returns DownloadResult — never raises.
    """
    start = time.monotonic()
    if not url:
        return DownloadResult(ok=False, error="invalid_url")
    if not output_path:
        return DownloadResult(ok=False, error="invalid_output_path")
    if not is_available():
        return DownloadResult(ok=False, error="httpx_not_installed")
    ok, reason = _guard_url(url)
    if not ok:
        return DownloadResult(
            ok=False, error=f"ssrf_blocked:{reason}"[:200],
            elapsed_s=time.monotonic() - start,
        )
    headers = dict(headers or {})
    cookies = dict(cookies or {})
    if content_length <= 0:
        pr = probe(url, headers=headers, cookies=cookies,
                   timeout_s=connect_timeout_s, proxy=proxy)
        if not pr.ok:
            return DownloadResult(
                ok=False, error=f"probe_failed:{pr.error}",
                elapsed_s=time.monotonic() - start,
            )
        content_length = pr.content_length
    if content_length <= 0:
        return DownloadResult(ok=False, error="zero_content_length")

    chunks = plan_chunks(content_length, chunk_count)
    if not chunks:
        return DownloadResult(ok=False, error="plan_empty")

    if not _allocate_sparse_file(output_path, content_length):
        return DownloadResult(
            ok=False, error="sparse_allocate_failed",
            elapsed_s=time.monotonic() - start,
        )

    # Shared progress state
    progress_lock = threading.Lock()
    total_downloaded = [0]  # cumulative unique logical output bytes
    chunk_progress = {chunk.index: 0 for chunk in chunks}
    chunk_lengths = {chunk.index: chunk.length for chunk in chunks}
    last_published = [0]
    chunk_results: dict = {}

    def _on_progress(chunk_index: int, attempt_bytes: int, delta: int):
        logical_advanced = False
        with progress_lock:
            prior = chunk_progress[chunk_index]
            current = min(chunk_lengths[chunk_index], max(prior, attempt_bytes))
            if current > prior:
                chunk_progress[chunk_index] = current
                total_downloaded[0] += current - prior
                logical_advanced = True
            logical_total = total_downloaded[0]
        # v3.43.76: feed the bandwidth supervisor (if configured) so
        # per-chunk writes respect the configured throttle. Called
        # OUTSIDE the lock so the supervisor's potential sleep doesn't
        # block other workers' delta accounting.
        if bytes_callback is not None:
            try:
                bytes_callback(delta)
            except Exception:
                pass
        if progress_cb and logical_advanced:
            # A slower earlier callback can resume after a later thread has
            # computed a larger total. Re-enter the progress lock and publish
            # only a new high-water mark so observers never see bytes regress.
            with progress_lock:
                if logical_total <= last_published[0]:
                    return
                last_published[0] = logical_total
                try:
                    progress_cb(logical_total, content_length)
                except Exception:
                    pass

    cancel_event = threading.Event()
    if cancel_check is not None:
        # Spawn a watcher that flips cancel_event when cancel_check is True
        def _watcher():
            while not cancel_event.is_set():
                try:
                    if cancel_check():
                        cancel_event.set()
                        return
                except Exception:
                    pass
                time.sleep(0.5)
        threading.Thread(target=_watcher, daemon=True,
                         name="multi-conn-cancel").start()

    threads: list = []
    try:
        import httpx
    except ImportError:
        return DownloadResult(ok=False, error="httpx_not_installed",
                              elapsed_s=time.monotonic() - start)

    # One Client per worker — sharing isn't safe and per-conn limits
    # are per-Client. Plus this gives each worker its own pool.
    def _worker(chunk: ChunkPlan):
        if cancel_event.is_set():
            chunk_results[chunk.index] = (False, 0, "cancelled")
            return
        try:
            with httpx.Client(timeout=timeout_s, follow_redirects=True,
                              cookies=cookies or None, proxy=proxy,
                              event_hooks={"response": [_redirect_guard_hook]}
                              ) as client:
                ok, bw, err = _download_chunk(
                    client, url, chunk, output_path,
                    headers=headers, on_progress=_on_progress,
                    chunk_retries=chunk_retries,
                )
        except Exception as e:
            ok, bw, err = False, 0, f"{type(e).__name__}:{str(e)[:80]}"
        chunk_results[chunk.index] = (ok, bw, err)

    for chunk in chunks:
        t = threading.Thread(
            target=_worker, args=(chunk,),
            daemon=True, name=f"mc-{chunk.index}",
        )
        threads.append(t)
        t.start()

    for t in threads:
        # Use a long join timeout — content_length / 1MB/s is the
        # absolute floor expected speed
        t.join()

    cancel_event.set()  # stop the watcher if still running

    elapsed = time.monotonic() - start
    completed = sum(1 for r in chunk_results.values() if r[0])
    failed = len(chunks) - completed
    total_bytes_written = sum(r[1] for r in chunk_results.values())

    if cancel_check is not None and cancel_check():
        return DownloadResult(
            ok=False,
            bytes_written=total_bytes_written,
            chunks_completed=completed,
            chunks_failed=failed,
            elapsed_s=elapsed,
            chunk_count=len(chunks),
            error="cancelled",
        )

    if failed > 0:
        err_summary = "; ".join(
            f"{i}={chunk_results[i][2]}"
            for i in sorted(chunk_results)
            if not chunk_results[i][0]
        )[:300]
        return DownloadResult(
            ok=False,
            bytes_written=total_bytes_written,
            chunks_completed=completed,
            chunks_failed=failed,
            elapsed_s=elapsed,
            chunk_count=len(chunks),
            error=f"chunks_failed:{err_summary}",
        )

    avg_bps = (total_bytes_written / elapsed) if elapsed > 0 else 0.0
    return DownloadResult(
        ok=True,
        bytes_written=total_bytes_written,
        chunks_completed=completed,
        chunks_failed=0,
        elapsed_s=elapsed,
        avg_speed_bps=avg_bps,
        chunk_count=len(chunks),
    )


# ─── Caller convenience ────────────────────────────────────────────


def should_use_multi_conn(
    content_length: int,
    accept_ranges: bool,
    min_size_bytes: int = DEFAULT_MIN_SIZE_BYTES,
) -> bool:
    """Decision: should the caller attempt multi-conn for a file of
    this size, given Range support? Returns False for tiny files
    where multi-conn overhead isn't worth it."""
    if content_length <= 0:
        return False
    if not accept_ranges:
        return False
    if content_length < min_size_bytes:
        return False
    return True


__all__ = [
    "DEFAULT_CHUNK_COUNT",
    "DEFAULT_MIN_SIZE_BYTES",
    "ProbeResult",
    "probe",
    "ChunkPlan",
    "plan_chunks",
    "DownloadResult",
    "download",
    "should_use_multi_conn",
    "is_available",
]


# ── EXT-3: throughput-adaptive connection count ──────────────────────
def adaptive_chunk_count(prev_n: int, *, chunks_failed: int = 0,
                         accept_ranges: bool = True) -> int:
    """AIMD-lite next-run connection count from a host's prior outcome.

    A clean prior run (no failed chunks) probes ONE more connection; a run with
    any failed chunk backs off by two; a ranges-unsupported host pins to the base
    (multi-conn can't help). Always clamped to [2, 16] -- the same envelope
    plan_chunks enforces. This is the observed-outcome feedback the fixed
    config N lacked; the caller records (chunk_count, chunks_failed, avg_speed_bps)
    per host after each run so the next run can adapt.
    """
    try:
        base = int(prev_n)
    except (TypeError, ValueError):
        base = 4
    if not accept_ranges:
        n = base
    elif chunks_failed and chunks_failed > 0:
        n = base - 2
    else:
        n = base + 1
    return max(2, min(16, n))
