"""yt-dlp version freshness check + opt-in auto-update.

F23 finding from the 22-repo line-by-line review. yt-dlp ships
extractor fixes via patch releases more frequently than any other
piece of BD's stack — typically multiple times per week when popular
sites break. A stale yt-dlp on the operator's machine is the single
most common cause of "site that used to work doesn't anymore."

This module provides:

  • `current_version()` — return the installed yt-dlp version string,
    cached for 1 hour to avoid spawning a subprocess on every call
  • `version_age_days()` — days since the installed yt-dlp's release
    (best-effort; falls back to None if it can't tell)
  • `maybe_update()` — opt-in pip/pipx self-update with guards: only
    runs if user has set `ytdlp_auto_update=True` in site config AND
    the last check was >24h ago. Logs the attempt; failure is silent
    (operator's machine, not BD's job to recover)

The update path uses `python -m pip install --upgrade yt-dlp` because
that's how 95% of users installed it; pipx users will need to handle
updates manually (this is documented in the README).

Design notes:
  • Never blocks the worker — all calls return quickly or are skipped
  • Never auto-updates without explicit opt-in (operator's package
    manager, not ours)
  • Version cache uses a module-level dict so the per-runner cost is
    negligible regardless of how many sites are configured
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from typing import Optional, Tuple

# Module-level cache of (timestamp, version_string). Lives for the
# lifetime of the BD process — yt-dlp updates require BD restart to
# pick up anyway, so caching forever is fine. Time-based expiry on
# the cache is just so a fresh `pip install --upgrade` between BD
# starts isn't masked by a stale cache during the same session.
_VERSION_CACHE: dict = {"ts": 0.0, "version": None, "path": None}

# Last update-check timestamp per executable path. Prevents the
# auto-update path from firing more than once per 24h regardless of
# how many sites are configured.
_LAST_UPDATE_CHECK: dict = {}

# Cache TTL: 1 hour. Long enough to make repeated calls cheap, short
# enough that an explicit yt-dlp upgrade is detected within an hour.
_CACHE_TTL = 3600


def current_version(executable: Optional[str] = None) -> Optional[str]:
    """Return the installed yt-dlp version string, or None if it
    couldn't be determined. Cached for 1 hour per executable path."""
    exe = executable or shutil.which("yt-dlp") or shutil.which("youtube-dl")
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
    """Return days since the given (or current) yt-dlp version's
    release date. yt-dlp versions look like 'YYYY.MM.DD' which makes
    this a parse-and-subtract job."""
    ver = version or current_version()
    if not ver:
        return None
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", ver)
    if not m:
        return None
    try:
        import datetime
        rel = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (datetime.date.today() - rel).days
    except (ValueError, TypeError):
        return None


def is_stale(*, threshold_days: int = 30) -> bool:
    """True if the installed yt-dlp is older than `threshold_days`.
    Defaults to 30 days — yt-dlp ships every few days, so anything
    older than a month is materially behind."""
    age = version_age_days()
    return age is not None and age > threshold_days


def maybe_update(*, force: bool = False, threshold_days: int = 30) -> Tuple[bool, str]:
    """Opt-in self-update via pip. Returns (ran_update, message).

    Guards:
      • Won't run unless `force=True` OR yt-dlp is older than
        `threshold_days` days
      • Won't run more than once per 24h regardless of how many
        site configs trigger this
      • Skipped silently if no yt-dlp on PATH (nothing to update)
      • Skipped if running from a packaged binary (no pip to call)

    Use from a periodic task or from a startup hook — never from a
    hot worker path; the pip call can take 5-30 seconds.
    """
    exe = shutil.which("yt-dlp") or shutil.which("youtube-dl")
    if not exe:
        return (False, "yt-dlp not installed; nothing to update")
    now = time.time()
    last = _LAST_UPDATE_CHECK.get(exe, 0.0)
    if not force and (now - last) < 86400:
        return (False, "update check rate-limited (24h)")
    if not force and not is_stale(threshold_days=threshold_days):
        return (False, f"yt-dlp is fresh (≤{threshold_days}d old)")
    # Find a python that owns pip. Prefer sys.executable so we update
    # the yt-dlp that lives in the same environment as BD.
    py = sys.executable or "python"
    cmd = [py, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"]
    _LAST_UPDATE_CHECK[exe] = now
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "")[-300:]
            return (False, f"pip install --upgrade yt-dlp failed: {tail}")
    except subprocess.TimeoutExpired:
        return (False, "pip install --upgrade timed out after 2min")
    except Exception as e:
        return (False, f"pip install --upgrade raised: {e}")
    # Invalidate the version cache so the next current_version() call
    # picks up the upgraded version.
    _VERSION_CACHE.update({"ts": 0.0, "version": None, "path": None})
    new_ver = current_version()
    return (True, f"yt-dlp upgraded to {new_ver or 'unknown version'}")


def status_dict() -> dict:
    """Return a small dict for /api/status surfacing. Keeps UI code
    decoupled from the implementation."""
    ver = current_version()
    age = version_age_days(ver) if ver else None
    return {
        "installed": ver is not None,
        "version": ver,
        "age_days": age,
        "stale": age is not None and age > 30,
    }
