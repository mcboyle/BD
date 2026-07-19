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
from typing import Optional

_CACHE: dict = {}


def reset() -> None:
    """Drop the resolution cache (a config change or a test must be able to
    re-resolve without a restart)."""
    _CACHE.clear()


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
