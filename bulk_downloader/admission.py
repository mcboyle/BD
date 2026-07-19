"""Shared pre-admission gate for download workers — Track F1-A.

A single seam that consolidates the signals which should *hold* a site from
admitting new work, plus a retry-scheduling helper that avoids firing a
backed-off retry inside a known-closed download window.

    F1.2  disk-aware admission   -> ``admission_hold`` reason ``"low_disk"``
    F1.3  cookie-expiry admission -> ``admission_hold`` reason ``"cookies_expired"``
    F1.1  bad-hours retry         -> ``next_eligible_retry`` snaps a retry
                                      timestamp forward to the next active
                                      window open

Design contract — **fail open**. Admission is a convenience gate, never a
safety gate: any internal error (bad config, unreadable cookie file, clock
weirdness) must *allow* work rather than wedge a site. ``admission_hold``
returns ``None`` (= admit) on any exception; ``next_eligible_retry`` returns
its input timestamp unchanged on any exception.

Notes on scope (honest accounting):
  * F1.2's runtime low-disk refusal already lived inline in ``runner.start()``
    prior to this cut. This module is the single reusable source of the same
    signal (so the cookie gate, tests, and a future consolidation share one
    function); the genuinely new *runtime* behaviour delivered here is the
    held-before-start cookie gate (F1.3) and the retry snap-to-window (F1.1).
  * The cookie gate is **opt-in** (``cookie_admission_enabled``) and
    conservative: it holds only when the jar's dated cookies are *all* expired
    and at least one dated cookie exists. Session cookies (no expiry) are
    treated as "can't prove expired" -> do not hold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

# Default system-free threshold in GiB. Mirrors runner.start()'s historical
# default so the consolidated signal matches the inline check it shares with.
DEFAULT_DISK_THRESHOLD_GB = 2.0


def _disk_hold(config: dict, disk_free_fn: Optional[Callable]) -> Optional[str]:
    """F1.2: return "low_disk" when system free space is below threshold."""
    dl_dir = config.get("download_dir", "") or ""
    if not dl_dir:
        return None
    try:
        threshold = float(config.get("disk_threshold_gb", DEFAULT_DISK_THRESHOLD_GB))
    except (TypeError, ValueError):
        threshold = DEFAULT_DISK_THRESHOLD_GB
    # F-COREBD18-01: a non-finite threshold (NaN from "nan", or inf) defeats the
    # `free < threshold` comparison below (free < NaN is always False), silently
    # disabling the low-disk hold -- reject non-finite and use the safe default.
    import math as _math
    if not _math.isfinite(threshold):
        threshold = DEFAULT_DISK_THRESHOLD_GB
    if disk_free_fn is None:
        # Import lazily so this module stays import-light for callers that
        # only want the retry helper.
        from .runner import disk_free_gb as disk_free_fn  # type: ignore
    free = disk_free_fn(dl_dir)
    if free is not None and free < threshold:
        return "low_disk"
    return None


def _cookies_all_expired(cookies, *, now: Optional[float] = None) -> bool:
    """True only when there is at least one cookie carrying an expiry and
    EVERY cookie carrying an expiry is already past. Session cookies (no
    expiry field) are ignored — we can't prove them expired, so their
    presence never triggers a hold on its own."""
    import time as _time

    if now is None:
        now = _time.time()
    dated = 0
    live = 0
    for c in cookies or []:
        try:
            exp = c.get("expires", 0) or c.get("expirationDate", 0)
        except AttributeError:
            continue
        if not exp:
            continue  # session cookie — ambiguous, ignore
        dated += 1
        if exp >= now:
            live += 1
    return dated > 0 and live == 0


def _cookie_hold(config: dict,
                 cookie_loader: Optional[Callable],
                 cookie_path: Optional[str]) -> Optional[str]:
    """F1.3: return "cookies_expired" when an opt-in site's auth jar has
    fully lapsed. Avoids admitting a burst of downloads that would all
    redirect-to-login / 401."""
    if not config.get("cookie_admission_enabled", False):
        return None
    path = cookie_path or config.get("cookie_file", "") or ""
    if not path:
        return None
    if cookie_loader is None:
        from .cookies import load_cookies_from_file as cookie_loader  # type: ignore
    cookies = cookie_loader(path)
    if _cookies_all_expired(cookies):
        return "cookies_expired"
    return None


def admission_hold(config: dict,
                   *,
                   now: Optional[float] = None,
                   disk_free_fn: Optional[Callable] = None,
                   cookie_loader: Optional[Callable] = None,
                   cookie_path: Optional[str] = None) -> Optional[str]:
    """Should this site HOLD new work right now?

    Returns a short reason string (a worker-state token) or ``None`` to admit.
    System-level disk pressure takes precedence over the cookie signal.

    Fail-open: any internal error returns ``None`` (admit).
    """
    if not isinstance(config, dict):
        return None
    try:
        reason = _disk_hold(config, disk_free_fn)
        if reason:
            return reason
        return _cookie_hold(config, cookie_loader, cookie_path)
    except Exception:
        # Admission must never wedge a site on an internal error.
        return None


def next_eligible_retry(retry_after_ts: float,
                        config: dict,
                        *,
                        now: Optional[datetime] = None) -> float:
    """F1.1: given a computed retry timestamp, if it would fire while the
    site's active download window is CLOSED, snap it forward to the next
    window open. Otherwise return it unchanged.

    Inert unless the site has ``window_enabled`` with a valid spec — matches
    the existing window-gating semantics (no window config => no adjustment).

    Fail-open: returns ``retry_after_ts`` unchanged on any error.
    """
    try:
        if not isinstance(config, dict) or not config.get("window_enabled"):
            return retry_after_ts
        from . import download_window as _dw
        retry_dt = datetime.fromtimestamp(retry_after_ts)
        if _dw.site_in_window(config, now=retry_dt):
            return retry_after_ts  # retry lands inside an open window
        delta = _dw.next_transition_seconds(config, now=retry_dt)
        if not delta:
            return retry_after_ts  # no transition / always-open spec
        return retry_after_ts + delta
    except Exception:
        return retry_after_ts
