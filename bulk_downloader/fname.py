"""Filename templating with path-traversal-safe variable substitution.

v3.43.64 adds:
  - KNOWN_VARIABLES — canonical list of placeholders the runner will
    populate. Used by the edit modal hint and template_extractor.
  - available_variable_names() — convenience for UI introspection.
  - format_duration_for_filename() — used by runner to build {duration}.
  - The existing resolve_filename_template() API is unchanged; any
    new variables the runner passes in (e.g. {performer}, {studio},
    {year}, {duration}, {quality}) just flow through it.

The runner is the source of truth for which variables are actually
*populated*. This module just defines how substitution works and
what the catalog looks like.
"""
import re


# ─── Canonical variable catalog (v3.43.64) ─────────────────────────
# Order matters: this is also the display order in the UI hint.
# Each entry: (name, description, example, since_version)
#
# Variables added BEFORE v3.43.64 are kept with their original since
# tag for traceability. New ones in v3.43.64 are documented inline.

KNOWN_VARIABLES = [
    # Pre-v3.43.64 (already populated by runner.py)
    ("filename",   "Site-suggested filename, no extension",     "scene_2024_08_14",      "v3.0"),
    ("stem",       "Alias for {filename}",                       "scene_2024_08_14",      "v3.0"),
    ("ext",        "File extension including the leading dot",  ".mp4",                  "v3.0"),
    ("site",       "Site display name from your config",        "Pornhub",               "v3.0"),
    ("title",      "Page title (from page.title() or extractor)","Hot Yoga Session",      "v3.0"),
    ("resolution", "Heuristic resolution label",                "1080p",                 "v3.43.54"),
    ("date",       "Today's date (download time)",              "2026-05-13",            "v3.0"),
    ("time",       "Today's time (download time)",              "14-32-07",              "v3.0"),
    ("datetime",   "Combined date+time",                        "2026-05-13_14-32-07",   "v3.0"),
    # v3.43.64 — populated when a library extractor or scraper has
    # the relevant field (else empty string, leaving an empty span
    # in the resolved filename, which post-template scrubbing trims).
    ("performer",  "Performer / uploader / channel (extractor)","Mia Khalifa",           "v3.43.64"),
    ("artist",     "Alias for {performer}",                     "Mia Khalifa",           "v3.43.64"),
    ("studio",     "Studio name; falls back to {site}",         "Brazzers",              "v3.43.64"),
    ("year",       "Upload year (extractor.upload_date)",       "2024",                  "v3.43.64"),
    ("upload_date","Full upload date if known (YYYY-MM-DD)",    "2024-08-14",            "v3.43.64"),
    ("duration",   "Video length, HH:MM:SS",                    "00:24:17",              "v3.43.64"),
    ("quality",    "Picked quality label from extractor",       "1080p",                 "v3.43.64"),
    ("extractor",  "Which library was used",                    "phub",                  "v3.43.64"),
]


def available_variable_names():
    """Return list of all known placeholder names. UI uses this to
    populate the edit-modal hint without having to hardcode the list."""
    return [name for (name, _desc, _ex, _since) in KNOWN_VARIABLES]


# ─── FILENAME TEMPLATING ─────────────────────────────────────────────────────
# Replace characters that are illegal on Windows or rough on POSIX. We keep
# forward slashes intact in the FINAL templated string (the user can include
# {date}/{title} to get subdirectories) but sanitize each VARIABLE before
# substitution so a malicious title doesn't escape the download dir.
_FILENAME_FORBIDDEN = re.compile(r'[<>:"|?*\x00-\x1f]')
_FILENAME_PATHSEP = re.compile(r'[\\/]')

# v3.46.4 F2: Windows reserved filenames. These can't be used as file
# names (or even as filename stems with an extension) on Windows; even
# in 2026 they crash CreateFile() with ERROR_INVALID_NAME. macOS/Linux
# tolerate them, but BD frequently writes to NTFS / SMB / exFAT shares
# from Linux and the resulting unusable files only fail at sync time.
# Source: elhacker-main/downloader.py F2 finding.
_RESERVED_WINDOWS_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9",
})


def _is_reserved_windows_name(stem: str) -> bool:
    """A bare filename stem (no extension) matching one of the reserved
    DOS device names. Comparison is case-insensitive and strips the
    extension first so e.g. 'CON.mp4' is also caught."""
    if not stem:
        return False
    # Compare just the part before any dot; Windows treats "CON" and
    # "CON.txt" identically and rejects both.
    base = stem.split(".", 1)[0].upper()
    return base in _RESERVED_WINDOWS_NAMES


