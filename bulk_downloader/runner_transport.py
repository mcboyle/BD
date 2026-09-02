"""runner_transport -- HTTP download engine: proxy/direct/multi-conn/parallel/probe/promote (VPN/Track-K)

Extracted from runner.py (SiteRunner) @v3.66.403, PHASE 3 runner cut 7 (FINAL
runner unit). Mixin: methods reference self.* only; NO __init__. Import block
derived by AST free-name scan (the seams doc omitted the 4 conditional
soft-import blocks). Cycle rule: kernel from .runner_util, nothing from .runner.

GOTCHA-A: the 5 intra-unit static-helper calls were class-qualified
``SiteRunner._foo(...)`` (static dispatch). Since this module cannot reference
SiteRunner (would import-cycle), and 2 of the call sites are themselves inside
@staticmethod bodies (no ``self`` in scope), all 5 were rewritten to
``TransportMixin._foo(...)`` -- which preserves the original static-dispatch
semantics exactly (NOT self._foo, which would silently switch to dynamic
MRO dispatch and is impossible in the staticmethod contexts anyway).
The 4 adapter soft-import blocks are DUPLICATED here (the core dispatch in
runner.py still references the same flags); flat-sibling imports are idempotent.
"""
import contextlib, json, math, os, re, shutil, sqlite3, sys, threading, time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from .runner_util import (
    _bump_learned_stat, gate_candidate_url, record_bandwidth,
    resolve_url_attribute,
)
from .db import db_log, db_skip_identity
from .detect import res_label, fmt_bytes, safe_dest
from .fname import resolve_filename_template
from .website_title import history_title_kwargs
from .constants import (
    _HTTPDownloadFailed, _DownloadTruncated, _StagingUnavailable,
)
from . import staging_claim
from .download_egress import effective_download_proxy
from . import proxy_pool

# httpx soft import (moved verbatim from runner.py; flat sibling).
try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

# vpn_runtime soft import.
try:
    from . import vpn_runtime
    _VPN_RUNTIME_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_transport] vpn_runtime import failed (degraded): {_e}\n")
    _VPN_RUNTIME_AVAILABLE = False

# multi_conn soft import.
try:
    from . import multi_conn as _mconn
    _MULTI_CONN_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_transport] multi_conn import failed (degraded): {_e}\n")
    _mconn = None
    _MULTI_CONN_AVAILABLE = False

# download_supervisor soft import.
try:
    from . import download_supervisor as _supervisor
    _SUPERVISOR_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_transport] download_supervisor import failed: {_e}\n")
    _supervisor = None
    _SUPERVISOR_AVAILABLE = False


def _finite_config_float(raw, default):
    """Coerce a config-sourced value to a FINITE float, falling back to
    ``default`` on a non-numeric OR non-finite (NaN/inf) value.

    A bare ``float()`` accepts ``'nan'``/``'inf'``, and the Phase-17.20
    size-sanity gate that consumes ``min_size_pct`` (``ratio < min_pct``) then
    silently misbehaves: ``ratio < NaN`` is always False, so a NaN
    ``min_size_pct`` would ACCEPT a wildly-undersized download (error page /
    login wall) as done (F-RUN02-01). Rejecting non-finite here restores the
    intended gate on a hand-edited / overlaid / corrupt config value.
    """
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(v):
        return float(default)
    return v


def _closeable_response_context(response):
    """Turn a closeable HTTP response into a context manager.

    curl_cffi returns a ``Response`` from the module-level
    ``request(..., stream=True)`` call, but that object only exposes
    ``close()``; it does not implement ``__enter__``/``__exit__``.
    ``contextlib.closing`` gives it the lifecycle expected by the shared
    streaming loop without changing the httpx branch.
    """
    return contextlib.closing(response)


# RFC 9110 14.4: a 416 answer carries the UNSATISFIED-RANGE form of
# Content-Range -- ``bytes */N`` -- where N is the resource's complete length.
# Nothing else on a 416 tells us how long the resource is, so nothing else is
# accepted here: the satisfied-range form (``bytes 0-5/10``), an unknown length
# (``bytes */*``), a missing header, or anything unparseable all return None,
# and None means UNKNOWN at the call site, never "assume it fits".
_CONTENT_RANGE_UNSATISFIED_RE = re.compile(
    r"^\s*bytes\s*\*\s*/\s*(\d+)\s*$", re.IGNORECASE)


def _content_range_complete_length(value):
    """Complete length N from a 416's ``Content-Range: bytes */N``, else None.

    Returns an int only when the header is present AND is the unsatisfied-range
    form AND the length is a non-negative integer. Every other input is
    unmeasurable, which is a refusal, not a default.
    """
    if not value:
        return None
    m = _CONTENT_RANGE_UNSATISFIED_RE.match(str(value))
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if n >= 0 else None


_DAILY_ACCUMULATOR_REGISTRY_BOOTSTRAP_LOCK = threading.Lock()


class _ParallelDailyByteAccounting:
    """Shared accumulator plus exact worker-lifecycle ownership."""

    def __init__(self, runner, workers):
        self.runner = runner
        self.accumulator = runner._start_daily_byte_accumulator()
        self.remaining = workers
        self.lock = threading.Lock()

    def add(self, n_bytes):
        if self.accumulator is not None:
            self.accumulator.add(n_bytes)

    def flush(self):
        if self.accumulator is not None:
            self.accumulator.flush()

    def worker_finished(self, count=1):
        with self.lock:
            self.remaining -= count
            final_worker = self.remaining == 0
        if final_worker:
            self.runner._finish_daily_byte_accumulator(self.accumulator)


