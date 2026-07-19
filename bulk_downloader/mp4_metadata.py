"""v3.43.64: Embedded MP4 metadata + cover art via mutagen.

# Why this module exists

After v3.43.63 wired in the library-extractor fast path, BD has access
to a lot of *structured metadata* per download:

    ExtractResult(title, author, upload_date, duration_sec,
                  thumbnail_url, quality, ...)

Before this module, all of that was thrown away after the file landed
on disk — the only thing reflected outward was the filename. Media
servers like Plex, Jellyfin, Emby, Infuse, and Stash all read embedded
MP4 metadata atoms when present, so writing those tags during the
download lifecycle means the files are immediately library-ready
without any post-processing pass.

# Architecture

This module is a thin, fail-open wrapper around `mutagen.mp4.MP4`:

  1. `is_available()` -- True if mutagen is importable
  2. `is_mp4_path(path)` -- True if the path looks like an MP4 container
     (extension check + magic-byte check; the magic-byte fallback handles
     files where ffmpeg remuxed an HLS stream and didn't preserve .mp4)
  3. `MetadataContext` -- dataclass bundling everything we know about
     a video. Most fields optional; defaults to empty string / None.
  4. `tag_mp4(path, ctx, cover_bytes=None)` -> bool -- writes the atoms.
     Returns True on success, False on any failure (caller's job state
     should not depend on this succeeding).
  5. `fetch_cover(url, *, timeout=10.0)` -> bytes | None -- downloads a
     thumbnail URL and validates it's actually an image. Returns the
     raw bytes if it looks usable, None otherwise. Size-capped at
     10 MB so a malicious or buggy site can't blow up our memory.
  6. `build_context_from_extract_result(result, **overrides)` --
     convenience constructor for the v3.43.63 library-extractor path.

# Fail-open design

Every public function returns a sentinel (False / None) on any error.
We never raise into the caller. Reasoning:

  - The download SUCCEEDED. The file is on disk. Failing to tag it is
    annoying, not fatal — the user can re-tag manually or via Plex's
    own scanners.
  - Mutagen is a small pure-Python library, but it's still possible
    for it to be absent in weird install environments (frozen exe
    that didn't bundle it, restricted pip mirrors, etc).
  - Some MP4 files are slightly non-conformant — mutagen will refuse
    to write them. Better to skip silently than break the download.

So: try the tag, swallow any exception, log a one-line warning, move on.

# Atom mapping

MP4 metadata uses 4-byte atom IDs, several of which start with the
Apple-reserved \\xa9 byte ("copyright" symbol). Mapping we use:

    \\xa9nam   title              ← ExtractResult.title
    \\xa9ART   artist             ← ExtractResult.author (performer/uploader)
    \\xa9alb   album              ← site display name (e.g. "Pornhub")
    \\xa9day   year/date          ← ExtractResult.upload_date or today
    \\xa9cmt   comment            ← source page URL
    \\xa9too   encoder/tool       ← "BulkDownloader v3.43.64"
    \\xa9gen   genre              ← config.metadata_genre (optional, free-text)
    desc      description        ← config.metadata_description (optional)
    covr      cover art          ← fetched from ExtractResult.thumbnail_url

Any field whose value is empty/None is OMITTED entirely — we never
write empty tags (Plex displays them as a placeholder hyphen).

# Not in this module

  - Filename template variable resolution: that's `fname.py`.
  - Reading metadata FROM files: media servers do that. We're write-only.
  - MKV/AVI/WebM tagging: out of scope for v3.43.64. Most adult-content
    extractors output MP4 anyway (HLS remux defaults to mp4 container).
    A v3.43.65+ release could add mkvtoolnix-driven mkv tagging if needed.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ─── Module-cache for mutagen import ────────────────────────────────
#
# Import once. Cache the result. If the import fails, cache None so
# we don't hammer the importer on every download.
_mutagen_mp4 = None
_mutagen_attempted = False


def _try_import_mutagen():
    """Lazy import of mutagen.mp4. Returns the module on success, None
    if mutagen isn't installed or doesn't have the MP4 submodule."""
    global _mutagen_mp4, _mutagen_attempted
    if _mutagen_attempted:
        return _mutagen_mp4
    _mutagen_attempted = True
    try:
        from mutagen import mp4 as _mp4_mod  # noqa: F401
        _mutagen_mp4 = _mp4_mod
        log.info("mp4_metadata: mutagen.mp4 available (%s)", _mp4_mod.__name__)
    except ImportError as e:
        _mutagen_mp4 = None
        # INFO not WARNING — absence is legitimate (user opted out by
        # not installing it). The audit-noise rule from v3.43.55.
        log.info(
            "mp4_metadata: mutagen not installed (%s); "
            "MP4 tagging disabled — install with `pip install mutagen` "
            "to enable", e,
        )
    except Exception as e:
        _mutagen_mp4 = None
        log.warning("mp4_metadata: mutagen import raised %s: %s",
                    type(e).__name__, e)
    return _mutagen_mp4


def is_available() -> bool:
    """True if MP4 tagging is possible in this process."""
    return _try_import_mutagen() is not None


# ─── MP4 detection ──────────────────────────────────────────────────


# MP4 magic: 4 bytes of size at offset 0, then "ftyp" at offset 4.
# We don't validate the size — only the brand fingerprint.
_MP4_BRANDS = {b"ftyp", b"moov", b"mdat", b"styp"}


def is_mp4_path(path) -> bool:
    """Best-effort check that `path` is an MP4 container.

    Two-stage:
      1. Extension hint (.mp4, .m4a, .m4v, .mov) — fast path
      2. Magic-byte read of the first 12 bytes — handles cases where
         ffmpeg remuxed without a sane extension, or where the file
         was renamed.

    Returns False for nonexistent paths, unreadable files, or anything
    that doesn't look like an MP4. Never raises.
    """
    try:
        p = str(path)
    except Exception:
        return False
    # Cheap extension check covers ~99% of real-world cases
    low = p.lower()
    if low.endswith((".mp4", ".m4a", ".m4v", ".mov")):
        # Still verify the file exists — caller may have a typo'd path
        return os.path.exists(p)
    # Extension wasn't the typical set — peek at magic bytes
    try:
        if not os.path.exists(p):
            return False
        with open(p, "rb") as f:
            head = f.read(12)
        if len(head) < 8:
            return False
        # Standard MP4: bytes 4-8 are the major brand atom name
        if head[4:8] in _MP4_BRANDS:
            return True
        return False
    except Exception:
        return False


# ─── MetadataContext ────────────────────────────────────────────────


@dataclass
class MetadataContext:
    """Everything we might want to write into an MP4's metadata atoms.

    All fields are optional — leave empty string / None / 0 for any
    we don't have. The writer will skip empty values rather than
    writing blank tags.

    Field names map to MP4 atom semantics, not to the source-of-truth
    naming convention. E.g. `artist` is the MP4 "\\xa9ART" atom,
    which in our usage holds the performer / uploader / channel.
    """
    title: str = ""              # \xa9nam
    artist: str = ""             # \xa9ART  -- performer / uploader
    album: str = ""              # \xa9alb  -- site name
    date: str = ""               # \xa9day  -- YYYY or YYYY-MM-DD
    comment: str = ""            # \xa9cmt  -- source URL by default
    encoder: str = ""            # \xa9too  -- "BulkDownloader vX.Y.Z"
    genre: str = ""              # \xa9gen  -- optional config-driven tag
    description: str = ""        # desc     -- optional long description
    cover_url: str = ""          # used by fetch_cover; not an atom itself
    # Free-form extra atoms — write only if both key and value present
    extras: dict = field(default_factory=dict)


# Filenames carry a date in DDMMYYYY, YYYYMMDD, YYYY-MM-DD shapes, etc.
# For the \xa9day atom we only need *something* parseable; iTunes is
# happy with bare YYYY or full YYYY-MM-DD. So we normalize down to one
# of those if we can, otherwise pass the raw string through.
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_DATE_RE = re.compile(r"((?:19|20)\d{2})[-/.\\]?(\d{1,2})[-/.\\]?(\d{1,2})")


def _normalize_date(raw: str) -> str:
    """Return a YYYY or YYYY-MM-DD string suitable for the \\xa9day
    atom, or "" if we can't make sense of the input."""
    if not raw:
        return ""
    s = str(raw).strip()
    m = _DATE_RE.search(s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        try:
            yi, moi, di = int(y), int(mo), int(d)
            if 1 <= moi <= 12 and 1 <= di <= 31:
                return f"{yi:04d}-{moi:02d}-{di:02d}"
        except ValueError:
            pass
    # Maybe just a year
    m = _YEAR_RE.search(s)
    if m:
        return m.group(0)
    return ""


def build_context_from_extract_result(
    result,
    *,
    page_url: str = "",
    site_display_name: str = "",
    encoder_version: str = "",
    extra_genre: str = "",
) -> MetadataContext:
    """Convenience constructor that adapts a v3.43.63 ExtractResult to
    a MetadataContext. Keeps the runner's call site terse.

    Falls back gracefully on missing fields. None of the keyword args
    are required — defaults to "" everywhere so a partial extract still
    produces a usable context.
    """
    # Defensively pull attributes — ExtractResult is a dataclass so
    # these always exist, but a future refactor could rename them.
    title = getattr(result, "title", "") or ""
    author = getattr(result, "author", "") or ""
    upload_date = getattr(result, "upload_date", "") or ""
    thumb = getattr(result, "thumbnail_url", "") or ""
    return MetadataContext(
        title=str(title)[:255],
        artist=str(author)[:255],
        album=str(site_display_name or "")[:255],
        date=_normalize_date(upload_date),
        comment=str(page_url or "")[:1024],
        encoder=str(encoder_version or "")[:255],
        genre=str(extra_genre or "")[:64],
        cover_url=str(thumb or ""),
    )


# ─── Cover-art fetch ────────────────────────────────────────────────


# Hard cap to keep a hostile or buggy CDN from blowing up our memory.
_COVER_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Magic-byte signatures for the two cover formats mutagen supports
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _detect_image_format(b: bytes) -> Optional[str]:
    """Return 'jpeg' / 'png' / None based on magic bytes."""
    if len(b) < 8:
        return None
    if b[:3] == _JPEG_MAGIC:
        return "jpeg"
    if b[:8] == _PNG_MAGIC:
        return "png"
    return None


def fetch_cover(url: str, *, timeout: float = 10.0,
                referer: str = "", user_agent: str = "") -> Optional[bytes]:
    """Download `url` and return its bytes if it's a JPEG or PNG image.

    Returns None on any failure (network error, non-200 response, wrong
    content type, oversized payload, unsupported format). Never raises.

    Caller is responsible for deciding whether to call this; we don't
    enforce a per-site rate limit (covers are small and fetched once
    per download, so the natural cadence is fine).
    """
    if not url or not isinstance(url, str):
        return None
    if not url.lower().startswith(("http://", "https://")):
        # Local file paths or data: URIs not supported
        return None
    try:
        import httpx
    except ImportError:
        log.info("mp4_metadata: httpx not available; cannot fetch cover")
        return None
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    if referer:
        headers["Referer"] = referer
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as r:
                if r.status_code != 200:
                    log.info("mp4_metadata: cover fetch HTTP %s for %s",
                             r.status_code, url[:120])
                    return None
                # Check Content-Type if present; some CDNs serve images
                # with an inferred type. We don't reject on type alone
                # — magic bytes are authoritative — but we DO use the
                # size hint to bail early on obvious junk.
                clen = r.headers.get("content-length")
                if clen:
                    try:
                        if int(clen) > _COVER_MAX_BYTES:
                            log.info("mp4_metadata: cover too large "
                                     "(%s bytes); skipping", clen)
                            return None
                    except ValueError:
                        pass
                # Stream into memory, capped
                buf = bytearray()
                for chunk in r.iter_bytes(64 * 1024):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > _COVER_MAX_BYTES:
                        log.info("mp4_metadata: cover stream exceeded "
                                 "%d bytes; aborting", _COVER_MAX_BYTES)
                        return None
                data = bytes(buf)
    except httpx.RequestError as e:
        log.info("mp4_metadata: cover fetch error %s: %s",
                 type(e).__name__, e)
        return None
    except Exception as e:
        log.warning("mp4_metadata: unexpected error fetching cover: %s", e)
        return None
    # Verify it's actually an image format mutagen accepts
    fmt = _detect_image_format(data)
    if fmt is None:
        log.info("mp4_metadata: cover URL %s returned non-image bytes "
                 "(first 4 = %r)", url[:80], data[:4])
        return None
    return data


# ─── Tagger ─────────────────────────────────────────────────────────


def tag_mp4(path, ctx: MetadataContext, *,
            cover_bytes: Optional[bytes] = None) -> bool:
    """Write the MetadataContext into the MP4 file at `path`.

    Returns True if the file was successfully tagged, False otherwise.
    Reasons for False include: mutagen not installed, file not an MP4,
    file not writable, mutagen refused the container, or no context
    fields were populated (nothing to write).

    Never raises — even if the file gets corrupted by a mutagen bug,
    the caller's job state is independent of the result.

    Cover bytes are passed in separately rather than fetched here so
    the caller can decide on policy (e.g. skip cover if config says
    embed_cover_art=False) without coupling to the network.
    """
    mod = _try_import_mutagen()
    if mod is None:
        return False
    if not is_mp4_path(path):
        log.info("mp4_metadata: %s is not an MP4 file; skipping tag", path)
        return False
    # Build the atoms we actually have values for. Empty-string skipping
    # is intentional — an empty \xa9ART atom causes Plex to render a
    # placeholder "-" rather than just omitting the field.
    atoms: dict = {}
    if ctx.title:
        atoms["\xa9nam"] = [ctx.title]
    if ctx.artist:
        atoms["\xa9ART"] = [ctx.artist]
    if ctx.album:
        atoms["\xa9alb"] = [ctx.album]
    if ctx.date:
        atoms["\xa9day"] = [ctx.date]
    if ctx.comment:
        atoms["\xa9cmt"] = [ctx.comment]
    if ctx.encoder:
        atoms["\xa9too"] = [ctx.encoder]
    if ctx.genre:
        atoms["\xa9gen"] = [ctx.genre]
    if ctx.description:
        atoms["desc"] = [ctx.description]
    # Cover art comes last — it's bigger and we want to skip the
    # mutagen save entirely if there's nothing else to write.
    if cover_bytes:
        fmt = _detect_image_format(cover_bytes)
        if fmt is not None:
            try:
                MP4Cover = getattr(mod, "MP4Cover", None)
                if MP4Cover is not None:
                    fmt_const = (
                        MP4Cover.FORMAT_JPEG if fmt == "jpeg"
                        else MP4Cover.FORMAT_PNG
                    )
                    atoms["covr"] = [MP4Cover(cover_bytes,
                                              imageformat=fmt_const)]
            except Exception as e:
                log.warning("mp4_metadata: cover atom construction "
                            "failed: %s", e)
        else:
            log.info("mp4_metadata: cover bytes had unrecognized "
                     "format; skipping cover atom")
    # User-supplied extras — write only if both key and a non-empty
    # value present. We do NOT validate the atom key length / format
    # because mutagen will raise loudly on a bad key, which we catch
    # below. This keeps the surface area minimal.
    if isinstance(ctx.extras, dict):
        for k, v in ctx.extras.items():
            if k and v:
                atoms[k] = [str(v)] if not isinstance(v, list) else v
    if not atoms:
        # Nothing to write — return False so the caller can log that
        # the tag step was a no-op without trying again.
        log.info("mp4_metadata: no fields populated for %s; "
                 "nothing to tag", path)
        return False
    # Open + write + save. Wrap in a broad except: corruption from
    # a bad container, permission errors, and disk-full all need to
    # be tolerated — the download itself succeeded.
    try:
        MP4 = getattr(mod, "MP4", None)
        if MP4 is None:
            log.warning("mp4_metadata: mutagen.mp4 module missing MP4 class")
            return False
        f = MP4(str(path))
        for k, v in atoms.items():
            f[k] = v
        f.save()
        log.info("mp4_metadata: tagged %s with %d atoms",
                 path, len(atoms))
        return True
    except Exception as e:
        # mutagen raises mutagen.mp4.error or its base mutagen.MutagenError
        # for unsupported containers. Also catches IOError on read-only fs.
        log.warning("mp4_metadata: tag write failed for %s: %s: %s",
                    path, type(e).__name__, e)
        return False


# ─── Filename-template helper bridge ────────────────────────────────
#
# Not strictly required (fname.py has its own variable set) but exposed
# so callers that have a MetadataContext can render a filename in one
# step rather than constructing a separate dict.


def context_to_template_vars(ctx: MetadataContext) -> dict:
    """Convert a MetadataContext into the kwargs dict expected by
    `fname.resolve_filename_template()`. Empty values are kept (as
    empty strings) so a template like `{performer}` resolving to
    nothing won't error — it'll just produce an empty span.

    The fname module sanitizes each value for filesystem-safety, so
    we don't have to scrub here.
    """
    return {
        "title": ctx.title,
        "performer": ctx.artist,    # "performer" is the user-facing name
        "artist": ctx.artist,       # alias for muscle-memory
        "album": ctx.album,
        "studio": ctx.album,        # alias — sites are sometimes called "the studio"
        "site": ctx.album,
        "date": ctx.date,
        "year": ctx.date[:4] if ctx.date else "",
        "encoder": ctx.encoder,
        "genre": ctx.genre,
    }


__all__ = [
    "MetadataContext",
    "is_available",
    "is_mp4_path",
    "tag_mp4",
    "fetch_cover",
    "build_context_from_extract_result",
    "context_to_template_vars",
]