def _sanitize_filename_var(value, allow_paths=False):
    """Make a value safe for use INSIDE a filename. By default, strips path
    separators too, so a hostile {title} can't add directories."""
    s = str(value or "")
    s = _FILENAME_FORBIDDEN.sub("_", s)
    if not allow_paths:
        s = _FILENAME_PATHSEP.sub("_", s)
    # v3.65.2: strip curly braces from variable values. Without this,
    # a title like "Live at {date}" would have its literal "{date}"
    # substring picked up by resolve_filename_template's substitution
    # loop (if {date} is in context) or wiped entirely by the unknown-
    # placeholder regex (if it isn't). Either is wrong — the title is
    # data, not a template. Replacing with parens preserves readability.
    s = s.replace("{", "(").replace("}", ")")
    s = re.sub(r"\s+", " ", s).strip()
    # Trailing dots cause issues on Windows ("file.mp4." silently → "file.mp4"
    # which then overwrites another file). Strip ONLY trailing, not leading
    # — leading dots are needed for things like "{ext}" → ".mp4".
    s = s.rstrip(".")
    # v3.46.4 F2: if the resulting bare name is a Windows reserved
    # device name, prefix with an underscore so CreateFile() accepts
    # it. "CON" → "_CON", "CON.mp4" → "_CON.mp4".
    if _is_reserved_windows_name(s):
        s = "_" + s
    return s[:180] or "untitled"


def resolve_filename_template(template, context):
    """Render a template like '{site}/{date}/{title} [{resolution}]{ext}'.

    Each context value is sanitized for filesystem use first. Slashes in the
    TEMPLATE itself are preserved (so the user can build subdirectories),
    but slashes inside individual values are replaced.

    Unknown placeholders are STRIPPED from the output rather than raising —
    a misspelled {titel} just becomes empty in the output rather than a
    500 error mid-download. (The docstring said "left as-is" before
    v3.36.8; that was wrong, the code has always stripped.)

    v3.43.64: also strips empty `[]` and `()` left behind when optional
    placeholders like {performer} or {quality} resolve to empty strings.
    A template `{title} [{quality}].mp4` with no quality value now renders
    `Title.mp4` instead of `Title [].mp4`.
    """
    if not template:
        return ""
    # v3.43.64: empty/None values bypass the sanitizer (which would
    # otherwise convert them to "untitled") and substitute as empty.
    # This is what lets templates like "{title} [{quality}]{ext}"
    # render as "Scene.mp4" when quality is unknown, instead of
    # "Scene [untitled].mp4". Legacy callers of _sanitize_filename_var
    # directly still get the "untitled" fallback.
    safe = {}
    for k, v in context.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            safe[k] = ""
        else:
            safe[k] = _sanitize_filename_var(v)
    out = template
    for k, v in safe.items():
        out = out.replace("{" + k + "}", v)
    # Remove any remaining {placeholders} the user typoed — but preserve braces
    # in unrelated text by only stripping ones that look like a single token.
    out = re.sub(r"\{[A-Za-z_][A-Za-z_0-9]*\}", "", out)
    # Collapse double slashes / trailing-dot weirdness from missing values
    out = re.sub(r"/{2,}", "/", out)
    out = re.sub(r"\s+", " ", out).strip()
    # v3.43.64: clean up decorative brackets/parens orphaned by empty
    # placeholder values. e.g. "{title} [{quality}].mp4" with no quality.
    # Be conservative — only strip an EMPTY pair, never one containing
    # punctuation/letters/digits, because real titles legitimately contain
    # "[Director's Cut]" / "(2024)" etc.
    out = re.sub(r"\[\s*\]", "", out)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    # Trim leading/trailing path separators that survived (a template like
    # "{studio}/{title}" with studio empty would otherwise produce "/Title")
    out = out.lstrip("/").rstrip("/")

    # v3.47.8 (#unrud-port): enforce per-segment byte length AFTER template
    # rendering. Templates with subdirectories (e.g. "{site}/{title}") need
    # each path segment to fit the byte budget independently — only the
    # FINAL component (the actual filename) hits the 255-byte filesystem
    # limit, but intermediate directories have their own per-segment limit
    # too. Split, truncate each, rejoin.
    #
    # The ext (e.g. ".mp4") is preserved by extracting it before truncation
    # and re-attaching after — otherwise a 250-byte CJK title would lose
    # the extension at the truncation point and break downstream tooling
    # that infers media type from the extension.
    if out:
        segments = out.split("/")
        out_segments = []
        for seg in segments:
            if not seg:
                continue  # collapse, see lstrip/rstrip above
            # Preserve extension on the final segment only
            if seg is segments[-1] and "." in seg:
                stem, dot, ext = seg.rpartition(".")
                # Sanity: only treat as ext if it's short and alphanumeric.
                # Otherwise it's punctuation that happens to contain a dot,
                # and applying ext-preserving truncation would mangle it.
                if ext and len(ext) <= 8 and ext.replace("_","").isalnum():
                    # Leave room for the ".ext" suffix in the byte budget.
                    ext_bytes = len(("." + ext).encode("utf-8"))
                    seg_truncated = shorten_to_filesystem_limit(
                        stem, byte_limit=max(8, 250 - ext_bytes))
                    seg = seg_truncated + "." + ext
                else:
                    seg = shorten_to_filesystem_limit(seg, byte_limit=250)
            else:
                seg = shorten_to_filesystem_limit(seg, byte_limit=250)
            out_segments.append(seg)
        out = "/".join(out_segments)

    return out