class TransportMixin:
    def _register_daily_byte_accumulator(self, accumulator):
        """Expose an active transfer's pending accounting to pause/stop."""
        if accumulator is None:
            return
        registry_lock = getattr(
            self, "_daily_byte_accumulators_lock", None
        )
        if registry_lock is None:
            # SiteRunner initializes these fields. Keep the mixin usable by
            # lightweight adapters and tests that construct it without
            # SiteRunner.__init__.
            with _DAILY_ACCUMULATOR_REGISTRY_BOOTSTRAP_LOCK:
                registry_lock = getattr(
                    self, "_daily_byte_accumulators_lock", None
                )
                if registry_lock is None:
                    # Publish the lock last so another thread can never see a
                    # registry lock before its corresponding set exists.
                    self._daily_byte_accumulators = set()
                    registry_lock = threading.Lock()
                    self._daily_byte_accumulators_lock = registry_lock
        with registry_lock:
            self._daily_byte_accumulators.add(accumulator)

    def _unregister_daily_byte_accumulator(self, accumulator):
        if accumulator is None:
            return
        registry_lock = getattr(
            self, "_daily_byte_accumulators_lock", None
        )
        if registry_lock is None:
            return
        with registry_lock:
            self._daily_byte_accumulators.discard(accumulator)

    def _flush_daily_byte_accumulators(self):
        """Synchronously persist pending bytes for every active transfer."""
        registry_lock = getattr(
            self, "_daily_byte_accumulators_lock", None
        )
        if registry_lock is None:
            return
        with registry_lock:
            accumulators = tuple(self._daily_byte_accumulators)
        for accumulator in accumulators:
            try:
                accumulator.flush()
            except Exception:
                # Pause/stop must retain their historical fail-silent behavior;
                # DailyByteAccumulator itself keeps a failed delta pending.
                pass

    def _start_daily_byte_accumulator(self):
        try:
            from . import daily_budget
            accumulator = daily_budget.DailyByteAccumulator(self.site_id)
        except Exception:
            accumulator = None
        self._register_daily_byte_accumulator(accumulator)
        return accumulator

    def _finish_daily_byte_accumulator(self, accumulator):
        try:
            if accumulator is not None:
                accumulator.flush()
        except Exception:
            pass
        finally:
            self._unregister_daily_byte_accumulator(accumulator)

    def _transfer_gate_open(self, accumulator, local_stop=None):
        """Wait through pause and flush either side of an interrupt race."""
        stopped = self._stop.is_set() or (
            local_stop is not None and local_stop.is_set()
        )
        if stopped:
            return False
        if not self._pause.is_set() and accumulator is not None:
            accumulator.flush()
        while not self._pause.wait(timeout=1.0):
            if accumulator is not None:
                accumulator.flush()
            if self._stop.is_set() or (
                local_stop is not None and local_stop.is_set()
            ):
                return False
        return not self._stop.is_set() and not (
            local_stop is not None and local_stop.is_set()
        )

    def _flush_after_interrupted_write(self, accumulator, local_stop=None):
        stopped = self._stop.is_set() or (
            local_stop is not None and local_stop.is_set()
        )
        if (stopped or not self._pause.is_set()) and accumulator is not None:
            accumulator.flush()
        return not stopped

    def _download_proxy_url(self):
        """Effective proxy URL for this site's in-process payload downloads.

        v3.66.390 (Track-K): mirrors the browser's VPN-aware proxy selection
        (see the launch path's ``playwright_proxy_for_site`` use) for the
        curl_cffi/httpx/multi_conn download clients. An explicit per-site
        ``proxy`` wins; otherwise the site's VPN tunnel SOCKS url is used.

        Raises ``vpn_runtime.VPNRequiredError`` when the site is ``vpn_required``
        and its tunnel is down/killed -- callers must let it propagate (fail
        closed) and never build an unproxied client. Returns ``None`` when no
        tunnel is configured / the site is not required (degrade open), or when
        the VPN runtime is unavailable.
        """
        resolver = (vpn_runtime.get_socks_url_for_site
                    if _VPN_RUNTIME_AVAILABLE else None)
        # v3.66.685 (F4): an explicit per-site ``proxy`` still wins. When none
        # is set and a rotating ``proxy_pool`` is configured, pick a currently
        # healthy member and let it act as the explicit proxy (so it wins over
        # the tunnel exactly as a static proxy does). The VPN fail-closed
        # posture below is untouched. Egress-resilience only -- opt-in; with no
        # pool configured the behavior is byte-identical to before.
        explicit = (self.config.get("proxy") or "").strip()
        if not explicit:
            pool = self.config.get("proxy_pool")
            if isinstance(pool, list) and pool:
                st = getattr(self, "_proxy_pool_state", None)
                if not isinstance(st, dict):
                    st = {}
                    self._proxy_pool_state = st
                picked = proxy_pool.select_proxy(pool, st)
                if picked:
                    explicit = picked
        try:
            return effective_download_proxy(
                explicit or None, self.site_id, resolver)
        except Exception as e:
            if _VPN_RUNTIME_AVAILABLE and isinstance(e, vpn_runtime.VPNRequiredError):
                raise  # fail closed -- never fall back to clear-net egress
            sys.stderr.write(
                f"  vpn download-proxy resolution raised (continuing unproxied): {e}\n")
            return explicit or None
    # ── Row 439: the one gated seam for every segmented transfer ────────
    #
    # Six arms in this codebase move payload bytes with ffmpeg over an HLS/DASH
    # manifest -- jsonapi, vixen, aylo, plugin and library in runner_extractors,
    # plus the scrape-and-click arm in _do_download below. Every one of them
    # called hls_downloader.download() directly, so none passed the fail-closed
    # proxy resolution its siblings do, and ffmpeg fetched every segment on
    # whatever interface the host had. THIS is that gate, and it is the only
    # sanctioned way into hls_downloader.download from application code -- a
    # tree gate asserts the direct-call count outside it stays zero, so a
    # seventh arm cannot quietly reopen the hole.

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        """Resolve egress fail-closed, then run the segmented transfer.

        Returns whatever ``_hls.download`` returns, or a DownloadResult-shaped
        refusal built by ``_hls`` itself when this gate declines. The caller
        handles a non-ok result exactly as it already handles a failed
        transfer -- ``.ok``, ``.error`` and ``.error_detail`` are the contract.

        REFUSES (no subprocess is ever built) when:

          * ``_download_proxy_url()`` raises ``VPNRequiredError`` -- a
            ``vpn_required`` site whose tunnel is down or kill-switched;
          * resolution fails for ANY other reason on a ``vpn_required`` site.
            The shared resolver's own ``except`` continues unproxied there,
            which is the fail-open shape this row exists to close; a control
            that cannot evaluate its condition refuses (CLAUDE.md A2);
          * the site is ``vpn_required`` but resolution yielded no proxy at
            all -- ``get_socks_url_for_site`` returns None rather than raising
            when no tunnel is mapped to the site, and an unproxied transfer for
            a required site is precisely what must not happen;
          * the resolved proxy is one ffmpeg cannot carry (``socks5://``) --
            refused inside ``_hls.download`` with its own distinct code.

        PROCEEDS, with a scrubbed env and zero proxy arguments, when no proxy
        is in effect and the site is not ``vpn_required``. That is the
        operator's declared degrade-open posture and must keep working;
        refusing it would be the mirror defect.
        """
        required = False
        if _VPN_RUNTIME_AVAILABLE:
            try:
                required = bool(vpn_runtime.is_vpn_required_for_site(self.site_id))
            except Exception as e:
                # Cannot even ask whether the site is required. UNKNOWN.
                return _hls.DownloadResult(
                    ok=False, error="vpn_state_unknown",
                    error_detail=(
                        f"could not determine whether {self.site_id!r} requires "
                        f"a VPN, so the segmented transfer is refused rather "
                        f"than run outside the control: {type(e).__name__}: {e}"))
        try:
            proxy_url = self._download_proxy_url()
        except Exception as e:
            if _VPN_RUNTIME_AVAILABLE and isinstance(e, vpn_runtime.VPNRequiredError):
                return _hls.DownloadResult(
                    ok=False, error="vpn_required",
                    error_detail=(
                        f"VPN required for {self.site_id}, tunnel unavailable "
                        f"-- failing closed, no ffmpeg spawned: {e}"))
            # Refuse rather than raise, for BOTH postures. Four of the six arms
            # do not wrap this call, and hls_downloader.download's documented
            # contract is "never raises" -- propagating here would turn an
            # unresolved proxy into a worker error that skips the arm's own
            # needs_review handling. A refusal keeps the transfer unattempted
            # AND keeps the arm's reporting path intact. The vpn_required case
            # is named separately because the two are not the same finding.
            if required:
                return _hls.DownloadResult(
                    ok=False, error="vpn_proxy_unresolved",
                    error_detail=(
                        f"{self.site_id!r} is vpn_required and its egress proxy "
                        f"could not be resolved, so the segmented transfer is "
                        f"refused: {type(e).__name__}: {e}"))
            return _hls.DownloadResult(
                ok=False, error="proxy_unresolved",
                error_detail=(
                    f"egress proxy resolution failed for {self.site_id!r}, so "
                    f"the segmented transfer is refused rather than run outside "
                    f"the control: {type(e).__name__}: {e}"))
        if required and not (proxy_url or "").strip():
            return _hls.DownloadResult(
                ok=False, error="vpn_proxy_missing",
                error_detail=(
                    f"{self.site_id!r} is vpn_required but resolution produced "
                    f"no egress proxy (no tunnel mapped to the site?) -- "
                    f"refusing to fetch segments on the clear interface."))
        return _hls.download(manifest_url, output_path,
                             proxy_url=proxy_url or None, **kwargs)

    def _do_direct_http_download(
        self, page_url: str, file_url: str, output_path: str, referer: str = "",
    ) -> bool:
        """Simple httpx GET → file. Used by library extractor for non-HLS
        direct URLs. Streams in 1MB chunks; reports progress via _update_job.

        Returns True on success. Caller handles state on False.

        v3.43.74: when `use_multi_conn` is set on the site config AND
        the server supports HTTP Range AND the file is larger than
        `multi_conn_min_size_mb`, switches to parallel byte-range
        downloading via the multi_conn module. Falls through to the
        original single-connection path on any failure.
        """
        try:
            import httpx
        except ImportError:
            sys.stderr.write("  direct_http: httpx not installed\n")
            return False
        ua = self.config.get("user_agent", "") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        headers = {"User-Agent": ua}
        if referer:
            headers["Referer"] = referer

        # v3.66.390 (Track-K): resolve the fail-closed download proxy up front.
        # A vpn_required site whose tunnel is down/killed raises VPNRequiredError
        # -> fail closed here (return False, no client built) rather than stream
        # the payload on the clear interface. Explicit proxy or tunnel SOCKS url
        # otherwise; None == degrade open (site not required / no VPN).
        try:
            proxy_url = self._download_proxy_url()
        except Exception as e:
            if _VPN_RUNTIME_AVAILABLE and isinstance(e, vpn_runtime.VPNRequiredError):
                sys.stderr.write(
                    f"  direct_http: VPN required for {self.site_id}, tunnel "
                    f"unavailable -- failing closed: {e}\n")
                return False
            raise

        # v3.43.74: multi-conn try-first path. v3.66.392 (VPN-MULTICONN): the
        # parallel-range client now has a proxy-native path (multi_conn threads
        # the proxy into every per-worker httpx.Client), so it is no longer
        # disabled under a tunnel -- proxy_url is passed through and honored. A
        # vpn_required site with a down tunnel already failed closed above (the
        # _download_proxy_url raise), so proxy_url here is either None (degrade
        # open) or a usable proxy; if multi_conn cannot honor it, the probe
        # declines and the single-connection stream below carries the same proxy.
        if (self.config.get("use_multi_conn", False)
                and _MULTI_CONN_AVAILABLE and _mconn is not None):
            try:
                if self._try_multi_conn_download(
                    page_url, file_url, output_path,
                    headers=headers, proxy_url=proxy_url,
                ):
                    return True
                # fall through to single-conn
                sys.stderr.write(
                    "  multi_conn: not viable / failed; "
                    "falling back to single-connection\n"
                )
            except Exception as e:
                sys.stderr.write(
                    f"  multi_conn: raised {type(e).__name__}: {e}; "
                    "falling back\n"
                )

        # Use a generous timeout per chunk (CDNs can throttle)
        timeout = httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)
        try:
            with httpx.stream(
                "GET", file_url, headers=headers, timeout=timeout,
                follow_redirects=True, proxy=proxy_url,
            ) as r:
                if r.status_code != 200:
                    sys.stderr.write(
                        f"  direct_http: HTTP {r.status_code} from {file_url[:80]}\n"
                    )
                    return False
                total = int(r.headers.get("content-length", 0) or 0)
                got = 0
                last_emit = time.time()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_bytes(1024 * 1024):
                        if self._stop.is_set():
                            return False
                        if chunk:
                            # v3.43.76: bandwidth supervisor.
                            # Blocks until tokens are available when
                            # supervisor is enabled+configured.
                            # Fail-open: any error inside supervisor
                            # is swallowed below.
                            if (_SUPERVISOR_AVAILABLE
                                    and _supervisor is not None
                                    and _supervisor.is_enabled()):
                                try:
                                    _supervisor.acquire(
                                        self.site_id, len(chunk))
                                except Exception:
                                    pass
                            f.write(chunk)
                            got += len(chunk)
                        # Emit progress at most once per second
                        if time.time() - last_emit >= 1.0:
                            last_emit = time.time()
                            if total:
                                pct = int(100.0 * got / total)
                                self._update_job(
                                    page_url, "running",
                                    f"Direct {pct}% • {fmt_bytes(got)}",
                                    file_size=got,
                                )
                            else:
                                self._update_job(
                                    page_url, "running",
                                    f"Direct • {fmt_bytes(got)}",
                                    file_size=got,
                                )
            return True
        except httpx.RequestError as e:
            sys.stderr.write(f"  direct_http: request error {e}\n")
            return False
        except Exception as e:
            sys.stderr.write(f"  direct_http: {type(e).__name__}: {e}\n")
            return False
    def _try_multi_conn_download(
        self, page_url: str, file_url: str, output_path: str,
        *, headers: dict, proxy_url=None,
    ) -> bool:
        """v3.43.74: probe the URL and, if viable, run a parallel
        multi-connection download. Returns True only on full success.

        Probe step:
          - HEAD the URL (or 1-byte Range GET on 405)
          - Verify Content-Length > min_size AND Accept-Ranges: bytes
          - If not viable, return False so the caller falls through to
            standard single-conn streaming

        Download step:
          - Use config-specified chunk count (clamped 1-16)
          - Progress reported via _update_job at most once per second
          - On any chunk failure, return False; caller falls back

        Fail-open: never raises.
        """
        if not (_MULTI_CONN_AVAILABLE and _mconn is not None):
            return False
        min_mb = int(self.config.get("multi_conn_min_size_mb", 100) or 100)
        chunk_count = int(self.config.get("multi_conn_count", 4) or 4)
        chunk_count = max(2, min(16, chunk_count))
        timeout_s = float(self.config.get("multi_conn_timeout_s", 30.0) or 30.0)

        # Probe
        try:
            pr = _mconn.probe(file_url, headers=headers, timeout_s=15.0,
                              proxy=proxy_url)
        except Exception as e:
            sys.stderr.write(f"  multi_conn: probe raised {e}\n")
            return False
        if not pr.ok:
            sys.stderr.write(
                f"  multi_conn: probe declined ({pr.error}); "
                "falling back\n"
            )
            return False
        if not _mconn.should_use_multi_conn(
            pr.content_length, pr.accept_ranges,
            min_size_bytes=min_mb * 1024 * 1024,
        ):
            sys.stderr.write(
                f"  multi_conn: file too small ({pr.content_length} "
                f"< {min_mb}MB) or ranges not supported; falling back\n"
            )
            return False

        # EXT-3: derive this run's connection count from the host's last observed
        # outcome (opt-in via multi_conn_adaptive; default-off -> fixed config N,
        # byte-identical). AIMD-lite: a clean prior run probes one more conn, a
        # failed one backs off. Fail-open on any error.
        if self.config.get("multi_conn_adaptive"):
            try:
                from urllib.parse import urlsplit as _urlsplit
                from .db import host_throughput_get as _htg
                _hist = _htg(_urlsplit(file_url).netloc)
                if _hist:
                    chunk_count = _mconn.adaptive_chunk_count(
                        _hist.get("chunk_count") or chunk_count,
                        chunks_failed=_hist.get("chunks_failed", 0),
                        accept_ranges=pr.accept_ranges)
            except Exception:
                pass

        self.log_event(
            "multi_conn_start",
            f"{chunk_count}-way × {fmt_bytes(pr.content_length)}",
            url=page_url,
        )

        # Progress callback bridges multi_conn's (got, total) interface
        # back into _update_job. multi_conn invokes this from its mc-* child
        # threads, which do not inherit the outer worker's thread-local run
        # generation. Capture it here so a delayed child callback cannot be
        # mistaken for a control-plane write after stop/restart.
        generation_getter = getattr(self, "_worker_write_generation", None)
        run_generation = (
            generation_getter() if callable(generation_getter) else None)
        last_emit = [time.time()]

        def _on_progress(got: int, total: int):
            now = time.time()
            if now - last_emit[0] < 1.0:
                return
            last_emit[0] = now
            pct = int(100.0 * got / total) if total else 0
            accepted = self._update_job(
                page_url, "running",
                f"Multi-conn {chunk_count}× {pct}% • "
                f"{fmt_bytes(got)} / {fmt_bytes(total)}",
                file_size=got,
                _run_generation=run_generation,
            )
            if accepted is False:
                return
            # v3.48 (#24): also push the progress over SSE so the UI's
            # progress bar updates without polling. Throttled per-URL
            # at the broker level — 1/sec/URL — which matches the 1.0s
            # gate above but is enforced even if multiple progress
            # paths fire for the same URL.
            try:
                from . import sse_broker as _sse
                _sse.publish("download_progress", {
                    "site_id": self.site_id,
                    "url": page_url,
                    "got": got,
                    "total": total,
                    "pct": pct,
                    "chunks": chunk_count,
                }, throttle_key=f"{self.site_id}:{page_url}",
                   throttle_s=1.0)
            except Exception:
                pass  # SSE is best-effort

        def _cancel_check() -> bool:
            return self._stop.is_set()

        # v3.43.76: bandwidth supervisor hook. When the supervisor is
        # configured + enabled, each chunk write blocks here until
        # tokens are available. Fail-open: any exception inside the
        # supervisor is swallowed by multi_conn's bytes_callback
        # try/except.
        def _bytes_cb(delta: int) -> None:
            if (_SUPERVISOR_AVAILABLE and _supervisor is not None
                    and _supervisor.is_enabled()):
                _supervisor.acquire(self.site_id, delta)

        try:
            result = _mconn.download(
                file_url, output_path,
                content_length=pr.content_length,
                chunk_count=chunk_count,
                headers=headers,
                progress_cb=_on_progress,
                bytes_callback=_bytes_cb,
                timeout_s=timeout_s,
                cancel_check=_cancel_check,
                proxy=proxy_url,
            )
        except Exception as e:
            sys.stderr.write(
                f"  multi_conn: download raised {type(e).__name__}: {e}\n"
            )
            return False

        if not result.ok:
            self.log_event(
                "multi_conn_failed",
                f"{result.chunks_failed}/{result.chunk_count} chunks "
                f"failed: {result.error[:120]}",
                url=page_url,
            )
            # The sparse file might have been partially written — try
            # to remove it so the fallback single-conn starts clean.
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception:
                pass
            return False

        speed_mbps = (result.avg_speed_bps / (1024 * 1024)) if result.avg_speed_bps else 0.0
        self.log_event(
            "multi_conn_done",
            f"{fmt_bytes(result.bytes_written)} in "
            f"{result.elapsed_s:.1f}s ({speed_mbps:.1f} MB/s) "
            f"across {result.chunk_count} chunks",
            url=page_url,
        )
        # EXT-3: record this host's outcome so the next run adapts N (opt-in).
        if self.config.get("multi_conn_adaptive"):
            try:
                from urllib.parse import urlsplit as _urlsplit
                from .db import host_throughput_record as _htr
                _htr(_urlsplit(file_url).netloc,
                     chunk_count=result.chunk_count,
                     avg_speed_bps=result.avg_speed_bps,
                     chunks_failed=result.chunks_failed)
            except Exception:
                pass
        return True
    @staticmethod
    def _looks_like_media(ctype, head):
        """BP-VH1: True if the response is plausibly downloadable MEDIA, by
        content-type fast-path or magic bytes. A 2xx that is NOT media (an HTML
        interstitial / login-wall / JSON error with a body) must not be reported
        ``done`` — the caller routes it to ``needs_review`` instead."""
        ct = (ctype or "").split(";")[0].strip().lower()
        if ct.startswith(("video/", "audio/")):
            return True
        if ct in ("application/vnd.apple.mpegurl", "application/x-mpegurl",
                  "application/dash+xml", "application/mp4"):
            return True
        if ct in ("text/html", "text/plain", "application/json",
                  "application/xhtml+xml", "application/xml"):
            return False
        h = head or b""
        if h[:7] == b"#EXTM3U":                                   # HLS playlist
            return True
        if len(h) >= 8 and h[4:8] in (b"ftyp", b"styp", b"moov",
                                      b"moof", b"mdat"):           # ISO-BMFF mp4/mov
            return True
        if h[:4] == b"\x1a\x45\xdf\xa3":                           # EBML webm/mkv
            return True
        if h[:3] == b"FLV":
            return True
        if h[:4] == b"OggS":
            return True
        if h[:3] == b"ID3" or (len(h) >= 2 and h[0] == 0xFF
                               and (h[1] & 0xE0) == 0xE0):         # mp3
            return True
        if h[:4] == b"RIFF" and len(h) >= 12 and h[8:12] in (b"WAVE", b"AVI "):
            return True
        return False
    @staticmethod
    def _is_streaming_manifest(ctype, head):
        """Is this response a STREAM INDEX rather than a saveable file?

        v3.66.819. Delegates to hls_downloader, which owns _HLS_EXTS,
        _DASH_EXTS and the content-type tables. Deliberately NOT a local
        `endswith('.m3u8')`: that is a second copy of a denominator, and
        CLAUDE.md section 5 records what three copies of the system package list
        cost -- the copy nobody updated was the one the box ran.

        Falls back to the magic bytes if the import is unavailable, because this
        is on the download path and must never raise.
        """
        h = head or b""
        try:
            from . import hls_downloader as _hls
            if _hls.is_hls_content_type(ctype or "") or \
                    _hls.is_dash_content_type(ctype or ""):
                return True
        except Exception:
            pass
        return h[:7] == b"#EXTM3U"

    @staticmethod
    @staticmethod
    def _direct_media_route(href, page_url):
        """(media_url, destination_name) if `href` IS the file, else (None, None).

        v3.66.x row 384 -- THE SECOND ROUTING DECISION, AS A PURE FUNCTION.

        The direct-URL fast path above is gated on `_via_learned and url_attr`,
        and `_via_learned` is set at exactly ONE place (detect.py, the learned
        branch). So a WIDE-SWEEP winner can never reach it, however perfect its
        href, and falls through to expect_download(timeout=60000). Measured on
        test6 at v3.66.1342 with both ranking fixes deployed: BD chose
        A[href=https://content2a.nubilefilms.com/.../..._3840.mp4?st=..&e=..&dl=..],
        score 2160, size 5,368,709,120 -- the right link -- clicked it, the
        browser navigated a signed cross-host .mp4, and the job recorded
        "no dl event; scored ok but no download fired" a full minute later.

        MANIFESTS ARE NOT OURS. _stream_route is consulted first by the caller,
        and this function ALSO refuses .m3u8/.mpd outright, so the ordering
        cannot silently invert and hand ffmpeg's work to httpx.

        RESOLVE THE RELATIVE HREF -- Phase 19.fix's lesson and _stream_route's:
        a browser resolves it natively on click, httpx gets a string and cannot.

        PREFER THE SITE'S OWN NAME. `dl=` (and `filename=`) is what the site
        intends the file to be called, and it is what skip_if_exists compares
        on the next run; the path basename is the fallback.
        """
        if not href or not isinstance(href, str):
            return None, None
        raw = href.strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "data:")):
            return None, None
        try:
            from urllib.parse import urljoin, urlparse, parse_qs, unquote
            absolute = raw if raw.startswith(("http://", "https://")) \
                else urljoin(page_url or "", raw)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return None, None
            path = (parsed.path or "").lower()
            # Refuse a manifest even though the caller asks _stream_route first.
            # ASK THE OWNER. hls_downloader.is_streaming_url holds the streaming
            # table; a local ".m3u8"/".mpd" tuple here would be a SECOND COPY of
            # it, which tests/test_a_manifest_is_not_a_finished_download.py
            # refuses by name -- and it caught this function doing exactly that.
            # A soft import, like the PWTimeout branch below: if hls_downloader
            # cannot be reached we cannot rule a manifest out, so we decline the
            # direct fetch and fall back to the CLICK path, which is today's
            # behaviour. Never guess our way into handing ffmpeg's work to httpx.
            try:
                from . import hls_downloader as _hls
            except Exception:
                return None, None
            if _hls.is_streaming_url(absolute):
                return None, None
            if not path.endswith((".mp4", ".m4v", ".mkv", ".mov", ".webm",
                                  ".avi", ".wmv")):
                return None, None
            qs = parse_qs(parsed.query or "")
            name = ""
            for key in ("dl", "filename", "file"):
                if qs.get(key) and qs[key][0].strip():
                    name = unquote(qs[key][0].strip())
                    break
            if not name:
                name = unquote(Path(parsed.path).name or "")
            if not name:
                return None, None
            return absolute, name
        except Exception:
            # An unparseable href is not a direct fetch. Fail to the CLICK
            # path, which is what happens today -- never to a wrong URL.
            return None, None

    def _stream_route(href, page_url):
        """(manifest_url, destination_name) if `href` is a stream, else (None, None).

        v3.66.819 -- THE ROUTING DECISION, AS A PURE FUNCTION.

        BD scrapes the right link and then cannot use it. Measured on the deploy
        host six times over four days, and reproduced locally against the
        fixture: clicking `<a href='/hls/scene/2.m3u8'>` NAVIGATES to the
        manifest and fires no download event, so `expect_download(timeout=60000)`
        waits a full minute per URL to record that nothing happened. The link was
        never the problem -- BD scored it correctly as the 1080p HLS download --
        the problem is that a browser does not download a manifest.

        Two things this has to get right, both learned elsewhere in this file:

        RESOLVE THE RELATIVE HREF. The measured value is `/hls/scene/2.m3u8`. A
        browser resolves that natively on click; ffmpeg receives a string and
        cannot. Phase 19.fix records the same trap on the direct-URL path
        ("Request URL is missing scheme", and worse, hitting a wrong host).

        NAME THE DESTINATION .mp4. ffmpeg remuxes the segments into an MP4
        container, so a `.m3u8` destination would be a lie about the content and
        would also defeat skip_if_exists on the next run. runner_extractors.py
        makes the same choice for the extractor HLS paths.

        Pure by design: _do_download is ~500 lines of browser-coupled code whose
        transfer point sits well below its detection point, so a decision buried
        in there could only be checked by asserting over source. This takes two
        strings and returns two.
        """
        if not href or not isinstance(href, str):
            return None, None
        try:
            from . import hls_downloader as _hls
            if not _hls.is_streaming_url(href):
                return None, None
        except Exception:
            return None, None      # never break the download path
        url = href
        if not url.startswith(("http://", "https://")):
            try:
                from urllib.parse import urljoin
                url = urljoin(page_url or "", url)
            except Exception:
                return None, None  # unresolvable is not routable
        if not url.startswith(("http://", "https://")):
            return None, None
        try:
            from urllib.parse import urlparse
            stem = Path(urlparse(url).path).stem or "stream"
        except Exception:
            stem = "stream"
        return url, f"{stem}.mp4"

    @staticmethod
    def _probe_outcome(status, recv, ctype, head):
        """BP-VH1: map a probe result to one of done | streaming | non_media | fail.

        v3.66.819 -- `streaming` is new, and it replaces a `done` that was a lie.
        Measured against the fixture: a 204-byte HLS manifest
        (application/vnd.apple.mpegurl, body starting `#EXTM3U`) returned `done`,
        and since this cut's sibling change bytes_fetched carried the same 204 --
        so the history row read as a real transfer of a real file. A manifest is
        an INDEX of segments; nothing in it is video.

        `non_media` would have been its own falsehood. _looks_like_media answers
        "is this plausibly media", and a manifest IS media -- it is precisely what
        you hand to ffmpeg -- so that predicate is right to accept it and keeps
        its meaning unchanged. The verdict that was missing is the true one: this
        is a stream, and it needs the segmented downloader, not a file save.

        Order matters: the status/bytes check stays FIRST, so a 404 error page
        served as mpegurl is a failure rather than a stream awaiting download.
        """
        if not (200 <= status < 300) or recv <= 0:
            return "fail"
        if TransportMixin._is_streaming_manifest(ctype, head):
            return "streaming"
        return "done" if TransportMixin._looks_like_media(ctype, head) else "non_media"
    @staticmethod
    def _integrity_size_ok(downloaded, total):
        """BP-INT (v3.66.284): True if the received byte count satisfies the
        advertised Content-Length. ``total<=0`` means the server gave no length
        (chunked transfer, or no header) — we cannot judge, so fail-open and
        treat as OK; a missing length is not evidence of truncation. A positive
        ``total`` with ``downloaded < total`` is a truncated transfer -> not OK."""
        if total <= 0:
            return True
        return downloaded >= total
    @staticmethod
    def _promote_or_abort(tmp_path, final_path, downloaded, total, meta_path=None,
                          identity=None):
        """BP-INT (v3.66.284): atomically promote the ``.part`` to its final
        name ONLY when the received byte count satisfies the advertised
        Content-Length. On a truncated transfer, remove the ``.part`` (and meta
        sidecar) WITHOUT touching any existing final file, then raise
        ``_DownloadTruncated`` so the caller routes the URL to ``needs_review``
        — a short file must never masquerade as ``done``. Uses ``os.replace``
        for an atomic, same-filesystem swap. Returns the final path on success."""
        tmp_path = Path(tmp_path)
        final_path = Path(final_path)
        if not TransportMixin._integrity_size_ok(downloaded, total):
            for _p in (tmp_path, meta_path):
                if not _p:
                    continue
                try: Path(_p).unlink(missing_ok=True)
                except Exception: pass
            # part-staging-collision: the claim's lifetime is the .part's
            # lifetime. The .part is gone, so the claim goes with it.
            staging_claim.release(tmp_path, identity)
            raise _DownloadTruncated(
                f"truncated: received {downloaded} of {total} bytes "
                f"(Content-Length); not promoting to final")
        os.replace(str(tmp_path), str(final_path))
        if meta_path:
            try: Path(meta_path).unlink(missing_ok=True)
            except Exception: pass
        staging_claim.release(tmp_path, identity)
        return final_path
    def _do_probe_fetch(self,page_url,page,ctx,dl,best,res_lbl,suggested):
        """GCW probe mode (v3.66.274): the trigger has fired and ``dl.url`` is
        the media URL. Stream only the FIRST BYTES (<=256 KB) over HTTP and
        abort — proving the trigger->media->bytes path works without burning the
        whole file's bandwidth, and WITHOUT writing any file or needing a
        download_dir. Records the same ``/api/history`` verdict the GCW-4 gate
        reads: a 2xx with non-zero bytes -> ``done`` + ``file_size`` = bytes
        sampled (>0 = the path works); a non-2xx or 0-byte response ->
        ``needs_review`` + 0 (the gate must not pass)."""
        PROBE_CAP = 256 * 1024  # first bytes only — enough to prove media flows
        if not _HTTPX_AVAILABLE:
            self._update_job(page_url,"needs_review",
                             "Probe needs httpx (unavailable)",
                             filename=suggested,file_size=0)
            db_log(self.site_id,self.config.get("name","?"),page_url,
                   "needs_review",suggested,0,"probe: httpx unavailable")
            return
        file_url = getattr(dl,"url",None) or ""
        # Cancel Playwright's own download — we sample the media URL directly.
        try: dl.cancel()
        except Exception: pass
        try:
            cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        except Exception:
            cookies = {}
        ua = (self.config.get("fingerprint") or {}).get("user_agent") or \
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        headers = {"User-Agent": ua, "Referer": page_url, "Accept": "*/*",
                   "Range": f"bytes=0-{PROBE_CAP - 1}"}
        recv = 0; ctype = ""; total = 0; status = 0; head = b""
        # F-RUN02-02: resolve the fail-closed VPN download proxy first. A
        # vpn_required site whose tunnel is down/killed raises VPNRequiredError
        # -> fail closed (needs_review, no client built) rather than sample the
        # media on the clear interface. Mirrors _do_direct_http_download.
        # None == degrade open (site not required / no VPN configured).
        try:
            proxy_url = self._download_proxy_url()
        except Exception as _pe:
            if _VPN_RUNTIME_AVAILABLE and isinstance(_pe, vpn_runtime.VPNRequiredError):
                self._update_job(page_url,"needs_review",
                                 "Probe blocked: VPN required, tunnel unavailable",
                                 filename=suggested,file_size=0)
                db_log(self.site_id,self.config.get("name","?"),page_url,
                       "needs_review",suggested,0,
                       f"probe: VPN required, tunnel down -- failing closed: {_pe}")
                return
            raise
        try:
            with httpx.stream("GET", file_url, cookies=cookies, headers=headers,
                              follow_redirects=True, proxy=proxy_url,
                              timeout=httpx.Timeout(30.0, connect=15.0, read=60.0)) as resp:
                status = resp.status_code
                ctype = resp.headers.get("Content-Type","")
                try: total = int(resp.headers.get("Content-Length",0) or 0)
                except Exception: total = 0
                if 200 <= status < 300:
                    for chunk in resp.iter_bytes():
                        remaining = PROBE_CAP - recv
                        if remaining <= 0:
                            break
                        chunk = chunk[:remaining]
                        if len(head) < 512:
                            head += chunk[:512 - len(head)]   # BP-VH1: sniff media
                        recv += len(chunk)
                        if recv >= PROBE_CAP:
                            break
                # Leaving the `with` aborts the rest of the stream — no full
                # download, no file written.
        except Exception as e:
            self._update_job(page_url,"needs_review",
                             f"Probe error: {str(e)[:120]}",
                             filename=suggested,file_size=0)
            db_log(self.site_id,self.config.get("name","?"),page_url,
                   "needs_review",suggested,0,f"probe error: {str(e)[:160]}")
            return
        outcome = TransportMixin._probe_outcome(status, recv, ctype, head)
        if outcome == "done":
            note = (f"probe ok: first {fmt_bytes(recv)} of "
                    f"{fmt_bytes(total) or '?'}; {ctype or 'no content-type'} "
                    f"(aborted — no file saved)")
            self._update_job(page_url,"done",note,
                             filename=suggested,file_size=recv)
            # GCW probe: `recv` bytes really did cross the wire before the
            # transfer was aborted and the file discarded. Truthful as a
            # transfer count -- a consumer wanting "a file was produced" must
            # also require one, since this path saves nothing.
            db_log(self.site_id,self.config.get("name","?"),page_url,
                   "done",suggested,recv,note,bytes_fetched=recv,
                   **history_title_kwargs(self, page_url))
        elif outcome == "streaming":
            # v3.66.819. Without this branch a `streaming` outcome falls to the
            # else below and is reported "probe failed: status=200" -- false, and
            # a worse report than the `done` it replaced, because it accuses the
            # server of failing when the server answered correctly.
            note = (f"probe: HLS/DASH manifest — {ctype or 'no content-type'} "
                    f"({fmt_bytes(recv)} of stream index, not a saveable file). "
                    f"This needs the segmented downloader (ffmpeg via "
                    f"hls_downloader), which BD's generic scrape-and-click path "
                    f"does not reach; recording it done would count a segment "
                    f"index as a finished video.")
            self._update_job(page_url, "needs_review", note,
                             filename=suggested, file_size=0)
            # bytes_fetched deliberately omitted: bytes DID cross the wire, but
            # they were the index, and a consumer asking "did this row download
            # anything" must not be told yes. NULL is the honest UNKNOWN here --
            # see db.py's three-state contract.
            db_log(self.site_id, self.config.get("name", "?"), page_url,
                   "needs_review", suggested, 0, note)
        elif outcome == "non_media":
            note = (f"probe: non-media 2xx — {ctype or 'no content-type'} "
                    f"(first {fmt_bytes(recv)} not recognized as media — needs review)")
            self._update_job(page_url,"needs_review",note,
                             filename=suggested,file_size=0)
            db_log(self.site_id,self.config.get("name","?"),page_url,
                   "needs_review",suggested,0,note)
        else:
            note = f"probe failed: status={status}, {fmt_bytes(recv)} received"
            self._update_job(page_url,"needs_review",note,
                             filename=suggested,file_size=0)
            db_log(self.site_id,self.config.get("name","?"),page_url,
                   "needs_review",suggested,0,note)
    def _do_download(self,page,ctx,page_url,best,dl_dir,res_lbl,probe=False):
        """Click the download button and save the file. Tries the HTTP path
        first (httpx with progress, resume, real %), falls back to Playwright
        save_as if HTTP isn't available or fails partway through.

        Phase 5.7: when the candidate came from a learned row_selector AND
        the site has a learned `url_attribute` (e.g. data-href), read the
        URL straight off the element. Skips Playwright's expect_download
        entirely — saves 5-10 seconds per URL and dodges signed-URL race
        conditions on sites with short-lived URLs."""
        # Normally captured in _process_one before page-specific extractors.
        # Keep this idempotent call at the transport boundary for direct
        # callers and for any future path that enters with an already-open page.
        capture_title = getattr(self, "_capture_website_title", None)
        if callable(capture_title):
            try:
                capture_title(page, page_url)
            except Exception:
                # Title harvesting is metadata enrichment; it must never turn
                # a valid lightweight TransportMixin download into a failure.
                pass
        learned_dl=(self.config.get("learned",{}) or {}).get("download",{}) if isinstance(self.config.get("learned"),dict) else {}
        # AUDIT/v3.42.4: see resolve_url_attribute() for the three accepted
        # shapes (str / list / dict). Lets a single site config cover
        # multiple HTML variants via per-selector url_attribute.
        url_attr = resolve_url_attribute(
            learned_dl.get("url_attribute"),
            learned_dl.get("row_selectors") or [],
            best.get("_learned_sel") or "",
        )
        direct_url=None
        suggested=None

        # ── #3 runtime nav gate ───────────────────────────────────────────
        # Before extracting a direct URL or clicking, classify the winning
        # candidate's URL. A homepage / nav / account / search / unrelated-host
        # link is never a download: refuse it here so it can never reach the
        # filename step / become download.bin. URL-less click-targets are not
        # gated (they fall through to expect_download below).
        _gate_abs, _gate_reject = gate_candidate_url(
            best.get("locator"), getattr(page, "url", "") or page_url or "",
            url_attr=(url_attr if best.get("_via_learned") else None),
            learned_sel=best.get("_learned_sel") or "",
            text=best.get("text", ""))
        if _gate_reject:
            sys.stderr.write(
                f"  download: REJECTED non-download URL [{_gate_abs[:80]}] "
                f"— {_gate_reject}\n")
            ss = self._screenshot(page, page_url)
            self._update_job(
                page_url, "needs_review",
                f"Rejected navigation URL ({_gate_reject}): {_gate_abs[:120]} "
                f"— not a download. Set a Trigger/Download Selector or use a "
                f"reviewed template.", screenshot=ss)
            db_log(self.site_id, self.config.get("name", "?"), page_url,
                   "needs_review", "", 0,
                   f"nav-rejected: {_gate_reject}; url={_gate_abs[:120]}", ss)
            return

        # ── Phase 5.7 fast path: direct URL extraction from attribute ──
        if best.get("_via_learned") and url_attr:
            try:
                direct_url=best["locator"].get_attribute(url_attr)
            except Exception as e:
                sys.stderr.write(f"  download: attr read failed: {e}\n")
            if direct_url:
                # Phase 19.fix: resolve relative URLs (e.g. "/path/file.mp4"
                # or "../foo.mp4") against the current page URL. The browser
                # handles relative hrefs natively when clicked, but our
                # httpx fetcher sees the raw attribute value — a relative
                # path makes httpx fail with "Request URL is missing
                # scheme" or hit a wrong host. Symptom in the user's log:
                # "downloading in the tab and showing http error in the app"
                # — exactly this case.
                if not direct_url.startswith(("http://", "https://")):
                    try:
                        from urllib.parse import urljoin
                        direct_url = urljoin(page.url, direct_url)
                        sys.stderr.write(f"  download: resolved relative url -> {direct_url[:80]}\n")
                    except Exception as e:
                        sys.stderr.write(f"  download: urljoin failed ({e}); using raw value\n")
                # Try to extract a sensible filename from the URL
                # (wowgirls puts ?filename= in the query string; otherwise
                # use the last path component).
                try:
                    import urllib.parse as _up
                    parsed=_up.urlparse(direct_url)
                    qs=_up.parse_qs(parsed.query)
                    if "filename" in qs:
                        suggested=qs["filename"][0]
                    else:
                        suggested=Path(parsed.path).name or "download.bin"
                except Exception:
                    suggested="download.bin"
                sys.stderr.write(f"  download: direct URL extracted from [{url_attr}] -> {suggested}\n")
                _bump_learned_stat(self.config,"direct_extractions")

        # ── v3.66.819: a STREAM is decided before the click, not after 60s ──
        #
        # A browser navigates a manifest rather than downloading it, so
        # expect_download below can never fire for one and pays its full
        # 60000ms first. Measured on the deploy host: six needs_review rows over
        # four days, each a wasted minute of the capture. The href is available
        # right here, so the decision happens here.
        is_stream = False
        if not direct_url:
            try:
                _href = best["locator"].get_attribute("href") or ""
            except Exception:
                _href = ""          # a detached locator is not the subject
            _surl, _sname = TransportMixin._stream_route(_href, page.url)
            if _surl:
                direct_url, suggested, is_stream = _surl, _sname, True
                sys.stderr.write(
                    f"  download: streaming manifest -> segmented downloader "
                    f"({_surl[:90]})\n")
                self._update_job(page_url, "running",
                                 "Streaming manifest — downloading segments "
                                 "with ffmpeg...")
            elif _href:
                # row 384: not a manifest, but the href may BE the file. The
                # fast path above only fires for a learned hit, so without this
                # a wide-sweep winner is clicked and expect_download waits 60s
                # for an event a cross-host signed .mp4 will never produce.
                _durl, _dname = TransportMixin._direct_media_route(_href, page.url)
                if _durl:
                    direct_url, suggested = _durl, _dname
                    sys.stderr.write(
                        f"  download: direct media href -> {_dname} "
                        f"({_durl[:90]})\n")

        # ── Standard path: click and let Playwright capture the download ──
        if not direct_url:
            try:
                with page.expect_download(timeout=60000) as dli: best["locator"].click()
                dl=dli.value
                direct_url=dl.url
                suggested=dl.suggested_filename or "download.bin"
            except PWTimeout:
                # No actual download event fired.
                ss=self._screenshot(page,page_url)
                seen=" | ".join(
                    f"{res_label(c['score'])}({fmt_bytes(c['size']) or '?'}):{c['text'][:30]}"
                    for c in best.get("_all_candidates",[])[:6])
                # v3.66.819 -- SAY WHY, when why is knowable.
                #
                # The deploy host recorded six identical rows across four days:
                #   'no dl event; scored ok but no download fired;
                #    saw: 1080p(?):Download 1080p (HLS) /hls/scen | ...'
                # and measured locally, clicking <a href='/hls/scene/2.m3u8'>
                # NAVIGATES to the manifest and fires no download event at all.
                # The link was CORRECT -- BD scored it as the 1080p HLS download.
                # So "scored ok but no download fired" points the operator at
                # their selectors, and the other hint below literally offers
                # "set Trigger Selector", which cannot help. The cause is the
                # link's TYPE, and it is knowable right here from the href.
                href = ""
                try:
                    href = best["locator"].get_attribute("href") or ""
                except Exception:
                    href = ""      # a detached locator is not the subject
                streaming = False
                if href:
                    try:
                        from . import hls_downloader as _hls
                        streaming = _hls.is_streaming_url(href)
                    except Exception:
                        streaming = False
                if streaming:
                    hint=(f"the link is a streaming manifest ({href[:80]}) — a "
                          f"browser NAVIGATES those rather than downloading "
                          f"them, so no download event can fire. This needs the "
                          f"segmented downloader (ffmpeg via hls_downloader); "
                          f"it is not a selector problem")
                elif best["score"]==0:
                    hint="looks like a modal-trigger button — set Trigger Selector"
                else:
                    hint="scored ok but no download fired"
                self._update_job(page_url,"needs_review",
                                 f"Clicked but no download started — {hint}. Saw: {seen}",
                                 screenshot=ss)
                db_log(self.site_id,self.config.get("name","?"),page_url,"needs_review","",0,
                       f"no dl event; {hint}; saw: {seen}",ss)
                return
        else:
            # Direct-URL path: no Playwright Download object. Make a stub
            # so the rest of the function doesn't have to special-case.
            class _DLStub:
                def __init__(self,url,fn): self.url=url; self.suggested_filename=fn
                def cancel(self): pass
            dl=_DLStub(direct_url,suggested)

        suggested=dl.suggested_filename or "download.bin"
        # GCW probe mode (v3.66.274): the trigger has fired and dl.url is the
        # real media URL. Sample the first bytes and abort instead of computing
        # a final path / downloading the whole file. No dl_dir is dereferenced
        # past this point on the probe path (dl_dir may be None).
        if probe:
            self._do_probe_fetch(page_url,page,ctx,dl,best,res_lbl,suggested)
            return
        ext=Path(suggested).suffix or ".mp4"
        # Compute templated final path
        tpl=(self.config.get("filename_template","") or "{filename}").strip()
        try: title=page.title() or ""
        except Exception: title=""
        now=datetime.now()
        ctx_vars={
            "site":     self.config.get("name","site"),
            "title":    title,
            "filename": Path(suggested).stem,  # without extension; ext re-added below
            "stem":     Path(suggested).stem,
            "ext":      ext,
            "resolution": res_label(best["score"]),
            "date":     now.strftime("%Y-%m-%d"),
            "time":     now.strftime("%H-%M-%S"),
            "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
            # v3.43.64: extractor-only variables are kept in the dict
            # but empty so a template like "{studio}/{performer} - {title}"
            # still works (it renders to "/-Title" which gets cleaned to
            # "Title" by the v3.43.64 fname scrub). Studio falls back to
            # site name so templates that always include {studio} still
            # produce a meaningful directory tree on teach-path sites.
            "performer":   "",
            "artist":      "",
            "studio":      self.config.get("name", ""),
            "year":        now.strftime("%Y"),
            "upload_date": "",
            "duration":    "",
            "quality":     res_label(best["score"]),
            "extractor":   "",
        }
        rendered=resolve_filename_template(tpl,ctx_vars)
        # If the template didn't include {ext}, append it. Also, if it
        # somehow rendered to empty, fall back to the suggested filename.
        if not rendered: rendered=suggested
        elif not Path(rendered).suffix: rendered+=ext
        final_path=dl_dir/rendered
        final_path.parent.mkdir(parents=True,exist_ok=True)

        # ── "Already have" pre-download check ────────────────────────────
        # EXISTENCE IS NOT IDENTITY. This branch used to skip on
        # `final_path.exists()` alone and write a 'done' row -- which, through
        # db_log's done path and library_record's `title = CASE WHEN ?<>''`
        # UPDATE, also RETITLED whatever was already on disk to this page's
        # title. Two scenes that render one filename (a `{site} - {resolution}`
        # template is enough) therefore produced scene B's history row over
        # scene A's bytes, and scene A's library row titled as scene B. Wrong
        # file, right title.
        #
        # db_skip_identity answers the question this branch actually needs, in
        # three states. Only a PROVABLE same-work hit skips; "different" and
        # "unknown" alike fall through to the safe_dest below, which is what
        # exists to resolve same-name-different-content. UNKNOWN is a failing
        # third state, never permission (CLAUDE.md A7).
        # Row 559. THE IDENTITY IS MEASURED FOR EVERY JOB. This call used to sit
        # inside `if skip_if_exists and final_path.exists()`, with _identity
        # defaulting to "" otherwise -- but db_skip_identity's answer is keyed on
        # the URL (final_path only decides its "different" arm), so gating the
        # CALL on the rendered path handed the "unproven" diagnostic below an
        # EMPTY DENOMINATOR for exactly the upgraded-host rows it was written
        # for: turn skip_if_exists off, or change a tier or a date so the
        # template renders a name that is not on disk, and it never fired.
        # Measuring is separated from ACTING on the measurement below.
        _identity,_owned=db_skip_identity(page_url,str(final_path))
        # Row 545. Approve and the capture workflow's "verify live" both stamp
        # force_download on the job to bypass dedup. _dedup_preflight honours it;
        # this arm did not -- it skipped anyway, POPPED the flag, logged another
        # bytes_fetched=0 "already on disk" row and reported "Already have", so a
        # corrupt-but-present file could never be re-fetched from the UI and
        # guided capture graded that no-op as "Media validated". The flag is only
        # READ here; the success path below is what clears it, so a forced
        # attempt that fails stays forced.
        with self._lock:
            _forced=bool(self.jobs.get(page_url,{}).get("force_download"))
        # Exactly the condition that used to gate the call, plus the force flag.
        # Measuring the identity unconditionally must not widen the SKIP to a
        # rendered path that is not on disk, nor past the config gate.
        _may_skip=(bool(self.config.get("skip_if_exists",True))
                   and final_path.exists() and not _forced)
        if _identity=="unproven":
            # Row 479. A done row points at a file that is on disk, but nothing
            # in the record says BD ever fetched it -- a bytes_fetched of 0 or,
            # on a host upgraded across v3.66.1368, a pre-v8 NULL. The job falls
            # through to the transfer path below, and the unproven attribution
            # is made OPERATOR-VISIBLE instead of vanishing into a silent
            # re-download.
            #
            # Row 562. RECORDING a diagnostic must never cost the download it
            # only annotates. This db_log was unguarded, so a sqlite write lock
            # held past the 10s busy_timeout under multi-worker load, or a full
            # disk, turned a job that was about to download into an
            # unclassified "worker error: database is locked" parked 600s with
            # no history row at all. Proceeding is the DESIGNED decision here --
            # the unproven arm never skips, it annotates and falls through -- so
            # the failure is recorded rather than swallowed: A7 says a
            # diagnostic that collapses distinct failures costs the
            # investigation, so this names the step and carries sqlite's own
            # words instead of a generic "db error".
            try:
                db_log(self.site_id,self.config.get("name","?"),page_url,"needs_review",
                       Path(_owned).name,None,
                       f"existing attribution records no transfer, so it is not "
                       f"proof of ownership: {_owned}",
                       bytes_fetched=None,
                       **history_title_kwargs(self, page_url))
            except Exception as _needs_review_exc:
                self.log.warning(
                    "row 479 needs_review write failed for %s (%s: %s); the "
                    "unproven attribution %s is UNRECORDED and the download "
                    "proceeds",
                    page_url, type(_needs_review_exc).__name__,
                    _needs_review_exc, _owned)
        if _identity=="same" and _may_skip:
            # Report the file this url actually owns, not the freshly rendered
            # name. They differ whenever an earlier run landed on a safe_dest
            # suffix, and passing final_path here would hand db_log -- and so
            # library_record's title UPDATE -- the wrong row: the very defect
            # this branch is being corrected for.
            existing_path=Path(_owned)
            try:
                existing_size=existing_path.stat().st_size
            except OSError as _skip_stat_exc:
                # Row 519. The identity proof and this action are separate
                # observations.  A path that vanished after proof is UNKNOWN,
                # not permission to leak an unclassified worker error or to
                # record a skip; fall through to the transfer path below.
                self.log.warning(
                    "skip identity invalidated before action for %s (%s: %s); "
                    "proceeding to transfer",
                    page_url, type(_skip_stat_exc).__name__, _skip_stat_exc)
            else:
                # Row 545: the pop that used to stand here is gone. This arm is
                # now only reached when the flag is UNSET, so popping it could
                # only ever eat a force_download stamped by another thread
                # between the read above and this line. The success path below
                # (~"Clear the force_download flag on success") is the one place
                # that consumes it.
                self._update_job(page_url,"done",
                                 f"Already have: {existing_path.name} ({fmt_bytes(existing_size)})",
                                 filename=existing_path.name,file_size=existing_size)
                db_log(self.site_id,self.config.get("name","?"),page_url,"done",
                       existing_path.name,existing_size,"already on disk",
                       honeypot_score=best.get("_honeypot_score"),  # P5-2b
                       bytes_fetched=0,  # skip_if_exists: dl.cancel(), nothing fetched
                       file_path=str(existing_path),
                       **history_title_kwargs(self, page_url))
                try: dl.cancel()
                except Exception: pass
                return

        # Resolve filename collisions (different content, same name) by suffix.
        #
        # part-staging-collision: this was `safe_dest(final_path)`, which asks
        # "is this final name free RIGHT NOW". Two workers on one site ask that
        # a millisecond apart and both are told yes, because neither has
        # promoted anything yet -- and then both stage into one `.part`, the
        # second reading the first's partial bytes as a resume offset and
        # appending a different scene onto them. Reserving the name takes it,
        # atomically, keyed to this job's identity, so only one worker can be
        # told yes and a restart of THIS job still reclaims its own `.part`.
        try:
            final_path, _staging_path = staging_claim.reserve(
                final_path, staging_claim.job_identity(page_url))
        except staging_claim.StagingUnavailable as e:
            # UNKNOWN is not permission. Refuse rather than stage into a path
            # whose ownership we cannot prove.
            note = f"staging unavailable: {e}"
            self._update_job(page_url, "needs_review", note,
                             filename=final_path.name, file_size=0)
            db_log(self.site_id, self.config.get("name","?"), page_url,
                   "needs_review", final_path.name, 0, note,
                   bytes_fetched=0)
            try: dl.cancel()
            except Exception: pass
            return

        # ── Download path selection ──────────────────────────────────────
        use_http=self.config.get("use_http_dl",True) and _HTTPX_AVAILABLE
        # bytes_fetched initialised alongside downloaded_size so it is bound on
        # every path that reaches the db_log below, including the ones that
        # never call a download helper at all. 0 is the truthful default: no
        # helper ran, so nothing was transferred.
        downloaded_size=0; bytes_fetched=0; filename=final_path.name
        # transfer_mode is bound here for the same reason bytes_fetched is: the
        # db_log at the end of this function is reached by paths that never run
        # a transfer at all. None is the truthful default -- no arm ran, so
        # there is no transport to name, and NULL means unrecorded rather than
        # "not segmented" (db.db_log's docstring states that contract).
        transfer_mode=None
        if is_stream:
            transfer_mode="segmented"
            # THE SEGMENTED TRANSFER. hls_downloader drives ffmpeg over the
            # manifest and never raises; bytes_written is what actually crossed
            # the wire, which is the number #63's bytes_fetched contract wants --
            # not the manifest's ~204 bytes, and not the muxed file's size.
            try:
                from . import hls_downloader as _hls
            except Exception as e:
                note = f"hls_downloader unavailable ({e}); cannot fetch a stream"
                self._update_job(page_url, "needs_review", note,
                                 filename=final_path.name, file_size=0)
                db_log(self.site_id, self.config.get("name", "?"), page_url,
                       "needs_review", final_path.name, 0, note,
                       bytes_fetched=0)
                staging_claim.release(_staging_path, staging_claim.job_identity(page_url))
                return
            res = self._hls_download_guarded(
                _hls, direct_url, str(final_path), referer=page_url,
                cancel_check=lambda: self._stop.is_set())
            if not res.ok:
                # ffmpeg_not_installed is a DISTINCT code and gets a distinct
                # verdict: a missing dependency is not a broken stream, and an
                # operator cannot act on the two the same way. ffmpeg IS present
                # on the deploy host, so this is the honest-unknown branch rather
                # than the expected one.
                if res.error == "ffmpeg_not_installed":
                    note = ("ffmpeg is not installed, so the segmented "
                            "(HLS/DASH) download path cannot run on this host — "
                            "this is a missing dependency, not a failed stream")
                else:
                    note = (f"segmented download failed: {res.error} — "
                            f"{str(res.error_detail)[:160]}")
                try:
                    if final_path.exists():
                        final_path.unlink()
                except Exception:
                    pass
                self._update_job(page_url, "needs_review", note,
                                 filename=final_path.name, file_size=0)
                db_log(self.site_id, self.config.get("name", "?"), page_url,
                       "needs_review", final_path.name, 0, note,
                       bytes_fetched=max(0, int(res.bytes_written or 0)))
                staging_claim.release(_staging_path, staging_claim.job_identity(page_url))
                return
            try:
                downloaded_size = final_path.stat().st_size
            except Exception:
                downloaded_size = int(res.bytes_written or 0)
            bytes_fetched = max(0, int(res.bytes_written or 0))
            # part-staging-collision: the segmented path muxes straight to
            # the reserved final name and never stages a `.part`, so the
            # reservation has done its job and is released here rather than
            # left beside the finished file.
            staging_claim.release(_staging_path, staging_claim.job_identity(page_url))
        # elif, NOT a second `if`. v3.66.819 shipped these as two independent
        # ifs with `use_http = False` in the stream branch, and that did not skip
        # the transfer selection -- it SELECTED THE ELSE. Measured on the deploy
        # host: the route fired, ffmpeg transferred the bytes, and then
        #   worker error: '_DLStub' object has no attribute 'save_as'
        # because the else calls _pw_save(dl, ...) and the stream path's `dl` is
        # the _DLStub stand-in (url + suggested_filename + cancel only). Exactly
        # one of these three paths may run, so they are one chain.
        elif use_http:
            transfer_mode="http"
            try:
                file_url=dl.url
                # Cancel Playwright's own download — we'll fetch directly.
                try: dl.cancel()
                except Exception: pass
                # v3.43.65: speculatively probe higher-tier variants of
                # the file URL. When the site embeds the resolution as
                # a path segment (Vixen's `/mp4_480/`, VIP4K's
                # `/1080p.mp4`, etc.) and `tier_probe_enabled` is True,
                # this swaps in the highest 200-OK variant. Fail-open:
                # the probe returns the original URL unchanged on any
                # failure, so download proceeds either way.
                file_url = self._probe_for_higher_tier(file_url, referer=page_url)
                # Phase 17.16: build mirror URL list. Original first, then
                # alternates synthesized by swapping subdomains. We cap the
                # attempts so a misconfigured list can't loop forever.
                attempt_urls = [file_url] + self._build_mirror_urls(file_url)
                last_err = None
                downloaded_size = 0
                for attempt_url in attempt_urls[:6]:
                    try:
                        if attempt_url != file_url:
                            self._update_job(page_url, "running",
                                f"Trying mirror: {self._extract_host(attempt_url)}")
                            self.log_event("mirror", f"Falling back to {attempt_url[:80]}", url=page_url)
                        downloaded_size, bytes_fetched = self._http_download(
                            page_url, page, ctx, attempt_url, final_path)
                        break  # success
                    except _HTTPDownloadFailed as e:
                        last_err = e
                        # Don't retry on stop signal or rate-limit
                        s = str(e).lower()
                        if "stopped" in s or "rate" in s: raise
                        continue
                if downloaded_size == 0 and last_err is not None:
                    raise last_err
            except _StagingUnavailable as e:
                # part-staging-collision: another live download owns the .part
                # this transfer would have staged into, or ownership could not
                # be measured. Deliberately NOT the Playwright fallback: the
                # browser would write its bytes to the very destination this
                # refusal is protecting. needs_review, so the operator sees it.
                note = f"staging unavailable: {e}"
                self._update_job(page_url, "needs_review", note,
                                 filename=final_path.name, file_size=0)
                db_log(self.site_id, self.config.get("name","?"), page_url,
                       "needs_review", final_path.name, 0, note,
                       bytes_fetched=0)
                return
            except _DownloadTruncated as e:
                # BP-INT (v3.66.284): the transfer ended short of the
                # advertised Content-Length. The .part was already removed and
                # no final file exists, so this is NOT a `done`. Route to
                # needs_review (not failed, not the Playwright fallback) so the
                # operator can force a fresh re-download (BP-VH3).
                self._update_job(page_url, "needs_review", f"Truncated download — {e}",
                                 file_size=0)
                db_log(self.site_id, self.config.get("name","?"), page_url,
                       "needs_review", "", 0, f"integrity: {e}")
                return
            except _HTTPDownloadFailed as e:
                # Fall back to Playwright save_as. We need a fresh download
                # event; click again. Some sites won't let us do this twice
                # in quick succession, so this fallback is best-effort.
                self._update_job(page_url,"running",f"HTTP failed ({e}) — retrying via browser...")
                try:
                    with page.expect_download(timeout=60000) as dli2: best["locator"].click()
                    dl=dli2.value
                except PWTimeout:
                    self._handle_failure(page_url,f"HTTP failed and no fallback download event: {e}")
                    return
                # The httpx attempt failed and the browser moved the bytes, so
                # the row must say 'browser'. Leaving the 'http' set at the top
                # of this arm would record the transport that was ATTEMPTED --
                # a row that names a transfer which did not happen is the same
                # failure as the message prose this column replaces.
                transfer_mode="browser"
                downloaded_size, bytes_fetched = self._pw_save(dl,final_path)
                # part-staging-collision: the browser wrote straight to
                # the reserved final name, so the reservation has done
                # its job and is released.
                staging_claim.release(_staging_path, staging_claim.job_identity(page_url))
        else:
            transfer_mode="browser"
            downloaded_size, bytes_fetched = self._pw_save(dl,final_path)
            staging_claim.release(_staging_path, staging_claim.job_identity(page_url))

        # ── Phase 17.20: Size sanity check ───────────────────────────────
        # If the page advertised a file size and we got back something
        # wildly smaller (under min_size_pct % of expected, default 5%),
        # what we downloaded is almost certainly an error page, login wall,
        # or geo-block redirect — NOT the file. Reject it before the
        # integrity check (which would catch most but not all cases) and
        # before counting it as 'done'.
        expected = int(best.get("size") or 0)
        min_pct = _finite_config_float(self.config.get("min_size_pct", 5.0) or 5.0, 5.0)
        if expected > 1024*1024 and downloaded_size > 0:  # >1MB advertised
            ratio = (downloaded_size / expected) * 100
            if ratio < min_pct:
                # Quarantine and fail
                quarantine = final_path.parent / "_failed"
                quarantine.mkdir(exist_ok=True)
                try: shutil.move(str(final_path), str(quarantine/final_path.name))
                except Exception: pass
                msg = (f"Downloaded {fmt_bytes(downloaded_size)} but expected ~"
                       f"{fmt_bytes(expected)} ({ratio:.1f}% — likely an error page); "
                       f"moved to _failed/")
                self._update_job(page_url, "failed", msg,
                                 filename=filename, file_size=downloaded_size)
                db_log(self.site_id, self.config.get("name","?"), page_url,
                       "failed", filename, downloaded_size,
                       f"size sanity: got {downloaded_size}, expected {expected}")
                return

        # ── Phase 17.17: Hash verification ───────────────────────────────
        # If the page advertised a hash for this file, verify after download.
        # A mismatch means we got the wrong file (CDN serving a different
        # asset, error page that happens to pass size check, etc.). Quarantine
        # and fail; a retry might fetch a fresh copy from a different CDN node.
        expected_algo = best.get("expected_hash_algo")
        expected_hash = best.get("expected_hash_value")
        if (self.config.get("verify_hash", True) and expected_algo and
                expected_hash and final_path.exists() and downloaded_size > 0):
            if not self._verify_hash_or_quarantine(
                    page_url, expected_algo, expected_hash,
                    final_path, filename, downloaded_size):
                return  # handled inside helper (job marked failed, file quarantined)

        # ── ffprobe integrity verification ───────────────────────────────
        verify_msg=""
        if self.config.get("verify_integrity",True) and final_path.exists() and downloaded_size>0:
            ok, retry, reason = self._verify_integrity_or_quarantine(
                page_url, final_path, filename, downloaded_size)
            if retry:
                return  # job re-queued by helper
            if not ok:
                return  # quarantined
            # The checkmark is EARNED, not automatic. An empty reason means
            # ffprobe ran and was satisfied. A non-empty reason on the ok path
            # means the check failed open (e.g. "ffprobe not installed"), so
            # say that instead of claiming a verification that never happened.
            verify_msg=" ✓" if not reason else f" (unverified: {reason})"
        # Clear the force_download flag on success so a future retry
        # doesn't keep bypassing the threshold silently.
        with self._lock:
            if page_url in self.jobs:
                self.jobs[page_url].pop("force_download",None)
                # Phase 72: clear the corruption retry counter on success
                self.jobs[page_url].pop("corruption_retries", None)
        # v3.43.64: embed MP4 metadata for teach-path downloads too.
        # We don't have extractor metadata (no library involved) so we
        # pass only the fields we can derive cheaply: page title, source
        # URL, site name. Resolution comes from the heuristic scorer's
        # res_label which is what `quality` would be on this path.
        # Fail-open — never blocks the "Saved" state transition below.
        try:
            self._embed_metadata_if_mp4(
                str(final_path),
                title=title,
                performer="",  # teach path has no performer info
                site_name=self.config.get("name", ""),
                upload_date="",
                source_url=page_url,
                thumbnail_url="",  # teach path doesn't capture a thumb URL
                quality=res_label(best["score"]) if isinstance(best, dict) else "",
                duration_sec=0,
                extractor_name="",  # not via library extractor
            )
        except Exception as e:
            sys.stderr.write(f"  metadata (teach): {type(e).__name__}: {e}\n")
        file_size_on_disk = self._size_on_disk_after_tagging(
            str(final_path), downloaded_size)
        self._update_job(page_url,"done",f"Saved: {filename}{verify_msg}",
                         filename=filename,file_size=file_size_on_disk)
        db_log(self.site_id,self.config.get("name","?"),page_url,"done",filename,file_size_on_disk,"",
               honeypot_score=best.get("_honeypot_score"),  # P5-2b: stamp resolve-time score for per-site threshold learning
               bytes_fetched=bytes_fetched,
               transfer_mode=transfer_mode,  # which arm of the chain above ran
               file_path=str(final_path),
               **history_title_kwargs(self, page_url))
        # Phase 66 (v3.41.0): cross-site filename duplicate detection.
        # Look back through history for a successful download with the
        # same filename + similar size. If found, log + emit event so
        # the operator can decide whether to keep both. Opt-in via
        # cross_site_dedup config flag (default off).
        if self.config.get("cross_site_dedup", False):
            try:
                from .db import db_find_filename_duplicate
                dup = db_find_filename_duplicate(
                    filename, file_size=downloaded_size,
                    exclude_site=self.site_id,
                )
                if dup:
                    self.log_event("cross_site_dupe",
                        f"Likely duplicate of {dup['site_name']} URL from "
                        f"{dup['ts']}: {dup['filename']}",
                        url=page_url)
            except Exception as e:
                self.log.warning("cross_site_dedup check failed: %s", e)
        # Phase 21.1: track completion for dashboard ETA. Slide window
        # to drop entries older than 5 minutes; recompute per-minute rate
        # using the surviving entries' time span (avoids the spike where
        # the FIRST few completions look like infinite throughput).
        try:
            import time as _tt
            now = _tt.time()
            self._recent_completions.append(now)
            while self._recent_completions and (now - self._recent_completions[0]) > 300:
                self._recent_completions.popleft()
            if len(self._recent_completions) >= 2:
                span = max(1.0, self._recent_completions[-1] - self._recent_completions[0])
                self._recent_per_min = (len(self._recent_completions) - 1) * 60.0 / span
            else:
                self._recent_per_min = 0.0
        except Exception: pass
        # Phase 31: round-robin account rotation on success. Spreads load
        # across configured accounts to avoid hammering any single one.
        # Only kicks in when mode = "round_robin" and there are 2+ accounts;
        # otherwise falls through silently (failover mode rotates only on
        # auth failures, which is handled elsewhere).
        try:
            if self.config.get("accounts_mode") == "round_robin":
                accounts = self.config.get("accounts") or []
                if len(accounts) >= 2:
                    self._rr_completion_count = getattr(self, "_rr_completion_count", 0) + 1
                    every = max(1, int(self.config.get("accounts_rotate_every", 50) or 50))
                    if self._rr_completion_count >= every:
                        self._rr_completion_count = 0
                        # Round-robin to NEXT non-cooled-down account.
                        # Don't apply a 24h cooldown to the current one
                        # (it's healthy — we're just spreading load).
                        cur = getattr(self, "_active_account_idx", 0)
                        now = time.time()
                        for offset in range(1, len(accounts) + 1):
                            cand = (cur + offset) % len(accounts)
                            cd = float(accounts[cand].get("cooldown_until", 0) or 0)
                            if cd <= now:
                                self._active_account_idx = cand
                                self.config["username"] = accounts[cand].get("username","")
                                self.config["password"] = accounts[cand].get("password","")
                                self.config["cookie_file"] = accounts[cand].get("cookie_file","")
                                self.log_event("account_rotate",
                                    f"Round-robin: switched to account {cand+1}/{len(accounts)} "
                                    f"({accounts[cand].get('label','') or accounts[cand].get('username','?')})")
                                cf = self.config.get("cookie_file","")
                                if cf and Path(cf).exists():
                                    try:
                                        from .cookies import load_cookies_from_file
                                        self.set_cookies(load_cookies_from_file(cf))
                                    except Exception: pass
                                else:
                                    self.set_cookies([])
                                break
        except Exception as e:
            self.log.exception("round_robin rotation failed: %s", e)
        # Phase 20: fire integration hooks (post-download command,
        # webhooks, Stash scan, Plex refresh, etc.). Fire-and-forget;
        # hooks run in background threads and never block the worker.
        try:
            from .hooks import fire_event
            fire_event("completed", self.config, job={
                "url": page_url, "filename": filename, "path": str(final_path),
                "file_size": downloaded_size,
                "resolution": (best.get("text", "") or "")[:40],
                "hash": expected_hash or "",
                "message": f"Saved: {filename}{verify_msg}",
            })
        except Exception as e:
            sys.stderr.write(f"  hook: fire_event(completed) failed: {e}\n")
        # v3.43.28: deep Stash enrichment. Runs after the basic scan
        # trigger (above, in fire_event) has had time to start. The
        # enrichment method polls Stash for ~10s waiting for the scene
        # to appear, then pushes BulkDownloader-known metadata (studio,
        # tags, URL) onto the new scene. Best-effort — failures don't
        # propagate. Runs on a background thread so a slow Stash
        # response doesn't block the worker picking up the next URL.
        try:
            from . import stash_deep as _sd
            if _sd.deep_enabled(self.config):
                import threading as _t
                _t.Thread(
                    target=self._stash_enrich_after_scan,
                    args=(page_url, str(final_path)),
                    daemon=True,
                    name=f"stash-enrich-{self.site_id}",
                ).start()
        except Exception as e:
            sys.stderr.write(f"  stash_enrich spawn failed: {e}\n")
        # v3.43.29: deep Plex enrichment — analogous to Stash. Runs
        # after the basic refresh trigger (above, in fire_event).
        # Path-scoped refresh + match confirmation + recently-added
        # boost + collection routing all happen in the background.
        try:
            from . import plex_deep as _pd
            if _pd.deep_enabled(self.config):
                import threading as _t
                _t.Thread(
                    target=self._plex_enrich_after_scan,
                    args=(page_url, str(final_path)),
                    daemon=True,
                    name=f"plex-enrich-{self.site_id}",
                ).start()
        except Exception as e:
            sys.stderr.write(f"  plex_enrich spawn failed: {e}\n")
        # v3.43.37: deep Jellyfin enrichment — mirrors Plex. Per-item
        # refresh + match confirmation + collection routing in
        # background.
        try:
            from . import jellyfin_deep as _jd
            if _jd.deep_enabled(self.config):
                import threading as _t
                _t.Thread(
                    target=self._jellyfin_enrich_after_scan,
                    args=(page_url, str(final_path)),
                    daemon=True,
                    name=f"jellyfin-enrich-{self.site_id}",
                ).start()
        except Exception as e:
            sys.stderr.write(f"  jellyfin_enrich spawn failed: {e}\n")
    def _http_download(self,page_url,page,ctx,file_url,final_path):
        """Stream the file URL to disk via httpx, with progress updates,
        resume support via Range, and pause/stop responsiveness.

        Phase 6.1: if `max_mbps` is set on the site config (>0), pace
        chunks to that rate by sleeping between iter_bytes chunks. The
        scheme: track (bytes_since_window_start, window_start_time); if
        the actual rate exceeds the cap, sleep until it doesn't. Reset
        the window every second so a single slow chunk doesn't lock us
        in below the cap forever.

        Phase 15.4 — TLS fingerprint: when `curl_cffi` is installed AND
        `use_curl_cffi` is True (default), we send the request via
        curl_cffi.requests with `impersonate="chrome124"`. This makes
        the TLS handshake (JA3 hash, ALPN, HTTP/2 settings) match real
        Chrome instead of Python's stdlib SSL — which is the OTHER way
        Cloudflare detects Python clients beyond user-agent strings.
        Falls back to httpx silently if curl_cffi isn't available.

        Phase 17.15 — Parallel chunks: when `parallel_chunks` > 1 AND the
        file is large enough to benefit AND the server supports HTTP Range
        (Accept-Ranges: bytes), splits the download into N concurrent
        workers each fetching a slice. Falls back to sequential streaming
        if any precondition isn't met.

        Returns the final file size on success; raises _HTTPDownloadFailed
        on any failure so the caller can fall back to Playwright save_as."""
        # Phase 17.15: dispatch to parallel implementation when configured.
        # Threshold check: parallel only helps for big files; small files
        # have too much per-chunk HTTP overhead to benefit.
        n_chunks = int(self.config.get("parallel_chunks", 1) or 1)
        n_chunks = max(1, min(n_chunks, 8))  # cap at 8 to avoid hammering CDNs
        # AUDIT FIX (v3.42.0): Phase 69 speculative mirror selection was
        # defined but never invoked. Call it here so the rest of the
        # download path operates against the fastest-responding mirror.
        try:
            file_url = self._pick_fastest_mirror(file_url)
        except Exception:
            pass  # non-fatal — fall through with original URL
        if n_chunks > 1:
            min_size_mb = float(self.config.get("parallel_min_size_mb", 100) or 100)
            try:
                size = self._probe_size(file_url, page_url, ctx)
            except Exception:
                size = 0
            if size > min_size_mb * 1024 * 1024:
                try:
                    return self._http_download_parallel(page_url, ctx, file_url,
                                                        final_path, total=size,
                                                        n_chunks=n_chunks)
                except _HTTPDownloadFailed as e:
                    # User stop / rate limit / quota — propagate. Anything else
                    # we treat as "parallel didn't work, try sequential".
                    msg = str(e).lower()
                    if "stopped" in msg or "rate" in msg or "disk" in msg:
                        raise
                    sys.stderr.write(f"  parallel failed ({str(e)[:80]}), falling back to sequential\n")
                    self.log_event("parallel_fallback",
                                   f"Parallel chunks failed ({str(e)[:60]}); using sequential",
                                   url=page_url)
                except Exception as e:
                    # If parallel setup blew up unexpectedly, fall back to
                    # sequential rather than failing entirely.
                    sys.stderr.write(f"  parallel setup error, falling back: {str(e)[:80]}\n")

        cookies={c["name"]:c["value"] for c in ctx.cookies()}
        ua=(self.config.get("fingerprint") or {}).get("user_agent") or \
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        headers={"User-Agent":ua,"Referer":page_url,"Accept":"*/*"}
        # part-staging-collision: the staging path is CLAIMED, not derived and
        # hoped for. `_do_download` already reserved this exact name for this
        # exact job, so the claim below normally just re-reads our own -- but
        # this method is also the last thing standing between a second job and
        # somebody else's half-written `.part`, and a defence that only works
        # when the caller remembered to call it is not a defence. A claim held
        # by a different job refuses here; there is no alternative name to
        # divert to at this depth.
        try:
            tmp_path = staging_claim.claim(
                final_path, staging_claim.job_identity(page_url))
        except (staging_claim.StagingClaimedByAnotherJob,
                staging_claim.StagingUnavailable) as e:
            raise _StagingUnavailable(str(e))
        # The claim guards the ON-DISK staging name and is released with it,
        # even when the bytes are staged in RAM below.
        owner_path = staging_claim.owner_path_for(tmp_path)
        # v3.45.6 Phase 183: opt-in RAM-disk staging. If enabled AND
        # this file is sized to fit, redirect tmp_path to the ram
        # staging path. Meta sidecar moves with it (volatile — crash
        # loses resume state, which is fine since the .part is also
        # gone). On success we promote() back to final_path on disk.
        # Fail-open: any error returns None from reserve and we use
        # the original on-disk tmp_path.
        _ramdisk_staging_path = None
        if self.config.get("use_ramdisk_stage"):
            try:
                from . import ramdisk_stage as _rd
                # We don't know exact size yet (Content-Length comes
                # later), so reserve assuming max — caller can refine
                # after HEAD. Conservative: 0 → "reserve max_file_gb".
                _stage = _rd.reserve_staging_path(
                    str(final_path), 0, self.config)
                if _stage:
                    _ramdisk_staging_path = _stage
                    tmp_path = Path(_stage)
            except Exception as e:
                sys.stderr.write(
                    f"[{self.site_id}] ramdisk reserve failed, "
                    f"using disk: {str(e)[:60]}\n")
        # Phase 17.19: dynamic chunk size based on recent throughput. Falls
        # back to the user's static chunk_size_mb when auto_chunk_size is
        # off or there are no observations yet.
        chunk = self._recommended_chunk_bytes()
        # Phase 17.19: start the timer so we can measure throughput at the
        # end. Resumed bytes don't count toward the throughput sample (we
        # never streamed them this call), and since row 430 nothing needs a
        # count of them: the companion initial-bytes variable is gone with
        # the size-delta it existed to feed. Leaving a variable named for the
        # bytes already on disk next to a throughput comment is how the
        # size-delta gets written a second time.
        _dl_t0 = time.time()
        # Row 430: the only honest transfer count is the one the stream
        # produced. `downloaded` is a FILE POSITION (it starts at resume_from
        # on a 206), and the size on disk at the end is a position too, so
        # neither can answer "how many bytes crossed the wire on this call".
        # `streamed` is incremented once per received buffer and by nothing
        # else, so a 200 answer to a resume -- which restarts at byte 0 and
        # re-streams the whole resource -- reports what it moved instead of a
        # delta against an abandoned .part that can be larger than the file.
        streamed = 0
        # Phase 6.1: speed cap. 0 (or unset) = unlimited.
        cap_mbps=self._current_cap_mbps()
        cap_bps=cap_mbps*1024*1024 if cap_mbps>0 else 0
        # Resume from any existing .part file.
        # Phase 62 (v3.41.0): validate the .part is still resumable. We stash
        # the ETag / Last-Modified at the time the .part was written (in a
        # sidecar `.part.meta` JSON file). On resume we send If-Range so the
        # server returns 200 (full content) instead of 206 (range) if the
        # resource changed — and we restart from byte 0 rather than gluing
        # mismatched bytes together. If we have no sidecar metadata, fall
        # back to optimistic resume (legacy behavior).
        resume_from=tmp_path.stat().st_size if tmp_path.exists() else 0
        meta_path = tmp_path.with_suffix(tmp_path.suffix + ".meta")
        resume_validator = None
        if resume_from > 0 and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                resume_validator = meta.get("etag") or meta.get("last_modified")
            except Exception:
                resume_validator = None
        if resume_from>0:
            headers["Range"]=f"bytes={resume_from}-"
            # If-Range: server returns 200 (full) when validator no longer
            # matches, 206 (partial) when it does. Our existing handler already
            # restarts from byte 0 on 200, so this just makes the server
            # do the right thing instead of trusting bytes silently.
            if resume_validator:
                headers["If-Range"] = resume_validator
        # v3.43.31: acquire a per-domain rate-limit slot before opening
        # the HTTP stream. When the limiter is at default (no caps),
        # this is a fast no-op; otherwise it may block until a slot
        # opens. The slot is held for the entire stream duration, so
        # max_concurrent is interpreted as max in-flight downloads
        # per domain — accurate for the "shared CDN backend" case.
        from . import rate_limit as _rl
        _rl_slot = _rl.acquire(file_url)
        # Phase 15.4: try curl_cffi first if installed + enabled. We keep
        # the rest of the streaming code identical via duck-typed iter().
        use_cffi = self.config.get("use_curl_cffi", True)
        # v3.66.390 (Track-K): fail-closed VPN proxy for the payload. Raises
        # _HTTPDownloadFailed (the download-failed signal the worker handles)
        # when the site is vpn_required and its tunnel is down/killed -- no
        # unproxied client is built, so the bytes never touch the clear net.
        try:
            proxy_url = self._download_proxy_url()
        except Exception as _pe:
            if _VPN_RUNTIME_AVAILABLE and isinstance(_pe, vpn_runtime.VPNRequiredError):
                raise _HTTPDownloadFailed(
                    f"VPN required for {self.site_id} but tunnel unavailable; "
                    f"refusing to download on the clear interface")
            raise
        cffi_streamer = None
        if use_cffi:
            try:
                from curl_cffi import requests as cffi_requests
                cffi_streamer = (cffi_requests, "chrome124")
            except ImportError:
                cffi_streamer = None
        _daily_bytes = self._start_daily_byte_accumulator()
        try:
            if cffi_streamer is not None:
                cffi_requests, impersonate = cffi_streamer
                proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                # v3.63.10: curl_cffi has no module-level `stream` function;
                # only Session has a .stream() method. The public module-
                # level streaming API is `request(method, url, stream=True,
                # ...)` returning a Response with `.iter_content()`. This was a real
                # bug from v3.63.9: every HTTP-path download failed with
                # `AttributeError: module 'curl_cffi.requests' has no
                # attribute 'stream'` and fell back to the browser path,
                # which is functional but much slower and triggered the
                # 15-min worker-hung watchdog under load. See
                # `tests/test_curl_cffi_api.py` for the contract pin.
                resp_ctx = _closeable_response_context(
                    cffi_requests.request("GET", file_url, stream=True,
                                          cookies=cookies, headers=headers,
                                          allow_redirects=True,
                                          timeout=300, impersonate=impersonate,
                                          proxies=proxies))
            else:
                # Fallback: httpx, with optional proxy
                # v3.36.8: httpx 0.28+ removed the `proxies` parameter; use
                # the modern `proxy` singular form. Our requirements pin
                # `httpx>=0.25,<1.0` which includes 0.28+; the old plural
                # name would raise TypeError on those installs.
                client_kwargs = {"timeout": httpx.Timeout(30.0, connect=15.0, read=300.0)}
                if proxy_url: client_kwargs["proxy"] = proxy_url
                resp_ctx = httpx.stream("GET", file_url, cookies=cookies, headers=headers,
                                        follow_redirects=True, **client_kwargs)
            with resp_ctx as resp:
                if resp.status_code==416:
                    # Range not satisfiable. Row 428: this proves only that
                    # resume_from is not inside the CURRENT resource -- it is
                    # NOT evidence that the .part holds that resource's complete
                    # bytes. A .part left over from an OLD, larger resource
                    # whose URL now serves something smaller lands here too, and
                    # promoting it publishes a truncated stale file as `done`
                    # with bytes_fetched=0. So promote only on PROOF:
                    #   * the 416's Content-Range complete-length is readable,
                    #     and equals the .part's length exactly; and
                    #   * if we stashed a validator when the .part was written
                    #     AND this response carries one, they still match.
                    # The validator arm is conditional on both sides having one
                    # because many CDNs omit ETag on a 416; demanding one there
                    # would re-download every already-complete file. Anything
                    # unprovable is UNKNOWN, and UNKNOWN discards the .part and
                    # refuses (CLAUDE.md A7) -- keeping it would re-run this
                    # same 416 on every retry forever, so the refusal restarts
                    # the next attempt from byte 0 instead of wedging.
                    if resume_from>0:
                        complete_len = _content_range_complete_length(
                            resp.headers.get("Content-Range"))
                        part_len_err = None
                        try:
                            part_len = tmp_path.stat().st_size
                        except OSError as e:
                            part_len = None
                            part_len_err = e
                        resp_validator = (resp.headers.get("ETag")
                                          or resp.headers.get("Last-Modified"))
                        if complete_len is None:
                            reason = ("no parseable Content-Range "
                                      "complete-length in the response, so the "
                                      "resource's length is unknown")
                        elif part_len is None:
                            reason = (f"the .part length is unreadable "
                                      f"({part_len_err})")
                        elif part_len != complete_len:
                            reason = (f"the .part is {part_len} bytes but the "
                                      f"complete-length is {complete_len} "
                                      f"bytes, so it is a partial of some other "
                                      f"resource")
                        elif (resume_validator and resp_validator
                              and resume_validator != resp_validator):
                            reason = (f"the validator changed since the .part "
                                      f"was written ({resume_validator} -> "
                                      f"{resp_validator})")
                        else:
                            reason = None
                        if reason is not None:
                            for _p in (tmp_path, meta_path):
                                try: Path(_p).unlink(missing_ok=True)
                                except Exception: pass
                            # part-staging-collision: the .part is gone, so its
                            # claim goes with it. owner_path names the ON-DISK
                            # staging file even when the bytes were staged in
                            # RAM, which release(tmp_path) would miss.
                            try: owner_path.unlink(missing_ok=True)
                            except Exception: pass
                            self.log_event(
                                "resume",
                                f"HTTP 416 could not be proven complete "
                                f"({reason}); discarded the .part",
                                url=page_url)
                            raise _HTTPDownloadFailed(
                                f"HTTP 416: {reason}; discarded the "
                                f"unverifiable .part instead of promoting it")
                        # Proven complete. Zero bytes transferred: the size on
                        # disk is not, and never was, evidence of a download.
                        tmp_path.rename(final_path)
                        staging_claim.release(tmp_path, staging_claim.job_identity(page_url))
                        try: meta_path.unlink(missing_ok=True)
                        except Exception: pass
                        return final_path.stat().st_size, 0
                    raise _HTTPDownloadFailed("HTTP 416 with no resume position")
                if resp.status_code==206:  # partial content — resume worked
                    mode="ab"; downloaded=resume_from
                    total=resume_from+int(resp.headers.get("Content-Length",0))
                elif resp.status_code==200:  # full content; if we tried to resume, we restart
                    mode="wb"; downloaded=0
                    total=int(resp.headers.get("Content-Length",0))
                    # Phase 62: validator mismatch (or no validator) → restart.
                    # If we had a .part from a stale resource, drop it now.
                    if resume_from > 0:
                        self.log_event("resume", "Server returned 200 — resource changed, restarting download", url=page_url)
                else:
                    raise _HTTPDownloadFailed(f"HTTP {resp.status_code}")
                # Phase 62: stash the validators alongside the .part so a
                # subsequent resume attempt can use If-Range. Cheap — we
                # only write this once per download.
                # FIX: if we just restarted (200) and the new response has
                # NO validators, delete the old stale meta so the next
                # resume attempt is optimistic instead of using bad data.
                try:
                    validator_meta = {}
                    etag = resp.headers.get("ETag")
                    lm = resp.headers.get("Last-Modified")
                    if etag: validator_meta["etag"] = etag
                    if lm: validator_meta["last_modified"] = lm
                    if validator_meta:
                        # Audit 2026-05: atomic .tmp + replace. A crash mid-
                        # write here would leave a truncated sidecar that
                        # the next load would treat as corrupted (cookie
                        # parsing failure → re-download from scratch).
                        _tmp_meta = meta_path.with_suffix(meta_path.suffix + ".tmp")
                        _tmp_meta.write_text(json.dumps(validator_meta), encoding="utf-8")
                        _tmp_meta.replace(meta_path)
                    elif resp.status_code == 200 and meta_path.exists():
                        # Restart with no validators — drop the stale sidecar
                        meta_path.unlink(missing_ok=True)
                except OSError:
                    pass  # non-fatal; resume becomes optimistic next time
                last_update=0; last_bytes=downloaded; start=time.time()
                # Phase 6.1: throttle window. window_bytes counts bytes
                # written since window_start; if cap exceeded mid-window,
                # we sleep the difference. Reset every 1.0s.
                window_start=start; window_bytes=0
                # Phase 6.2: persist resume position to the queue table at
                # start so a crash doesn't lose track of where we are.
                if resume_from>0:
                    try:
                        from .db import queue_upsert
                        queue_upsert(self.site_id,page_url,status="running",
                                     message=f"resuming from {fmt_bytes(resume_from)}",
                                     file_size=resume_from)
                    except sqlite3.Error as e:
                        # Persist failure means resume state won't survive
                        # a crash, but in-memory download proceeds fine
                        self.log.warning("resume-state persist failed for %s: %s", page_url, e)
                with open(tmp_path,mode) as f:
                    # Phase 15.4: pick the iterator name based on which
                    # client we're using. httpx uses iter_bytes, curl_cffi
                    # uses iter_content. ``chunk_size`` is advisory: notably,
                    # curl_cffi may yield much smaller transport buffers, so
                    # hot-loop work must not assume one yield per requested
                    # chunk.
                    iterator = (resp.iter_content(chunk_size=chunk)
                                if cffi_streamer is not None
                                else resp.iter_bytes(chunk_size=chunk))
                    # v3.45.5 Phase 182: opt-in token-bucket throttle.
                    # When `use_token_bucket` config is True AND a cap
                    # is in effect, acquire bytes from the per-site
                    # bucket before processing each chunk. The bucket
                    # smoothes the rate across all workers + downloads
                    # for this site, while the windowed path (below)
                    # is per-download. Both paths fail-open: if bucket
                    # acquire times out, we fall through to windowed.
                    _use_bucket = (cap_bps > 0
                                    and self.config.get("use_token_bucket"))
                    _bucket = None
                    if _use_bucket:
                        try:
                            from . import bandwidth_shape as _bw
                            _bucket = _bw.get_bucket(
                                self.site_id,
                                max_bytes_per_sec=cap_bps)
                        except Exception:
                            _bucket = None
                    for buf in iterator:
                        if not self._transfer_gate_open(_daily_bytes):
                            raise _HTTPDownloadFailed("stopped")
                        if _bucket is not None:
                            # Acquire from token bucket — bounded wait
                            # so we never deadlock on a misconfigured
                            # bucket. Fail-open on timeout (let the
                            # windowed code below act as backup).
                            try:
                                _bucket.acquire(len(buf), timeout=30)
                            except Exception:
                                pass
                        f.write(buf)
                        downloaded+=len(buf)
                        streamed+=len(buf)
                        window_bytes+=len(buf)
                        if _daily_bytes is not None:
                            _daily_bytes.add(len(buf))
                        # Phase 8.3: feed the rolling bandwidth tracker
                        record_bandwidth(len(buf))
                        # Pause/stop may race after the pre-write gate. The
                        # operator-side registry flush handles actions before
                        # this add; this post-add check handles actions that
                        # landed while the file write was in flight.
                        if not self._flush_after_interrupted_write(_daily_bytes):
                            raise _HTTPDownloadFailed("stopped")
                        # Phase 6.1: throttle if exceeding cap. When
                        # the token bucket is active this is a no-op
                        # most of the time (bucket already paced us);
                        # it acts as a safety net for the bucket-
                        # timeout fail-open path.
                        if cap_bps>0:
                            now2=time.time()
                            elapsed_w=now2-window_start
                            if elapsed_w>0:
                                expected_w=window_bytes/cap_bps
                                if expected_w>elapsed_w:
                                    time.sleep(expected_w-elapsed_w)
                            # Roll the window every 1s so we adapt to varying
                            # throughput rather than locking in the first burst
                            if elapsed_w>=1.0:
                                window_start=time.time(); window_bytes=0
                                # Phase 17.18: re-read the cap so a day↔night
                                # transition during a long download takes effect
                                # within ~1 second instead of waiting for the
                                # next file.
                                cap_mbps = self._current_cap_mbps()
                                cap_bps = cap_mbps*1024*1024 if cap_mbps>0 else 0
                        # Throttle status updates to ~1Hz
                        now=time.time()
                        if now-last_update>=1.0:
                            elapsed=now-start
                            speed=(downloaded-last_bytes)/(now-last_update)
                            last_update=now; last_bytes=downloaded
                            cap_str=f" (cap {cap_mbps:.0f} MB/s)" if cap_mbps>0 else ""
                            if total>0:
                                pct=int(downloaded*100/total)
                                msg=f"⬇ {pct}% • {fmt_bytes(downloaded)}/{fmt_bytes(total)} • {fmt_bytes(int(speed))}/s{cap_str}"
                            else:
                                msg=f"⬇ {fmt_bytes(downloaded)} • {fmt_bytes(int(speed))}/s{cap_str}"
                            self._update_job(page_url,"running",msg,file_size=downloaded)
                            # Phase 6.2: every progress tick, persist
                            # current bytes-on-disk to the queue table.
                            # On crash recovery, resume picks up from this
                            # value (capped by actual .part file size).
                            try:
                                from .db import queue_upsert
                                queue_upsert(self.site_id,page_url,status="running",
                                             message=msg,file_size=downloaded)
                            except sqlite3.Error:
                                # Tick-rate persist — failures here are
                                # transient (DB busy, locked); the next
                                # tick will succeed. Not worth logging.
                                pass
        except httpx.HTTPError as e:
            raise _HTTPDownloadFailed(f"http error: {e}")
        except _HTTPDownloadFailed:
            raise
        except Exception as e:
            raise _HTTPDownloadFailed(f"unexpected: {e}")
        finally:
            # Completion, stop, iterator/write failure, and every other exit
            # from the response loop must persist the exact pending delta.
            self._finish_daily_byte_accumulator(_daily_bytes)
            # v3.43.31: always release the rate-limit slot, whether we
            # succeed, fail, or get stopped. Without finally, a worker
            # that crashed in the middle of streaming would leak its
            # slot and eventually deadlock the domain.
            try:
                _rl_slot.release()
            except Exception:
                pass
        # ── BP-INT (v3.66.284): download-integrity size gate ────────────
        # A stream that ended before the advertised Content-Length was
        # satisfied is a TRUNCATED transfer; the .part must never be promoted
        # to the final name (a short file must not masquerade as `done`). The
        # direct path gates inside _promote_or_abort (atomic os.replace on a
        # full transfer, else clean the .part + raise); the ramdisk path
        # gates explicitly here before its cross-device promote.
        # _DownloadTruncated propagates to the caller, which routes the URL to
        # needs_review so the operator can force a re-download.
        #
        # v3.45.6 Phase 183: if tmp_path is in ramdisk staging, use the
        # promote helper which does a real cross-device move + tracks the
        # release in the reservation table. Otherwise an atomic same-device
        # os.replace via the integrity helper.
        if _ramdisk_staging_path:
            if not TransportMixin._integrity_size_ok(downloaded, total):
                for _p in (_ramdisk_staging_path, tmp_path, meta_path):
                    if not _p:
                        continue
                    try: Path(_p).unlink(missing_ok=True)
                    except Exception: pass
                # part-staging-collision: the .part is gone, so its claim goes
                # with it. owner_path names the ON-DISK staging file even when
                # the bytes were staged in RAM.
                try: owner_path.unlink(missing_ok=True)
                except Exception: pass
                raise _DownloadTruncated(
                    f"truncated: received {downloaded} of {total} bytes "
                    f"(Content-Length); not promoting to final")
            try:
                from . import ramdisk_stage as _rd
                ok, err = _rd.promote(_ramdisk_staging_path,
                                       str(final_path))
                if not ok:
                    raise _HTTPDownloadFailed(
                        f"ramdisk promote failed: {err}")
                _rd.release(_ramdisk_staging_path)
            except Exception as e:
                # Fall back to direct rename if helper fails
                try: Path(_ramdisk_staging_path).rename(final_path)
                except Exception as e2:
                    raise _HTTPDownloadFailed(
                        f"rename failed: {e}; {e2}")
        else:
            try:
                TransportMixin._promote_or_abort(tmp_path, final_path,
                                             downloaded, total,
                                             meta_path=meta_path,
                                                 identity=staging_claim.job_identity(page_url))
            except _DownloadTruncated:
                raise
            except Exception as e:
                raise _HTTPDownloadFailed(f"rename failed: {e}")
        # Phase 62: cleanup the resume-meta sidecar now that the download
        # is complete and the .part no longer exists.
        try: meta_path.unlink(missing_ok=True)
        except Exception: pass
        # part-staging-collision: and the staging claim with it. Idempotent --
        # the direct path already released inside _promote_or_abort; this also
        # covers the ramdisk promote, which never goes through that helper.
        try: owner_path.unlink(missing_ok=True)
        except Exception: pass
        # Phase 17.19: feed this download's measured throughput into the
        # EWMA so the next download picks a better chunk size. Only count
        # bytes ACTUALLY transferred this call (not resumed bytes) -- which is
        # `streamed`, straight off the loop that received them.
        #
        # Row 430: this used to be `final_path.stat().st_size -
        # _dl_initial_bytes`, an on-disk size delta. A 200 answer to a resume
        # streams the whole new resource while the delta still subtracts the
        # abandoned .part, so a smaller new file went negative and max(0, ...)
        # reported 0 -- history's documented value for "nothing was
        # transferred" -- for a real transfer, and a larger one was
        # undercounted by resume_from. It also meant an unreadable stat left
        # the count at its initialised 0 and reported that as fact; the count
        # no longer depends on any stat, so an unmeasurable file now refuses
        # (the stat below raises) instead of claiming zero. The EWMA is
        # trained on the same corrected number.
        transferred = streamed
        try:
            self._observe_throughput(transferred, time.time() - _dl_t0)
        except Exception:
            pass
        # Returns (size_on_disk, bytes_transferred_this_call).
        # It travels by RETURN, not on self: runner.py:1120 starts one worker
        # thread per slot against a shared runner instance, so an attribute
        # would cross-attribute concurrent downloads.
        return final_path.stat().st_size, max(0, transferred)
    def _probe_size(self, file_url, page_url, ctx):
        """HEAD request to learn Content-Length + Accept-Ranges. Returns
        total size in bytes on success, 0 if size is unknown OR server
        doesn't support Range (in which case sequential is mandatory).
        Quick — 10s timeout — non-fatal on failure (caller falls back)."""
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        ua = (self.config.get("fingerprint") or {}).get("user_agent") or \
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        headers = {"User-Agent": ua, "Referer": page_url}
        # v3.66.390 (Track-K): fail-closed -- a vpn_required site whose tunnel is
        # down returns 0 (size unknown) so no clear-net HEAD is sent; the caller
        # falls back to the (also fail-closed) sequential path.
        try:
            proxy_url = self._download_proxy_url()
        except Exception as _pe:
            if _VPN_RUNTIME_AVAILABLE and isinstance(_pe, vpn_runtime.VPNRequiredError):
                sys.stderr.write(
                    f"  probe_size: VPN required for {self.site_id}, tunnel "
                    f"unavailable -- skipping probe (fail closed)\n")
                return 0
            raise
        try:
            if self.config.get("use_curl_cffi", True):
                try:
                    from curl_cffi import requests as cr
                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    r = cr.head(file_url, headers=headers, cookies=cookies,
                                allow_redirects=True, timeout=10,
                                impersonate="chrome124", proxies=proxies)
                except ImportError:
                    raise RuntimeError("curl_cffi missing")
            else:
                raise RuntimeError("skip cffi")
        except Exception:
            try:
                # v3.36.8: httpx 0.28+ uses `proxy` (singular).
                kw = {"timeout": 10}
                if proxy_url: kw["proxy"] = proxy_url
                r = httpx.head(file_url, headers=headers, cookies=cookies,
                               follow_redirects=True, **kw)
            except Exception: return 0
        accept = (r.headers.get("Accept-Ranges") or "").lower()
        cl = r.headers.get("Content-Length")
        if accept != "bytes" or not cl:
            return 0  # caller will fall back to sequential
        try: return int(cl)
        except Exception: return 0
    def _http_download_parallel(self, page_url, ctx, file_url, final_path,
                                 total, n_chunks):
        """Download `total` bytes via N parallel HTTP Range requests.

        Layout:
          - Pre-allocate the .part file to `total` bytes (sparse on most
            filesystems).
          - Split byte range [0..total) into N equal-ish slices.
          - Spawn N workers, each fetching its slice and writing at the
            appropriate offset via os.pwrite (no global file lock needed
            since slices don't overlap).
          - Main thread aggregates progress and applies the speed cap
            cumulatively (not per-worker, so cap_mbps is total throughput).
          - Stop event halts all workers; one worker's failure aborts the
            whole download.

        Falls back to sequential by raising _HTTPDownloadFailed("...");
        caller catches and tries the non-parallel path."""
        import threading as _t

        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        ua = (self.config.get("fingerprint") or {}).get("user_agent") or \
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        headers_base = {"User-Agent": ua, "Referer": page_url, "Accept": "*/*"}
        # v3.66.390 (Track-K): fail-closed VPN proxy for the parallel payload
        # connections. Raises _HTTPDownloadFailed when the site is vpn_required
        # and the tunnel is unavailable (caller falls back to sequential, which
        # is itself fail-closed) -- no unproxied range client is built.
        try:
            proxy_url = self._download_proxy_url()
        except Exception as _pe:
            if _VPN_RUNTIME_AVAILABLE and isinstance(_pe, vpn_runtime.VPNRequiredError):
                raise _HTTPDownloadFailed(
                    f"VPN required for {self.site_id} but tunnel unavailable; "
                    f"refusing parallel download on the clear interface")
            raise
        use_cffi = self.config.get("use_curl_cffi", True)
        try:
            if use_cffi:
                from curl_cffi import requests as _cffi
            else: _cffi = None
        except ImportError: _cffi = None

        # part-staging-collision: the segmented downloader stages into the
        # SAME `<final>.part` the sequential path uses, so it claims it the
        # same way. _do_download already reserved this name for this job, so
        # this normally re-reads our own claim; a claim held by a different
        # live download refuses rather than pwrite into its bytes.
        try:
            tmp_path = staging_claim.claim(
                final_path, staging_claim.job_identity(page_url))
        except (staging_claim.StagingClaimedByAnotherJob,
                staging_claim.StagingUnavailable) as e:
            raise _StagingUnavailable(str(e))
        # v3.43.27: per-file resume checkpoint. Replaces the previous
        # "always delete .part" behavior — now we check for a valid
        # resume point and only restart from scratch when validators
        # don't match. The big win is on 5-7GB 8K files: a crash 80%
        # through used to mean redownloading from byte 0; now we pick
        # up at byte 80%.
        from . import resume as _resume
        existing_cp = _resume.load(final_path)
        head_etag = head_last_modified = None
        # HEAD probe to learn ETag/Last-Modified for resume validation.
        # Failure is non-fatal — we just skip resume and start fresh.
        try:
            import httpx as _hx
            with _hx.Client(timeout=15.0, follow_redirects=True) as _hc:
                _head = _resume.head_probe(_hc, file_url,
                                            headers=headers_base,
                                            cookies=cookies)
                head_etag = _head.get("etag")
                head_last_modified = _head.get("last_modified")
        except Exception:
            pass

        resume_chunk_states = None  # parallel list to slices: done_bytes per chunk
        if existing_cp is not None:
            ok, why = _resume.is_resumable(existing_cp, file_url, total,
                                            etag=head_etag,
                                            last_modified=head_last_modified)
            if ok and _resume.reconcile_with_disk(existing_cp, final_path):
                # We can resume! Pull chunk states from the checkpoint.
                already = _resume.bytes_already_downloaded(existing_cp)
                self.log_event("resume",
                    f"Resuming parallel download from "
                    f"{already // 1024 // 1024} / "
                    f"{total // 1024 // 1024} MB "
                    f"({already / total * 100:.1f}%)",
                    url=page_url)
                resume_chunk_states = list(existing_cp["chunks"])
            else:
                # Validators don't match; nuke and restart.
                self.log_event("resume",
                    f"Resume aborted ({why}); starting fresh",
                    url=page_url)
                try:
                    if tmp_path.exists(): tmp_path.unlink()
                    _resume.cleanup(final_path)
                    meta_path = tmp_path.with_suffix(tmp_path.suffix + ".meta")
                    meta_path.unlink(missing_ok=True)
                except Exception: pass
        else:
            # No checkpoint at all — same legacy cleanup as before.
            # Phase 62 fix: also drop the .part.meta sidecar so a future
            # sequential resume attempt doesn't pick up stale validators
            # against this parallel run.
            try:
                if tmp_path.exists(): tmp_path.unlink()
                meta_path = tmp_path.with_suffix(tmp_path.suffix + ".meta")
                meta_path.unlink(missing_ok=True)
            except Exception: pass

        # Pre-allocate sparse file (no-op if resuming and file exists at total size)
        try:
            need_alloc = not tmp_path.exists() or tmp_path.stat().st_size < total
            if need_alloc:
                with open(tmp_path, "wb") as f: f.truncate(total)
        except Exception as e:
            raise _HTTPDownloadFailed(f"pre-allocate failed: {e}")

        slice_size = total // n_chunks
        slices = []
        for i in range(n_chunks):
            start = i * slice_size
            end = (total - 1) if i == n_chunks - 1 else (start + slice_size - 1)
            slices.append((start, end))

        # If resuming, replace slices with the checkpoint's chunk
        # boundaries (n_chunks might differ from the original run, so
        # we trust the checkpoint over the current config).
        if resume_chunk_states is not None:
            slices = [(c["start"], c["end"]) for c in resume_chunk_states]
            n_chunks = len(slices)

        # Build or restore the live checkpoint object that workers
        # update as they progress.
        if resume_chunk_states is not None:
            checkpoint = existing_cp
        else:
            checkpoint = _resume.initialize(final_path, file_url, total,
                                             n_chunks,
                                             etag=head_etag,
                                             last_modified=head_last_modified)
        # Lock for checkpoint mutation — workers update from multiple
        # threads, we save() from the monitor loop. The lock is held
        # only during dict mutation + the atomic write.
        checkpoint_lock = _t.Lock()

        # Worker state: progress[idx] is bytes downloaded BY THIS RUN,
        # which is added to the chunk's pre-resume done_bytes for the
        # absolute count. resume_offset[idx] is the chunk's done_bytes
        # at the start of this run (0 for non-resumed chunks).
        progress = [0] * n_chunks
        # Row 431: progress[idx] is what the monitor persists into the resume
        # checkpoint, so it may only ever count bytes the OS already has. The
        # write loop below therefore flushes BEFORE it counts -- see there.
        resume_offset = [int(c.get("done_bytes", 0))
                          for c in (resume_chunk_states or
                                     [{"done_bytes": 0}] * n_chunks)]
        worker_errors = [None] * n_chunks
        local_stop = _t.Event()
        _daily_bytes = _ParallelDailyByteAccounting(self, n_chunks)

        def worker(idx, byte_start, byte_end):
            # Non-overlapping per-worker handles need no file lock. Resume
            # advances both seek and Range start; progress is this-run only.
            effective_start = byte_start + resume_offset[idx]
            if effective_start > byte_end:
                # Chunk was already complete in a previous run. Nothing
                # to do; mark progress as the full slice and return.
                progress[idx] = (byte_end - byte_start + 1) - resume_offset[idx]
                _daily_bytes.worker_finished()
                return
            f = None  # [SAST 3:13pm 13 may] pre-bind so the except can close on seek-failure
            try:
                f = open(str(tmp_path), "r+b")
                f.seek(effective_start)
            except Exception as e:
                if f is not None:  # [SAST 3:13pm 13 may] open() succeeded but seek() raised — close handle
                    try: f.close()  # [SAST 3:13pm 13 may]
                    except Exception: pass  # [SAST 3:13pm 13 may]
                worker_errors[idx] = f"open failed: {e}"
                _daily_bytes.worker_finished()
                return
            try:
                req_headers = dict(headers_base)
                req_headers["Range"] = f"bytes={effective_start}-{byte_end}"
                # v3.43.31: each parallel-chunk worker acquires its OWN
                # rate-limit slot. The reasoning: max_concurrent caps
                # in-flight requests per domain, and a 4-chunk parallel
                # download IS 4 in-flight requests. Without per-worker
                # acquire, a single file would consume only one slot
                # while making four concurrent requests, defeating the
                # rate limit's purpose.
                from . import rate_limit as _rl_worker
                _rl_worker_slot = _rl_worker.acquire(file_url)
                try:
                    if _cffi:
                        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                        # v3.63.10: same fix as the single-stream path —
                        # curl_cffi has no module-level `stream`; use
                        # `request(method, url, stream=True, ...)`. See
                        # the contract pin in
                        # `tests/test_curl_cffi_api.py`.
                        resp_ctx = _closeable_response_context(
                            _cffi.request("GET", file_url, stream=True,
                                          headers=req_headers,
                                          cookies=cookies, allow_redirects=True,
                                          timeout=300, impersonate="chrome124",
                                          proxies=proxies))
                    else:
                        # v3.36.8: httpx 0.28+ uses `proxy` (singular).
                        kw = {"timeout": httpx.Timeout(30.0, connect=15.0, read=300.0)}
                        if proxy_url: kw["proxy"] = proxy_url
                        resp_ctx = httpx.stream("GET", file_url, headers=req_headers,
                                                cookies=cookies, follow_redirects=True, **kw)
                    with resp_ctx as resp:
                        if resp.status_code != 206:
                            worker_errors[idx] = f"chunk {idx}: HTTP {resp.status_code} (no Range support?)"; return
                        chunk_iter = (resp.iter_content(chunk_size=1024*1024) if _cffi
                                      else resp.iter_bytes(chunk_size=1024*1024))
                        for buf in chunk_iter:
                            if not self._transfer_gate_open(
                                    _daily_bytes.accumulator, local_stop):
                                worker_errors[idx] = "stopped"
                                return
                            f.write(buf)
                            # Row 431: hand the bytes to the OS BEFORE counting
                            # them. The monitor persists progress[] into the
                            # resume checkpoint, and a checkpoint is a promise
                            # that must survive SIGKILL -- which loses exactly
                            # the BufferedWriter's userspace residue and
                            # nothing else. curl_cffi can yield buffers far
                            # smaller than the requested chunk, so counting
                            # first left the checkpoint claiming bytes the OS
                            # had never seen; on resume the workers skipped
                            # that region and the promoted file carried
                            # zero-filled holes. Flushing first makes the
                            # count true at every instant a reader can observe
                            # it, including the mid-write window. flush(), not
                            # fsync(): the failure defended against is process
                            # death, and the page cache outlives the process.
                            # A write larger than the buffer goes straight
                            # through and this flush is a no-op.
                            f.flush()
                            progress[idx] += len(buf)
                            _daily_bytes.add(len(buf))
                            record_bandwidth(len(buf))
                            if not self._flush_after_interrupted_write(
                                    _daily_bytes.accumulator, local_stop):
                                worker_errors[idx] = "stopped"
                                return
                except Exception as e:
                    worker_errors[idx] = f"chunk {idx}: {str(e)[:80]}"
                finally:
                    # v3.43.31: always release the per-worker rate-limit
                    # slot. Worker errors are recorded above; slot release
                    # happens regardless so we don't leak under failure.
                    try:
                        _rl_worker_slot.release()
                    except Exception:
                        pass
            finally:
                try: f.close()
                except Exception: pass
                _daily_bytes.worker_finished()

        threads = []
        start_time = time.time()
        for i, (s, e) in enumerate(slices):
            try:
                t = _t.Thread(target=worker, args=(i, s, e), daemon=True)
                t.start()
            except Exception:
                # Started workers own their slot; abandon this and later slots.
                _daily_bytes.worker_finished(n_chunks - i)
                local_stop.set()
                for started_thread in threads:
                    started_thread.join(timeout=3)
                _daily_bytes.flush()
                raise
            threads.append(t)

        # Monitor — aggregate progress, apply cap, update UI
        cap_mbps = self._current_cap_mbps()
        last_update = 0
        last_total_bytes = 0
        # v3.43.27: track checkpoint saves separately from UI updates.
        # Save every 5MB of total progress so a crash loses at most 5MB
        # per chunk of redownload work. Frequent enough to be useful,
        # rare enough that disk write overhead is negligible.
        CHECKPOINT_SAVE_EVERY_BYTES = 5 * 1024 * 1024
        last_saved_bytes = sum(resume_offset)  # already-saved bytes from prior run
        # The pre-resume baseline — added to per-run progress to get absolute total
        pre_resume_total = sum(resume_offset)
        while True:
            alive = [t for t in threads if t.is_alive()]
            # Total bytes = prior checkpoint baseline + this-run progress.
            this_run = sum(progress)
            total_bytes = pre_resume_total + this_run
            if any(worker_errors):
                # One failed → abort all
                local_stop.set()
                for t in threads: t.join(timeout=3)
                err = next(e for e in worker_errors if e)
                # v3.43.27: SAVE checkpoint before bailing — partial
                # progress should survive the next run instead of being
                # discarded along with the .part file.
                with checkpoint_lock:
                    for i in range(n_chunks):
                        _resume.update_chunk_progress(checkpoint, i,
                            resume_offset[i] + progress[i])
                    _resume.save(final_path, checkpoint)
                # Don't unlink the .part anymore — leave it for resume.
                # The checkpoint is the source of truth for what's
                # downloaded; .part is the data behind it.
                _daily_bytes.flush()
                raise _HTTPDownloadFailed(f"parallel: {err}")
            now = time.time()
            if now - last_update >= 1.0:
                # Throttle (apply cap to total throughput across all workers)
                if cap_mbps > 0:
                    elapsed = now - start_time
                    expected_min = this_run / (cap_mbps * 1024 * 1024)
                    if expected_min > elapsed:
                        # Briefly pause workers globally
                        sleep_for = min(0.5, expected_min - elapsed)
                        time.sleep(sleep_for)
                # UI update — show absolute progress including resume baseline
                speed = (total_bytes - last_total_bytes) / max(0.001, now - last_update)
                pct = (total_bytes / total) * 100 if total else 0
                # v3.43.27: mention resume in the message so the user
                # can see at a glance that the % > 0 isn't a fresh start
                resume_tag = " (resumed)" if pre_resume_total > 0 else ""
                msg = (f"Downloading {n_chunks}× parallel{resume_tag} · "
                       f"{fmt_bytes(total_bytes)}/{fmt_bytes(total)} "
                       f"({pct:.0f}%) · {fmt_bytes(int(speed))}/s")
                self._update_job(page_url, "running", msg, file_size=total_bytes)
                # Re-read schedule cap so day↔night transitions work mid-file
                cap_mbps = self._current_cap_mbps()
                last_update = now; last_total_bytes = total_bytes
            # v3.43.27: periodic checkpoint save
            if total_bytes - last_saved_bytes >= CHECKPOINT_SAVE_EVERY_BYTES:
                with checkpoint_lock:
                    for i in range(n_chunks):
                        _resume.update_chunk_progress(checkpoint, i,
                            resume_offset[i] + progress[i])
                    _resume.save(final_path, checkpoint)
                last_saved_bytes = total_bytes
            if not alive:
                break
            time.sleep(0.2)

        # Final check — all workers done with no errors
        if any(worker_errors):
            err = next(e for e in worker_errors if e)
            # v3.43.27: save checkpoint before bailing so the next run
            # can resume from this point.
            with checkpoint_lock:
                for i in range(n_chunks):
                    _resume.update_chunk_progress(checkpoint, i,
                        resume_offset[i] + progress[i])
                _resume.save(final_path, checkpoint)
            _daily_bytes.flush()
            raise _HTTPDownloadFailed(f"parallel: {err}")
        # v3.43.27: absolute completion check = pre-resume + this run
        absolute_total = pre_resume_total + sum(progress)
        if absolute_total != total:
            # Partial success — checkpoint the state and surface the error.
            with checkpoint_lock:
                for i in range(n_chunks):
                    _resume.update_chunk_progress(checkpoint, i,
                        resume_offset[i] + progress[i])
                _resume.save(final_path, checkpoint)
            _daily_bytes.flush()
            raise _HTTPDownloadFailed(
                f"parallel: short read ({absolute_total}/{total})")

        # Atomic rename
        try: tmp_path.rename(final_path)
        except Exception as e:
            _daily_bytes.flush()
            raise _HTTPDownloadFailed(f"rename failed: {e}")
        # part-staging-collision: the .part is gone, so its claim goes too.
        staging_claim.release(tmp_path, staging_claim.job_identity(page_url))
        # v3.43.27: file is complete; clean up the checkpoint sidecar.
        _resume.cleanup(final_path)
        # (size_on_disk, bytes_transferred_this_call) -- `progress` holds the
        # per-chunk byte counts fetched by THIS call, excluding whatever
        # resume_offset already had on disk.
        try:
            _transferred = sum(progress)
        except Exception:
            _transferred = 0
        _daily_bytes.flush()
        return final_path.stat().st_size, max(0, _transferred)
    def _current_cap_mbps(self):
        """Return the current effective speed cap in MB/s.

        Three modes:
          1. Static `max_mbps` (legacy Phase 6.1 behavior). Used when
             `bandwidth_schedule_enabled` is false.
          2. Day/night split: when bandwidth_schedule_enabled is true,
             use `max_mbps_day` during work hours
             (`day_start`..`day_end`, e.g. 09:00..17:00) and `max_mbps_night`
             outside that window. 0 in either field = unlimited for that
             window.
          3. If schedule is enabled but no day/night fields set, fall back
             to legacy max_mbps.

        Times are local (the host's timezone), parsed as HH:MM. If the
        end time is earlier than the start time we treat it as crossing
        midnight (e.g. 22:00..06:00)."""
        if not self.config.get("bandwidth_schedule_enabled"):
            return float(self.config.get("max_mbps", 0) or 0)
        try:
            from datetime import datetime
            now = datetime.now()
            cur_min = now.hour * 60 + now.minute
            day_start = self._parse_hm(self.config.get("day_start", "09:00"), 9*60)
            day_end   = self._parse_hm(self.config.get("day_end", "17:00"), 17*60)
            in_day = (day_start <= cur_min < day_end) if day_start < day_end \
                else (cur_min >= day_start or cur_min < day_end)
            mbps_field = "max_mbps_day" if in_day else "max_mbps_night"
            v = self.config.get(mbps_field)
            if v is None or v == "": return float(self.config.get("max_mbps", 0) or 0)
            return float(v)
        except Exception:
            return float(self.config.get("max_mbps", 0) or 0)
    def _recommended_chunk_bytes(self):
        """Return a chunk size in bytes, tuned to recent observed throughput.

        Behavior:
          • If `auto_chunk_size` is False (default until user opts in), return
            the user's static `chunk_size_mb` setting.
          • If we've never observed throughput, return the static setting too
            (no data → no tuning).
          • Otherwise: chunk = throughput_bps × 0.5, clamped to [256 KiB, 64 MiB].

        The 0.5-second window is the sweet spot: long enough that per-chunk
        Python overhead is negligible, short enough that pause/stop responds
        snappily and progress updates feel smooth on the UI."""
        if not self.config.get("auto_chunk_size", False):
            return max(64*1024, int(self.config.get("chunk_size_mb", 4)) * 1024 * 1024)
        if self._throughput_samples < 1 or self._throughput_ewma_bps <= 0:
            # No observations yet — use the static fallback
            return max(64*1024, int(self.config.get("chunk_size_mb", 4)) * 1024 * 1024)
        target = int(self._throughput_ewma_bps * 0.5)  # 0.5 sec of bandwidth
        # Clamp: 256 KiB minimum (prevents pathologically small chunks on
        # very slow links — Python loop overhead would dominate); 64 MiB
        # maximum (caps memory use even on very fast links).
        target = max(256 * 1024, min(64 * 1024 * 1024, target))
        return target
    def _observe_throughput(self, bytes_downloaded, seconds_elapsed):
        """Update the EWMA throughput tracker after a download. Called
        once per successful HTTP download. We use alpha=0.3 so a single
        atypically-fast or -slow download doesn't swing the recommended
        chunk size more than ~30%, but a sustained speed change converges
        in 4-5 downloads."""
        if seconds_elapsed <= 0.1 or bytes_downloaded < 256 * 1024:
            return  # too small to be a reliable sample
        bps = bytes_downloaded / seconds_elapsed
        if self._throughput_samples == 0:
            # First sample — adopt directly. Avoids the 0→large jump that
            # would happen with EWMA alpha applied to a 0 baseline.
            self._throughput_ewma_bps = bps
        else:
            alpha = 0.3
            self._throughput_ewma_bps = alpha * bps + (1 - alpha) * self._throughput_ewma_bps
        self._throughput_samples += 1
