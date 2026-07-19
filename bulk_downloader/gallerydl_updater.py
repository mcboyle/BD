"""gallery-dl version check + opt-in self-update.

C6 (8.4): the gallery-dl analogue of ``ytdlp_updater``. gallery-dl is a managed
fallback extractor for sites BD's Playwright path (and yt-dlp) don't handle;
this module surfaces the installed version and offers an operator-initiated pip
upgrade, mirroring the yt-dlp shim.

Key difference from ytdlp_updater: yt-dlp versions are DATES (``YYYY.MM.DD``), so
freshness is a parse-and-subtract. gallery-dl versions are SEMVER (e.g.
``1.32.5``), which carries no release date -- so age/staleness is NOT locally
derivable and is reported as ``None``/``False`` rather than fabricated. The
update path is therefore operator-forced only (there is no local staleness
signal to trigger it automatically).

  * ``current_version()`` -- installed gallery-dl version, cached 1h
  * ``version_age_days()`` -- always ``None`` (semver has no date)
  * ``is_stale()``        -- always ``False`` (no age signal)
  * ``maybe_update()``    -- opt-in ``pip install --upgrade gallery-dl``; runs
    only with ``force=True`` (24h rate-limited regardless)
  * ``status_dict()``     -- ``{installed, version, age_days, stale}`` for the API
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from typing import Optional, Tuple

# Module-level (timestamp, version, path) cache -- see ytdlp_updater for the
# rationale (per-process; an explicit upgrade between BD starts is picked up
# within the TTL).
_VERSION_CACHE: dict = {"ts": 0.0, "version": None, "path": None}

# Per-executable last-update-check timestamp; rate-limits the pip path to once
# per 24h regardless of how many site configs trigger it.
_LAST_UPDATE_CHECK: dict = {}

_CACHE_TTL = 3600  # 1 hour


def current_version(executable: Optional[str] = None) -> Optional[str]:
    """Return the installed gallery-dl version string, or ``None`` if it can't
    be determined. Cached for 1 hour per executable path."""
    exe = executable or shutil.which("gallery-dl")
    if not exe:
        return None
    now = time.time()
    cache = _VERSION_CACHE
    if cache["path"] == exe and (now - cache["ts"]) < _CACHE_TTL and cache["version"]:
        return cache["version"]
    try:
        r = subprocess.run([exe, "--version"], capture_output=True,
                           text=True, timeout=10)
        if r.returncode != 0:
            return None
        ver = (r.stdout or "").strip().splitlines()[0] if r.stdout else None
    except (subprocess.TimeoutExpired, OSError):
        return None
    _VERSION_CACHE.update({"ts": now, "version": ver, "path": exe})
    return ver


def version_age_days(version: Optional[str] = None) -> Optional[int]:
    """Always ``None``: gallery-dl uses semver (no embedded release date), so an
    age can't be derived locally the way yt-dlp's ``YYYY.MM.DD`` allows. Kept for
    signature parity with ``ytdlp_updater`` and to feed ``status_dict``."""
    return None


def is_stale(*, threshold_days: int = 30) -> bool:
    """Always ``False``: with no derivable age there is no local staleness
    signal. gallery-dl updates are therefore operator-forced (see
    ``maybe_update``)."""
    return False


def maybe_update(*, force: bool = False, threshold_days: int = 30) -> Tuple[bool, str]:
    """Opt-in self-update via pip. Returns ``(ran_update, message)``.

    Because there is no local staleness signal (semver, no date), this only runs
    with ``force=True``; a non-forced call is a no-op. A 24h per-executable rate
    limit applies regardless of ``force``. Skipped silently when gallery-dl is
    not on PATH.
    """
    exe = shutil.which("gallery-dl")
    if not exe:
        return (False, "gallery-dl not installed; nothing to update")
    if not force:
        # No local staleness signal for semver -- never auto-update.
        return (False, "gallery-dl update is operator-forced only "
                       "(no local staleness signal for semver)")
    now = time.time()
    last = _LAST_UPDATE_CHECK.get(exe, 0.0)
    if (now - last) < 86400:
        return (False, "update check rate-limited (24h)")
    py = sys.executable or "python"
    cmd = [py, "-m", "pip", "install", "--upgrade", "--quiet", "gallery-dl"]
    _LAST_UPDATE_CHECK[exe] = now
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "")[-300:]
            return (False, f"pip install --upgrade gallery-dl failed: {tail}")
    except subprocess.TimeoutExpired:
        return (False, "pip install --upgrade timed out after 2min")
    except Exception as e:
        return (False, f"pip install --upgrade raised: {e}")
    _VERSION_CACHE.update({"ts": 0.0, "version": None, "path": None})
    new_ver = current_version()
    return (True, f"gallery-dl upgraded to {new_ver or 'unknown version'}")


def status_dict() -> dict:
    """Small dict for ``/api/gallerydl_status``. ``age_days`` is always ``None``
    and ``stale`` always ``False`` (semver has no date)."""
    ver = current_version()
    return {
        "installed": ver is not None,
        "version": ver,
        "age_days": None,
        "stale": False,
    }