# ── v3.47.8 (port from unrud/video-downloader) ──────────────────────────
# Ported from `_short_filename` in
#   github.com/Unrud/video-downloader/blob/master/src/downloader/yt_dlp_slave.py
# Original is GPLv3; this is an adaptation, not a verbatim copy.
#
# Why: most filesystem limits are byte-based (eCryptfs 143 bytes, ext4 255
# bytes, NTFS 255 chars-via-UTF-16, exFAT 255 UTF-16-code-units), but
# Python's len() counts characters. A title with multibyte characters
# (CJK, emoji, accented Latin) can pass a character-length check and then
# blow past the byte limit at write time, causing a cryptic ENAMETOOLONG.
#
# The fix is to truncate by the encoded byte length of the path, not the
# character count. Iterate from the right, sanitize each candidate (which
# may change the byte length further), and stop at the first candidate
# that fits. Adds an ellipsis to signal truncation occurred.
#
# Common limits to pass to `byte_limit`:
#   - 143: eCryptfs encrypted home directory (very old debian/ubuntu install)
#   - 250: leave headroom under 255-byte ext4/exfat limits
#   - 255: hard ext4/NTFS limit (exact char limit on POSIX is 255 BYTES)
def shorten_to_filesystem_limit(name: str, byte_limit: int = 250,
                                ellipsis: str = "…") -> str:
    """Truncate `name` so its UTF-8 byte length fits within `byte_limit`.

    If truncation occurs, an ellipsis is inserted to make it visible.
    `name` is assumed to NOT include the extension — pass `stem` only,
    re-attach `ext` separately so you don't lose the extension at the
    truncation point.

    Returns the original name if it already fits. Returns an empty string
    if `byte_limit` is so small not even the ellipsis fits (defensive;
    callers should never pass < 8). Raises ValueError if the name can't
    be shortened to fit (filesystem-encoding pathological case).
    """
    if not name:
        return name
    if byte_limit < 8:
        # Below ~8 bytes there's no room for content + ellipsis; refuse
        # rather than return something useless.
        return ""
    # Fast path: already fits.
    if len(name.encode("utf-8", errors="replace")) <= byte_limit:
        return name
    # Iterate from full length down, looking for the longest prefix
    # whose sanitized form fits the byte budget. The +1 over original
    # unrud version handles the edge case where the very last truncation
    # happens to fit exactly.
    for i in range(len(name), -1, -1):
        candidate = name[:i].rstrip()
        if i < len(name):
            candidate = candidate + ellipsis
        # Re-apply path sanitization — important because removing chars
        # may unmask reserved Windows names (e.g. "CONFIG" → "CON")
        candidate = _sanitize_filename_var(candidate)
        if len(candidate.encode("utf-8", errors="replace")) <= byte_limit:
            return candidate
    # Should not reach: byte_limit >= 8 guarantees the ellipsis alone fits.
    raise ValueError(
        f"cannot shorten {name!r} to {byte_limit} bytes "
        "(filesystem encoding pathological case)"
    )


def format_duration_for_filename(seconds):
    """Return HH:MM:SS for use in {duration} substitution. v3.43.64.

    Accepts int, float, or None. Zero / negative / non-numeric returns "".
    Hours can exceed 99 — for a 200-hour stream we'll emit '200:00:00'
    rather than wrapping or padding."""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"
