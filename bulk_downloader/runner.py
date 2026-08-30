"""SiteRunner — the per-site worker pool, scheduler, and state machine.

This is the largest module. v3.0 refactored from ThreadPoolExecutor
to persistent worker threads + queue. Each thread owns one Chromium
for its full lifetime so we launch the browser N times instead of
once per URL.

──────────────────────────────────────────────────────────────────────
Convention: `except Exception: pass` clauses in this file

These clauses are kept in cases where a teardown / cleanup operation
SHOULD silently absorb any exception because:
  - The work has already succeeded; cleanup failure mustn't undo it
  - Playwright is known to throw arbitrary exception types during
    browser/context shutdown that aren't worth catching specifically
  - The OS may have already cleaned up the resource (sockets, files)

Patterns to LEAVE alone:
  try: browser.close() except Exception: pass    # Playwright teardown
  try: pw.stop() except Exception: pass          # Playwright teardown
  try: thread.join(timeout=N) except Exception: pass  # thread cleanup

Patterns to NARROW (and log at appropriate level):
  - SQLite operations    → except sqlite3.Error
  - File I/O             → except OSError
  - JSON parsing         → except (json.JSONDecodeError, ValueError)
  - HTTP calls           → except (httpx.HTTPError, ConnectionError)

History: Phase 34/39 narrowed several queue_upsert, rl_file, cookie_save
excepts. v3.43.17 narrowed the remaining bare `except:` clauses to
`except Exception:` so Ctrl-C and SystemExit propagate cleanly. The
`pass` bodies are still intentional in the teardown contexts above.
──────────────────────────────────────────────────────────────────────
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
import collections, contextlib, enum, functools, inspect, itertools, json, math, os, queue, re, shutil, sqlite3, subprocess, sys, threading, time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from .constants import (
    SCREENSHOTS_DIR, RETRY_DELAYS, RL_RE,
    BLOCK_HINTS, AUTH_HINTS, AUTH_BODY_RE, _HTTPDownloadFailed,
    _DownloadTruncated,
)
# Row 390: imported at module scope ON PURPOSE. A safety gate reached through a
# lazy `try: from . import ...` inside start() would fail OPEN on an import
# error -- the exact shape this row exists to remove. download_hold imports only
# the standard library at module scope, so there is no cycle to dodge.
from . import download_hold as _download_hold

# A0 / BEH-1 + BEH-2 (v3.66.322): canonical creation-time defaults. These are the
# values the legacy Add-Site form stored at create time; the SPA wizard stores no
# defaults, so SPA-added sites fall back to these read-time defaults. Every read
# site references the constant so a missing key resolves identically everywhere
# (an explicit `min_resolution: 0` still means "no minimum" via the `or 0` tail).
DEFAULT_MAX_CONCURRENT = 2

from .cookies import (
    load_cookies_from_file, cookies_expiry_info, cookie_age_str,
)
from .detect import (
    find_best_download, res_label, fmt_bytes,
    disk_free_gb, safe_dest,
)
from .fname import resolve_filename_template, format_duration_for_filename
from .website_title import (
    harvest_page_title,
    strip_repeated_title_template,
)
from .integrity import verify_media_integrity
from .login import do_login
# v3.66.144: reviewed-template runtime bridge. Soft import so the runner
# still works if the template subsystem is ever absent (degraded mode: no
# reviewed-template hints, learned/configured selectors only).
try:
    from .template_assist import merge_template_download_hints
except Exception:  # pragma: no cover - defensive
    def merge_template_download_hints(page, learned_dl):
        return (learned_dl or {}), None
from .db import (
    db_log, db_normalize_history_title,
    queue_load, queue_upsert, queue_bulk_upsert, queue_delete,
    queue_delete_status, queue_bulk_delete, queue_bulk_update,
    queue_reorder, queue_set_priority,
)

# v3.43.60: VPN runtime integration. Keep runner.py importable for diagnostics
# if the VPN modules fail to load, but workers refuse startup below because an
# unavailable admission module cannot prove that a site has no VPN configured.
try:
    from . import vpn_runtime
    _VPN_RUNTIME_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] vpn_runtime import failed (degraded): {_e}\n")
    _VPN_RUNTIME_AVAILABLE = False

# v3.66.390 (Track-K): fail-closed proxy selection for in-process payload
# downloads. Pure decision; reuses the per-site VPN resolver the browser uses.
from .download_egress import effective_download_proxy
# F5 Phase 2 (v3.66.701): per-capture netns for the BROWSER launch. The engine
# shipped @686 and the shim @699; this is the bracket that owns a worker's
# namespace for its browser's whole lifetime (see _worker_loop).
from . import netns_isolation
from . import interstitial as _interstitial

# v3.43.60: captcha_relay — soft import. If unavailable, the per-site
# use_captcha_relay flag silently becomes a no-op.
try:
    from . import captcha_relay
    _CAPTCHA_RELAY_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] captcha_relay import failed (degraded): {_e}\n")
    _CAPTCHA_RELAY_AVAILABLE = False

# v3.43.64: MP4 metadata tagging — soft import. Workers run fine without
# mutagen; the embed step just becomes a no-op.
try:
    from . import mp4_metadata as _mp4_metadata
    _MP4_METADATA_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] mp4_metadata import failed (degraded): {_e}\n")
    _mp4_metadata = None
    _MP4_METADATA_AVAILABLE = False

# v3.43.65: tier_probe — soft import. Workers run fine without; tier
# probing just becomes a no-op (we use whatever the player picked).
try:
    from . import tier_probe as _tier_probe
    _TIER_PROBE_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] tier_probe import failed (degraded): {_e}\n")
    _tier_probe = None
    _TIER_PROBE_AVAILABLE = False

# v3.43.66: Aylo network flashvars extractor — soft import. When
# available AND the URL matches an Aylo brand domain AND
# use_aylo_extractor is enabled (default for matched sites), the
# extractor parses the flashvars block from the page HTML and picks
# the highest-quality variant per the user's quality_preference.
try:
    from . import extractors_aylo as _aylo
    _AYLO_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] extractors_aylo import failed (degraded): {_e}\n")
    _aylo = None
    _AYLO_AVAILABLE = False

# v3.43.67: Vixen Media Group extractor — soft import. Three-path
# strategy: __NEXT_DATA__ first, <video src> fallback, GraphQL POST
# (not yet implemented). Auto-enables for Vixen / Blacked / Tushy /
# Deeper / Slayed / Wifey / MilfyMD / VixenPlus / BlackedRaw /
# TushyRaw.
try:
    from . import extractors_vixen as _vixen
    _VIXEN_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] extractors_vixen import failed (degraded): {_e}\n")
    _vixen = None
    _VIXEN_AVAILABLE = False

# v3.43.69: dl8-video VR extractor + Badoink filename prediction —
# soft import. Auto-enables for TmwVRnet, BadoinkVR, BabeVR,
# VRCosplayX, 18VR, RealVR. For Badoink-family hosts the trailer
# URLs on public pages are used to predict member-area URLs via
# HEAD probing (no member-area scraping needed).
try:
    from . import extractors_dl8 as _dl8
    _DL8_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] extractors_dl8 import failed (degraded): {_e}\n")
    _dl8 = None
    _DL8_AVAILABLE = False


# v3.43.68: HereSphere/DeoVR JSON API extractor — soft import.
# Off-by-default per site (each site has to be PROBED at site-add
# time to know if either protocol is exposed). When enabled and
# the configured endpoint returns JSON for the scene-id we extract,
# we skip Playwright entirely and direct-download.
try:
    from . import extractors_jsonapi as _jsonapi
    _JSONAPI_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] extractors_jsonapi import failed (degraded): {_e}\n")
    _jsonapi = None
    _JSONAPI_AVAILABLE = False

# v3.43.72: perceptual dedup — soft import. When the `videohash`
# library is installed AND ffmpeg is on PATH, the runner eagerly
# computes a pHash for each completed download and registers it in
# the SQLite hash registry. Fail-open everywhere: missing library,
# missing ffmpeg, corrupt file, etc. all silently skip.
try:
    from . import dedup as _dedup
    _DEDUP_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] dedup import failed (degraded): {_e}\n")
    _dedup = None
    _DEDUP_AVAILABLE = False

# v3.43.73: Scrapling adaptive selectors + Turnstile bypass — soft
# import. When `scrapling` is installed, the runner can (a) recover
# broken learned selectors using content-based fingerprints, and (b)
# detect + bypass Cloudflare Turnstile challenges via StealthyFetcher.
# Both features are opt-in per site (default off until templates
# accumulate enough fingerprints to justify enabling globally).
try:
    from . import scrapling_adapter as _scrap
    _SCRAPLING_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] scrapling_adapter import failed (degraded): {_e}\n")
    _scrap = None
    _SCRAPLING_AVAILABLE = False


def _turnstile_bypass_state() -> dict:
    """Measure the exact Scrapling capability used by the runner."""
    if not (_SCRAPLING_AVAILABLE and _scrap is not None):
        return {
            "available": False,
            "status": "unknown",
            "reason": "scrapling_adapter_unavailable",
        }
    try:
        state = _scrap.capability_status()["turnstile_bypass"]
    except Exception as exc:
        return {
            "available": False,
            "status": "unknown",
            "reason": f"capability_probe_failed:{type(exc).__name__}",
        }
    if not isinstance(state, dict):
        return {
            "available": False,
            "status": "unknown",
            "reason": "capability_probe_returned_invalid_state",
        }
    return dict(state)


def _translate_failed_message(message) -> str:
    """Translate a failed-job message with any required live measurement."""
    from .friendly_error import friendly_error
    context = None
    if "turnstile" in str(message).lower():
        context = {"turnstile_bypass": _turnstile_bypass_state()}
    return friendly_error(message, context=context)


def _try_scrapling_turnstile(owner, page, ctx, url: str) -> str:
    """Run the Turnstile seam only after measuring a usable fetcher.

    The string result is intentionally diagnostic: callers currently continue
    fail-open, while tests and future telemetry can distinguish disabled,
    unavailable, unknown, not-detected, failed, and bypassed outcomes.
    """
    config = getattr(owner, "config", {}) or {}
    if not bool(config.get("use_scrapling_turnstile", False)):
        return "disabled"
    state = _turnstile_bypass_state()
    if not (state.get("status") == "available"
            and state.get("available") is True):
        status = state.get("status")
        return status if status in {"unavailable", "unknown"} else "unknown"

    try:
        html_now = page.content()
    except Exception:
        html_now = ""
    if not html_now or not _scrap.is_turnstile_page(html_now):
        return "not_detected"

    _scrap.note_turnstile_detected()
    owner.log_event(
        "turnstile_detected",
        "Cloudflare Turnstile challenge — invoking bypass",
        url=url,
    )
    try:
        ua = page.evaluate("() => navigator.userAgent")
    except Exception:
        ua = config.get("user_agent", "")
    bypass = _scrap.bypass_turnstile(url, user_agent=ua, timeout_s=60.0)
    if not (bypass.ok and bypass.cookies):
        owner.log_event(
            "turnstile_bypass_failed",
            f"bypass failed: {bypass.error}",
            url=url,
        )
        return "failed"
    try:
        ctx.add_cookies(bypass.cookies)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        owner.log_event(
            "turnstile_bypassed",
            f"bypass ok ({bypass.elapsed_s:.1f}s); "
            f"injected {len(bypass.cookies)} cookies",
            url=url,
        )
        return "bypassed"
    except Exception as exc:
        owner.log_event(
            "turnstile_inject_failed",
            f"cookies received but inject raised: {exc}",
            url=url,
        )
        return "inject_failed"

# v3.43.74: FlareSolverr client — soft import. Alternative to
# Scrapling's StealthyFetcher for Cloudflare-challenge handling;
# external service over HTTP instead of in-process Chromium spawn.
try:
    from . import flaresolverr_client as _flare
    _FLARE_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] flaresolverr_client import failed (degraded): {_e}\n")
    _flare = None
    _FLARE_AVAILABLE = False

# v3.43.74: Multi-connection chunked downloader — soft import. Opens
# N parallel byte-range connections for large files to bypass per-
# connection CDN rate limits.
try:
    from . import multi_conn as _mconn
    _MULTI_CONN_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] multi_conn import failed (degraded): {_e}\n")
    _mconn = None
    _MULTI_CONN_AVAILABLE = False

# v3.43.75: four feature modules — soft imports.
try:
    from . import playlist_extractor as _playlist
    _PLAYLIST_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] playlist_extractor import failed: {_e}\n")
    _playlist = None
    _PLAYLIST_AVAILABLE = False

try:
    from . import yt_dlp_archive as _ytdlp_arch
    _YTDLP_ARCH_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] yt_dlp_archive import failed: {_e}\n")
    _ytdlp_arch = None
    _YTDLP_ARCH_AVAILABLE = False

try:
    from . import lazy_player_wait as _lazy_player
    _LAZY_PLAYER_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] lazy_player_wait import failed: {_e}\n")
    _lazy_player = None
    _LAZY_PLAYER_AVAILABLE = False

# v3.43.76: PhoenixAdult catalog (offline brand routing) — soft import
try:
    from . import phoenix_catalog as _phoenix
    _PHOENIX_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] phoenix_catalog import failed: {_e}\n")
    _phoenix = None
    _PHOENIX_AVAILABLE = False

# v3.43.76: per-download throttling supervisor — soft import
try:
    from . import download_supervisor as _supervisor
    _SUPERVISOR_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] download_supervisor import failed: {_e}\n")
    _supervisor = None
    _SUPERVISOR_AVAILABLE = False

# v3.43.76: thumbnail generator — soft import
try:
    from . import thumbnail_gen as _thumb
    _THUMB_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] thumbnail_gen import failed: {_e}\n")
    _thumb = None
    _THUMB_AVAILABLE = False

# v3.43.77: Search-and-add by query — soft import
try:
    from . import search_extractor as _search
    _SEARCH_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner] search_extractor import failed: {_e}\n")
    _search = None
    _SEARCH_AVAILABLE = False

# ─── SITE RUNNER ─────────────────────────────────────────────────────────────
# --- leaf helpers + shared consts moved to runner_util.py (cut 1: runner decomposition) ---
# Re-exported here to preserve the bulk_downloader.runner import surface.
from .runner_util import (  # noqa: F401  -- external re-export surface
    _ts, _ts_iso, _resolve_safe, _check_video_magic_bytes, resolve_url_attribute,
    gate_candidate_url, _bump_learned_stat, _bump_per_selector,
    _maybe_demote_selectors, record_bandwidth, get_bandwidth_history,
    _bw_history, DEFAULT_MIN_RESOLUTION, _BD_TO_APPRISE_EVENT,
)


# ─── Phase 6.4: Global concurrency cap across all sites ────────────────────
# Each site independently runs up to `max_concurrent` workers, but the sum
# across all sites can saturate the host network. This semaphore caps the
# total number of URLs being actively processed at once across all sites.
# Default 0 = uncapped (per-site limits are the only constraint).
# Workers acquire the semaphore in _process_one and release after; if the
# cap is 0 we skip acquire/release entirely. Updated by the sites_config
# load and by an /api/global/concurrent endpoint.
_global_sem = None
_global_sem_size = 0
_global_sem_lock = threading.Lock()

def set_global_concurrent_cap(n):
    """Resize the global semaphore. n=0 disables the cap."""
    global _global_sem, _global_sem_size
    with _global_sem_lock:
        _global_sem_size = int(n)
        if _global_sem_size > 0:
            _global_sem = threading.BoundedSemaphore(_global_sem_size)
        else:
            _global_sem = None

def get_global_concurrent_cap():
    return _global_sem_size


# ── Phase 41.3: Manual Download Session (dedicated-thread Playwright owner)
#
# WHY THIS EXISTS: Playwright's sync API is thread-bound — a sync_playwright
# instance and the Browser/Context/Page derived from it can ONLY be accessed
# from the thread that created them. Cross-thread calls raise.
#
# The previous (broken) design launched Playwright in the Flask request
# handler thread and stashed (pw, browser, ctx) tuples on self for later
# requests to read. That worked only on older Werkzeug versions that happened
# to reuse threads — newer Werkzeug spawns a fresh thread per request, the
# original thread terminates immediately, and Playwright's subprocess gets
# garbage-collected. Symptom Matt hit: "first download browser opens then
# closes no takeover option" — exactly what GC of an abandoned subprocess
# looks like.
#
# Same fix pattern as ManualLoginSession in login.py (Phase 19.fix).


from .runner_integrations import IntegrationsMixin  # noqa: E402
from .runner_challenge import ChallengeMixin  # noqa: E402
from .runner_teach import TeachMixin  # noqa: E402
from .runner_integrity import IntegrityMixin  # noqa: E402
from .runner_manual import ManualMixin, _ManualDownloadSession  # noqa: E402
from .runner_accounts import AccountsMixin  # noqa: E402
from .runner_browser import BrowserMixin  # noqa: E402
from .runner_scheduler import SchedulerMixin  # noqa: E402
from .runner_telemetry import TelemetryMixin  # noqa: E402
from .runner_queue import QueueMixin, job_status_writer  # noqa: E402
from .runner_extractors import ExtractorsMixin  # noqa: E402
from .runner_auth import AuthMixin  # noqa: E402
from .runner_transport import TransportMixin  # noqa: E402


def _finite_config_float(raw, default):
    """Coerce a config-sourced value to a FINITE float, falling back to
    ``default`` on a non-numeric OR non-finite (NaN/inf) value.

    A bare ``float()`` accepts ``'nan'``/``'inf'``, and the numeric gates that
    consume these config values (``free < threshold``, ``target_mbps <= 0``)
    then silently misbehave: ``free < NaN`` is always False, so a NaN
    ``disk_threshold_gb`` would DISABLE the low_disk stop / disk-pressure
    throttle entirely (F-RUN01-03). Rejecting non-finite here restores the
    intended gate on a hand-edited / overlaid / corrupt config value.
    """
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(v):
        return float(default)
    return v


_RUN_LIFECYCLE_BOOTSTRAP_LOCK = threading.Lock()
_START_RECHECK_TEARDOWN = object()


class StartOutcome(str, enum.Enum):
    """Exceptional public outcomes from ``start()``.

    Successful and policy-blocked starts keep their historical ``None``
    return so existing callers remain compatible. A bounded teardown wait is
    operationally different: no replacement run was admitted, so callers that
    surface operator actions need an explicit result.
    """

    TEARDOWN_PENDING = "worker_teardown"


def _run_lifecycle_serialized(method):
    """Serialize public run transitions through one re-entrant lock."""
    @functools.wraps(method)
    def serialized(self, *args, **kwargs):
        lock = getattr(self, "_run_lifecycle_lock", None)
        if lock is None:
            # Preserve direct unbound-method use by legacy adapters/tests while
            # still making first-use initialization safe between two callers.
            with _RUN_LIFECYCLE_BOOTSTRAP_LOCK:
                lock = getattr(self, "_run_lifecycle_lock", None)
                if lock is None:
                    lock = threading.RLock()
                    self._run_lifecycle_lock = lock
        with lock:
            return method(self, *args, **kwargs)
    return serialized


class SiteRunner(TransportMixin, AuthMixin, ExtractorsMixin, QueueMixin, TelemetryMixin, SchedulerMixin, BrowserMixin, AccountsMixin, ManualMixin, IntegrityMixin, TeachMixin, ChallengeMixin, IntegrationsMixin):
    _WORKER_CLAIM_STALE = "stale"
    _WORKER_CLAIM_INELIGIBLE = "ineligible"
    _WORKER_CLAIM_PROCESSED = "processed"

    def __init__(self,site_id,config):
        # Phase 34: structured logger, scoped to this site. Used in
        # preference to sys.stderr.write for code added from Phase 34
        # onward. Older sys.stderr.write calls remain for now — they're
        # captured by the same root logger's stderr handler.
        from .log import get_logger
        self.log = get_logger("bulk_downloader.runner").with_site(site_id)
        self.site_id=site_id; self.config=config
        self.cookies=[]; self.cookie_saved_at=0.0
        self.urls=[]; self.jobs={}
        # Raw observations are retained only in memory to learn a repeated
        # per-site title template. The normalized value and winning source are
        # carried per URL to the history completion path.
        self._website_title_observations={}
        self._website_titles={}
        self._listing_titles={}
        self._lock=threading.Lock()
        self._stop=threading.Event()
        # P3-A: one-shot flag for rate-limit auto-resume. Set in
        # trigger_rate_limit(), cleared in stop(); the cooldown-wait thread
        # only re-resumes if it's still set (so an operator stop cancels it).
        self._rl_autostart=False
        self._pause=threading.Event(); self._pause.set()
        # Active HTTP transfers register their bounded daily-byte batches here
        # so operator pause/stop can persist already-written bytes even when a
        # transport iterator is blocked waiting for its next response buffer.
        self._daily_byte_accumulators_lock=threading.Lock()
        self._daily_byte_accumulators=set()
        # Phase 3: persistent worker threads pull from this queue.
        self._url_queue=queue.Queue()
        self._worker_threads=[]
        self._watchdog_thread=None
        self._state="idle"; self._login_thread=None; self._login_status=""
        # Phase 18.fix: session-recovery gate. Cleared while a re-login is
        # in flight; workers wait on this before pulling the next URL so
        # they don't drain the queue with auth-failure repeats.
        self._session_ok = threading.Event(); self._session_ok.set()
        # Timestamp of last successful cookie update. Workers compare this
        # to their own "last injected" timestamp and refresh the context's
        # cookies when newer cookies are available. Replaces the broken
        # "skip if name exists" injection that left stale session cookies.
        self._cookies_updated_at = 0.0
        # Phase 63 (v3.38.x): pre-emptive re-login tracking. When cookies
        # approach expiry, trigger a manual login session BEFORE downloads
        # start failing. _preemptive_login_attempted is a one-shot flag
        # so we don't spam the user with retry prompts on every poll.
        # Reset when cookies are refreshed (any login flow updates
        # _cookies_updated_at, which serves as the reset trigger).
        self._preemptive_login_attempted_at = 0.0
        self._consec_no_btn=0
        self._rl_until=0.0; self._rl_file=f"rl_{site_id}.json"
        self._load_rl()
        self._ss_dir=SCREENSHOTS_DIR/site_id; self._ss_dir.mkdir(parents=True, exist_ok=True)
        self._sched_stop=threading.Event()
        self._sched_thread=None
        # Serialize scheduler generation publication with bounded stop/join.
        # Deletion also marks the runner retired under this lock so a caller
        # that captured it before registry detachment cannot resurrect it.
        self._sched_lifecycle_lock=threading.RLock()
        self._sched_retired=False
        # Phase 30: auto-retry thread. Periodically scans for stuck
        # needs_review / failed jobs and bumps them back to pending
        # according to the per-site auto_retry_schedule. Stays running
        # for the runner's lifetime; per-site enable flags gate the
        # actual retry actions.
        self._auto_retry_stop=threading.Event()
        self._auto_retry_thread=None
        self._auto_retry_lifecycle_lock=threading.RLock()
        self._auto_retry_retired=False
        # Phase 13: structured event log ring buffer. Each entry is a small
        # dict {ts, url, kind, message, extra}. Capped at 500 entries to
        # prevent unbounded memory growth. The UI streams the tail via
        # /api/sites/<sid>/events polling. Existing sys.stderr.write paths
        # stay — the log MIRRORS them, doesn't replace them.
        self._event_log=collections.deque(maxlen=500)
        # Phase 42 (v3.36.10): use itertools.count for the monotonic id.
        # `self._event_seq += 1` is a read-modify-write that can interleave
        # between worker threads, causing two events to claim the same seq
        # (or skip values). `next()` on an itertools.count is a single C-level
        # operation, atomic under the GIL — no lock needed.
        # We keep `_event_seq` as a property that reads the counter's current
        # value via _peek (its private attr), since the dashboard / API reads
        # it directly to report "max seq seen."
        self._event_seq_counter = itertools.count(1)
        self._event_seq = 0  # last-issued value, updated in log_event
        # Phase 21.1: rolling completion-rate tracking for the dashboard
        # ETA. Each entry is a Unix timestamp of a job moving to "done".
        # We keep a 5-minute window; _recent_per_min is recomputed lazily
        # on dashboard polls so we don't burn CPU recomputing per-tick.
        self._recent_completions = collections.deque(maxlen=500)
        self._recent_per_min = 0.0
        # Phase 21.1: rolling throughput (bytes/sec) — sampled at HTTP
        # download level when integration tests pump bytes through.
        self._bytes_per_sec = 0.0
        # Phase 32: captcha statistics. Aggregate counters per site so
        # the UI can show success rates, average solve times, and last
        # failures without scraping the event log. Reset on runner init;
        # not persisted — these are runtime-only metrics.
        self._captcha_stats = {
            "submitted": 0,         # total challenges submitted
            "solved": 0,            # token successfully injected
            "failed": 0,            # provider returned an error
            "timeouts": 0,          # exceeded our wait deadline
            "total_solve_time_ms": 0,
            "last_solve_time_ms": 0,
            "last_failure": "",
            "last_failure_at": 0,
            "last_success_at": 0,
        }
        # Exact encounter timestamps for the dashboard's rolling 24-hour
        # count. Unlike the general 500-message event log, this deque is
        # pruned by age and records one entry per detected challenge.
        self._captcha_encounters_lock = threading.Lock()
        self._captcha_encounters = collections.deque()
        # Phase 15.7: session warming. Track the timestamp of the last
        # successful warm-up so we don't re-warm before every URL — that
        # would itself look bot-like (humans don't visit the homepage
        # before every video). We re-warm every 30 minutes.
        self._last_warmup_at = 0.0
        # v3.43.21: rolling JD-backend outcome tracking. When the site's
        # `backend` is "jd", we forward URLs to JDownloader 2 and only
        # fall back to the teach flow on failure. The deque keeps the
        # last N outcomes (True = succeeded via JD, False = JD failed
        # and we fell back) so the UI can surface a "JD plugin may be
        # broken" warning when the success rate drops. In-memory only;
        # resets on restart, which is fine — the warning would resurface
        # within the next few URLs if JD is genuinely broken on this site.
        self._jd_recent_outcomes = collections.deque(maxlen=20)
        # Per-call client cache. Constructed lazily on first JD-backed
        # URL; closed when the runner stops. None when backend != "jd"
        # so we don't waste a socket on teach-only sites.
        self._jd_client = None
        # v3.43.26: same pattern as JD but for qBittorrent. Sites with
        # backend="qbittorrent" route everything through qB; sites with
        # other backends still route torrent/magnet URLs through qB
        # automatically (via qb_bridge.looks_like_torrent_url). Either
        # way, success/failure feeds into this rolling window for the
        # health pill.
        self._qb_recent_outcomes = collections.deque(maxlen=20)
        self._qb_client = None
        # v3.43.24: per-worker heartbeat tracking for the watchdog.
        # Each worker stamps `_worker_heartbeats[worker_idx]` at the
        # top of every iteration (with a unix-seconds timestamp). The
        # watchdog thread scans this and flags any worker that hasn't
        # stamped in >15 minutes — that's a hung Chromium instance, a
        # Playwright deadlock, or a worker stuck on a uninterruptible
        # syscall. Watchdog can't kill threads safely in Python, but
        # it logs a worker_hung event so the user sees it in the
        # event log and can choose to restart the site.
        self._worker_heartbeats = {}   # {worker_idx: last_beat_unix_seconds}
        self._worker_current_urls = {} # {worker_idx: url currently processing}
        self._worker_url_generations = {} # {worker_idx: run generation}
        self._worker_run_generation = 0
        self._worker_generation_invalidated = False
        self._worker_context = threading.local()
        self._job_progress_samples = {} # {url: {bytes, at, bps}}
        self._job_status_version = 0
        self._completion_notification_token = None
        self._completion_notification_serial = 0
        self._claimed_completion_notification = None
        # Run transitions use this outermost lock. Any operation needing more
        # than one lock follows: lifecycle -> jobs -> heartbeats -> queue.
        # RLock is intentional: overseer finalization atomically calls start().
        self._run_lifecycle_lock = threading.RLock()
        self._run_retired = False
        # Auth/manual launch calls can spend tens of seconds constructing a
        # thread-owned browser before publishing its session handle. Retain the
        # caller generation across that gap so deletion can fail closed rather
        # than miss an in-flight owner.
        self._auxiliary_start_threads = {}
        self._worker_heartbeats_lock = threading.Lock()
        # v3.43.24: watchdog populates this with {worker_idx, last_beat_age_s}
        # dicts for any worker that hasn't heartbeat in >15min. Empty
        # in the healthy case. Exposed via get_status() so the UI can
        # surface a per-site warning.
        self._hung_workers = []
        # Phase 17.19: auto chunk-size tuning. Track recent download
        # throughput as an EWMA (exponentially-weighted moving average) so
        # we can pick an optimal chunk size for the next download. Bigger
        # chunks reduce per-iteration overhead (fewer Python-level loops)
        # but cost more memory and reduce pause/stop responsiveness; the
        # right size depends on the actual link speed.
        #   Rule of thumb: chunk ≈ 0.5 seconds of throughput, clamped to
        #   [256 KiB, 64 MiB]. So a 100 MB/s link gets 50 MB chunks; a
        #   2 MB/s link gets 1 MB chunks. Updates after every successful
        #   download.
        self._throughput_ewma_bps = 0.0  # exponentially-weighted bytes/sec
        self._throughput_samples = 0     # for warmup detection
        # Phase 6.5: account rotation. Pick the first non-cooled-down
        # account at startup (or 0 if none configured / all cooled down).
        self._active_account_idx=0
        accounts=config.get("accounts") or []
        if accounts:
            now=time.time()
            for i,a in enumerate(accounts):
                if float(a.get("cooldown_until",0) or 0)<=now:
                    self._active_account_idx=i; break
            # Push active account creds into top-level config so existing
            # login code paths (which read username/password) just work.
            a=accounts[self._active_account_idx]
            config["username"]=a.get("username","")
            config["password"]=a.get("password","")
            config["cookie_file"]=a.get("cookie_file","")
        # Phase 4.2: restore queue state from SQLite.
        self._restore_queue()
        self.start_scheduler()
        # Phase 30: start the auto-retry scanner. Runs for the runner's
        # lifetime; per-tick checks per-site config flags before acting.
        self._start_auto_retry()
        # Phase 41.5: explicit defaults for session-state attributes so
        # readers don't have to use getattr() everywhere. These were
        # previously implicit (set lazily by start_manual_login etc.) which
        # made it easy to typo the attribute name and never notice.
        self._manual_download_session = None
        self._manual_login_handle = None
        self._manual_snapshot_thread = None
        self._manual_snapshot_stop = None
        self._captcha_solve_sessions = {}
        self._auto_teach_logged = False
        # GH-2a (v3.66.693): opt-in yt-dlp @extractor adapter. A site that sets
        # the *undeclared* site-cfg key `ytdlp_extractor` truthy registers a
        # yt-dlp info-probe shim under its site_id, so the 691 plugin dispatch
        # (_try_plugin_extractor) routes it through yt-dlp's -j info JSON
        # (progressive http only; HLS/DASH defers). Undeclared cfg key ->
        # invisible to the config/env inventory (a backend-only opt-in). Guarded
        # so a registration hiccup can never break runner construction.
        try:
            from . import ytdlp_extractor as _yte
            _yte.maybe_register_from_config(site_id, config)
        except Exception as _e:  # noqa: BLE001
            self.log.warning("ytdlp_extractor opt-in registration failed: %s", _e)






    def _scrape_listing_urls(self, listing_url):
        """Phase 73: same scrape logic as /api/scrape_listing endpoint —
        fetch the listing page, extract video-looking URLs. Reused here
        for the subscription scanner so the behavior matches.

        AUDIT FIX (v3.42.0): SSRF defence — reject private/loopback IPs.
        Subscriptions are config-driven; an operator with write access
        could already shell to the box, but it's still better hygiene
        to fail safely if config-by-mistake points at internal services."""
        # v3.66.540 (F-RUN01-01): route the listing-scrape SSRF check through the
        # single canonical predicate (provider_resolve_impl._common._is_safe_public_host,
        # fixed for RFC 6598 CGNAT under VR-P15 @524) instead of a local denylist
        # copy that missed 100.64.0.0/10. Same public/private semantics, one guard.
        try:
            from urllib.parse import urlparse as _up
            from bulk_downloader.provider_resolve_impl._common import (
                _is_safe_public_host,
            )
            p = _up(listing_url)
            if p.scheme not in ("http", "https"):
                raise RuntimeError("scheme must be http or https")
            host = p.hostname or ""
            if not host:
                raise RuntimeError("invalid host")
            ok, reason = _is_safe_public_host(host)
            if not ok:
                raise RuntimeError(f"refusing to scrape non-public address: {reason}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"URL validation failed: {e}")
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        # v3.66.392 (VPN-CONTROLPLANE): route the listing scrape through the
        # same fail-closed VPN proxy as the payload path -- never scrape a
        # vpn_required site's listing on the clear interface when the tunnel
        # is down.
        try:
            _cp_proxy = self._download_proxy_url()
        except Exception as _cpe:
            if _VPN_RUNTIME_AVAILABLE and isinstance(
                    _cpe, vpn_runtime.VPNRequiredError):
                raise RuntimeError(
                    "VPN required for this site but the tunnel is down -- "
                    "refusing clear-interface listing scrape")
            raise
        try:
            # AUDIT FIX: no redirect following — same reasoning as Phase 71.
            with httpx.Client(timeout=30.0, follow_redirects=False,
                              proxy=_cp_proxy) as cl:
                r = cl.get(listing_url, headers=headers)
                if r.status_code in (301, 302, 303, 307, 308):
                    raise RuntimeError(f"got {r.status_code} redirect — set subscription URL to the final destination")
                r.raise_for_status()
                html = r.text
                # AUDIT FIX: cap response size
                if len(html) > 5 * 1024 * 1024:
                    html = html[: 5 * 1024 * 1024]
        except httpx.HTTPError as e:
            raise RuntimeError(f"fetch failed: {type(e).__name__}: {e}")
        import re as _re
        from urllib.parse import urljoin, urlparse
        hrefs = _re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, _re.I)
        VIDEO_EXT = _re.compile(r"\.(mp4|mkv|webm|avi|mov|m3u8|mpd|ts|flv)(\?|#|$)", _re.I)
        VIDEO_PATTERNS = _re.compile(r"/(video|watch|v|play|movie|episode|stream)/", _re.I)
        LISTING_PATTERNS = _re.compile(r"/(category|categories|tag|tags|page|search|browse|list|channel|playlist|feed|sitemap)/", _re.I)
        seen, found = set(), []
        for href in hrefs:
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            absolute = urljoin(listing_url, href)
            if not absolute.startswith("http"): continue
            if absolute in seen: continue
            is_video = bool(VIDEO_EXT.search(absolute) or VIDEO_PATTERNS.search(absolute))
            if not is_video: continue
            if LISTING_PATTERNS.search(absolute):
                try:
                    last = urlparse(absolute).path.rstrip("/").rsplit("/", 1)[-1]
                    if not last.isdigit():
                        continue
                except Exception: pass
            seen.add(absolute); found.append(absolute)
            if len(found) >= 500: break  # safety cap
        return found




    def update_config(self,cfg):
        """Swap in a new config dict and restart the scheduler so the
        new values (max_concurrent, headless, etc.) take effect. Workers
        currently in flight finish their URL first, then pick up the new
        config when they pull the next URL."""
        with self._lock: self.config=cfg
        self.stop_scheduler(); self.start_scheduler()

    def set_cookies_from_file(self,path):
        """Load Playwright-format cookies from `path` into this runner.
        Returns (ok, message). Updates _cookies_updated_at so any worker
        with a live browser context refreshes its injected cookies on the
        next URL pull (see Phase 18.fix)."""
        try:
            self.cookies=load_cookies_from_file(path)
            self.cookie_saved_at=Path(path).stat().st_mtime
            # Phase 18.fix: signal workers that cookies are fresh
            self._cookies_updated_at = time.time()
            return True,f"Loaded {len(self.cookies)} cookies"
        except Exception as e: return False,str(e)

    def set_cookies(self,cl):
        """Replace the runner's cookie list in memory and bump the
        refresh timestamp. Used when login produces fresh cookies that
        haven't been written to disk yet."""
        self.cookies=cl
        self.cookie_saved_at=time.time()
        # Phase 18.fix: signal workers that cookies are fresh
        self._cookies_updated_at = time.time()

    def cookie_info(self):
        """Return a snapshot dict describing cookie health for the UI:
        {expired, session, count, age}. The age is human-readable like
        '3h ago'; the rest are numeric counts."""
        info=cookies_expiry_info(self.cookies)
        info["age"]=cookie_age_str(self.cookie_saved_at); info["count"]=len(self.cookies)
        return info







    # ── v3.49 (#5 / #55): Bulk pause/resume/retry/reorder ──────────────
    # Each operates on a set of URLs in a single lock acquisition. Returns
    # the count of jobs ACTUALLY transitioned (which may be less than the
    # set size — a "running" job can't be retried directly, an "stopped"
    # job is already paused, etc).









    # Alias so the action endpoint generator in app.py — which derives method
    # names from URL action names — finds it. The endpoint /api/sites/<sid>/retry
    # was crashing with AttributeError before this; "retry" is the public name
    # while "retry_failed" is the historical implementation name.

    # Same fix for /api/sites/<sid>/clear which expected a method literally
    # named clear. Historical implementation is clear_completed.



    def _begin_auxiliary_start(self):
        """Register the current auth/manual launcher before it can block."""
        with self._run_lifecycle_lock:
            if self._run_retired:
                return False
            thread = threading.current_thread()
            starts = self._auxiliary_start_threads
            starts[thread] = starts.get(thread, 0) + 1
            return True

    def _end_auxiliary_start(self):
        """Release a launcher, retaining its handle after retirement."""
        with self._run_lifecycle_lock:
            thread = threading.current_thread()
            starts = self._auxiliary_start_threads
            count = starts.get(thread, 0)
            if count <= 0 or self._run_retired:
                # A retired caller is still alive until its wrapper returns.
                # Keep that Thread identity for the next teardown proof.
                return
            if count == 1:
                starts.pop(thread, None)
            else:
                starts[thread] = count - 1

    def _start_owned_auxiliary_thread(self, attribute, thread):
        """Atomically publish/start one auxiliary generation or refuse it."""
        with self._run_lifecycle_lock:
            if self._run_retired:
                return False
            setattr(self, attribute, thread)
            try:
                thread.start()
            except BaseException:
                if getattr(self, attribute, None) is thread:
                    setattr(self, attribute, None)
                raise
            return True

    def _start_tracked_auxiliary_thread(self, thread):
        """Publish a callback-only thread in the shared auxiliary registry."""
        with self._run_lifecycle_lock:
            if self._run_retired:
                return False
            starts = self._auxiliary_start_threads
            starts[thread] = starts.get(thread, 0) + 1
            try:
                thread.start()
            except BaseException:
                starts.pop(thread, None)
                raise
            return True

    def _finish_tracked_auxiliary_thread(self, thread):
        with self._run_lifecycle_lock:
            if not self._run_retired:
                self._auxiliary_start_threads.pop(thread, None)

    def start(self):
        # A delete transaction permanently retires this object before waiting
        # for its workers.  This fast path handles stale action callbacks; the
        # serialized recheck below closes the race with retirement itself.
        if getattr(self, "_run_retired", False):
            return StartOutcome.TEARDOWN_PENDING
        # A stopped generation may still be unwinding a browser owned by one
        # of its worker threads. Wait outside the lifecycle lock: a worker
        # that reached a status writer just before stop must be able to enter
        # that lock, observe its stale generation, and exit. Browser teardown
        # happens in the worker's finally block before Thread.join returns.
        while True:
            teardown_generation = None
            worker_lock = getattr(self, "_worker_heartbeats_lock", None)
            if worker_lock is not None:
                with worker_lock:
                    if getattr(self, "_worker_generation_invalidated", False):
                        teardown_generation = self._worker_run_generation
            elif getattr(self, "_worker_generation_invalidated", False):
                teardown_generation = getattr(self, "_worker_run_generation", 0)

            captured_watchdog = None
            if (getattr(self, "_watchdog_thread", None) is not None
                    and getattr(self, "_state", None) != "running"):
                # A completed run can leave its watchdog sleeping for up to a
                # minute.  Claim and signal that exact identity under the run
                # lock before a replacement is allowed to clear `_stop`.
                with self._run_lifecycle_lock:
                    if self._state != "running":
                        captured_watchdog = self._watchdog_thread
                        if captured_watchdog is not None:
                            self._stop.set()

            if teardown_generation is not None or captured_watchdog is not None:
                captured_workers = tuple(getattr(self, "_worker_threads", ()))
                current_thread = threading.current_thread()
                wait_budget = max(0.0, _finite_config_float(
                    self.config.get("worker_teardown_wait_s", 5.0), 5.0))
                deadline = time.monotonic() + wait_budget
                for worker in captured_workers:
                    if worker is current_thread:
                        continue
                    try:
                        worker.join(
                            timeout=max(0.0, deadline - time.monotonic()))
                    except (RuntimeError, AttributeError):
                        pass
                still_alive = []
                for worker in captured_workers:
                    try:
                        if worker.is_alive():
                            still_alive.append(worker)
                    except AttributeError:
                        pass
                watchdog_alive = False
                if captured_watchdog is not None:
                    if captured_watchdog is current_thread:
                        watchdog_alive = True
                    else:
                        try:
                            captured_watchdog.join(
                                timeout=max(0.0, deadline - time.monotonic()))
                        except (RuntimeError, AttributeError):
                            pass
                        try:
                            watchdog_alive = captured_watchdog.is_alive()
                        except AttributeError:
                            watchdog_alive = True
                if still_alive:
                    log = getattr(self, "log", None)
                    if log is not None:
                        log.warning(
                            "start refused: %d prior worker(s) still tearing down",
                            len(still_alive))
                    return StartOutcome.TEARDOWN_PENDING
                if watchdog_alive:
                    log = getattr(self, "log", None)
                    if log is not None:
                        log.warning(
                            "start refused: prior watchdog still tearing down")
                    return StartOutcome.TEARDOWN_PENDING
                if captured_watchdog is not None:
                    with self._run_lifecycle_lock:
                        if self._watchdog_thread is captured_watchdog:
                            self._watchdog_thread = None

            outcome = self._start_serialized(
                _teardown_generation=teardown_generation)
            if outcome is _START_RECHECK_TEARDOWN:
                continue
            return outcome

    @_run_lifecycle_serialized
    def _start_serialized(self, _teardown_generation=None):
        if getattr(self, "_run_retired", False):
            return StartOutcome.TEARDOWN_PENDING
        if self._state=="running": return
        # A fresh run must not inherit liveness state from worker threads that
        # belonged to the previous run. Guard both maps with the same lock so
        # the watchdog never observes a half-reset worker snapshot.
        with self._worker_heartbeats_lock:
            if getattr(self, "_worker_generation_invalidated", False):
                if _teardown_generation != self._worker_run_generation:
                    return _START_RECHECK_TEARDOWN
                self._worker_generation_invalidated = False
            else:
                self._worker_run_generation += 1
            run_generation = self._worker_run_generation
            self._worker_heartbeats.clear()
            self._worker_current_urls.clear()
            self._worker_url_generations.clear()
            self._hung_workers = []
        # Progress samples are guarded separately by the jobs lock. Never hold
        # both locks at once: _update_job follows the same one-way ordering.
        with self._lock:
            self._job_progress_samples.clear()
            self._completion_notification_token = None
        if self.is_rate_limited(): return
        # v3.43.41: download window check. When the site is configured
        # with active hours (window_enabled=True), refuse to start
        # outside those hours. The window scheduler thread will call
        # start() again when the next active window arrives.
        try:
            from . import download_window as _dw
            if not _dw.site_in_window(self.config):
                if self._state != "window_paused":
                    self._state = "window_paused"
                    self.log_event("window",
                        "Outside active hours — workers paused. "
                        f"Configured windows: {self.config.get('window_active_hours','')}")
                return
        except Exception as e:
            # Window module failure must NOT block start() — fail-open
            sys.stderr.write(
                f"[{self.site_id}] window check failed, allowing start: {e}\n")
        # v3.43.80 Phase 160: maintenance-window pause check. If an
        # active maintenance window lists "workers" or "all" in its
        # actions_paused, don't pick up new URLs. In-flight downloads
        # complete naturally; only new pickups are gated. Fail-open
        # so a broken maintenance module never silently halts the
        # queue.
        try:
            from . import maintenance as _mw
            if _mw.is_action_paused("workers"):
                if self._state != "maintenance_paused":
                    self._state = "maintenance_paused"
                    self.log_event(
                        "maintenance",
                        "Active maintenance window pauses workers. "
                        "Scheduled work will resume when window ends.")
                return
        except Exception as e:
            sys.stderr.write(
                f"[{self.site_id}] maintenance check failed, "
                f"allowing start: {e}\n")
        # v3.44.4 Phase 184: smart-wakeup check. Per-site quiet-hours
        # consultation — if the operator has configured quiet hours
        # and we're in one, defer to the wakeup module's decision.
        # This is OPT-IN via `use_smart_wakeup` config flag so existing
        # sites are unaffected. Fail-open: any module exception falls
        # through to normal start logic. Placed AFTER the maintenance
        # check so it can't push any earlier test-pinned strings past
        # their character-offset windows.
        if self.config.get("use_smart_wakeup", False):
            try:
                from . import smart_wakeup as _sw
                quiet = self.config.get("smart_wakeup_quiet_hours") or []
                decision = _sw.should_wake_now(
                    quiet_hours=quiet)
                # v3.66.522 (VR-P07): should_wake_now's contract is
                # {"wake": bool, ...} -- it has no site_id param (the old
                # site_id= kwarg raised a swallowed TypeError -> "allowing
                # start", smart_wakeup dead-when-enabled) and never returns a
                # "decision"/"stay_asleep" key (the old check could never fire
                # even with the TypeError gone). Defer when wake is False.
                if decision and not decision.get("wake"):
                    if self._state != "wakeup_deferred":
                        self._state = "wakeup_deferred"
                        self.log_event(
                            "smart_wakeup",
                            f"Wake deferred: {decision.get('reason', '')}")
                    return
            except Exception as e:
                sys.stderr.write(
                    f"[{self.site_id}] smart_wakeup check failed, "
                    f"allowing start: {e}\n")
        # Track F1-A (F1.3): cookie-expiry admission. Opt-in via
        # `cookie_admission_enabled`. When a site's auth jar has fully
        # lapsed, hold new pickups rather than admitting a burst that would
        # all redirect-to-login. The shared `admission_hold` seam ALSO knows
        # about disk (F1.2), but the inline low-disk block just below remains
        # the runtime actor for that path (it owns the fire_event hook), so
        # here we only act on the cookie reason. Fail-open: any failure
        # allows start to proceed.
        try:
            from . import admission as _adm
            _hold = _adm.admission_hold(self.config)
            if _hold == "cookies_expired":
                if self._state != "cookies_expired":
                    self._state = "cookies_expired"
                    self.log_event("admission",
                        "Auth cookies expired — holding new work until relogin.")
                return
        except Exception as e:
            sys.stderr.write(
                f"[{self.site_id}] admission check failed, allowing start: {e}\n")
        # Row 390: the DURABLE download hold. Every gate above this line fails
        # OPEN on error, because each is a convenience gate. This one does NOT.
        # An operator hold placed with /api/pause_all lived only in process
        # memory, so deploy.sh -- which restarts the app on every deployment --
        # silently re-armed unattended downloading on exactly the hosts that
        # restart most. start() is the only path into a worker pool, so this is
        # where the hold is re-applied after a restart, before any download can
        # begin. downloads_allowed() returns False for BOTH a recorded hold and
        # an unmeasurable one (CLAUDE.md A7: UNKNOWN never resolves to "no hold,
        # carry on"), and never raises, so there is no fail-open branch to add.
        _hold_allowed, _hold_state = _download_hold.downloads_allowed()
        if not _hold_allowed:
            _token = _download_hold.runner_state_token(_hold_state)
            if self._state != _token:
                self._state = _token
                self.log_event(
                    "download_hold",
                    "Downloads are held; refusing to start workers "
                    f"({_hold_state.get('state')}: {_hold_state.get('reason')})",
                    extra={"hold_state": _hold_state.get("state"),
                           "hold_reason": _hold_state.get("reason"),
                           "hold_detail": _hold_state.get("detail")})
            return
        dl_dir=self.config.get("download_dir","")
        threshold=_finite_config_float(self.config.get("disk_threshold_gb",2.0), 2.0)
        if dl_dir:
            free=disk_free_gb(dl_dir)
            if free is not None and free<threshold:
                self._state="low_disk"
                # Phase 20: notify operator via configured hooks. This
                # only fires when start() detects low disk; doesn't repeat
                # while staying in low_disk state.
                try:
                    from .hooks import fire_event
                    fire_event("low_disk", self.config, job={
                        "message": f"Disk free {free:.1f}GB < threshold {threshold}GB on {dl_dir}",
                    }, extra={"disk_free_gb": free, "threshold_gb": threshold,
                             "download_dir": dl_dir})
                except Exception as e:
                    self.log.warning("fire_event(low_disk) failed: %s", e)
                return
        # Phase 65 (v3.38.x): per-site disk quota. Independent of the
        # SYSTEM disk free threshold above — this is a USED-bytes cap.
        # Useful when one volume hosts multiple sites and you want to
        # prevent any single site from monopolizing the volume.
        quota_gb = self.config.get("site_quota_gb")
        if quota_gb and dl_dir:
            try:
                quota_bytes = int(float(quota_gb) * 1024**3)
                used = self._compute_site_usage(dl_dir)
                if used is None:
                    self._state = "quota_usage_unknown"
                    self.log_event(
                        "quota_unknown",
                        "Site quota usage unavailable; refusing to start workers",
                        extra={"download_dir": dl_dir,
                               "quota_gb": float(quota_gb)},
                    )
                    return
                if used >= quota_bytes:
                    self._state = "low_disk"
                    self.log_event("quota_hit",
                        f"Site quota exceeded: {used/1024**3:.1f}GB used >= {quota_gb}GB limit")
                    try:
                        from .hooks import fire_event
                        fire_event("low_disk", self.config, job={
                            "message": f"Site quota: {used/1024**3:.1f}GB / {quota_gb}GB used",
                        }, extra={"used_gb": used/1024**3, "quota_gb": float(quota_gb)})
                    except Exception:
                        pass
                    return
            except (TypeError, ValueError):
                pass  # bad quota config, ignore
        with self._lock:
            # v3.36.8: build position map once to avoid O(n²) self.urls.index()
            # calls inside the sort key. For Matt's 2,875-URL queue, the old
            # code did ~65,000 linear scans of self.urls; this is now a single
            # dict construction + O(1) lookups.
            pos = {u: i for i, u in enumerate(self.urls)}
            pending_urls = [u for u, j in self.jobs.items()
                            if j["status"] == "pending"
                            and time.time() >= j.get("retry_after", 0)]
            # v3.43.80 Phase 157: optional priority scoring. When enabled,
            # within each priority tier (high vs not), URLs are ordered by
            # queue_priority score (freshness, site preference, cookie
            # quality, etc.) instead of just enqueue position. Off by
            # default — feature flag use_priority_scoring on the site cfg.
            # Context-gather (DB reads) happens once for the whole batch,
            # NOT per-URL in the sort key.
            url_scores: dict = {}
            if self.config.get("use_priority_scoring", False) and pending_urls:
                try:
                    from . import queue_priority as _qp
                    # Snapshot of all sites; queue_priority needs the dict
                    # to compute cookie-quality context. Pull from app if
                    # available, otherwise just our own entry.
                    try:
                        from .app_state import s_cfg as _all_sites
                        s_cfg_snap = dict(_all_sites)
                    except Exception:
                        s_cfg_snap = {self.site_id: self.config}
                    url_scores = _qp.score_urls_in_memory(
                        urls=pending_urls,
                        site_id=self.site_id,
                        jobs=self.jobs,
                        s_cfg=s_cfg_snap,
                    )
                except Exception as e:
                    sys.stderr.write(
                        f"[{self.site_id}] priority scoring failed: {e}\n")
                    url_scores = {}
            pending = sorted(
                pending_urls,
                key=lambda u: (
                    0 if self.jobs[u].get("priority") == "high" else 1,
                    # Score descending (higher score = pick sooner)
                    -url_scores.get(u, 100.0),
                    pos.get(u, 9999),
                ))
        if not pending: return

        # Phase 41.5: auto-teach pre-flight. If auto_teach_first_run is on
        # AND we have no learned download selectors yet, spawning workers
        # is a waste — they'd open browsers, immediately mark the first URL
        # needs_review, then _watch_done would tear them down because the
        # queue is drained. The user sees Chromium briefly pop up and
        # vanish, with no obvious way to recover.
        #
        # Instead: mark the first URL needs_review DIRECTLY here. The Take
        # Over button appears on the row, user clicks it, teach flow runs.
        # finish_manual_download/teach_commit re-enqueue URLs and call
        # start() again, at which point has_dl is True and we go through
        # the normal worker spawn path.
        if self.config.get("auto_teach_first_run", True):
            learned_dl = (self.config.get("learned") or {}).get("download") or {}
            has_dl = bool(learned_dl.get("trigger_selectors") or learned_dl.get("row_selectors"))
            # v3.62.2: a site with an applied template is treated as
            # ready-to-run — skip the first-run teach prompt entirely.
            # The template's selectors get exercised by the normal worker
            # path; if they fail at download time, the regular
            # download-failure flow still falls back to teach. This stops
            # a teach window from opening on every first run of a site
            # the operator already templated.
            has_template = bool(self.config.get("applied_template"))
            if not has_dl and not has_template:
                # If a URL is ALREADY in needs_review with auto_teach_seen,
                # don't flag another one — user just needs to teach the
                # existing one. Clicking Start again while waiting for teach
                # should be a no-op, not a "flag another URL" action.
                with self._lock:
                    already_teaching = any(
                        j.get("status") == "needs_review" and j.get("auto_teach_seen")
                        for j in self.jobs.values()
                    )
                if already_teaching:
                    self._state = "idle"
                    sys.stderr.write(
                        "  start: already waiting for teach — no-op\n")
                    return
                first_url = pending[0]
                self._update_job(first_url, "needs_review",
                    "Auto-teach: take over to teach download selectors. "
                    "Click 'Take over' on this row, then complete the download "
                    "in the popup browser and click 'I'm Done'.",
                    auto_teach_seen=True)
                if not getattr(self, "_auto_teach_logged", False):
                    self.log_event("auto_teach",
                        "First URL needs selector teaching. Click 'Take over' "
                        "to begin. Workers will start once selectors are saved.",
                        url=first_url)
                    self._auto_teach_logged = True
                # Don't spawn workers — leave runner in idle state. The
                # user's Take Over → I'm Done flow will call start() again
                # via finish_manual_download (which re-enqueues and starts).
                self._state = "idle"
                sys.stderr.write(
                    f"  start: deferred worker spawn — {len(pending)} URL(s) "
                    f"waiting for selector teach\n")
                return

        # Drain any leftover items from a previous run, then enqueue. (F1:
        # the drain repays unfinished_tasks so _watch_done's `==0` gate stays
        # reachable after a stop->start mid-queue.)
        with self._worker_heartbeats_lock:
            if run_generation != self._worker_run_generation:
                return
            self._stop.clear(); self._pause.set(); self._state="running"; self._consec_no_btn=0
            self._drain_url_queue()
            for u in pending:
                self._url_queue.put((run_generation, u))
        n=max(1,int(self.config.get("max_concurrent", DEFAULT_MAX_CONCURRENT)))
        # Spawn N persistent worker threads. Each owns one playwright/browser
        # and serves URLs from the queue until stop or queue exhaustion.
        # worker_idx is passed so each worker gets its own profile dir
        # (Chrome won't let two processes share one).
        self._worker_threads=[
            threading.Thread(target=self._worker_loop,args=(i,run_generation),daemon=True,name=f"dl-{self.site_id}-{i}")
            for i in range(n)]
        for t in self._worker_threads: t.start()
        worker_snapshot = tuple(self._worker_threads)
        threading.Thread(
            target=self._watch_done,
            args=(run_generation, worker_snapshot),
            daemon=True).start()
        # v3.43.24: spawn watchdog thread. Polls worker heartbeats every
        # 60s, logs a worker_hung event if any worker hasn't stamped
        # in 15min. Can't safely kill threads in Python, so we don't
        # try — just surface the signal loudly so the user can choose
        # to restart the site.
        watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            args=(run_generation,),
            daemon=True,
            name=f"watchdog-{self.site_id}")
        self._watchdog_thread = watchdog_thread
        try:
            watchdog_thread.start()
        except BaseException:
            if self._watchdog_thread is watchdog_thread:
                self._watchdog_thread = None
            raise

    def _publish_watchdog_snapshot(self, run_generation, beats, hung_workers):
        """Publish only a still-current heartbeat snapshot for this run."""
        with self._worker_heartbeats_lock:
            if run_generation != self._worker_run_generation:
                return False
            if beats != self._worker_heartbeats:
                return False
            self._hung_workers = list(hung_workers)
            return True

    def _watchdog_loop(self, run_generation=None):
        """v3.43.24: monitor worker heartbeats. Threads should stamp
        every iteration (~1s under load, longer when blocked on the
        URL queue). A stamp older than 15min means the worker is hung.

        We can't kill the thread in Python, but we can:
          1. Log a worker_hung event so the user sees it
          2. Track in self._hung_workers so /api/status surfaces it
          3. Emit a stderr warning so logs make the cause visible

        The watchdog itself runs on a fixed 60s cadence — frequent
        enough to catch hangs while they matter, infrequent enough
        to not noise up the event log."""
        HUNG_THRESHOLD_S = 15 * 60  # 15 minutes
        POLL_INTERVAL_S = 60
        already_flagged = set()
        if run_generation is None:
            with self._worker_heartbeats_lock:
                run_generation = self._worker_run_generation
        while not self._stop.is_set():
            with self._worker_heartbeats_lock:
                if run_generation != self._worker_run_generation:
                    return
            # Use stop.wait() so we exit promptly on shutdown
            if self._stop.wait(POLL_INTERVAL_S):
                return
            now = time.time()
            with self._worker_heartbeats_lock:
                if run_generation != self._worker_run_generation:
                    return
                beats = dict(self._worker_heartbeats)
            hung_workers = []
            for widx, last_beat in beats.items():
                age_s = now - last_beat
                if age_s > HUNG_THRESHOLD_S:
                    hung_workers.append({
                        "worker_idx": widx,
                        "last_beat_age_s": round(age_s, 1),
                    })
            if not self._publish_watchdog_snapshot(
                    run_generation, beats, hung_workers):
                continue
            hung_ids = {item["worker_idx"] for item in hung_workers}
            already_flagged = {
                key for key in already_flagged if key[0] in hung_ids}
            for item in hung_workers:
                widx = item["worker_idx"]
                last_beat = beats[widx]
                key = (widx, int(last_beat))
                if key in already_flagged:
                    continue
                already_flagged.add(key)
                age_s = item["last_beat_age_s"]
                self.log_event("worker_hung",
                    f"Worker {widx} has not heartbeat in "
                    f"{int(age_s/60)}min — likely hung. "
                    f"Consider stopping and restarting the site.")
                sys.stderr.write(
                    f"[{self.site_id}] WATCHDOG: worker {widx} "
                    f"hung for {int(age_s/60)}min\n")

    def _effective_concurrency(self):
        """Phase 64 (v3.41.0): bandwidth-aware concurrency. If
        bandwidth_target_mbps is set, return a worker count scaled
        toward that target based on recent observed throughput.

        Algorithm: simple step controller.
          - If current throughput < 80% of target, scale up by 1.
          - If current throughput > 120% of target, scale down by 1.
          - Bounds: [1, max_concurrent].
          - Step rate-limited to once per 30 seconds to prevent flapping.

        When bandwidth_target_mbps is 0/None/missing, returns the static
        max_concurrent value (legacy behavior).

        v3.43.80 Phase 85: disk-pressure soft throttle applies on top.
        When free space is within 2× the hard threshold, the cap is
        scaled down proportionally (75% reduction at the threshold).
        This gives the operator a smooth ramp-down before the hard
        low_disk stop fires, instead of slamming straight into it."""
        cap = max(1, int(self.config.get("max_concurrent", DEFAULT_MAX_CONCURRENT)))
        # v3.43.80 Phase 85: disk-pressure factor first. Cheap readonly
        # check, fail-open on any error. Applied as a CEILING to whatever
        # the bandwidth controller decides below.
        try:
            dl_dir = self.config.get("download_dir", "")
            if dl_dir:
                threshold = _finite_config_float(self.config.get("disk_threshold_gb", 2.0), 2.0)
                free = disk_free_gb(dl_dir)
                if free is not None and free < threshold * 2.0:
                    # Pressure 1.0 at the hard threshold (where low_disk fires);
                    # 0.0 at 2× the threshold. Scales the cap by up to 75%.
                    pressure = max(0.0, min(1.0, 1.0 - max(0.0, free - threshold) / threshold))
                    disk_cap = max(1, int(round(cap * (1.0 - pressure * 0.75))))
                    if disk_cap < cap:
                        now = time.time()
                        if (now - getattr(self, "_disk_throttle_log_ts", 0)) > 600:
                            self.log_event("disk_throttle",
                                f"Disk pressure: {free:.1f}GB free < {threshold*2:.1f}GB; "
                                f"scaling workers from {cap} to {disk_cap} (pressure {pressure:.0%})")
                            self._disk_throttle_log_ts = now
                        cap = disk_cap
        except Exception:
            pass  # never let a disk read break the worker controller
        target_mbps = _finite_config_float(self.config.get("bandwidth_target_mbps", 0) or 0, 0.0)
        if target_mbps <= 0:
            return cap
        # Initialize state on first call
        if not hasattr(self, "_eff_conc_state"):
            self._eff_conc_state = {"workers": cap, "last_step": 0.0}
        st = self._eff_conc_state
        now = time.time()
        if now - st["last_step"] < 30:
            return st["workers"]  # rate-limit decisions
        # AUDIT FIX (v3.42.0): the original code read self._bytes_per_sec which
        # is a stub that's never updated, making the controller always think
        # throughput is 0 and scale up to max. Use the EWMA tracker that
        # _observe_throughput actually populates after each download.
        bytes_per_sec = getattr(self, "_throughput_ewma_bps", 0.0) or 0.0
        current_mbps = bytes_per_sec * 8 / 1024 / 1024  # bytes → megabits
        # Don't make scaling decisions until we have ≥2 throughput samples
        # — first sample is the worst-case "we just started downloading"
        # noise and would push us toward max workers prematurely.
        if getattr(self, "_throughput_samples", 0) < 2:
            return st["workers"]
        if current_mbps < target_mbps * 0.8 and st["workers"] < cap:
            st["workers"] = min(cap, st["workers"] + 1)
            st["last_step"] = now
            self.log_event("bandwidth_scale",
                f"Scaling up workers to {st['workers']} (throughput {current_mbps:.1f} < target {target_mbps:.1f} Mbps)")
        elif current_mbps > target_mbps * 1.2 and st["workers"] > 1:
            st["workers"] = max(1, st["workers"] - 1)
            st["last_step"] = now
            self.log_event("bandwidth_scale",
                f"Scaling down workers to {st['workers']} (throughput {current_mbps:.1f} > target {target_mbps:.1f} Mbps)")
        # v3.43.80 Phase 85: disk-pressure cap was applied at the top of
        # this method; respect it here by clamping bandwidth's decision.
        return min(cap, st["workers"])

    def pause(self):
        """Pause the worker pool. Workers finish the URL they're currently
        on (or interrupt at the next chunk boundary in _http_download), then
        block on self._pause until resume() is called. Idempotent — calling
        pause() on an already-paused runner is a no-op.

        Bug fixed in v3.43.19: the original implementation used two
        sequential `if` statements that cancelled each other out, leaving
        a "running" runner in "running" state. Split into pause()/resume()
        so each verb means exactly one thing, matching the separate UI
        buttons that call them."""
        if self._state == "running":
            self._pause.clear()
            self._state = "paused"
            _flush_pending = getattr(
                self, "_flush_daily_byte_accumulators", None)
            if _flush_pending:
                _flush_pending()

    def resume(self):
        """Resume from paused / paused_no_button / low_disk states.
        Idempotent — no-op if already running. Added in v3.43.19 to match
        the UI's separate Resume button (the /api/sites/<sid>/resume
        endpoint had previously been failing with 400 because this method
        didn't exist).

        Row 390: gated by the durable download hold. resume() flips paused ->
        running WITHOUT passing through start(), so leaving it ungated would let
        /api/resume_all defeat a hold that start() honours. Same fail-closed
        contract: an unmeasurable hold refuses."""
        if self._state in ("paused", "low_disk", "paused_no_button"):
            _hold_allowed, _hold_state = _download_hold.downloads_allowed()
            if not _hold_allowed:
                self._state = _download_hold.runner_state_token(_hold_state)
                self.log_event(
                    "download_hold",
                    "Downloads are held; refusing to resume workers "
                    f"({_hold_state.get('state')}: {_hold_state.get('reason')})",
                    extra={"hold_state": _hold_state.get("state"),
                           "hold_reason": _hold_state.get("reason")})
                return
            self._state = "running"
            self._pause.set()

    @_run_lifecycle_serialized
    def stop(self):
        self._rl_autostart=False  # P3-A: operator stop cancels a pending rate-limit resume
        self._stop.set(); self._pause.set()
        _flush_pending = getattr(self, "_flush_daily_byte_accumulators", None)
        if _flush_pending:
            _flush_pending()
        # Invalidate the generation before changing job state. An in-flight
        # worker may return from Playwright after stop(), but it no longer owns
        # status, failure, progress, or heartbeat publication for this runner.
        worker_lock = getattr(self, "_worker_heartbeats_lock", None)
        if worker_lock is None:
            # Preserve the long-standing unbound-method adapter surface. The
            # lifecycle decorator already serializes this bootstrap.
            worker_lock = threading.Lock()
            self._worker_heartbeats_lock = worker_lock
        with worker_lock:
            self._worker_run_generation = (
                getattr(self, "_worker_run_generation", 0) + 1)
            self._worker_generation_invalidated = True
        # Wake any worker blocked on queue.get with a sentinel each.
        for _ in self._worker_threads:
            try: self._url_queue.put_nowait(None)
            except Exception: pass
        with job_status_writer(self) as mark_status_changed:
            changed = False
            for u,j in self.jobs.items():
                # Corrupt/legacy queue payloads must not make lifecycle
                # teardown fail open.  The status endpoint already reports
                # such values defensively; stop them only when a mutable job
                # record is actually present.
                if (isinstance(j, dict)
                        and j.get("status") in ("pending", "running")):
                    j.update({"status":"stopped","message":"Stopped","ts":_ts()})
                    changed = True
            if changed:
                mark_status_changed()
        self._state="stopped"
        # Workers close their own browsers in their finally blocks. We don't
        # call browser.close() across threads — Playwright sync is not
        # thread-safe and that crashes hard.
        #
        # Signal auxiliary browser owners without waiting under the run lock.
        # ``retire_workers`` performs the bounded joins and identity-checked
        # clears outside this serialized stop transition.
        snapshot_stop = getattr(self, "_manual_snapshot_stop", None)
        if snapshot_stop is not None:
            try: snapshot_stop.set()
            except Exception: pass
        dl_session = getattr(self, "_manual_download_session", None)
        if dl_session:
            try: dl_session.cancel(timeout=0)
            except Exception as e: self.log.debug("stop: dl session cancel: %s", e)
        login_session = getattr(self, "_manual_login_handle", None)
        if login_session and hasattr(login_session, "cancel"):
            try: login_session.cancel(timeout=0)
            except Exception as e: self.log.debug("stop: login session cancel: %s", e)
        for captcha_session in tuple(
                (getattr(self, "_captcha_solve_sessions", None) or {}).values()):
            handle = captcha_session.get("handle") if isinstance(
                captcha_session, dict) else None
            if handle is None:
                continue
            try:
                if captcha_session.get("kind") == "vnc" and hasattr(handle, "stop"):
                    handle.stop(timeout=0)
                elif hasattr(handle, "cancel"):
                    handle.cancel(timeout=0)
            except Exception as e:
                self.log.debug("stop: captcha session cancel: %s", e)
        # Phase 30: stop the auto-retry thread cleanly
        try:
            try:
                parameters = inspect.signature(
                    self._stop_auto_retry).parameters.values()
            except (TypeError, ValueError):
                # Legacy adapters are Python callables in practice. If an
                # opaque callable cannot expose a signature, prefer the old
                # no-argument contract rather than risking a second invocation
                # after an internal TypeError.
                accepts_timeout = False
            else:
                accepts_timeout = any(
                    (parameter.name == "timeout"
                     and parameter.kind in (
                         inspect.Parameter.POSITIONAL_OR_KEYWORD,
                         inspect.Parameter.KEYWORD_ONLY,
                     ))
                    or parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
            if accepts_timeout:
                self._stop_auto_retry(timeout=0)
            else:
                self._stop_auto_retry()
        except Exception as e:
            debug = getattr(getattr(self, "log", None), "debug", None)
            if callable(debug):
                debug("stop: auto-retry stop: %s", e)

    def retire_workers(self, timeout=5.0):
        """Permanently stop and prove every runner-owned writer quiescent.

        Besides download workers, the runner owns auto-login, manual browser,
        snapshot-poller, captcha-takeover, and in-flight launch generations.
        Every class shares one deadline. A survivor keeps both its handle and
        the permanently retired runner identity for a fail-closed delete retry.
        """
        lifecycle_lock = getattr(self, "_run_lifecycle_lock", None)
        if lifecycle_lock is None:
            with _RUN_LIFECYCLE_BOOTSTRAP_LOCK:
                lifecycle_lock = getattr(self, "_run_lifecycle_lock", None)
                if lifecycle_lock is None:
                    lifecycle_lock = threading.RLock()
                    self._run_lifecycle_lock = lifecycle_lock
        worker_lock = getattr(self, "_worker_heartbeats_lock", None)
        if worker_lock is None:
            worker_lock = threading.Lock()
            self._worker_heartbeats_lock = worker_lock

        wait_budget = max(0.0, _finite_config_float(timeout, 0.0))
        deadline = time.monotonic() + wait_budget

        # Publication retirement and stop-generation invalidation are one
        # lifecycle transaction. Do not hold this lock across cancels/joins:
        # worker finalizers and guarded launch wrappers need it to settle.
        with lifecycle_lock:
            self._run_retired = True
            self.stop()
            with worker_lock:
                captured_workers = tuple(getattr(self, "_worker_threads", ()))
            captured_watchdog = getattr(self, "_watchdog_thread", None)
            captured_auxiliary = tuple(
                (getattr(self, "_auxiliary_start_threads", None) or {}).keys())
            captured_login = getattr(self, "_login_thread", None)
            captured_snapshot = getattr(self, "_manual_snapshot_thread", None)
            captured_download = getattr(
                self, "_manual_download_session", None)
            captured_manual_login = getattr(
                self, "_manual_login_handle", None)
            captured_captcha = tuple(
                (getattr(self, "_captcha_solve_sessions", None) or {}).items())

        snapshot_stop = getattr(self, "_manual_snapshot_stop", None)
        if snapshot_stop is not None:
            try:
                snapshot_stop.set()
            except Exception:
                pass

        signal_verdicts = {}

        def remaining():
            return max(0.0, deadline - time.monotonic())

        def signal_session(handle, *, kind="session"):
            if handle is None or id(handle) in signal_verdicts:
                return
            verdict = False
            try:
                if kind == "vnc" and hasattr(handle, "stop"):
                    result = handle.stop(timeout=remaining())
                    verdict = result is True
                elif hasattr(handle, "cancel"):
                    result = handle.cancel(timeout=remaining())
                    verdict = result is True
                elif kind == "manual_login" and isinstance(handle, tuple):
                    # Legacy pre-thread-owned login handle: synchronous close
                    # is its complete ownership proof.
                    from .login import cancel_manual_login
                    cancel_manual_login(handle)
                    verdict = True
            except Exception:
                verdict = False
            signal_verdicts[id(handle)] = verdict

        signal_session(captured_download, kind="manual_download")
        signal_session(captured_manual_login, kind="manual_login")
        for _url, captcha_session in captured_captcha:
            if not isinstance(captcha_session, dict):
                continue
            signal_session(
                captcha_session.get("handle"),
                kind=("vnc" if captcha_session.get("kind") == "vnc"
                      else "captcha"),
            )

        def handle_thread(handle):
            thread = getattr(handle, "_thread", None)
            return thread if thread is not None else None

        threads = []
        seen = set()
        for thread in (
            *captured_workers,
            captured_watchdog,
            *captured_auxiliary,
            captured_login,
            captured_snapshot,
            handle_thread(captured_download),
            handle_thread(captured_manual_login),
            *(handle_thread(session.get("handle"))
              for _url, session in captured_captcha
              if isinstance(session, dict)),
        ):
            if thread is None or id(thread) in seen:
                continue
            seen.add(id(thread))
            threads.append(thread)

        current = threading.current_thread()
        for thread in threads:
            if thread is current:
                continue
            try:
                thread.join(timeout=remaining())
            except (RuntimeError, AttributeError):
                pass

        def _is_live(thread):
            if thread is current:
                return True
            try:
                return bool(thread.is_alive())
            except Exception:
                return True

        # A guarded launcher captured above can publish its browser/session
        # while retirement is joining that launcher. Resnapshot once after the
        # producer stage settles, then signal and join every newly published
        # owner within the same original deadline.
        observed_captcha = list(captured_captcha)
        with lifecycle_lock:
            captured_auxiliary = tuple(
                (getattr(self, "_auxiliary_start_threads", None) or {}).keys())
            captured_login = getattr(self, "_login_thread", None)
            captured_snapshot = getattr(self, "_manual_snapshot_thread", None)
            captured_download = getattr(
                self, "_manual_download_session", None)
            captured_manual_login = getattr(
                self, "_manual_login_handle", None)
            captured_captcha = tuple(
                (getattr(self, "_captcha_solve_sessions", None) or {}).items())
            staged_snapshot_stop = getattr(
                self, "_manual_snapshot_stop", None)

        if staged_snapshot_stop is not None:
            try:
                staged_snapshot_stop.set()
            except Exception:
                pass
        signal_session(captured_download, kind="manual_download")
        signal_session(captured_manual_login, kind="manual_login")
        observed_ids = {id(session) for _url, session in observed_captcha}
        for url, captcha_session in captured_captcha:
            if id(captcha_session) not in observed_ids:
                observed_captcha.append((url, captcha_session))
                observed_ids.add(id(captcha_session))
            if not isinstance(captcha_session, dict):
                continue
            signal_session(
                captcha_session.get("handle"),
                kind=("vnc" if captcha_session.get("kind") == "vnc"
                      else "captcha"),
            )

        staged_threads = (
            *captured_auxiliary,
            captured_login,
            captured_snapshot,
            handle_thread(captured_download),
            handle_thread(captured_manual_login),
            *(handle_thread(session.get("handle"))
              for _url, session in captured_captcha
              if isinstance(session, dict)),
        )
        for thread in staged_threads:
            if thread is None or id(thread) in seen:
                continue
            seen.add(id(thread))
            threads.append(thread)
            if thread is current:
                continue
            try:
                thread.join(timeout=remaining())
            except (RuntimeError, AttributeError):
                pass

        def _handle_quiescent(handle):
            if handle is None:
                return True
            thread = handle_thread(handle)
            if thread is not None:
                return not _is_live(thread)
            closed = getattr(handle, "_closed", None)
            if closed is not None and hasattr(closed, "is_set"):
                try:
                    return bool(closed.is_set())
                except Exception:
                    return False
            return signal_verdicts.get(id(handle), False) is True

        # Once a VNC owner is proven dead, reap its module registry/channel.
        # A survivor is deliberately left published in both registries.
        for _url, captcha_session in observed_captcha:
            if (not isinstance(captcha_session, dict)
                    or captcha_session.get("kind") != "vnc"):
                continue
            handle = captcha_session.get("handle")
            if not _handle_quiescent(handle):
                continue
            try:
                from . import takeover_vnc as _takeover_vnc
                _takeover_vnc.teardown(captcha_session.get("session_id"))
            except Exception:
                # The browser is already proven dead; registry cleanup is
                # idempotent and can be retried without permitting a writer.
                pass

        all_quiescent = not any(_is_live(thread) for thread in threads)
        with lifecycle_lock:
            # An in-flight guarded launch may have published its session after
            # the first capture. Retired publication gates prevent new starts,
            # so a changed/current live identity is a survivor for this pass.
            with worker_lock:
                owned = tuple(getattr(self, "_worker_threads", ()))
                if any(_is_live(worker) for worker in owned):
                    all_quiescent = False
                elif tuple(getattr(self, "_worker_threads", ())) == owned:
                    self._worker_threads = []

            current_watchdog = getattr(self, "_watchdog_thread", None)
            if current_watchdog is not None:
                if _is_live(current_watchdog):
                    all_quiescent = False
                elif current_watchdog is captured_watchdog:
                    self._watchdog_thread = None

            starts = getattr(self, "_auxiliary_start_threads", None) or {}
            for thread in tuple(starts):
                if _is_live(thread):
                    all_quiescent = False
                else:
                    starts.pop(thread, None)

            current_login = getattr(self, "_login_thread", None)
            if current_login is not None:
                if _is_live(current_login):
                    all_quiescent = False
                elif current_login is captured_login:
                    self._login_thread = None

            current_snapshot = getattr(self, "_manual_snapshot_thread", None)
            if current_snapshot is not None:
                if _is_live(current_snapshot):
                    all_quiescent = False
                elif current_snapshot is captured_snapshot:
                    self._manual_snapshot_thread = None

            current_download = getattr(self, "_manual_download_session", None)
            if current_download is not None:
                if not _handle_quiescent(current_download):
                    all_quiescent = False
                elif current_download is captured_download:
                    self._manual_download_session = None

            current_manual_login = getattr(self, "_manual_login_handle", None)
            if current_manual_login is not None:
                if not _handle_quiescent(current_manual_login):
                    all_quiescent = False
                elif current_manual_login is captured_manual_login:
                    self._manual_login_handle = None

            captcha_sessions = getattr(self, "_captcha_solve_sessions", None) or {}
            captured_captcha_map = dict(captured_captcha)
            for url, session in tuple(captcha_sessions.items()):
                handle = session.get("handle") if isinstance(session, dict) else None
                if not _handle_quiescent(handle):
                    all_quiescent = False
                elif captured_captcha_map.get(url) is session:
                    captcha_sessions.pop(url, None)
        return all_quiescent




    # ────────────────────────────────────────────────────────────────
    # v3.43.60: Captcha relay takeover sessions
    # ────────────────────────────────────────────────────────────────







    # ── Phase 5.4: Manual takeover for downloads ──────────────────────────





    # ── Phase 10: Teach Mode helpers ──────────────────────────────────────





    # ── Phase 5.8: Drift detection — periodic check on learned hit rate ───

    # ── rate limit ──────────────────────────────────────────────────────────



    # ── scheduler ────────────────────────────────────────────────────────────









    # ── Phase 6.5: Multi-account rotation ─────────────────────────────────







    def _current_throughput_bps(self, now: float | None = None) -> float:
        """Sum recent byte rates for jobs that are still running.

        Progress emitters tick about once per second. A sample older than five
        seconds is therefore no longer evidence of live transfer activity and
        must decay to zero instead of leaving the dashboard stuck at the last
        completed-download EWMA.
        """
        current = time.time() if now is None else float(now)
        total = 0.0
        with self._lock:
            for url, sample in self._job_progress_samples.items():
                job = self.jobs.get(url) or {}
                if job.get("status") != "running":
                    continue
                sample_at = sample.get("at")
                sample_bps = float(sample.get("bps", 0.0) or 0.0)
                if sample_at is None or sample_bps <= 0:
                    continue
                age = current - float(sample_at)
                if 0.0 <= age <= 5.0:
                    total += sample_bps
        return total

    def get_status(self,light=False):
        """Return runner state. With `light=True`, omit `jobs` and
        `url_order` — the two heavy fields. Used by the sidebar poll which
        only needs counts/state/cookie info, not the full job list. At 10k+
        URLs, light mode cuts /api/status payload from MBs to KBs."""
        # v3.43.19: counts, total, jobs, and urls all read under a single
        # lock acquisition. Previously two with-blocks created a TOCTOU
        # window: if a worker added a job between them, total reflected
        # the new count but counts didn't include it.
        with self._lock:
            jobs={u:dict(j) for u,j in self.jobs.items()} if not light else {}
            urls=list(self.urls) if not light else []
            counts={"pending":0,"running":0,"done":0,"failed":0,"stopped":0,
                    "needs_review":0}
            for j in self.jobs.values():
                s=j.get("status","")
                if s in counts: counts[s]+=1
            total=len(self.jobs)
            # v3.43.80 Phase 84: per-row "what's it doing right now" inline
            # event. Walk the event log (capped at 500 entries) and stamp
            # the latest event onto each running job's dict. Cheap: ≤500
            # events × small dict construction, under one lock acquisition.
            # Frontend renders this as a small italic line under the message
            # cell. Skipped for done/failed/stopped — they have a final
            # message already and don't need rolling event context.
            if not light and jobs:
                latest_by_url = {}
                for ev in reversed(self._event_log):
                    u = ev.get("url")
                    if u and u not in latest_by_url and u in jobs:
                        j = jobs[u]
                        if j.get("status") in ("running", "pending"):
                            latest_by_url[u] = {
                                "kind": ev.get("kind", ""),
                                "message": ev.get("message", "")[:200],
                                "ts": ev.get("ts", 0),
                            }
                for u, last_ev in latest_by_url.items():
                    jobs[u]["last_event"] = last_ev
        rl=self.is_rate_limited()
        # v3.48 (#8): Queue ETA. Combine rolling throughput with a
        # rolling average of recently-completed job sizes to estimate
        # how long the remaining pending+running work will take.
        #
        # The math:
        #   throughput  = EWMA bytes/sec across completed downloads
        #   avg_size    = mean file_size from last 20 completions
        #   remaining   = (pending + running) * avg_size  bytes
        #   eta_s       = remaining / throughput
        #
        # Returns 0 when throughput is unknown (first job, no history
        # warmup yet) — frontend should render that as "—".
        eta_s = 0
        try:
            tp_bps = getattr(self, "_throughput_ewma_bps", 0.0) or 0.0
            if tp_bps > 0:
                # Average size from recently-completed jobs in memory.
                recent_sizes = [
                    j.get("file_size", 0) or 0
                    for j in list(self.jobs.values())[-20:]
                    if j.get("status") == "done"
                       and (j.get("file_size") or 0) > 0
                ]
                if recent_sizes:
                    avg_size = sum(recent_sizes) / len(recent_sizes)
                    remaining_count = counts["pending"] + counts["running"]
                    if remaining_count > 0:
                        eta_s = int(remaining_count * avg_size / tp_bps)
                        # Cap at 24h — anything longer probably means
                        # bad throughput data, don't expose a panic-
                        # inducing number to the UI.
                        if eta_s > 86400:
                            eta_s = 86400
        except Exception:
            eta_s = 0
        return {"state":"rate_limited" if rl else self._state,"login_status":self._login_status,
                "cookie_info":self.cookie_info(),"counts":counts,"total":total,
                "jobs":jobs,"url_order":urls,"rate_limited":rl,"rl_remaining":self.rl_remaining() if rl else "",
                "disk_free_gb":disk_free_gb(self.config.get("download_dir","")),"disk_threshold_gb":float(self.config.get("disk_threshold_gb",2.0)),
                "sched_next":self.sched_next_str(),"sched_enabled":bool(self.config.get("sched_enabled")),
                "awaiting_manual_login":self.is_awaiting_manual_login(),
                "awaiting_manual_download":self.is_awaiting_manual_download(),
                "cookie_age_hours": self._cookie_age_hours(),
                # Live rate from fresh running-job byte samples. Completion
                # EWMA remains separate for chunk tuning and queue ETA.
                "bytes_per_sec": self._current_throughput_bps(),
                # v3.48 (#8): queue-completion ETA in seconds. 0 = unknown.
                "eta_s": eta_s,
                "learned_summary":self._learned_summary(),
                # v3.43.21: JD bridge health for the per-site insight strip
                # and the global JD pill in the header.
                "jd_health": self.jd_health(),
                # v3.43.26: qB bridge health. Same shape as jd_health
                # so the UI can render both with the same component.
                "qb_health": self.qb_health(),
                # v3.43.24: watchdog signal. Empty list = no problems;
                # populated = at least one worker stuck >15min. UI shows
                # a banner in the site card when non-empty.
                "hung_workers": list(getattr(self, "_hung_workers", []))}



    def _learned_summary(self):
        """Compact summary for the UI: which kinds are learned, how many
        manual takeovers, drift recoveries."""
        l=self.config.get("learned",{}) if isinstance(self.config.get("learned"),dict) else {}
        login=l.get("login",{}) or {}
        dl=l.get("download",{}) or {}
        stats=l.get("stats",{}) or {}
        return {
            "login_learned": bool(login.get("user_field") or login.get("submit_btn")),
            "download_learned": bool(dl.get("row_selectors") or dl.get("trigger_selectors")),
            "url_attribute": dl.get("url_attribute",""),
            "manual_logins": stats.get("manual_login",0),
            "manual_downloads": stats.get("manual_download",0),
            "direct_extractions": stats.get("direct_extractions",0),
            "drift_recoveries": stats.get("drift_recoveries",0),
        }

    def state(self): return self._state

    def _compute_site_usage(self, dl_dir):
        """Phase 65 (v3.38.x): sum the byte size of all files under
        dl_dir. Used for site_quota_gb checks. Walks the tree; for
        a large library with tens of thousands of files this is the
        slowest part of start(), so we cache for 60s.

        Returns bytes after a complete walk, or ``None`` if any subtree or
        file size could not be measured.  A partial total is not quota
        evidence; start() publishes quota_usage_unknown and holds workers."""
        cache_attr = "_site_usage_cache"
        if not hasattr(self, cache_attr):
            setattr(self, cache_attr, (0.0, 0))
        ts, cached = getattr(self, cache_attr)
        if time.time() - ts < 60 and cached > 0:
            return cached
        total = 0
        errors = []

        def walk_error(error):
            errors.append(error)

        try:
            for root, dirs, files in os.walk(dl_dir, onerror=walk_error):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError as e:
                        errors.append(e)
        except OSError:
            return None
        if errors:
            return None
        setattr(self, cache_attr, (time.time(), total))
        return total


    def _worker_write_generation(self, explicit_generation=None):
        """Return a worker-thread generation, or None for control-plane writes."""
        if explicit_generation is not None:
            return explicit_generation
        context = getattr(self, "_worker_context", None)
        return getattr(context, "run_generation", None) if context else None

    def _worker_write_generation_is_current(self, explicit_generation=None):
        """Reject mutations from worker threads whose run was invalidated."""
        run_generation = self._worker_write_generation(explicit_generation)
        if run_generation is None:
            return True
        return self._worker_generation_is_current(run_generation)

    def _capture_website_title(self, page, url):
        """Harvest a settled detail page once and retain its provenance."""
        page_identity = id(page)
        with self._lock:
            existing = self._website_titles.get(url)
            if (existing is not None
                    and existing.get("page_identity") == page_identity):
                return existing["title"], existing["source"]
            job = self.jobs.get(url) or {}
            listing_title = (
                job.get("listing_title")
                or self._listing_titles.get(url, "")
                or ""
            )

        raw_title, source = harvest_page_title(
            page, listing_title=listing_title)
        retroactive = []
        with self._lock:
            # Evaluation happens outside the lock. Re-read observations here
            # so two concurrent scene pages cannot both miss each other.
            if raw_title:
                self._website_title_observations[url] = raw_title
            record = {
                "title": raw_title,
                "source": source,
                "raw": raw_title,
                "page_identity": page_identity,
            }
            self._website_titles[url] = record
            # A template is knowable only after another distinct scene repeats
            # it. Re-normalize all cached observations when that happens, and
            # remember any already-completed row that needs safe enrichment.
            for observed_url, observed_raw in (
                    self._website_title_observations.items()):
                observed = self._website_titles.get(observed_url)
                if observed is None:
                    continue
                normalized = strip_repeated_title_template(
                    observed_raw, self._website_title_observations)
                previous = observed.get("title", "")
                observed["title"] = normalized
                if observed_url in self.jobs:
                    self.jobs[observed_url].update({
                        "website_title": normalized,
                        "website_title_source": observed.get("source", ""),
                        "website_title_raw": observed_raw,
                    })
                if previous and normalized != previous:
                    retroactive.append((
                        observed_url,
                        observed_raw,
                        normalized,
                        observed.get("source", ""),
                    ))
            title = record["title"]
        # Never hold the runner lock across SQLite I/O. The helper updates only
        # rows whose title still equals the raw harvested value, so an operator
        # edit wins over late template learning.
        for observed_url, observed_raw, normalized, observed_source in retroactive:
            db_normalize_history_title(
                self.site_id, observed_url, observed_raw,
                normalized, observed_source,
            )
        return title, source

    def _history_title_fields(self, url):
        """Return db_log kwargs without inventing a title from a filename."""
        with self._lock:
            record = self._website_titles.get(url)
            if record is not None and record.get("raw"):
                title = strip_repeated_title_template(
                    record.get("raw", ""), self._website_title_observations)
                record["title"] = title
                return {
                    "title": title,
                    "title_source": record.get("source", ""),
                }
            job = self.jobs.get(url) or {}
            listing_title = (
                job.get("listing_title")
                or self._listing_titles.get(url, "")
                or ""
            )
            title = strip_repeated_title_template(
                listing_title, self._website_title_observations)
            if listing_title:
                self._website_title_observations[url] = listing_title
            source = "listing_card" if title else ""
            self._website_titles[url] = {
                "title": title,
                "source": source,
                "raw": listing_title,
                "page_identity": None,
            }
            return {"title": title, "title_source": source}

    def _update_job(self,url,status,message,**extra):
        """Serialize worker-originated publication against stop/start."""
        explicit_generation = extra.pop("_run_generation", None)
        run_generation = self._worker_write_generation(explicit_generation)
        if run_generation is None:
            return self._update_job_current(url, status, message, **extra)
        # Fast stale rejection prevents an invalidated worker from waiting on
        # a replacement start's lifecycle transaction.
        if not self._worker_generation_is_current(run_generation):
            return False
        with self._run_lifecycle_lock:
            if not self._worker_generation_is_current(run_generation):
                return False
            return self._update_job_current(url, status, message, **extra)

    def _update_job_current(self,url,status,message,**extra):
        """Central state-mutation: change a job's status/message, log
        the transition, and persist to the queue table. `extra` accepts
        kv pairs (filename, file_size, retries, retry_after, screenshot,
        force_download, priority) applied to memory + persisted row.

        Locking: takes self._lock briefly, RELEASES it before SQLite
        (DB ops can take 10-100ms under load and stalling other workers
        behind them is worse than racing a status field). DB failures
        log to stderr but never propagate.

        v3.43.23: stamps `last_progress_at` on byte advance OR status
        change. The UI flags status='running' with no progress in 60min
        as stuck (amber row). Reset on status transitions because a
        brand-new state ('running' from 'pending') isn't stuck."""
        _transition_prev_status = extra.pop("_transition_prev_status", None)
        _memory_already_updated = bool(
            extra.pop("_memory_already_updated", False))
        now = time.time()
        # v3.43.80 Phase 86: friendly_error translates raw failure msgs.
        if status == "failed" and message:
            try:
                message = _translate_failed_message(message)
            except Exception:
                pass  # translator failure must never block queue update
        byte_advanced = False
        with self._job_status_writer() as mark_status_changed:
            prev_status = (self.jobs.get(url) or {}).get("status")
            prev_bytes = int((self.jobs.get(url) or {}).get("file_size", 0))
            # v3.43.80: auto-create entry for unknown URL so stale retry_one isn't a no-op.
            if url not in self.jobs:
                self.jobs[url] = {"last_progress_at": now}
            if url in self.jobs:
                if not _memory_already_updated:
                    self.jobs[url].update({
                        "status": status, "message": message,
                        "ts": _ts(),
                        # CUT #31: date-comparable sibling of `ts`. `ts` stays
                        # HH:MM:SS because that is what the queue UI renders;
                        # `ts_iso` is a full LOCAL ISO stamp so day-window
                        # consumers (done_today_count etc.) can filter by date.
                        # Comparing an HH:MM:SS value against a "%Y-%m-%d"
                        # prefix is False for every possible pair of values,
                        # which is how done_today_count came to be
                        # structurally always 0.
                        "ts_iso": _ts_iso(),
                        **extra,
                    })
                    mark_status_changed()
                # v3.43.23: stamp last_progress_at whenever there's a
                # real signal of progress. Two cases count as progress:
                #   (a) the status changed (running → done, running →
                #       failed, pending → running)
                #   (b) status stayed 'running' but file_size advanced
                # If neither — the runner is calling _update_job to
                # refresh a message but nothing actually changed — we
                # do NOT update last_progress_at. That's the entire
                # point of the stuck-URL detector: silent message
                # churn doesn't reset the timer.
                new_bytes = int(extra.get("file_size", prev_bytes) or 0)
                if prev_status != status or new_bytes > prev_bytes:
                    self.jobs[url]["last_progress_at"] = now
                if new_bytes > prev_bytes:
                    byte_advanced = True
                    previous_sample = self._job_progress_samples.get(url) or {}
                    previous_sample_bytes = previous_sample.get("bytes")
                    previous_sample_at = previous_sample.get("at")
                    sample_bps = 0.0
                    if (previous_sample_bytes is not None
                            and previous_sample_at is not None):
                        elapsed = now - float(previous_sample_at)
                        if elapsed > 0:
                            sample_bps = (
                                new_bytes - int(previous_sample_bytes)
                            ) / elapsed
                    self._job_progress_samples[url] = {
                        "bytes": new_bytes,
                        "at": now,
                        "bps": max(0.0, sample_bps),
                    }
                # AUDIT FIX (v3.42.0): centrally clear the Phase 72 retry
                # counter on ANY success transition. Previously only the
                # HTTP-download success path cleared it; Playwright/manual/
                # ytdlp success paths left the counter behind, which would
                # disable retry on the NEXT corruption event for that URL.
                if status == "done":
                    self.jobs[url].pop("corruption_retries", None)
                    # v3.43.44: fold the successful URL's host +
                    # path-prefix into the per-site fingerprint so
                    # future scoring favors similar CDN/path patterns.
                    try:
                        from . import heuristic_scoring
                        fp = self.config.get("url_fingerprint") or {}
                        new_fp = heuristic_scoring.update_fingerprint_from_success(
                            fp, url)
                        self.config["url_fingerprint"] = new_fp
                    except Exception:
                        # Never let scoring code break the queue
                        pass
        if _transition_prev_status is not None:
            # Worker claim already committed pending -> running atomically.
            # Reuse the normal post-lock publisher with its captured prior
            # status so logs/history/persistence/SSE happen exactly once.
            prev_status = _transition_prev_status
        # Keep lock ordering one-way: never acquire the heartbeat lock while
        # holding the jobs lock. Only genuine byte advancement proves that a
        # worker blocked inside a long stream is alive; message churn does not.
        if byte_advanced:
            with self._worker_heartbeats_lock:
                for worker_idx, current_url in self._worker_current_urls.items():
                    if current_url == url:
                        self._worker_heartbeats[worker_idx] = now
        # Phase 13: log status TRANSITIONS (not every spurious update with
        # the same status). Skips noisy intra-state messages like "Opening
        # page..." / "Downloading 23%". Filename and file_size land in
        # extra and are exposed as a structured field rather than text.
        if prev_status != status:
            log_extra = {"prev": prev_status} if prev_status else {}
            for k in ("filename","file_size","retries","screenshot"):
                if k in extra: log_extra[k] = extra[k]
            self.log_event("state", f"{status}: {message}", url=url, extra=log_extra)
            # B1 (post-365): advisory run-history hook. Open a run on the
            # transition INTO 'running' and close it on a terminal status.
            # Entirely fail-open — a history-store problem must NEVER perturb
            # the download path (the whole try is swallowed).
            try:
                from . import run_history as _rh
                _RUN_TERMINAL = ("done", "failed", "error",
                                 "skipped_duplicate", "cancelled")
                if status == "running" and prev_status != "running":
                    rid = _rh.record_run_start(self.site_id, url)
                    if rid:
                        with self._lock:
                            if url in self.jobs:
                                self.jobs[url]["_run_id"] = rid
                        _rh.emit_lifecycle(self, "start", run_id=rid, url=url,
                                           message=message)
                elif status in _RUN_TERMINAL:
                    rid = (self.jobs.get(url) or {}).get("_run_id")
                    if rid:
                        # Cut 4: persist an operator reason_code on failures so
                        # /api/runs?status=failed can group + explain them.
                        rc = None
                        if status in ("failed", "error"):
                            try:
                                from . import failure_reasons as _fr
                                rc = _fr.reason_for(message).get("reason_code")
                            except Exception:
                                rc = None
                        _rh.record_run_finish(rid, status, reason_code=rc)
                        _rh.emit_lifecycle(self, "finish", run_id=rid, url=url,
                                           message=status)
            except Exception:
                pass  # advisory: history never breaks the worker
        # Phase 4.2: persist the change. Outside the lock to avoid holding
        # the runner's lock during a SQLite write — the DB has its own
        # locking and these are quick. Failures are silent (DB outage
        # shouldn't break the worker; queue would just be in-memory only
        # for this run).
        try:
            persist_fields = {"status": status, "message": message}
            for k in ("retries","retry_after","screenshot","filename","file_size","force_download","priority"):
                if k in extra:
                    v = extra[k]
                    if k == "force_download": v = 1 if v else 0
                    persist_fields[k] = v
            queue_upsert(self.site_id, url, **persist_fields)
        except Exception as e:
            sys.stderr.write(f"[{self.site_id}] queue persist failed: {e}\n")
        # v3.43.34: real-time push to SSE subscribers. Throttled per URL
        # so a fast download (one progress update per chunk write) emits
        # at most 1 event/sec to the wire. Status transitions
        # (pending→running, running→done) DO pass through immediately
        # because they have a different throttle key.
        try:
            from . import sse_broker as _sse
            # Use a per-(site, url, status) throttle key so a status
            # transition pushes through even if a recent progress event
            # was rate-limited (it's a different key).
            throttle_key = f"{self.site_id}:{url}:{status}"
            _sse.publish("queue_change", {
                "site_id": self.site_id,
                "url": url,
                "status": status,
                "message": message,
                "filename": (self.jobs.get(url) or {}).get("filename", ""),
                "file_size": (self.jobs.get(url) or {}).get("file_size", 0),
                "ts": _ts(),
            }, throttle_key=throttle_key, throttle_s=1.0)
        except Exception:
            pass

        # v3.43.38: feed dashboard widgets — rolling rate window + recent
        # finishes for the success-rate headline. Defensive: widgets
        # module's shortcuts already swallow exceptions, but the extra
        # try/except keeps the runner safe even if the module import
        # itself somehow fails.
        try:
            from . import dashboard_widgets as _dw
            # Progress: bytes delta since the previous _update_job call
            # for this url. We computed prev_bytes earlier; new_bytes is
            # the latest. Only count positive deltas (status-only
            # updates produce no progress).
            new_bytes = int(extra.get("file_size", prev_bytes) or 0)
            if status == "running" and new_bytes > prev_bytes:
                _dw.note_progress(self.site_id, new_bytes - prev_bytes)
            # Finish: a terminal status with retries==0 is a clean
            # success; anything else (failed, needs_review, or done
            # but with retries>0) is a failure for success-rate
            # purposes. Status transitions only — we don't want to
            # log the same finish twice if _update_job is called
            # repeatedly with status='done'.
            if status in ("done", "failed", "needs_review") and prev_status != status:
                retries = int((self.jobs.get(url) or {}).get("retries", 0) or 0)
                success = (status == "done" and retries == 0)
                _dw.note_finish(self.site_id, url, success)
                # v3.43.53: feed worker successes into the session
                # keeper's state. Workers using their own profile +
                # the on-disk cookies prove the session is alive,
                # even if the keeper's own heartbeat browser is in a
                # bad state. The UI uses this to suppress stale
                # "disconnected" badges.
                if status == "done":
                    try:
                        from . import session_keeper as _sk
                        _sk.note_worker_success(self.site_id)
                    except Exception:
                        # Best-effort hook; keeper module may be
                        # disabled via BD_DISABLE_KEEPALIVE
                        pass
        except Exception:
            pass

        # E1 (v3.66.494): plugin event surface for download progress + retry.
        # Fired through the isolated emit seam AFTER the lock; a throwing
        # consumer never perturbs the download path. progress fires only on a
        # real byte advance (same signal as last_progress_at / the widgets
        # window); retry fires when a requeue raised the attempt count.
        try:
            from . import plugins as _pl
            _newb = int(extra.get("file_size", prev_bytes) or 0)
            if status == "running" and _newb > prev_bytes:
                _pl.emit("download.progress",
                         {"site_id": self.site_id, "url": url,
                          "file_size": _newb, "ts": _ts()})
            if status == "pending" and "retries" in extra:
                try:
                    _newr = int(extra.get("retries") or 0)
                except (TypeError, ValueError):
                    _newr = 0
                # A real retry carries an attempt count >= 1; a queue reset
                # (approve/resume/clear) passes retries=0 and is NOT a retry.
                if _newr > 0:
                    _pl.emit("download.retry",
                             {"site_id": self.site_id, "url": url,
                              "retries": _newr, "message": message,
                              "ts": _ts()})
        except Exception:
            pass

        # v3.43.72: perceptual dedup hook. On every download-done with a
        # filename, schedule a background pHash computation that
        # registers the file in the hash registry. Slow (10-30s) so we
        # fire-and-forget on a daemon thread. Fail-open everywhere —
        # missing videohash / missing ffmpeg / corrupt file all silently
        # become no-ops. Opt-out via site config `dedup_hash_on_done`.
        if (status == "done" and prev_status != "done"
                and self.config.get("dedup_hash_on_done", True)
                and _DEDUP_AVAILABLE and _dedup is not None):
            try:
                filename = (self.jobs.get(url) or {}).get("filename", "")
                dl_dir = (self.config.get("download_dir") or "").strip()
                if filename and dl_dir:
                    file_path = os.path.join(dl_dir, filename)
                    if os.path.isfile(file_path):
                        # Spawn a daemon thread so we don't block this
                        # _update_job call (which is on the worker
                        # thread). The hash takes 10-30s and the
                        # worker should already be moving to the next
                        # URL.
                        t = threading.Thread(
                            target=self._dedup_hash_worker,
                            args=(file_path, url),
                            daemon=True,
                            name=f"dedup-{self.site_id}-{int(time.time())}",
                        )
                        t.start()
            except Exception as e:
                sys.stderr.write(
                    f"[{self.site_id}] dedup spawn failed: {e}\n")

        # v3.43.75: yt-dlp download_archive append. On every done
        # transition, append the URL's derived (extractor, id) to the
        # configured archive file so yt-dlp also sees it as already
        # downloaded. Fail-open: any error logs but doesn't block.
        if (status == "done" and prev_status != "done"
                and self.config.get("use_ytdlp_archive", False)
                and _YTDLP_ARCH_AVAILABLE and _ytdlp_arch is not None):
            archive_path = (self.config.get("ytdlp_archive_path", "") or "").strip()
            if archive_path:
                try:
                    derived = _ytdlp_arch.derive_id(url)
                    if derived is not None:
                        extractor, vid = derived
                        _ytdlp_arch.append_entry(archive_path, extractor, vid)
                except Exception as e:
                    sys.stderr.write(
                        f"[{self.site_id}] ytdlp_archive append failed: {e}\n")

        # v3.43.76: thumbnail generation on download-done. Fires the
        # background worker queue with the file path + per-site
        # config. Fail-open: missing ffmpeg / corrupt file all
        # silently skip via the worker's own fail-open paths.
        if (status == "done" and prev_status != "done"
                and self.config.get("use_thumbnails", False)
                and _THUMB_AVAILABLE and _thumb is not None):
            try:
                filename = (self.jobs.get(url) or {}).get("filename", "")
                dl_dir = (self.config.get("download_dir") or "").strip()
                if filename and dl_dir:
                    file_path = os.path.join(dl_dir, filename)
                    if os.path.isfile(file_path):
                        worker = _thumb.get_default_worker()
                        worker.submit(file_path, config={
                            "mode": self.config.get(
                                "thumbnail_mode", "single"),
                            "output_dir_mode": self.config.get(
                                "thumbnail_dir_mode", "sidecar"),
                            "download_dir": dl_dir,
                            "sheet_rows": int(self.config.get(
                                "thumbnail_sheet_rows", 3) or 3),
                            "sheet_cols": int(self.config.get(
                                "thumbnail_sheet_cols", 3) or 3),
                            "skip_existing": True,
                        })
            except Exception as e:
                sys.stderr.write(
                    f"[{self.site_id}] thumbnail spawn failed: {e}\n")
        # v3.43.80 Phase 88: TPDB metadata fetch on download-done.
        # Looks up the scene on ThePornDB.net using URL or filename,
        # converts to a metadata dict, and writes a sidecar .nfo via
        # library_final.write_nfo. Daemon thread — TPDB latency (1-3s)
        # should never block the worker. Opt-in via use_tpdb=True;
        # requires tpdb_api_key on the site config.
        if (status == "done" and prev_status != "done"
                and self.config.get("use_tpdb", False)):
            try:
                filename_ = (self.jobs.get(url) or {}).get("filename", "")
                dl_dir = (self.config.get("download_dir") or "").strip()
                api_key = self.config.get("tpdb_api_key", "")
                if filename_ and dl_dir and api_key:
                    file_path = os.path.join(dl_dir, filename_)
                    if os.path.isfile(file_path):
                        def _tpdb_worker(fp, source_url, key, site_id):
                            try:
                                from . import tpdb as _tpdb
                                from . import library_final as _lf
                                meta = _tpdb.enrich(source_url, fp,
                                                    api_key=key)
                                if meta:
                                    _lf.write_nfo(fp, meta)
                            except Exception as e:
                                sys.stderr.write(
                                    f"[{site_id}] tpdb enrich failed: {e}\n")
                        threading.Thread(
                            target=_tpdb_worker,
                            args=(file_path, url, api_key, self.site_id),
                            daemon=True,
                            name=f"tpdb-{self.site_id}-{int(time.time())}",
                        ).start()
            except Exception as e:
                sys.stderr.write(
                    f"[{self.site_id}] tpdb spawn failed: {e}\n")
        # v3.43.80 Phase 89: subtitle auto-download. After a successful
        # video download, kick subliminal to fetch matching subtitles
        # from OpenSubtitles. Languages from site cfg subtitle_languages
        # (default: ['en']). Daemon thread — network IO can take 5-30s.
        # Opt-in via use_subtitles=True.
        if (status == "done" and prev_status != "done"
                and self.config.get("use_subtitles", False)):
            try:
                filename_ = (self.jobs.get(url) or {}).get("filename", "")
                dl_dir = (self.config.get("download_dir") or "").strip()
                if filename_ and dl_dir:
                    file_path = os.path.join(dl_dir, filename_)
                    if os.path.isfile(file_path):
                        langs = self.config.get("subtitle_languages") or ["en"]
                        def _sub_worker(fp, languages, site_id):
                            try:
                                from . import subtitles as _sub
                                _sub.download_for_file(fp, languages=languages)
                            except Exception as e:
                                sys.stderr.write(
                                    f"[{site_id}] subtitle fetch failed: {e}\n")
                        threading.Thread(
                            target=_sub_worker,
                            args=(file_path, list(langs), self.site_id),
                            daemon=True,
                            name=f"sub-{self.site_id}-{int(time.time())}",
                        ).start()
            except Exception as e:
                sys.stderr.write(
                    f"[{self.site_id}] subtitles spawn failed: {e}\n")
        # v3.43.80 Phase 99: feed the per-host circuit breaker. Every
        # success/failure observation tightens the per-host model so
        # the retry loop can avoid hosts that are failing chronically.
        # Cheap O(1); fail-open via try/except. Fires after the queue
        # update so the observation reflects DB-committed state, not a
        # transient intermediate.
        if status in ("done", "failed"):
            try:
                from . import circuit_breaker as _cb
                _cb.observe(url, success=(status == "done"))
            except Exception:
                pass
        # v3.43.80 Phase 104: record successful downloads in the
        # provenance ledger. Best-effort — ledger write failure must
        # never block the queue update. Only fires when this is the
        # first transition to "done" and we have something to record
        # (filename + size). Placed after the existing function body
        # so provenance reflects acknowledged state.
        if status == "done" and extra.get("filename"):
            try:
                from . import provenance as _prov
                _prov.record(
                    site_id=self.site_id,
                    source_url=url,
                    final_filename=extra.get("filename") or "",
                    file_size=int(extra.get("file_size", 0) or 0),
                    sha256="",  # caller can fill via separate post-step
                    ts_finished=now,
                )
            except Exception:
                pass

        # v3.43.80 Phase 142: per-VPN-endpoint outcome stats. Only fires
        # when an active VPN profile is known + this is a terminal state.
        # Used by best_profile_for() to inform routing decisions and by
        # auto_blacklist_check to drop bad endpoints for a site.
        if status in ("done", "failed"):
            try:
                vpn_profile = (getattr(self, "current_vpn_profile", "")
                               or self.config.get("active_vpn_profile", ""))
                if vpn_profile:
                    from . import vpn_stats as _vs
                    _vs.record_outcome(
                        vpn_profile, self.site_id,
                        success=(status == "done"),
                        latency_ms=int(extra.get("duration_ms", 0) or 0) or None,
                        reason=(message or "")[:200],
                    )
            except Exception:
                pass

        # v3.43.80 Phase 121: outgoing webhook fanout. Fires for terminal
        # transitions; the webhook queue handles delivery + retry async.
        # Cheap insert; safe to fail.
        if status in ("done", "failed", "needs_review"):
            try:
                from . import webhooks as _wh
                event_name = {"done": "download.done",
                              "failed": "download.failed",
                              "needs_review": "download.needs_review"}[status]
                _wh.fire(event_name, {
                    "site_id": self.site_id,
                    "url": url,
                    "filename": extra.get("filename", ""),
                    "file_size": int(extra.get("file_size", 0) or 0),
                    "message": (message or "")[:300],
                    "ts": now,
                })
            except Exception:
                pass

        # v3.43.80 Phase 116: in-process plugin hooks. Same event names
        # as webhooks but runs callbacks synchronously inside BD. Per
        # plugins.fire_hook, exceptions in plugin callbacks are caught
        # and logged — never propagate.
        if status in ("done", "failed", "needs_review"):
            try:
                from . import plugins as _pl
                event_name = {"done": "download.done",
                              "failed": "download.failed",
                              "needs_review": "download.needs_review"}[status]
                _dl_dir = (self.config.get("download_dir") or "").strip()
                _fname = extra.get("filename", "")
                _payload = {
                    "site_id": self.site_id,
                    "url": url,
                    "filename": _fname,
                    "path": (str(Path(_dl_dir) / _fname) if (_dl_dir and _fname) else ""),
                    "file_size": int(extra.get("file_size", 0) or 0),
                    "message": (message or "")[:300],
                    "ts": now,
                }
                _pl.fire_hook(event_name, _payload)
                # v3.66.465: post-download processors (ordered, isolated,
                # quarantine-guarded). Inert unless a processor is registered.
                if status == "done":
                    _pl.run_processors(_payload)
            except Exception:
                pass

        # v3.43.80 Phase 159: error fingerprinting. On failure, compute
        # the fingerprint so clustered failure analysis (cluster_recent_failures)
        # can group identical-root-cause errors. Pure compute, no DB
        # write — fingerprint is read on demand from the message field.
        # Logged here only so failures surface in stderr with the fp.
        if status == "failed" and message:
            try:
                from . import error_fingerprint as _ef
                fp = _ef.fingerprint_for(message)
                sys.stderr.write(
                    f"[{self.site_id}] failure fp={fp}: {message[:120]}\n")
            except Exception:
                pass

    def _wait_for_lazy_video(
        self, page, *,
        extra_selectors=None,
    ):
        """v3.43.75: wait for a <video> or <source> to appear in the
        page DOM via MutationObserver. Used as a more-reliable
        alternative to polling-based wait_for_selector for sites that
        lazy-load their player after some interaction.

        Returns (found: bool, reason: str). Opt-in per site via
        `use_mutation_observer`.

        Fail-open: never raises.
        """
        if not (_LAZY_PLAYER_AVAILABLE and _lazy_player is not None):
            return (False, "lazy_player_module_unavailable")
        if not self.config.get("use_mutation_observer", False):
            return (False, "not_enabled")
        timeout_ms = int(self.config.get(
            "mutation_observer_timeout_ms", 15000) or 15000)
        timeout_ms = max(100, min(60000, timeout_ms))
        try:
            return _lazy_player.wait_for_any_video_via_observer(
                page, extra_selectors=extra_selectors,
                timeout_ms=timeout_ms,
            )
        except Exception as e:
            return (False, f"raised:{type(e).__name__}")

    def _playlist_expand_one(self, listing_url: str) -> list:
        """v3.43.75: expand one listing URL into scene URLs.

        Spawns a transient Playwright page (no profile reuse — this
        runs from load_urls(), which can be called from any context
        including the folder watcher).

        Returns a list of scene URLs, or [] on any failure.
        """
        if not _PLAYLIST_AVAILABLE or _playlist is None:
            return []
        max_pages = int(self.config.get("playlist_max_pages", 1) or 1)
        ua = self.config.get("user_agent", "") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            from . import cloak as _cloak
        except Exception as e:
            sys.stderr.write(
                f"  playlist_expand: browser backend not available: {e}\n")
            return []
        # Pause keepers for this site if one is running — sync_playwright
        # in the same process is incompatible with the keeper's own
        # sync_playwright (v3.43.52 gotcha).
        try:
            from . import session_keeper as _sk
            _sk.pause_site_keepers(self.site_id)  # INV-001
        except Exception:
            pass
        try:
            with _cloak.cloaked_page(headless=True, user_agent=ua) as page:
                result = _playlist.extract_playlist_urls(
                    page, listing_url,
                    template=self.config,
                    max_pages=max_pages,
                )
            if not result.ok:
                return []
            with self._lock:
                self._listing_titles.update(result.titles or {})
            return list(result.urls)
        except Exception as e:
            sys.stderr.write(
                f"  playlist_expand: raised {type(e).__name__}: {e}\n")
            return []
        # NOTE: no explicit resume call — session_keeper detects the
        # torn-down browser on its next heartbeat and reconnects
        # automatically. Calling a non-existent resume_site_keepers()
        # would just be noise.

    def _search_site(self, query: str, max_results: int = 50):
        """v3.43.77: search this site for `query`. Returns SearchResult.

        Spawns a transient Playwright page (same pattern as
        _playlist_expand_one — pause keepers, run sync_playwright,
        let keeper auto-reconnect on next heartbeat).

        Returns SearchResult(ok=False) on any failure — never raises.
        """
        if not (_SEARCH_AVAILABLE and _search is not None):
            return None
        # Build a minimal SearchResult on the empty fail-path
        if not query or not isinstance(query, str) or not query.strip():
            return _search.SearchResult(
                ok=False, site_id=self.site_id, query=query or "",
                error="empty_query",
            )
        if not _search.is_site_searchable(self.config):
            return _search.SearchResult(
                ok=False, site_id=self.site_id, query=query,
                error="site_not_searchable",
            )
        ua = self.config.get("user_agent", "") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            from . import cloak as _cloak
        except Exception as e:
            sys.stderr.write(
                f"  search: browser backend not available: {e}\n")
            return _search.SearchResult(
                ok=False, site_id=self.site_id, query=query,
                error=f"browser_unavailable:{type(e).__name__}",
            )
        # Pause keepers (v3.43.52 collision pattern)
        try:
            from . import session_keeper as _sk
            _sk.pause_site_keepers(self.site_id)  # INV-001
        except Exception:
            pass
        try:
            with _cloak.cloaked_page(headless=True, user_agent=ua) as page:
                result = _search.search_site(
                    page, self.site_id, query, self.config,
                    max_results=max_results,
                )
            return result
        except Exception as e:
            sys.stderr.write(
                f"  search: raised {type(e).__name__}: {e}\n")
            return _search.SearchResult(
                ok=False, site_id=self.site_id, query=query,
                error=f"search_raised:{type(e).__name__}",
            )



    def _worker_generation_is_current(self, run_generation):
        with self._worker_heartbeats_lock:
            return run_generation == self._worker_run_generation

    def _watch_done(self, run_generation=None, worker_threads=None):
        """Background overseer thread spawned by start(). Polls the queue
        until either:
          • All URLs are processed (queue.unfinished_tasks == 0)
          • A stop has been requested

        Then drains worker threads with sentinels, joins them, and
        decides what to do next:
          • If any pending URLs have retry_after timers, sleep until the
            earliest one and restart the worker pool (auto-retry path).
          • Otherwise, mark the runner 'done' (or 'idle' if some URLs
            are still pending without timers) and fire the 'queue
            complete' push notification.

        Only one _watch_done is ever active per runner — start() spawns
        it as a daemon and lets it manage its own lifetime."""
        if run_generation is None or worker_threads is None:
            with self._worker_heartbeats_lock:
                if run_generation is None:
                    run_generation = self._worker_run_generation
                if worker_threads is None:
                    worker_threads = tuple(self._worker_threads)
        if not self._worker_generation_is_current(run_generation):
            return
        # Wait for the queue to drain AND all captured worker threads to exit.
        # Workers exit when the queue is empty + a sentinel is sent, or when
        # _stop is set.
        while True:
            if not self._worker_generation_is_current(run_generation):
                return
            if self._stop.is_set(): break
            if self._url_queue.unfinished_tasks==0: break
            time.sleep(0.5)
        # The generation check and sentinel publication are atomic with a new
        # start's drain/enqueue block, so an old overseer cannot poison it.
        with self._worker_heartbeats_lock:
            if run_generation != self._worker_run_generation:
                return
            for _ in worker_threads:
                try: self._url_queue.put_nowait(None)
                except Exception: pass
        for t in worker_threads:
            try: t.join(timeout=30)
            except Exception: pass
        with self._worker_heartbeats_lock:
            if run_generation != self._worker_run_generation:
                return
            if tuple(self._worker_threads) == tuple(worker_threads):
                self._worker_threads=[]
        while True:
            outcome = self._finalize_watch_done(run_generation)
            if outcome is None:
                return
            action, payload = outcome
            if action == "retry_wait":
                if self._stop.wait(payload):
                    return
                continue
            if action == "notify":
                self._notify_watch_done_if_current(run_generation, payload)
            return
    def _finalize_watch_done(self, run_generation):
        """Commit retry/final state only if this overseer still owns the run."""
        with self._run_lifecycle_lock:
            if self._stop.is_set():
                return None
            with self._worker_heartbeats_lock:
                if run_generation != self._worker_run_generation:
                    return None

            restart = False
            with self._lock:
                self._completion_notification_token = None
                retryable = [
                    (u, j["retry_after"]) for u, j in self.jobs.items()
                    if j["status"] == "pending" and j.get("retry_after", 0) > 0
                ]
                if retryable:
                    wait = min(ra for _, ra in retryable) - time.time()
                    if wait > 0:
                        now = time.time()
                        for url, retry_after in retryable:
                            mins = max(1, int((retry_after - now) // 60))
                            if url in self.jobs:
                                self.jobs[url]["message"] = f"Retry in ~{mins}m"
                        return "retry_wait", max(0, wait)
                    self._state = "idle"
                    restart = True
                    snapshot = None
                else:
                    counts = {
                        status: sum(
                            1 for job in self.jobs.values()
                            if job["status"] == status)
                        for status in ("pending", "done", "failed", "needs_review")
                    }
                    self._state = "done" if counts["pending"] == 0 else "idle"
                    snapshot = (self._state, counts["pending"], counts["done"],
                                counts["failed"], counts["needs_review"])
                    if counts["pending"] == 0:
                        self._completion_notification_serial = (
                            getattr(self, "_completion_notification_serial", 0)
                            + 1)
                        token = (
                            run_generation,
                            self._completion_notification_serial,
                            getattr(self, "_job_status_version", 0),
                            snapshot,
                        )
                        self._completion_notification_token = token
                    else:
                        token = None

            if restart:
                # RLock keeps the generation check and restart indivisible.
                self.start()
                return "restarted", None
            if snapshot[1] == 0:
                return "notify", token
            return "complete", snapshot

    def _claim_completion_notification(self, run_generation, token):
        """Atomically claim a still-current completion token for delivery."""
        with self._run_lifecycle_lock:
            with self._lock:
                if self._stop.is_set():
                    return None
                with self._worker_heartbeats_lock:
                    if run_generation != self._worker_run_generation:
                        return None
                if token != getattr(self, "_completion_notification_token", None):
                    return None
                token_generation, _, token_version, snapshot = token
                if (token_generation != run_generation
                        or token_version != getattr(self, "_job_status_version", 0)):
                    return None
                counts = {
                    status: sum(
                        1 for job in self.jobs.values()
                        if job["status"] == status)
                    for status in ("pending", "done", "failed", "needs_review")
                }
                current = (self._state, counts["pending"], counts["done"],
                           counts["failed"], counts["needs_review"])
                if current != snapshot or snapshot[0] != "done":
                    return None
                # This is the atomic decision point. External delivery occurs
                # without lifecycle/jobs locks; later status changes are
                # semantically after the completion notice was claimed.
                self._completion_notification_token = None
                self._claimed_completion_notification = token
                return snapshot

    def _notify_watch_done_if_current(self, run_generation, token):
        """Deliver a completion token only after an atomic current-state claim."""
        snapshot = self._claim_completion_notification(run_generation, token)
        if snapshot is None:
            return False

        _, _, done, failed, review = snapshot
        try:
            from . import push as _push
            site_name = self.config.get("name", self.site_id)
            parts = [f"{done} done"]
            if failed: parts.append(f"{failed} failed")
            if review: parts.append(f"{review} need review")
            _push.send_push(
                title=f"{site_name}: queue complete",
                body=" · ".join(parts),
                url=f"/?site={self.site_id}",
                tag=f"qdone:{self.site_id}",
                throttle_seconds=30,
            )
        except Exception as e:
            sys.stderr.write(f"  push send failed (non-fatal): {str(e)[:80]}\n")
        try:
            from . import plugins as _pl
            _pl.emit("queue.drained",
                     {"site_id": self.site_id, "done": done,
                      "failed": failed, "review": review, "ts": _ts()})
        except Exception:
            pass
        return True

    def _requeue_generation_item(self, item_generation, url):
        """Restore eligible work using the documented lifecycle lock order."""
        with self._run_lifecycle_lock:
            with self._lock:
                if (self.jobs.get(url) or {}).get("status") != "pending":
                    return False
                with self._worker_heartbeats_lock:
                    if item_generation != self._worker_run_generation:
                        return False
                    tagged_item = (item_generation, url)
                    q = self._url_queue
                    with q.mutex:
                        for queued in q.queue:
                            if queued == tagged_item or queued == url:
                                return False
                        q._put(tagged_item)
                        q.unfinished_tasks += 1
                        q.not_empty.notify()
                    return True

    def _generation_item_is_processable(self, item_generation, url):
        """Validate a dequeued item against the current run and job state."""
        with self._run_lifecycle_lock:
            with self._lock:
                if (self.jobs.get(url) or {}).get("status") != "pending":
                    return False
                with self._worker_heartbeats_lock:
                    return item_generation == self._worker_run_generation

    def _claim_worker_item(self, worker_idx, url, run_generation=None):
        """Atomically claim eligible current-run work immediately pre-process."""
        with self._job_status_writer() as mark_status_changed:
            with self._worker_heartbeats_lock:
                current_generation = self._worker_run_generation
                if run_generation is None:
                    run_generation = current_generation
                if run_generation != current_generation:
                    return self._WORKER_CLAIM_STALE, run_generation
            job = self.jobs.get(url)
            if not job or job.get("status") != "pending":
                return self._WORKER_CLAIM_INELIGIBLE, run_generation
            now = time.time()
            job.update({
                "status": "running",
                "message": "Claimed by worker",
                "ts": _ts(),
                "last_progress_at": now,
            })
            mark_status_changed()
            with self._worker_heartbeats_lock:
                self._worker_current_urls[worker_idx] = url
                self._worker_url_generations[worker_idx] = run_generation
            return "claimed", run_generation

    def _process_worker_url(self, worker_idx, browser, url,
                            persistent_ctx=None, run_generation=None):
        """Claim, map, and process one URL with an unambiguous result."""
        claim_result, run_generation = self._claim_worker_item(
            worker_idx, url, run_generation)
        if claim_result != "claimed":
            return claim_result
        try:
            self._update_job(
                url, "running", "Claimed by worker",
                _transition_prev_status="pending",
                _memory_already_updated=True)
            self._process_one(browser, url, persistent_ctx=persistent_ctx)
            return self._WORKER_CLAIM_PROCESSED
        finally:
            with self._worker_heartbeats_lock:
                if (self._worker_url_generations.get(worker_idx) == run_generation
                        and self._worker_current_urls.get(worker_idx) == url):
                    self._worker_current_urls.pop(worker_idx, None)
                    self._worker_url_generations.pop(worker_idx, None)

    def _resource_admission_hold(self):
        """Return a visible hold when a configured resource gate is not safe.

        A return value means the dequeued URL must be requeued.  ``None`` is
        earned only by disabled gates or measured-under-budget results.
        """
        def hold(state, wait_s, reason, report):
            # Publish immediately at the decision seam. The worker loop also
            # emits the event after requeueing, but readers must not need that
            # later side effect to observe why admission was refused.
            self._state = state
            return {"state": state, "wait_s": wait_s, "reason": reason,
                    "report": report}

        try:
            from . import daily_budget as _db_budget
            site_report = _db_budget.is_over_budget(
                self.site_id, site_cfg=self.config)
        except Exception as e:
            configured_site = int(max(0.0, _finite_config_float(
                (self.config or {}).get("daily_byte_budget"), 0.0)))
            if configured_site > 0:
                site_report = {
                    "over": None, "unknown": True, "available": False,
                    "budget_bytes": configured_site,
                    "error": f"site daily-byte gate unavailable: {e}"[:200],
                }
            else:
                site_report = None
        if site_report and site_report.get("budget_bytes", 0) > 0:
            if site_report.get("unknown") or site_report.get("over") is None:
                return hold(
                    "daily_budget_unknown", 60,
                    site_report.get("error") or
                    "site daily-byte counter unavailable", site_report)
            if site_report.get("over"):
                return hold("daily_budget_exhausted", 60,
                            "site daily-byte budget exhausted", site_report)

        try:
            from . import daily_budget as _db_budget_global
            global_report = _db_budget_global.is_over_global_budget()
        except Exception as e:
            global_report = {
                "over": None, "unknown": True, "available": False,
                # An exception here also prevents proving the global gate is
                # disabled, so do not manufacture budget_bytes=0 permission.
                "budget_bytes": None,
                "error": f"global daily-byte gate unavailable: {e}"[:200],
            }
        if global_report.get("unknown") or global_report.get("over") is None:
            if global_report.get("budget_bytes") != 0:
                return hold(
                    "global_daily_budget_unknown", 60,
                    global_report.get("error") or
                    "global daily-byte counter unavailable", global_report)
        elif global_report.get("over"):
            return hold("daily_budget_exhausted", 60,
                        "global daily-byte budget exhausted", global_report)

        try:
            from . import run_budget as _run_budget
            mem_report = _run_budget.is_over_mem_budget(self.config)
        except Exception as e:
            configured_mem = int(max(0.0, _finite_config_float(
                (self.config or {}).get("run_mem_budget_mb"), 0.0)))
            mem_report = {
                "over": None, "unknown": True, "available": False,
                "budget_mb": configured_mem,
                "error": f"RSS admission gate unavailable: {e}"[:200],
            }
        if mem_report.get("budget_mb", 0) > 0:
            if mem_report.get("unknown") or mem_report.get("over") is None:
                return hold(
                    "mem_budget_unknown", 30,
                    mem_report.get("error") or "RSS measurement unavailable",
                    mem_report)
            if mem_report.get("over"):
                return hold("mem_budget_exhausted", 30,
                            "memory budget exhausted", mem_report)
        return None

    def _worker_loop(self, worker_idx=0, run_generation=None):
        """One persistent worker thread. Owns its own playwright + browser
        for the entire lifetime; pulls URLs from self._url_queue and
        processes them one at a time. Exits when:
          • a sentinel (None) is received, OR
          • self._stop is set, OR
          • a fatal error occurs while launching the browser.

        Phase 9: when use_persistent_profile is on, _launch_browser returns
        (None, ctx) and the ctx is reused for the entire worker's lifetime.
        Cookies and Cloudflare's __cf_bm survive across URLs.
        When persistent is off, browser is real and we create a fresh ctx
        per URL (legacy behavior — preserves isolation between URLs).

        Phase 19: worker_idx is used to pick a per-worker profile dir so
        multiple workers don't fight over Chrome's SingletonLock."""
        pw=None; browser=None; persistent_ctx=None
        if run_generation is None:
            with self._worker_heartbeats_lock:
                run_generation = self._worker_run_generation
        worker_context = getattr(self, "_worker_context", None)
        if worker_context is None:
            worker_context = threading.local()
            self._worker_context = worker_context
        previous_generation = getattr(worker_context, "run_generation", None)
        worker_context.run_generation = run_generation
        # Phase 18.fix: track when this worker last refreshed the ctx
        # cookies. Compared against self._cookies_updated_at; when newer
        # cookies are available (e.g. after a re-login), the worker
        # clears+re-adds cookies on the persistent context.
        my_cookie_ts = 0.0
        # F5 Phase 2 (v3.66.701): the browser outlives any single URL -- it is
        # owned by the WORKER -- so the per-capture netns must bracket the whole
        # worker, not one call. An ExitStack (not a `with`) holds it so the
        # namespace is torn down in the existing finally AFTER the browser is
        # closed; tearing the ns down under a live browser would strand it.
        # No opt-in (site_wants_isolation False) -> capture_netns yields None and
        # nothing at all is created: the prior path, unchanged.
        _ns_stack = contextlib.ExitStack()
        try:
            if not _VPN_RUNTIME_AVAILABLE:
                # The soft import cannot tell us whether this site requires a
                # tunnel.  Refuse the worker before browser launch instead of
                # treating an unavailable admission instrument as permission.
                raise RuntimeError(
                    "vpn runtime unavailable; refusing worker startup")
            try:
                netns = _ns_stack.enter_context(netns_isolation.capture_netns(
                    self.config, "browser", f"{self.site_id}/{worker_idx}"))
            except netns_isolation.NetnsRequiredError as e:
                # Fail-closed (mirrors the VPN guard + the 689 subprocess path):
                # the site REQUIRES isolation and cannot have it, so this worker
                # exits WITHOUT ever launching an un-isolated browser.
                sys.stderr.write(f"[{self.site_id}] worker {worker_idx}: {e}\n")
                return
            browser,persistent_ctx,pw,backend=self._launch_browser(
                worker_idx=worker_idx, netns=netns)
            while not self._stop.is_set():
                # v3.43.24: stamp this worker's heartbeat. Watchdog
                # reads these from another thread to detect hung
                # workers (no stamp in >15min = hung). Done outside
                # the per-URL critical section so the stamp updates
                # even if the worker is paused or waiting for
                # session_ok — both are legitimate "alive" states.
                with self._worker_heartbeats_lock:
                    if run_generation != self._worker_run_generation:
                        break
                    self._worker_heartbeats[worker_idx] = time.time()
                self._pause.wait()
                if self._stop.is_set(): break
                # Phase 64 (v3.41.0): bandwidth-aware concurrency. If the
                # effective worker count has been scaled below this worker's
                # index, park here and re-check in 30s. Cheap — the browser
                # stays warm; we just skip the next URL claim.
                if worker_idx >= self._effective_concurrency():
                    self._stop.wait(30)
                    continue
                # v3.43.60: VPN kill-switch gate. If this site's tunnel
                # is killed, wait for it to clear (or skip this iteration
                # and re-check in 30s). Cheap when no VPN is configured —
                # vpn_runtime.maybe_wait_for_vpn returns True immediately.
                try:
                    vpn_ready = bool(vpn_runtime.maybe_wait_for_vpn(
                        self.site_id, timeout=30.0))
                except Exception as e:
                    sys.stderr.write(f"[runner] vpn check raised: {e}\n")
                    vpn_ready = False
                if not vpn_ready:
                    # Down and unavailable are both holds.  Only an explicit
                    # True (including the no-VPN-configured fast path) admits.
                    self._stop.wait(30)
                    continue
                # v3.43.80 Phase 160: maintenance window gate. If any
                # active window pauses 'workers', park here and recheck
                # in 60s. The window detector module is the source of
                # truth — bg_scheduler also fires start/end webhooks.
                # Cheap; failure-isolated so a broken maintenance row
                # never blocks the queue.
                try:
                    from . import maintenance as _mw
                    if _mw.is_action_paused("workers"):
                        self._stop.wait(60)
                        continue
                except Exception:
                    pass
                # v3.43.80 Phase 134: smart wakeup gate. Outside quiet
                # hours this is a no-op (always wake=True). Inside quiet
                # hours with a small pending count, we sleep until either
                # the window ends OR pending exceeds the configured
                # threshold (rechecked every 60s).
                try:
                    from . import smart_wakeup as _sw
                    try:
                        from .global_config import get_config
                        gc = get_config() or {}
                    except Exception:
                        gc = {}
                    decision = _sw.should_wake_now(
                        quiet_hours=gc.get("quiet_hours") or [],
                        wakeup_threshold=int(gc.get("wakeup_threshold", 50)),
                        cool_down_seconds=int(gc.get("wakeup_cool_down_seconds", 14400)))
                    if not decision.get("wake"):
                        self._stop.wait(60)
                        continue
                except Exception:
                    pass
                # Phase 18.fix: wait if a session recovery is in progress.
                # Other workers may be re-logging in; pulling URLs now would
                # just hammer the auth-required path.
                if not self._session_ok.is_set():
                    self._session_ok.wait(timeout=120)
                    if self._stop.is_set(): break
                # Phase 18.fix: refresh cookies in the persistent context if
                # they've been updated since our last sync. Playwright's
                # add_cookies overrides existing cookies by name+domain+path,
                # so we don't need clear_cookies() — that would nuke valuable
                # non-login cookies (Cloudflare __cf_bm, GA, etc.) that build
                # trust over time. The previous "skip if name exists" filter
                # was the actual bug: it left STALE login cookies in place.
                if (persistent_ctx is not None and self.cookies
                        and self._cookies_updated_at > my_cookie_ts):
                    try:
                        persistent_ctx.add_cookies(self.cookies)
                        my_cookie_ts = self._cookies_updated_at
                    except Exception as e:
                        sys.stderr.write(f"[{self.site_id}] cookie refresh failed: {e}\n")
                try: url=self._url_queue.get(timeout=1)
                except queue.Empty: continue
                if url is None:
                    self._url_queue.task_done(); break  # sentinel
                queue_item = url
                if (isinstance(queue_item, tuple) and len(queue_item) == 2
                        and isinstance(queue_item[0], int)):
                    item_generation, url = queue_item
                else:
                    with self._worker_heartbeats_lock:
                        item_generation = self._worker_run_generation
                if item_generation != run_generation:
                    self._requeue_generation_item(item_generation, url)
                    self._url_queue.task_done()
                    continue
                # A status writer may legitimately run after queue insertion.
                # Revalidate at dequeue so stopped/completed work is consumed,
                # never resurrected into processing.
                if not self._generation_item_is_processable(
                        item_generation, url):
                    self._url_queue.task_done()
                    continue
                # v3.45.4 / v3.46.3 Phase 185: worker affinity by tag.
                # When the site has `worker_affinity` config set, each
                # worker only processes URLs whose pre-applied tags
                # match its affinity tag. Algorithm:
                #
                #   1. Worker N dequeues a URL.
                #   2. If config gives N an affinity tag and the URL
                #      doesn't carry that tag, check: does ANY worker
                #      have an affinity matching this URL's tags?
                #   3. If yes → requeue + sleep (let that worker take it).
                #   4. If no → process the URL ourselves (no other worker
                #      will accept it either; better than spinning forever).
                #
                # This avoids the degenerate case where a URL nobody
                # claims gets infinitely requeued. Without the "any
                # other worker?" check, an untagged URL with two
                # tag-bound workers would spin the queue forever.
                #
                # Config shape: {worker_affinity: {0: 'hi-res',
                #                                  1: 'hi-res',
                #                                  2: 'mobile'}}
                # Workers without an entry process any URL (default).
                affinity_map = self.config.get("worker_affinity") or {}
                my_affinity = (affinity_map.get(str(worker_idx))
                                or affinity_map.get(worker_idx))
                if my_affinity:
                    try:
                        from . import tags as _t
                        j = self.jobs.get(url, {})
                        hid = j.get("history_id")
                        url_tags = set()
                        if hid:
                            url_tags = set(_t.tags_for(hid))
                        if hid and my_affinity not in url_tags:
                            # Does any OTHER worker have an affinity
                            # matching one of this URL's tags?
                            other_can_take = any(
                                aff in url_tags
                                for w, aff in affinity_map.items()
                                if str(w) != str(worker_idx))
                            # Also: is there an unrestricted worker
                            # (one without an affinity)? Those take
                            # anything.
                            unrestricted_exists = (
                                len(affinity_map) <
                                self.config.get("max_concurrent", DEFAULT_MAX_CONCURRENT))
                            if other_can_take or unrestricted_exists:
                                # Someone else can take it; requeue
                                self._url_queue.put_nowait(queue_item)
                                self._url_queue.task_done()
                                self._stop.wait(0.2)
                                continue
                            # Nobody else will claim it — fall through
                            # and process it ourselves.
                    except Exception:
                        # Fail-open: if tags module misbehaves, take the URL
                        pass
                # Phase 2 Cut 2.1: dependency gate. A job with `depends_on` waits
                # until the job it depends on is 'done'. If that dependency is
                # permanently gone (dead_letter/failed/missing) the dependent can
                # never run -> dead-letter it (surfaced to the operator) rather
                # than requeue forever. Fail-open: any error means take the URL.
                try:
                    from . import queue_policy as _qp
                    _j = self.jobs.get(url, {})
                    if str(_j.get("depends_on") or "").strip():
                        if _qp.dependency_blocked(_j, self.jobs):
                            self._update_job(url, "dead_letter",
                                             "dependency can never complete (blocked)")
                            try:
                                from .db import db_queue_dead_letter as _dql
                                _dql(self.site_id, url, "dependency blocked")
                            except Exception:
                                pass
                            self._url_queue.task_done()
                            continue
                        if not _qp.dependency_satisfied(_j, self.jobs):
                            # dependency still in flight -> requeue + brief wait
                            self._url_queue.put_nowait(queue_item)
                            self._url_queue.task_done()
                            self._stop.wait(1.0)
                            continue
                except Exception:
                    pass
                # The three resource measurements share one tri-state seam.
                # A configured gate that cannot measure holds this URL just
                # like a measured breach, but publishes a distinct state so
                # operators can fix the instrument rather than wait for a cap.
                resource_hold = self._resource_admission_hold()
                if resource_hold is not None:
                    self._state = resource_hold["state"]
                    self.log_event(
                        "resource_admission_hold",
                        resource_hold["reason"],
                        url=url,
                        extra={"report": resource_hold["report"]},
                    )
                    self._url_queue.put_nowait(queue_item)
                    self._url_queue.task_done()
                    self._stop.wait(resource_hold["wait_s"])
                    continue
                # Phase 6.4: acquire global semaphore (if active) before
                # processing. Released in finally. If no cap, this is a no-op.
                acquired_global=False
                try:
                    if _global_sem is not None:
                        # blocking — but with periodic check for stop signal
                        # via short timeout retries so a stuck cap doesn't
                        # block worker shutdown forever
                        while not self._stop.is_set():
                            if _global_sem.acquire(timeout=0.5):
                                acquired_global=True; break
                        if not acquired_global:
                            self._url_queue.task_done(); continue
                    processed = self._process_worker_url(
                        worker_idx, browser, url,
                        persistent_ctx=persistent_ctx,
                        run_generation=run_generation)
                    if processed in (
                            self._WORKER_CLAIM_STALE,
                            self._WORKER_CLAIM_INELIGIBLE):
                        # Both paths consume the dequeued entry. Stale work has
                        # a replacement generation copy; ineligible work was
                        # explicitly stopped/completed before the claim.
                        pass
                except Exception as e:
                    try:
                        self._handle_failure(
                            url, f"worker error: {str(e)[:100]}",
                            _run_generation=run_generation)
                    except Exception: pass
                finally:
                    if acquired_global and _global_sem is not None:
                        try: _global_sem.release()
                        except ValueError: pass  # already released; defensive
                    self._url_queue.task_done()
                    # Phase 5.8: cheap drift check after every URL.
                    try: self._maybe_drift_recover()
                    except Exception: pass
        except Exception as e:
            # Browser launch failed — fail any URLs already taken from the queue.
            sys.stderr.write(f"[{self.site_id}] worker_loop fatal: {e}\n")
        finally:
            # Phase 9.3: persistent ctx must close on shutdown to flush
            # cookies to disk. Browser handle is None when ctx is persistent.
            if persistent_ctx:
                try: persistent_ctx.close()
                except Exception: pass
            if browser:
                try: browser.close()
                except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass
            # 701: tear the namespace down LAST -- after the browser inside it
            # is gone. Unguarded by design: capture_netns' teardown path
            # (netns_isolation.destroy -> _run) swallows its own errors and is
            # contractually non-raising, so a broad except here would only add a
            # blind spot (and a new DP-13) without protecting anything.
            _ns_stack.close()
            if previous_generation is None:
                try: del worker_context.run_generation
                except AttributeError: pass
            else:
                worker_context.run_generation = previous_generation









    def _dismiss_page_gates(self, page, url):
        """Clear configured/generic gates and publish every observed action."""
        actions = _interstitial.dismiss_gates(
            page,
            self.config.get("dismiss_selectors", ""),
            destination_url=url,
        )
        for action in actions:
            message = action.get("reason", "page gate outcome UNKNOWN")
            if action.get("destination_re_requested"):
                message += "; destination re-requested"
            self.log_event(
                "page_gate", message, url=url, extra=action)
        return actions

    def _page_gates_are_safe(self, page, url):
        """Run/report page gates and hold the job on any UNKNOWN verdict."""
        actions = self._dismiss_page_gates(page, url)
        unknown = _interstitial.first_safety_unknown(actions)
        if unknown is not None:
            message = _interstitial.safety_unknown_diagnostic(unknown)
            self._update_job(url, "needs_review", message)
            return False
        # A re-requested destination is a NEW navigation: the render budget
        # already paid was spent on the upsell page that replaced it. Pay it
        # once more, and only then -- an ordinary URL whose banner was cleared
        # without navigating must not pay a second full render wait.
        if any(action.get("destination_re_requested") for action in actions):
            for _ in range(int(float(self.config.get("wait", 4)) * 2)):
                self._pause.wait()
                if self._stop.is_set():
                    self._update_job(url, "stopped", "Stopped")
                    return False
                time.sleep(0.5)
        return True

    def _process_one(self,browser,url,persistent_ctx=None):  # INV-002
        """Process a single URL.

        Two paths depending on whether a persistent context exists:
          • persistent_ctx is None: legacy path. Caller passed a real browser
            handle; we create a fresh BrowserContext per URL for isolation
            (cookie state can't bleed between URLs).
          • persistent_ctx is set: Phase 9.3 path. Reuse the same context
            for every URL — Cloudflare's __cf_bm and other trust cookies
            survive across URLs and the site sees us as one returning visitor
            instead of N first-time visitors.

        In persistent mode we still create a fresh PAGE per URL (so popups
        and tab-state don't leak), and we DON'T close the context after
        — that's the worker's responsibility on shutdown."""
        with self._lock: job=dict(self.jobs.get(url,{}))
        ra=job.get("retry_after",0)
        if ra and time.time()<ra: self._update_job(url,"pending","Waiting for retry"); return

        # F1.5: pre-download history-match dedup. If this exact URL (or, when
        # dedup_fuzzy is on, a same-filename+size download) is already 'done'
        # in history, skip the re-download and link the prior row. Default-ON
        # for exact URL; force_download bypasses; fail-soft (never blocks).
        _dup = self._dedup_preflight(url, job)
        if _dup:
            self._update_job(url, "skipped_duplicate", _dup)
            return

        # Phase 19: auto-teach for first download. Extracted in v3.43.18.
        if self._handle_auto_teach_check(url, job):
            return

        # v3.43.80 Phase 144: cluster-wide rate limit (federation/multi-
        # machine deployments). If cluster_rate is enabled AND the cap
        # is hit, defer this URL — the worker will pick it up on the
        # next start() pass when a peer has released or its lease has
        # expired. Lease auto-expires after `lease_seconds`; no
        # explicit release needed since BD downloads usually finish
        # well within the default 10min window. Opt-in via config —
        # single-machine setups don't need this (their per-site worker
        # cap already handles concurrency).
        if self.config.get("use_cluster_rate", False):
            try:
                from . import cluster_rate as _cr
                max_cc = int(self.config.get(
                    "cluster_rate_max_concurrent", 4))
                lease_sec = int(self.config.get(
                    "cluster_rate_lease_seconds", 600))
                result = _cr.acquire_lease(self.site_id,
                                           max_concurrent=max_cc,
                                           lease_seconds=lease_sec)
                if not result.get("ok"):
                    self._update_job(url, "pending",
                        f"Cluster rate limit hit "
                        f"({result.get('active_count', '?')}/{max_cc})")
                    return
            except Exception as e:
                # Cluster rate failure must NEVER block downloads —
                # the cluster service is an optimization. Fall through.
                sys.stderr.write(
                    f"[{self.site_id}] cluster_rate.acquire failed, "
                    f"allowing fetch: {e}\n")

        self._update_job(url,"running","Opening page...")

        # Cookie expiry check → auto-relogin. Extracted in v3.43.18.
        if not self._check_cookies_or_relogin(url):
            return

        # v3.43.28: Stash dedup check. If the site has deep Stash
        # integration enabled AND this URL is already in Stash's
        # library, skip the download entirely. Massive bandwidth and
        # disk savings on 8K files (5-7GB each) where the user has
        # already grabbed it manually.
        if self._stash_dedup_check(url):
            return  # Marked as done; nothing left to do

        # v3.43.63: library-extractor fast path. When the site config opts
        # in via `use_library_extractor: True` AND a maintained PyPI
        # extractor library is installed for this URL's host, skip the
        # browser entirely and go straight to a direct/HLS download.
        # This is faster than yt-dlp (no subprocess startup, in-process
        # API), more reliable than CSS scraping (libraries get bumped
        # when sites change), and gives us metadata for tagging.
        # On any failure falls through to the existing JD/qB/teach path.
        if self.config.get("use_library_extractor", False):
            try:
                if self._try_library_extractor(url):
                    return  # extractor downloaded; we're done
            except Exception as e:
                sys.stderr.write(f"  library_extractor: exception {e}\n")
                # fall through to qB/JD/teach

        # PLUGIN-DISPATCH (v3.66.691): a plugin-registered @extractor for this
        # site (exec/node/py-bridge plugin, or GH-2's yt-dlp shim) is tried
        # before the browser path -- makes a plugin-extractor site behave like a
        # native one. Naturally opt-in: no-op (one dict lookup) unless an
        # extractor is registered for self.site_id. Fail-open on any error.
        try:
            if self._try_plugin_extractor(url):
                return  # plugin extractor delivered the file
        except Exception as e:
            sys.stderr.write(f"  plugin_extractor: exception {e}\n")
            # fall through to the rest of the dispatch chain

        # v3.43.68: HereSphere/DeoVR JSON API short-circuit. When the
        # site has been confirmed (at site-add time, via wizard probe)
        # to expose either protocol, we can skip the browser entirely
        # and hit the API for structured tier data + direct stream
        # URLs. Off by default; turned on by setting `use_jsonapi=True`
        # and populating `jsonapi_url` (the endpoint base, e.g.
        # `https://members.<site>.com/heresphere` or for split-API
        # sites like NaughtyAmerica `https://api.naughtyapi.com/heresphere`).
        # Fail-open — on any failure falls through to the rest of the
        # dispatch chain. The JSON API endpoint isn't always populated
        # for every scene (some sites only expose a subset of their
        # library); we want a graceful degradation, not a hard failure.
        if self.config.get("use_jsonapi", False):
            try:
                if self._try_jsonapi_extractor(url):
                    return  # JSON API delivered the file
            except Exception as e:
                sys.stderr.write(f"  jsonapi: exception {e}\n")
                # fall through

        # v3.43.21: JD-backend short-circuit. Sites with backend="jd"
        # (brazzers, bangbros, adulttime, xnxx, xempire, evilangel,
        # filthykings, etc. where JD's plugins work well) skip the
        # Playwright path entirely. The session keeper holds the auth
        # state; we forward the URL to JD's local Remote API with the
        # latest cookies inline and poll for completion.
        # On any JD failure the runner falls through to the teach path
        # below — URL still completes, just slower. This means JD being
        # absent / broken doesn't break the queue; it just degrades to
        # current-day behavior.
        # v3.43.26: qB takes precedence for torrent/magnet URLs (auto-
        # routing) AND for sites with backend="qbittorrent" (explicit).
        # Order: qB-explicit OR torrent-URL → qB → fallback chain → teach.
        try:
            from . import jd_bridge as _jd_bridge_mod
            _is_jd = _jd_bridge_mod.is_jd_backend(self.config)
        except Exception:
            _is_jd = False
        try:
            from . import qb_bridge as _qb_bridge_mod
            _is_qb_explicit = _qb_bridge_mod.is_qb_backend(self.config)
            _is_torrent_url = _qb_bridge_mod.looks_like_torrent_url(url)
            _use_qb = _is_qb_explicit or _is_torrent_url
        except Exception:
            _use_qb = False
            _is_torrent_url = False
        # qB takes priority for torrent URLs regardless of backend
        # setting (you can't HTTP-download a magnet link).
        if _use_qb:
            dl_dir_qb = self.config.get("download_dir") or ""
            ok, _reason = self._try_qb_download(url, dl_dir_qb)
            self._record_qb_outcome(ok)
            if ok:
                return
            # qB failed — for torrent URLs there's no useful fallback
            # (teach can't download a magnet). Mark needs_review and
            # exit so the user can see what happened.
            if _is_torrent_url:
                self._update_job(url, "needs_review",
                    f"qB failed and URL is torrent-style: {_reason}")
                return
            # For explicit-backend sites, fall through to JD/teach below.
        if _is_jd:
            dl_dir_jd = self.config.get("download_dir") or ""
            ok, _reason = self._try_jd_download(url, dl_dir_jd)
            self._record_jd_outcome(ok)
            if ok:
                return
            # JD failed — fall through to the existing teach path.
            # _try_jd_download has already emitted a jd_fallback event
            # so the user can see why; no need to log again here.

        # Phase 9.3: pick context strategy
        if persistent_ctx is not None:
            ctx=persistent_ctx
            ctx_owned=False  # don't close on exit
            # Cookie injection is handled by the worker loop's refresh check
            # before each URL — it injects fresh cookies whenever
            # self._cookies_updated_at advances. The previous inline path
            # used a "skip if name exists" filter, which left stale session
            # cookies after re-login and was the root cause of the
            # Session-expired storm.
        else:
            ctx=browser.new_context(**self._context_options(
                headless=bool(self.config.get("headless", True))))
            ctx_owned=True
            self._install_stealth(ctx)
        try:
            if ctx_owned and self.cookies: ctx.add_cookies(self.cookies)
            page=ctx.new_page()
            # v3.43.56: apply playwright-stealth library if configured
            self._apply_stealth_library_to_page(page)
            # Phase 13: capture JS errors + (optionally) network log on
            # this page. Listeners auto-detach when the page closes.
            self._install_event_listeners(page, url)
            # Phase 15.7: session warming — visit a few configured warmup
            # URLs before the real target so we look like a returning
            # visitor instead of someone who deep-links straight to /v/123.
            # Skipped if recently warmed (configurable), if no warmup
            # URLs are configured, or if the persistent profile already
            # has __cf_bm cookies (already trusted).
            try: self._warm_session(page)
            except Exception as e:
                sys.stderr.write(f"  warmup error (non-fatal): {str(e)[:80]}\n")
            try: page.goto(url,wait_until="domcontentloaded",timeout=30000)
            except PWTimeout:
                self._handle_failure(url,"Page load timeout"); return
            # v3.45.8 Phase 186: pre-download macro replay. If the site
            # has `pre_download_macro` configured (a stored macro name),
            # run its actions against the freshly-loaded page. Useful for
            # age-gate clickthroughs, dismissing overlays, or clicking
            # "show high-res" before the teach path looks for download
            # selectors. Fail-open: if the macro is missing or any action
            # fails, log + continue (the teach path may still succeed).
            _premacro = (self.config.get("pre_download_macro") or "").strip()
            if _premacro:
                try:
                    from . import macro_recorder as _mr
                    bundle = _mr.get_macro(self.site_id, _premacro)
                    if bundle and bundle.get("actions"):
                        # replay_macro persists last_replay_* via
                        # mark_replay_result when site_id+name are passed.
                        # strict=False so a missing optional dismiss
                        # button doesn't fail the whole pre-flight.
                        result = _mr.replay_macro(
                            page, bundle,
                            site_id=self.site_id, name=_premacro,
                            strict=False)
                        if not result.get("ok"):
                            sys.stderr.write(
                                f"  pre_download_macro '{_premacro}' "
                                f"failed at action "
                                f"{result.get('failed_at')}: "
                                f"{(result.get('error') or '')[:100]}\n")
                    elif bundle is None:
                        sys.stderr.write(
                            f"  pre_download_macro '{_premacro}' "
                            f"not found in storage\n")
                except Exception as e:
                    # Module-level failure shouldn't break the download
                    sys.stderr.write(
                        f"  pre_download_macro exception: {str(e)[:120]}\n")
            # v3.43.73: Turnstile detection + bypass. If the page is a
            # Cloudflare Turnstile challenge AND the site has opted in
            # (`use_scrapling_turnstile`) AND Scrapling+StealthyFetcher
            # are available, run a one-shot bypass that returns
            # post-challenge cookies. Inject those into the live
            # context and reload — subsequent requests skip the
            # challenge.
            _try_scrapling_turnstile(self, page, ctx, url)
            # v3.43.74: FlareSolverr fallback. If the page still looks
            # like a Cloudflare challenge AND `use_flaresolverr` is
            # configured AND an endpoint is set, POST to the external
            # FlareSolverr service to solve the challenge. Inject the
            # returned cookies into the live context and reload.
            #
            # This is independent of the Scrapling path above — both
            # can be enabled, with Scrapling running first (lighter on
            # FlareSolverr load) and this fallback only if Scrapling
            # didn't succeed or wasn't enabled. Skipping the duplicate
            # detection cost: we only do the check if Scrapling didn't
            # already detect-and-bypass on this page.
            if (_FLARE_AVAILABLE and _flare is not None
                    and self.config.get("use_flaresolverr", False)
                    and self.config.get("flaresolverr_endpoint", "")):
                try:
                    html_now = page.content()
                except Exception:
                    html_now = ""
                # Use Scrapling's detector if available; otherwise a
                # cheap signal-string check inline.
                is_challenge = False
                if _SCRAPLING_AVAILABLE and _scrap is not None:
                    try:
                        is_challenge = _scrap.is_turnstile_page(html_now)
                    except Exception:
                        is_challenge = False
                if (not is_challenge) and html_now:
                    # Inline fallback detector (mirrors Scrapling's signal list)
                    is_challenge = any(
                        s in html_now for s in (
                            "challenges.cloudflare.com",
                            "cf-turnstile",
                            "cf-clearance-required",
                            "turnstile-wrapper",
                        )
                    )
                if is_challenge:
                    self.log_event(
                        "flaresolverr_solve_start",
                        "Cloudflare challenge — invoking FlareSolverr",
                        url=url,
                    )
                    try:
                        ua = page.evaluate("() => navigator.userAgent")
                    except Exception:
                        ua = self.config.get("user_agent", "")
                    endpoint = self.config.get(
                        "flaresolverr_endpoint",
                        "http://localhost:8191/v1")
                    flare_timeout = float(self.config.get(
                        "flaresolverr_timeout_s", 60.0) or 60.0)
                    flare_max_ms = int(self.config.get(
                        "flaresolverr_max_timeout_ms", 60000) or 60000)
                    sr = _flare.solve_cloudflare(
                        url,
                        endpoint=endpoint,
                        timeout_s=flare_timeout,
                        max_timeout_ms=flare_max_ms,
                        user_agent=ua,
                    )
                    if sr.ok and sr.cookies:
                        try:
                            ctx.add_cookies(sr.cookies)
                            page.goto(url, wait_until="domcontentloaded",
                                      timeout=30000)
                            self.log_event(
                                "flaresolverr_solved",
                                f"solve ok ({sr.elapsed_s:.1f}s); "
                                f"injected {len(sr.cookies)} cookies",
                                url=url,
                            )
                        except Exception as e:
                            self.log_event(
                                "flaresolverr_inject_failed",
                                f"cookies received but inject raised: {e}",
                                url=url,
                            )
                    else:
                        self.log_event(
                            "flaresolverr_failed",
                            f"solve failed: {sr.error}",
                            url=url,
                        )
            # Phase 18.26: opportunistic thumbnail. The page is already
            # loaded for download; reading og:image is essentially free
            # (single querySelector, no network). We store the URL on the
            # job so the UI can render it as a small thumbnail next to
            # the URL row.
            try:
                og = page.evaluate("""() => {
                    const meta = document.querySelector('meta[property="og:image"]') ||
                                 document.querySelector('meta[name="twitter:image"]');
                    return meta ? meta.getAttribute('content') : null;
                }""")
                if og and og.startswith("http"):
                    with self._lock:
                        if url in self.jobs: self.jobs[url]["thumbnail"] = og
            except Exception:
                pass
            # Phase 7.2: captcha auto-pause. Extracted in v3.43.18.
            if not self._handle_captcha_check(page, url):
                # v3.43.60: auto-solve failed (URL marked needs_review).
                # If the per-site relay is enabled, register a pending
                # captcha + push the user to RDP-in-and-solve.
                if _CAPTCHA_RELAY_AVAILABLE and self.config.get("use_captcha_relay"):
                    try:
                        captcha_relay.check_and_handle(
                            page, self.site_id, url,
                            worker_idx=getattr(self, "_current_worker_idx", None),
                        )
                    except Exception as _ce:
                        sys.stderr.write(
                            f"[runner] captcha_relay.check_and_handle failed (non-fatal): {_ce}\n"
                        )
                return
            chk=self._check_redirect(page,url)
            if chk=="rl":
                self.trigger_rate_limit(url,f"Rate limit at {page.url}"); return
            if chk=="auth":
                self._handle_auth_required(url); return
            for _ in range(int(float(self.config.get("wait",4))*2)):
                self._pause.wait()
                if self._stop.is_set(): self._update_job(url,"stopped","Stopped"); return
                time.sleep(0.5)
            # Clear layered content gates before looking for the download.
            # Configured selectors are the measured per-site tier and run
            # first; conservative generic consent, age, and upsell tiers follow
            # one click at a time.  The helper refuses exit/decline controls,
            # restores the requested origin after any escape, and re-requests
            # this exact URL after an upsell interstitial sends us home.
            _interstitial.clear_gates(
                page,
                site_gates=self.config.get("dismiss_selectors", ""),
                url=url,
                log=lambda message: self.log_event(
                    "gate", message, url=url),
            )
            trigger=self.config.get("trigger_selector","").strip()
            # Phase 5.5: learned trigger selectors as fallback for the
            # configured one. If neither produces a click, the modal-based
            # download flow (wowgirls etc.) won't open and find_best falls
            # back to direct-link selectors on the still-unopened page.
            learned_dl=(self.config.get("learned",{}) or {}).get("download",{}) if isinstance(self.config.get("learned"),dict) else {}
            # v3.66.144: merge enabled reviewed-template selectors (e.g. the
            # app.reptyle.com template) into learned_dl BEFORE the trigger scan,
            # so reviewed trigger/row selectors are tried first and the download
            # path prefers the template's real media/API URLs over generic links.
            # v3.66.240 (B2): a per-site draft-test override (if set via
            # /api/template/test_extract) is passed through here as a SEPARATE
            # branch -> the only path letting an unreviewed draft drive the run.
            # When no override is set this is byte-identical to the prior call.
            learned_dl, _reviewed_template = merge_template_download_hints(
                page, learned_dl,
                override_template=self._draft_override_template())
            triggers_to_try=([trigger] if trigger else []) + (learned_dl.get("trigger_selectors") or [])
            # v3.65.2: hover-trigger support. Some sites reveal their
            # download menu only on :hover, with no click handler bound
            # to the trigger element. Default behavior is "click", which
            # works for the dominant click-to-open modal pattern (wowgirls,
            # Brazzers, etc.). Sites whose menu is hover-only set
            # trigger_action="hover"; sites whose menu needs hover-then-
            # click (rare — usually a hover-reveal button with its own
            # click handler) set "click_after_hover". Resolution order:
            # per-site config trigger_action wins, then the learned
            # block's trigger_action, then "click".
            trigger_action = (
                (self.config.get("trigger_action") or "").strip()
                or (learned_dl.get("trigger_action") or "").strip()
                or "click"
            ).lower()
            if trigger_action not in ("click", "hover", "click_after_hover"):
                sys.stderr.write(
                    f"  download: unknown trigger_action {trigger_action!r}; "
                    f"defaulting to click\n")
                trigger_action = "click"
            trigger_clicked = False
            for tsel in triggers_to_try:
                try:
                    loc = page.locator(tsel).first
                    loc.wait_for(timeout=5000)
                    if trigger_action in ("hover", "click_after_hover"):
                        # Hover dispatches the real mouseenter/mouseover/
                        # mousemove events that hover-only menus listen
                        # for. A brief settle gives the menu's transition
                        # time to finish before find_best_download scrapes.
                        loc.hover(timeout=2000)
                        time.sleep(0.3)
                    if trigger_action != "hover":
                        loc.click()
                    time.sleep(1.5)
                    sys.stderr.write(
                        f"  download: triggered via [{tsel}] "
                        f"action={trigger_action}\n")
                    trigger_clicked = True
                    break
                except Exception: continue
            # v3.43.73: Scrapling-based selector recovery. If all learned
            # triggers failed AND the site has opted in to recovery AND
            # Scrapling is installed AND we have stored fingerprints, try
            # to re-locate the trigger using content-based matching. The
            # recovered selector is one-shot (not persisted) — if it
            # works repeatedly we'll see it in the event log and can
            # bake it back into the learned set.
            if (not trigger_clicked and triggers_to_try
                    and _SCRAPLING_AVAILABLE and _scrap is not None
                    and self.config.get("use_scrapling_recovery", False)):
                try:
                    html_now = page.content()
                except Exception:
                    html_now = ""
                if html_now:
                    new_sel = self._recover_selector(
                        html_now, kind="download")
                    if new_sel:
                        try:
                            rloc = page.locator(new_sel).first
                            rloc.wait_for(timeout=5000)
                            # v3.65.2: honor the site's trigger_action
                            # for recovered selectors too — if the site
                            # was teaching us a hover-reveal trigger and
                            # the original selector drifted, the recovered
                            # one needs to be hovered, not clicked.
                            if trigger_action in ("hover", "click_after_hover"):
                                rloc.hover(timeout=2000)
                                time.sleep(0.3)
                            if trigger_action != "hover":
                                rloc.click()
                            time.sleep(1.5)
                            sys.stderr.write(
                                f"  download: recovered trigger via "
                                f"[{new_sel}] action={trigger_action}\n")
                            trigger_clicked = True
                        except Exception as _re:
                            sys.stderr.write(
                                f"  download: recovered selector "
                                f"[{new_sel}] still failed: {_re}\n")
            chk=self._check_redirect(page,url)
            if chk=="rl":
                self.trigger_rate_limit(url,f"Rate limit at {page.url}"); return
            if chk=="auth":
                self._handle_auth_required(url); return
            self._update_job(url,"running","Finding download button...")
            # v3.43.65: optional pre-scrape action — click the quality
            # menu to "highest" before find_best_download scrapes the
            # <video src>. Opt-in per site via pre_scrape_action config.
            self._run_pre_scrape_action(page)
            # v3.43.66: Aylo flashvars extractor short-circuit. For
            # Brazzers / RealityKings / BangBros / Mofos / and 11
            # other Aylo brands, the page contains a `flashvars_<id>`
            # JS block whose `mediaDefinitions` array lists every
            # quality variant with direct URLs. Skip the entire
            # find_best_download wide-scan + scoring path and use the
            # variant the user's quality_preference selects.
            # Fail-open — on any error fall through to teach path.
            if self.config.get("use_aylo_extractor", True) and \
               _AYLO_AVAILABLE and _aylo is not None and \
               _aylo.is_aylo_url(url):
                try:
                    if self._try_aylo_extractor(url, page):
                        return  # extractor took over and finished
                except Exception as e:
                    sys.stderr.write(
                        f"  aylo: extractor raised {type(e).__name__}: "
                        f"{str(e)[:100]}\n"
                    )
                    # fall through to teach path
            # v3.43.67: Vixen Media Group extractor short-circuit. For
            # Vixen / Blacked / Tushy / Deeper / Slayed / Wifey /
            # MilfyMD / VixenPlus / BlackedRaw / TushyRaw — three-path
            # extraction: __NEXT_DATA__ → <video src> → GraphQL. After
            # picking a URL it hands off to tier-probe for upgrade (if
            # the user has tier_probe_enabled, which makes sense for
            # Vixen given the predictable mp4_<N>/ path segments).
            if self.config.get("use_vixen_extractor", True) and \
               _VIXEN_AVAILABLE and _vixen is not None and \
               _vixen.is_vixen_url(url):
                try:
                    if self._try_vixen_extractor(url, page):
                        return  # extractor took over and finished
                except Exception as e:
                    sys.stderr.write(
                        f"  vixen: extractor raised {type(e).__name__}: "
                        f"{str(e)[:100]}\n"
                    )
            # v3.43.69: dl8-video VR extractor short-circuit. For
            # TmwVRnet / BadoinkVR / BabeVR / VRCosplayX / 18VR /
            # RealVR, the page contains a <dl8-video> custom element
            # with per-tier <source> children. For Badoink-family
            # hosts the trailer URLs can predict member-area URLs
            # via HEAD probing — skip the member-area HTML entirely.
            if self.config.get("use_dl8_extractor", True) and \
               _DL8_AVAILABLE and _dl8 is not None and \
               _dl8.is_dl8_url(url):
                try:
                    if self._try_dl8_extractor(url, page):
                        return  # extractor took over and finished
                except Exception as e:
                    sys.stderr.write(
                        f"  dl8: extractor raised {type(e).__name__}: "
                        f"{str(e)[:100]}\n"
                    )
            # Phase 5.5: pass learned block so find_best can try learned
            # row_selectors before the full 14-element-type sweep.
            best=find_best_download(page,self.config.get("dl_selector","").strip(),
                                    learned=learned_dl,runner=self)
            # F9/F10 detect-side: by now the page's fingerprinting (if any)
            # has executed; read back and report what was observed.
            self._flush_fingerprint_observation(page, url)
            if best and best.get("_via_learned"):
                won_sel=best.get("_learned_sel","")
                sys.stderr.write(f"  download: learned hit via [{won_sel}]\n")
                _bump_learned_stat(self.config,"download_hits")
                # Phase 7.3: per-selector hit. Bump THIS selector's hit
                # count. Move it to the front of row_selectors next time
                # so the most-reliable pattern is tried first.
                _bump_per_selector(self.config,"download","row_selectors",won_sel,"hits")
            elif best:
                # Auto path matched via wide-scan — every learned selector we
                # had got a "miss" because none of them produced this hit.
                if learned_dl.get("row_selectors"):
                    _bump_learned_stat(self.config,"download_misses")
                    for stale_sel in learned_dl.get("row_selectors") or []:
                        _bump_per_selector(self.config,"download","row_selectors",stale_sel,"misses")
                    _maybe_demote_selectors(self.config,"download","row_selectors")
            if not best:
                chk=self._check_redirect(page,url)
                if chk=="rl":
                    self.trigger_rate_limit(url,f"Rate limit at {page.url}"); return
                if chk=="auth":
                    self._handle_auth_required(url); return
                # v3.66.6 — Backlog #7: deep_detect fallback. Opt-in via
                # per-site `deep_detect_fallback=True` or
                # `template_auto_detect_mode='deep'`. Tries the offline
                # deep_detect analyzer on the current DOM and re-runs
                # find_best_download with the newly discovered selectors.
                # Silent on failure — falls through to the existing
                # no-button-found handling below.
                best = self._try_deep_detect_fallback(page, url, learned_dl)
                if best:
                    sys.stderr.write(
                        f"  download: deep-detect rescued the scrape "
                        f"(score={best.get('score')})\n")
                    # Reset drift counter (a deep-detect rescue still
                    # counts as a successful scrape from the operator's
                    # POV — the original template just needs updating).
                    try:
                        from . import selector_drift as _sd
                        _sd.record_success(self.site_id)
                    except Exception:
                        pass
                    # Fall through to the downstream download path
                    # (the code immediately after this if-block treats
                    # `best` as the live result).
            if not best:
                ss=self._screenshot(page,url)
                self._consec_no_btn+=1
                threshold=int(self.config.get("no_button_threshold",5))
                if self._consec_no_btn>=threshold:
                    self._state="paused_no_button"; self._pause.clear()
                    self._flush_daily_byte_accumulators()
                # Phase 198: record this 0-match against the drift counter
                # so the Review tab can flag persistent template breakage.
                # Only when no redirect classification matched — auth/rate-
                # limit-triggered zero matches aren't real drift.
                try:
                    from . import selector_drift as _sd
                    _sd.record_zero_match(
                        self.site_id,
                        self.config.get("dl_selector", ""),
                        url)
                except Exception:
                    pass
                self._handle_failure(url,"No download button found",screenshot=ss); return
            # We DID find a download — reset the drift counter so a stretch
            # of failures + one success doesn't keep flagging the site.
            try:
                from . import selector_drift as _sd
                _sd.record_success(self.site_id)
            except Exception:
                pass
            self._consec_no_btn=0

            # Min-resolution gate
            min_res=int(float(self.config.get("min_resolution", DEFAULT_MIN_RESOLUTION) or 0))  # v3.66.527: float() so a non-API (hand-edit/overlay) fractional value truncates, not ValueError
            with self._lock:
                forced=bool(self.jobs.get(url,{}).get("force_download"))
            # Phase 67: explicit quality preference order. Extracted in v3.43.18.
            qpref = (self.config.get("quality_preference") or "").strip()
            if qpref and not forced:
                best = self._apply_quality_preference(best, qpref)
            if min_res>0 and best["score"]>0 and best["score"]<min_res and not forced:
                ss=self._screenshot(page,url)
                avail=res_label(best["score"])
                # Format the candidate list so the user can see exactly what
                # the scorer found. Helps diagnose "why didn't it pick 4K?"
                # cases where 4K is on the page but not detected.
                seen=" | ".join(
                    f"{res_label(c['score'])}({fmt_bytes(c['size']) or '?'}):{c['text'][:30]}"
                    for c in best.get("_all_candidates",[])[:6])
                msg=f"Best is {avail} (below {min_res}p) — Approve to force. Saw: {seen}"
                # v3.43.12: log to stderr too so users watching the terminal
                # can see WHY URLs are sitting in needs_review silently.
                # Previously only _update_job + db_log were called, both of
                # which write to in-memory state / SQLite respectively, and
                # the user couldn't tell from the terminal that the worker
                # had reached this branch.
                sys.stderr.write(
                    f"  download: skipped {url[-40:]} — best is {avail} "
                    f"(below min_res={min_res}p). Saw: {seen[:120]}\n")
                self._update_job(url,"needs_review",msg,screenshot=ss)
                db_log(self.site_id,self.config.get("name","?"),url,"needs_review","",0,
                       f"below {min_res}p; got {avail}; saw: {seen}",ss)
                return

            lbl=res_label(best["score"])
            if best.get("size"): lbl+=f" • {fmt_bytes(best['size'])}"
            self._update_job(url,"running",f"Clicking [{lbl}]...")
            # GCW probe mode (v3.66.274): trigger -> media -> first bytes ->
            # abort. The trigger still fires (so the media URL + session are
            # real), but we fetch only the first bytes and write NO file, so no
            # download_dir is needed — route around the no-dl-dir branch below.
            if bool(self.jobs.get(url,{}).get("probe")):
                self._do_download(page,ctx,url,best,None,lbl,probe=True)
                return
            dl_dir=self.config.get("download_dir","").strip()
            if not dl_dir:
                # Fall back to the deployment default rather than discarding the
                # file. A blank per-site download_dir means "the operator has not
                # chosen one" -- it is load-bearing state that the GCW-4 promote
                # gate reads -- so it is NOT filled in the config; it is resolved
                # here, where the file is about to be written.
                #
                # Without this the branch below marked the job `done` with a
                # zero-byte history row. Measured on the box 2026-07-29: every
                # seeded URL returned `"message": "Clicked (no dl dir)"`,
                # `"filename": ""`, which left L12 (segmented download) and L14
                # (dedup skip) unable to clear on a working pipeline.
                #
                # Lazy import for the same reason app_queue.py uses one: at
                # module scope it would close an import cycle with app.
                try:
                    import importlib
                    dl_dir = str(getattr(importlib.import_module(
                        "bulk_downloader.app"),
                        "_oi_default_download_dir")() or "").strip()
                except Exception:
                    dl_dir = ""
                if dl_dir:
                    sys.stderr.write(
                        f"  download: site has no download_dir; using the "
                        f"deployment default {dl_dir}\n")
            if not dl_dir:
                # No dl dir configured and no default resolvable — click and
                # assume the browser handles it. Reachable only when the default
                # resolver itself fails.
                best["locator"].click(); time.sleep(float(self.config.get("delay",3)))
                self._update_job(url,"done","Clicked (no dl dir)")
                # Nothing was fetched: the locator was clicked and the
                # browser assumed responsible. Measured on the box 2026-07-29
                # producing done rows with an empty filename and no file.
                db_log(self.site_id,self.config.get("name","?"),url,"done","",0,"",
                       bytes_fetched=0,
                       **self._history_title_fields(url))
                return
            # Phase 20.6: auto-spillover. If `spillover_dirs` is configured,
            # pick the first dir (primary OR spillover) with enough free
            # space. Falls back to the primary if all are below threshold;
            # the existing low_disk check will catch that case and pause
            # the queue with a clear error.
            try:
                from .hooks import resolve_download_dir
                chosen, reason = resolve_download_dir(
                    self.config,
                    free_threshold_pct=float(self.config.get("spillover_threshold_pct", 5.0) or 5.0),
                )
                if chosen and chosen != dl_dir:
                    sys.stderr.write(f"  spillover: using {chosen} ({reason})\n")
                    dl_dir = chosen
            except Exception as e:
                sys.stderr.write(f"  spillover: error ({e}); using primary\n")
            Path(dl_dir).mkdir(parents=True,exist_ok=True)
            self._do_download(page,ctx,url,best,Path(dl_dir),lbl)
        finally:
            # Phase 9.3: only close the context if we own it. When the
            # worker passes a persistent_ctx, that ctx lives for the worker's
            # whole lifetime so __cf_bm and other trust cookies survive.
            if ctx_owned:
                try: ctx.close()
                except Exception: pass
            else:
                # Persistent path: just close the page so the next URL
                # starts fresh. Context (and cookies) stay.
                try: page.close()
                except Exception: pass



    # ── v3.43.28: deep Stash integration ───────────────────────────────







    # ── v3.43.29: deep Plex integration ────────────────────────────────



    # ── v3.43.37: deep Jellyfin integration ────────────────────────────



    # ── v3.43.26: qBittorrent bridge integration ───────────────────────





    # ── v3.43.21: JDownloader 2 bridge integration ─────────────────────





    # ── v3.43.64: metadata embedding ──────────────────────────────────


    # ── v3.43.68: HereSphere/DeoVR JSON API extractor ─────────────────


    # ── v3.43.67: Vixen Media Group extractor ─────────────────────────


    # ── v3.43.69: dl8-video VR + Badoink filename prediction ────────


    # ── v3.43.66: Aylo flashvars extractor ───────────────────────────


    # ── v3.43.65: tier-probe + pre-scrape action ──────────────────────















    # ── Phase 17.15: Parallel chunk download ───────────────────────────────




    # ── Phase 9: Cloudflare-resistant browser launch ──────────────────────
    # Combines:
    #   9.1 — system Chrome channel (instead of bundled Chromium)
    #   9.2 — stealth init script applied to every new page
    #   9.3 — persistent user data dir per site (cookies survive launches,
    #         which Cloudflare's __cf_bm tracking relies on for "trusted"
    #         scoring)
    #
    # Each site can opt out via `use_real_chrome=False`, `use_stealth=False`,
    # or `use_persistent_profile=False`. Defaults all True.






    # ── Phase 13: Event log + per-URL timeline ─────────────────────────────






    # ── Phase 17.18: Bandwidth schedule ────────────────────────────────────


    # ── Phase 17.19: Auto chunk-size tuning ───────────────────────────────


    # ── Phase 17.16: Mirror failover ───────────────────────────────────────







    # Phase 6.3: retry schedule per error category. Permanent errors don't
    # retry at all; rate limits get long delays; network gets quick retries.
    _RETRY_DELAYS_BY_KIND={
        "permanent": [],                  # don't retry, fail immediately
        "rate_limit": [3600, 7200, 14400],# 1h, 2h, 4h
        "network":   [30, 120, 600],      # 30s, 2m, 10m
        "transient": [600, 3600],         # 10m, 1h (legacy default)
    }
