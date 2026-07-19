"""Subtitle download via subliminal (Phase 89 / 5-star repo integration).

subliminal is the canonical Python subtitle library:
  • Searches OpenSubtitles, Addic7ed, Podnapisi, NapiProjekt
  • Uses guessit for filename → video-metadata extraction
  • Reads MKV/MP4 embedded streams (when present) to skip downloads
  • Caches negative results so repeated misses don't re-query providers

BD uses this opt-in as a post-download step:

  1. Download finishes → BD has a video file path
  2. If subtitles_enabled, call download_for_file(path, languages=[...])
  3. subliminal writes .srt sidecars next to the video
  4. Plex/Jellyfin/Kodi auto-pick them up on next library scan

Fail-open: subliminal is an optional dep. If not installed OR import
fails OR any per-file step raises, we log and skip. Subtitles are a
nice-to-have; they must never block the queue or the actual download.

Provider auth: subliminal supports per-provider configs (some require
accounts, e.g. OpenSubtitles VIP). We accept a `providers` config dict
that maps provider names to credential blobs. Default behavior with
no creds: use the public-only subset.

Concurrency: subliminal does its own threading internally. We treat
each call as blocking and serialize at the BD layer via the
post-download single-threaded pipeline. Don't fire multiple concurrent
calls — providers throttle hard.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, List


# Capability flags — set on import. The rest of BD checks these
# before calling functions in this module.
_HAS_SUBLIMINAL = False
_IMPORT_ERROR: Optional[str] = None
try:
    import subliminal  # type: ignore
    from subliminal import Video, download_best_subtitles, save_subtitles  # type: ignore
    from babelfish import Language  # type: ignore
    _HAS_SUBLIMINAL = True
except ImportError as e:
    _IMPORT_ERROR = str(e)
except Exception as e:
    _IMPORT_ERROR = f"unexpected import error: {e}"


# Common language code → BCP47 / ISO 639-3 mapping for the most
# requested languages. subliminal/babelfish accepts ISO 639-3 codes;
# operators usually think in 2-letter codes.
_LANG_ALIASES = {
    "en": "eng", "english": "eng",
    "es": "spa", "spanish": "spa",
    "fr": "fra", "french": "fra",
    "de": "deu", "german": "deu",
    "it": "ita", "italian": "ita",
    "ja": "jpn", "japanese": "jpn",
    "ko": "kor", "korean": "kor",
    "pt": "por", "portuguese": "por",
    "pt-br": "por",
    "ru": "rus", "russian": "rus",
    "zh": "zho", "chinese": "zho",
    "ar": "ara", "arabic": "ara",
    "nl": "nld", "dutch": "nld",
    "pl": "pol", "polish": "pol",
    "tr": "tur", "turkish": "tur",
}


def is_available() -> bool:
    """True when subliminal imported successfully and can be called."""
    return _HAS_SUBLIMINAL


def import_error() -> Optional[str]:
    """When unavailable, returns the import error message for the UI.
    None when available."""
    return _IMPORT_ERROR


def _normalize_language(code: str):
    """Convert user-friendly code (en, english, eng) into a babelfish
    Language object. Returns None on unknown — caller skips it."""
    if not code:
        return None
    s = code.strip().lower()
    s = _LANG_ALIASES.get(s, s)
    try:
        return Language(s)
    except Exception:
        try:
            return Language.fromalpha2(s)
        except Exception:
            return None


def _is_video_file(path: Path) -> bool:
    """Subliminal supports the standard video extensions. We pre-filter
    so we don't try to fetch subtitles for thumbnails or stray .nfo
    files that wandered into the download dir."""
    ext = path.suffix.lower()
    return ext in {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv",
                   ".webm", ".flv", ".ts", ".mpg", ".mpeg"}


def download_for_file(
    path,
    *,
    languages: Optional[List[str]] = None,
    providers: Optional[List[str]] = None,
    only_one: bool = True,
    overwrite: bool = False,
) -> dict:
    """Download subtitle(s) for one video file. Returns a result dict:
      {ok, downloaded: [lang_code, ...], skipped, error}

    Arguments:
      path: video file path (str or Path)
      languages: list of language codes (BCP47 or ISO). Default ["en"].
      providers: subset of subliminal providers to consult. Default = all.
      only_one: when True, downloads the best subtitle per language.
                When False, downloads all matching (one file per).
      overwrite: when True, re-downloads even if .srt already exists.

    Never raises — failures return {ok: False, error: "..."}.
    """
    if not _HAS_SUBLIMINAL:
        return {"ok": False, "downloaded": [], "skipped": [],
                "error": f"subliminal not installed ({_IMPORT_ERROR or 'unknown'})"}
    p = Path(path)
    if not p.exists():
        return {"ok": False, "downloaded": [], "skipped": [],
                "error": f"file not found: {p}"}
    if not _is_video_file(p):
        return {"ok": False, "downloaded": [], "skipped": [],
                "error": f"not a video extension: {p.suffix}"}
    lang_codes = languages or ["en"]
    langs = []
    for c in lang_codes:
        lang = _normalize_language(c)
        if lang:
            langs.append(lang)
    if not langs:
        return {"ok": False, "downloaded": [], "skipped": [],
                "error": "no valid languages after normalization"}
    # Skip languages where .srt already exists (unless overwrite=True).
    # Convention: <video>.<lang>.srt for additional languages,
    # <video>.srt for the default first language.
    needed = []
    for lang in langs:
        sibling = p.with_suffix(f".{lang.alpha2}.srt") if hasattr(lang, "alpha2") else None
        bare = p.with_suffix(".srt")
        if overwrite or (sibling and not sibling.exists() and not bare.exists()):
            needed.append(lang)
    if not needed:
        return {"ok": True, "downloaded": [], "skipped": [str(l) for l in langs],
                "error": None, "reason": "all languages already have .srt"}
    try:
        video = Video.fromname(str(p))
    except Exception as e:
        return {"ok": False, "downloaded": [], "skipped": [],
                "error": f"subliminal couldn't parse filename: {e}"}
    try:
        kwargs = {"only_one": only_one}
        if providers:
            kwargs["providers"] = list(providers)
        results = download_best_subtitles([video], set(needed), **kwargs)
        subs = results.get(video, [])
        if not subs:
            return {"ok": True, "downloaded": [], "skipped": [],
                    "error": None, "reason": "no subtitles found at any provider"}
        # Save with descriptive single_name (False = always write a
        # language code in the filename) so future BD runs can detect
        # which langs are present without parsing each .srt.
        save_subtitles(video, subs, single=False)
        downloaded = []
        for s in subs:
            try:
                downloaded.append(str(s.language))
            except Exception:
                downloaded.append("unknown")
        return {"ok": True, "downloaded": downloaded, "skipped": [],
                "error": None}
    except Exception as e:
        sys.stderr.write(f"[subtitles] {p.name}: {e}\n")
        return {"ok": False, "downloaded": [], "skipped": [],
                "error": str(e)[:300]}


def status_dict() -> dict:
    """Surface in /api/status for the UI. Tells the operator whether
    subtitle support is even reachable."""
    return {
        "available": _HAS_SUBLIMINAL,
        "error": _IMPORT_ERROR,
        "providers_default": ["opensubtitles", "addic7ed", "podnapisi",
                              "napiprojekt", "tvsubtitles"] if _HAS_SUBLIMINAL else [],
    }
