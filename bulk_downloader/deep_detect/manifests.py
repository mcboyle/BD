from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import re

from .resolution import (classify_resolution)
from .urls import (decode_url)
from ..drm_detect import classify_protection


_HLS_ATTR_RE = re.compile(
    # v3.66.x ReDoS: the attribute-name run is BOUNDED ({1,64}, not +). HLS
    # attribute names are short and known (BANDWIDTH, RESOLUTION, CODECS,
    # AVERAGE-BANDWIDTH, FRAME-RATE, …); without the bound a long '='-less run on
    # a malformed/oversized tag line backtracks O(n²). 64 >> any real name, so
    # matching is unchanged. The value branches stop at a delimiter (linear) and
    # need no bound. See test_deep_detect_redos.
    r'([A-Za-z0-9-]{1,64})=(?:"((?:\\.|[^"\\])*)"|([^,\s]*))',
    re.I,
)


def _parse_hls_attrs(line: str) -> dict:
    """Parse the attribute list on an HLS tag line into a dict.

    The regex emits three groups: (key, quoted_content, unquoted_content).
    Exactly one of groups 2 or 3 is non-None for each match.

    v3.66.10: handles backslash escapes (\\X → X) inside quoted values.
    The HLS spec doesn't define escapes in quoted-strings, but
    defensive parsers in the wild do — and ignoring them silently
    truncated CODECS / URI values containing them."""
    out: Dict[str, str] = {}
    # Strip the leading "#TAG:" portion if present
    if ":" in line:
        _, _, rest = line.partition(":")
    else:
        rest = line
    for m in _HLS_ATTR_RE.finditer(rest):
        k = m.group(1)
        if m.group(2) is not None:
            # Quoted form — process backslash escapes.
            v = m.group(2)
            if "\\" in v:
                # Process \X → X (any character)
                result = []
                i = 0
                while i < len(v):
                    if v[i] == "\\" and i + 1 < len(v):
                        result.append(v[i + 1])
                        i += 2
                    else:
                        result.append(v[i])
                        i += 1
                v = "".join(result)
        else:
            v = (m.group(3) or "").strip()
        out[k] = v
    return out


def is_hls_manifest(text: str) -> bool:
    """Cheap structural check: starts with #EXTM3U OR contains it
    early in the body. We accept a small amount of leading whitespace
    or BOM since some CDNs emit those."""
    if not isinstance(text, str):
        return False
    head = text.lstrip("\ufeff \t\r\n")[:64]
    return head.startswith("#EXTM3U")


def is_hls_master(text: str) -> bool:
    """A master playlist contains #EXT-X-STREAM-INF; a media playlist
    contains #EXTINF segment durations instead. Both start with
    #EXTM3U."""
    return is_hls_manifest(text) and "#EXT-X-STREAM-INF" in text


def hls_has_encryption(text: str) -> bool:
    """Return True iff the HLS playlist defines an #EXT-X-KEY with a
    non-NONE METHOD.

    v3.66.11 (bug R): the previous check was
        ``"#EXT-X-KEY" in text and "METHOD=NONE" not in text``
    which false-negatived in two ways:
      1. A URI containing the literal substring ``METHOD=NONE`` would
         suppress detection of a sibling encrypted #EXT-X-KEY tag.
      2. ``METHOD=NONE`` could appear in a comment or an attribute on
         an unrelated tag and would still suppress.

    The fix scans each #EXT-X-KEY line and parses its METHOD attribute
    properly. Encrypted iff ANY key tag has METHOD != NONE.
    """
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("#EXT-X-KEY"):
            continue
        # The line is "#EXT-X-KEY:METHOD=AES-128,URI=..." — split on the
        # FIRST colon to get the attribute list, then look for METHOD=.
        if ":" not in stripped:
            continue
        attrs = stripped.split(":", 1)[1]
        # Attribute list is comma-separated; METHOD's value is up to
        # the next comma (it's never quoted, per RFC 8216).
        for attr in attrs.split(","):
            if attr.strip().upper().startswith("METHOD="):
                method = attr.split("=", 1)[1].strip().upper()
                if method and method != "NONE":
                    return True
                break  # found METHOD, value was NONE; check next key tag
    return False


def parse_hls_master(text: str, *, base_url: str = "") -> dict:
    """Extract every variant from an HLS master playlist, plus alt
    audio/subtitle tracks. Returns:

        {
            "kind":     "hls_master" | "hls_media" | "not_hls",
            "variants": [{url, width, height, resolution, bandwidth,
                          codecs, frame_rate, audio_group,
                          subtitles_group}, ...],
            "audio":    [{url, name, lang, default, group_id,
                          channels, forced}, ...],
            "subtitles":[{url, name, lang, default, group_id,
                          channels, forced}, ...],
            "drm_or_encryption_detected": bool,
            "low_latency": bool,
            "warnings": [...],
        }

    Alt-media entry schema (audio + subtitles share it; v3.66.11
    bug S — full schema previously undocumented):
      - url:       absolute URL (joined against base_url) or None
                   for inline tracks. Closed-captions are muxed and
                   never get a URI; they're dropped at parse time.
      - name:      NAME attribute, e.g. "English".
      - lang:      LANGUAGE attribute (BCP-47 tag), e.g. "en-US".
      - default:   True iff DEFAULT=YES.
      - group_id:  GROUP-ID attribute; links the track to variants
                   that name it via AUDIO=... / SUBTITLES=... .
      - channels:  CHANNELS attribute (audio only in practice).
      - forced:    True iff FORCED=YES (subtitles).

    Variants are sorted highest-quality first (resolution rank then
    bandwidth). The caller picks variants[0] for highest quality."""
    out = {
        "kind": "not_hls",
        "variants": [],
        "audio": [],
        "subtitles": [],
        "drm_or_encryption_detected": False,
        "drm_category": "none",
        "drm_system": None,
        "low_latency": False,
        "warnings": [],
    }
    if not is_hls_manifest(text):
        return out

    if not is_hls_master(text):
        out["kind"] = "hls_media"
        # Encryption detection on media playlists too — useful even
        # when we can't pick a variant.
        if hls_has_encryption(text):
            out["drm_or_encryption_detected"] = True
            out["warnings"].append("HLS media playlist is encrypted "
                                   "(#EXT-X-KEY METHOD set)")
            _prot = classify_protection(hls_text=text)
            out["drm_category"] = _prot["category"]
            out["drm_system"] = _prot["system"]
        return out

    out["kind"] = "hls_master"

    # Low-latency markers (F5: + #EXT-X-PRELOAD-HINT, the LL-HLS
    # preload-hint tag that accompanies partial segments)
    for marker in ("#EXT-X-PART-INF", "#EXT-X-SERVER-CONTROL",
                   "#EXT-X-RENDITION-REPORT", "#EXT-X-PRELOAD-HINT"):
        if marker in text:
            out["low_latency"] = True
            break

    # F5: #EXT-X-MAP declares an initialization segment (fMP4 / CMAF
    # packaging). Report it so downstream knows variants are fragmented
    # MP4 rather than MPEG-TS; not itself an encryption signal.
    if "#EXT-X-MAP" in text:
        out["init_segment_present"] = True

    # Encryption / DRM
    if hls_has_encryption(text):
        out["drm_or_encryption_detected"] = True
        out["warnings"].append(
            "HLS master references #EXT-X-KEY — variants may be "
            "encrypted; do not attempt to bypass")
    if "#EXT-X-SESSION-KEY" in text:
        out["drm_or_encryption_detected"] = True
        out["warnings"].append(
            "HLS master defines #EXT-X-SESSION-KEY — session-level "
            "encryption in use")
    if out["drm_or_encryption_detected"]:
        # Structural category (detection only): downloadable-aes (fetchable-key
        # AES the session is served) vs cdm-drm (Widevine/PlayReady/FairPlay).
        _prot = classify_protection(hls_text=text)
        out["drm_category"] = _prot["category"]
        out["drm_system"] = _prot["system"]

    lines = [ln.rstrip("\r") for ln in text.splitlines()]

    # Alt media tracks (#EXT-X-MEDIA appears before STREAM-INF)
    for ln in lines:
        if not ln.startswith("#EXT-X-MEDIA"):
            continue
        attrs = _parse_hls_attrs(ln)
        track_type = attrs.get("TYPE", "").upper()
        if track_type not in ("AUDIO", "SUBTITLES", "CLOSED-CAPTIONS"):
            continue
        uri = attrs.get("URI", "")
        # Closed-captions are muxed into video segments and don't
        # carry a URI of their own — skip those for output purposes.
        if track_type == "CLOSED-CAPTIONS":
            continue
        entry = {
            "url": decode_url(uri, base_url=base_url) if uri else None,
            "name": attrs.get("NAME"),
            "lang": attrs.get("LANGUAGE"),
            "default": attrs.get("DEFAULT", "").upper() == "YES",
            "group_id": attrs.get("GROUP-ID"),
            "channels": attrs.get("CHANNELS"),
            "forced": attrs.get("FORCED", "").upper() == "YES",
        }
        if track_type == "AUDIO":
            out["audio"].append(entry)
        else:
            out["subtitles"].append(entry)

    # Variant streams (#EXT-X-STREAM-INF lines followed by a URL line)
    for i, ln in enumerate(lines):
        if not ln.startswith("#EXT-X-STREAM-INF"):
            continue
        attrs = _parse_hls_attrs(ln)
        # The next non-comment, non-blank line is the variant URL.
        variant_url = None
        for j in range(i + 1, len(lines)):
            nxt = lines[j].strip()
            if not nxt or nxt.startswith("#"):
                continue
            variant_url = nxt
            break
        if not variant_url:
            out["warnings"].append(
                "STREAM-INF line missing trailing URL — skipped")
            continue
        width = height = None
        resolution = (attrs.get("RESOLUTION") or "").strip()
        # v3.66.11 (bug T): use fullmatch so RESOLUTION=1920x1080extra
        # or RESOLUTION=1920x1080,foo is rejected outright rather than
        # truncated. The previous re.match() was start-anchored only,
        # accepting any garbage tail. The HLS spec defines RESOLUTION
        # as decimal-resolution = decimal-integer "x" decimal-integer
        # with no allowed suffix, so fullmatch is correct.
        # Pre-stripped to tolerate the (non-spec but seen) trailing
        # whitespace some encoders emit.
        rm = re.fullmatch(r"(\d+)x(\d+)", resolution)
        if rm:
            width, height = int(rm.group(1)), int(rm.group(2))
        res_info = classify_resolution(width, height) if (
            width and height) else None
        try:
            # v3.66.11 (bug U): some encoders emit BANDWIDTH=8000000.0
            # (float-shaped) rather than an integer. int("8000000.0")
            # raises ValueError → bandwidth silently became 0, dropping
            # the variant's quality ordering. Coerce via float first
            # then truncate.
            _bw_raw = attrs.get("BANDWIDTH") or 0
            bandwidth = int(float(_bw_raw))
        except (ValueError, TypeError, OverflowError):
            bandwidth = 0
        try:
            # v3.66.11 (bug V): distinguish FRAME-RATE=0 (explicit
            # malformed-but-present) from missing FRAME-RATE. `... or
            # None` collapsed both into None, which lost information
            # for the (rare) case of a manifest that names FRAME-RATE
            # but with a meaningless value.
            _fr_raw = attrs.get("FRAME-RATE")
            if _fr_raw is None or _fr_raw == "":
                frame_rate = None
            else:
                fr_val = float(_fr_raw)
                # 0.0 is non-meaningful but distinguishable from
                # missing — preserve it as 0.0 for diagnostic visibility.
                # Callers that want "usable frame rate" should treat
                # any value <= 0 as missing.
                # F-REC03-02: a non-finite FRAME-RATE (inf/nan) is meaningless;
                # treat it as missing rather than propagating it verbatim.
                import math as _math
                if not _math.isfinite(fr_val):
                    frame_rate = None
                else:
                    frame_rate = fr_val if fr_val > 0 else 0.0
        except (ValueError, TypeError):
            frame_rate = None
        out["variants"].append({
            "url": decode_url(variant_url, base_url=base_url),
            "width": width,
            "height": height,
            "resolution": res_info,
            "bandwidth": bandwidth,
            "codecs": attrs.get("CODECS"),
            "frame_rate": frame_rate,
            "audio_group": attrs.get("AUDIO"),
            "subtitles_group": attrs.get("SUBTITLES"),
        })

    # Sort by resolution rank (highest first), then bandwidth.
    def _rank(v):
        r = (v.get("resolution") or {}).get("rank") or 0
        return (r, v.get("bandwidth") or 0)

    out["variants"].sort(key=_rank, reverse=True)
    return out


def is_dash_manifest(text: str) -> bool:
    """Cheap structural check: contains <MPD … xmlns or starts with
    one. Catches both pretty-printed and minified MPDs."""
    if not isinstance(text, str) or not text:
        return False
    head = text.lstrip("\ufeff \t\r\n")[:512]
    return head.startswith("<?xml") and "<MPD" in head[:512] \
        or head.startswith("<MPD")


def parse_dash_mpd(text: str, *, base_url: str = "") -> dict:
    """Extract every video/audio Representation from a DASH MPD,
    plus DRM and low-latency markers. Returns:

        {
            "kind": "dash_mpd" | "not_dash",
            "video": [{id, width, height, resolution, bandwidth,
                       codecs, mime, period}, ...],
            "audio": [{id, lang, bandwidth, codecs, mime, channels,
                       period}, ...],
            "subtitles": [{id, lang, mime, period}, ...],
            "drm_or_encryption_detected": bool,
            "low_latency": bool,
            "warnings": [...],
        }

    Uses xml.etree.ElementTree from the stdlib — no extra deps.
    Returns kind="not_dash" if the input doesn't parse.
    """
    out = {
        "kind": "not_dash",
        "video": [],
        "audio": [],
        "subtitles": [],
        "drm_or_encryption_detected": False,
        "drm_category": "none",
        "drm_system": None,
        "low_latency": False,
        "warnings": [],
    }
    if not is_dash_manifest(text):
        return out

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        out["warnings"].append(f"MPD XML parse failed: {e}")
        return out

    # MPD uses namespace prefixes; strip them so attribute lookups
    # work consistently regardless of how the document declares xmlns.
    def localname(tag):
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    out["kind"] = "dash_mpd"

    # Low-latency: profile contains "urn:mpeg:dash:profile:llss" or
    # availabilityTimeOffset is set on any segment template.
    profile = root.get("profiles") or ""
    if "low-latency" in profile.lower() or "ll-dash" in profile.lower() \
            or "llss" in profile.lower():
        out["low_latency"] = True

    # ContentProtection anywhere in the tree → encrypted.
    for elt in root.iter():
        if localname(elt.tag) == "ContentProtection":
            out["drm_or_encryption_detected"] = True
            scheme = elt.get("schemeIdUri", "")
            if scheme:
                out["warnings"].append(
                    f"DASH ContentProtection scheme: {scheme}")
            break
    if out["drm_or_encryption_detected"]:
        # Structural category (detection only); DASH ContentProtection is CDM-DRM.
        _prot = classify_protection(dash_text=text)
        out["drm_category"] = _prot["category"]
        out["drm_system"] = _prot["system"]

    # Walk Period → AdaptationSet → Representation.
    #
    # v3.66.11 (bug 26): use direct-child iteration instead of
    # root.iter() / period.iter(). The previous code descended into
    # arbitrary nested structures and counted any tag named "Period"
    # — so a non-spec MPD with `<SupplementalProperty><Period>...
    # </Period></SupplementalProperty>` advanced period_idx by one
    # extra. AdaptationSet/Representation walks had the same issue.
    # Now: we walk the MPD's tree but iterate via list(elem) (direct
    # children only) at each level.
    def direct_children_named(parent, name):
        return [c for c in list(parent) if localname(c.tag) == name]

    period_idx = 0
    for period in direct_children_named(root, "Period"):
        period_idx += 1
        for aset in direct_children_named(period, "AdaptationSet"):
            aset_mime = (aset.get("mimeType") or aset.get("contentType")
                         or "").lower()
            aset_content_type = (aset.get("contentType") or "").lower()
            aset_lang = aset.get("lang") or aset.get("xml:lang")
            for rep in direct_children_named(aset, "Representation"):
                rep_mime = (rep.get("mimeType") or aset_mime).lower()
                width = rep.get("width") or aset.get("width")
                height = rep.get("height") or aset.get("height")
                try:
                    width_i = int(width) if width else None
                    height_i = int(height) if height else None
                except ValueError:
                    width_i = height_i = None
                try:
                    bw = int(rep.get("bandwidth") or 0)
                except ValueError:
                    bw = 0
                codecs = rep.get("codecs") or aset.get("codecs")
                entry = {
                    "id": rep.get("id"),
                    "bandwidth": bw,
                    "codecs": codecs,
                    "mime": rep_mime,
                    "period": period_idx,
                    "lang": aset_lang,
                    "channels": rep.get("audioSamplingRate")
                    or aset.get("audioSamplingRate"),
                }
                # v3.66.11 (bug 25): subtitle precedence. The
                # AdaptationSet's contentType (when set to one of
                # "text", "subtitle", "captions", "caption") is now
                # an explicit signal for the subtitle bucket,
                # regardless of what the Representation's mimeType
                # is. This catches the muxed-subtitle case where
                # contentType="text" lives on the AdaptationSet but
                # the Representation declares mimeType="application/
                # mp4" (legitimate per the spec; missed by the
                # previous text/-mime-only check).
                is_subtitle_by_content_type = aset_content_type in (
                    "text", "subtitle", "subtitles",
                    "caption", "captions",
                )
                if rep_mime.startswith("video") or width_i:
                    res = (classify_resolution(width_i, height_i)
                           if width_i and height_i else None)
                    out["video"].append({
                        **entry,
                        "width": width_i,
                        "height": height_i,
                        "resolution": res,
                    })
                elif rep_mime.startswith("audio"):
                    out["audio"].append(entry)
                elif (rep_mime.startswith("text")
                      or is_subtitle_by_content_type
                      or (rep_mime == "application/mp4"
                          and "subtitle" in aset_content_type)):
                    out["subtitles"].append(entry)

    # Sort video by resolution rank then bandwidth.
    def _vrank(v):
        r = (v.get("resolution") or {}).get("rank") or 0
        return (r, v.get("bandwidth") or 0)

    out["video"].sort(key=_vrank, reverse=True)
    out["audio"].sort(key=lambda a: a.get("bandwidth") or 0, reverse=True)
    return out


def is_smooth_manifest(text: str) -> bool:
    """True if `text` looks like a Microsoft Smooth Streaming client
    manifest (the `*.ism/Manifest` / `.isml` XML, root
    <SmoothStreamingMedia>). Cheap substring guard before the XML
    parse, mirroring is_dash_manifest."""
    if not text:
        return False
    return "<SmoothStreamingMedia" in text


def parse_smooth_streaming(text: str, *, base_url: str = "") -> dict:
    """Extract every QualityLevel from a Smooth Streaming client
    manifest, plus DRM markers. Output shape mirrors parse_dash_mpd:

        {
            "kind": "smooth_streaming" | "not_smooth",
            "video": [{index, width, height, resolution, bandwidth,
                       codecs, fourcc, stream}, ...],
            "audio": [{index, lang, bandwidth, codecs, fourcc,
                       channels, sampling_rate, stream}, ...],
            "subtitles": [{index, lang, stream}, ...],
            "drm_or_encryption_detected": bool,
            "low_latency": bool,
            "warnings": [...],
        }

    POSTURE: this REPORTS QualityLevels and PlayReady/WideVine
    protection. It does not assemble Fragment URL templates into a
    playable stream, and a manifest carrying <Protection> is reported
    as DRM, never decrypted. Uses stdlib ElementTree — no extra deps.
    Returns kind="not_smooth" if the input doesn't parse.
    """
    out = {
        "kind": "not_smooth",
        "video": [],
        "audio": [],
        "subtitles": [],
        "drm_or_encryption_detected": False,
        "low_latency": False,
        "warnings": [],
    }
    if not is_smooth_manifest(text):
        return out

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        out["warnings"].append(f"Smooth manifest XML parse failed: {e}")
        return out

    def localname(tag):
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    if localname(root.tag) != "SmoothStreamingMedia":
        return out
    out["kind"] = "smooth_streaming"

    # IsLive="TRUE" → live/low-latency stream.
    if (root.get("IsLive") or "").upper() == "TRUE":
        out["low_latency"] = True

    # <Protection> anywhere → DRM (PlayReady/WideVine). Report, never
    # bypass — same line netlog_classify holds.
    for elt in root.iter():
        if localname(elt.tag) == "Protection":
            out["drm_or_encryption_detected"] = True
            out["warnings"].append(
                "Smooth Streaming manifest carries <Protection> "
                "(PlayReady/DRM) — encrypted; do not attempt to bypass")
            break

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # StreamIndex elements carry Type=video|audio|text; each holds
    # QualityLevel children.
    for si in root.iter():
        if localname(si.tag) != "StreamIndex":
            continue
        stype = (si.get("Type") or "").lower()
        sname = si.get("Name") or ""
        slang = si.get("Language") or ""
        for ql in list(si):
            if localname(ql.tag) != "QualityLevel":
                continue
            idx = _int(ql.get("Index"))
            bw = _int(ql.get("Bitrate"))
            fourcc = ql.get("FourCC") or ""
            codecs = ql.get("CodecPrivateData") and fourcc or fourcc
            if stype == "video":
                w, h = _int(ql.get("MaxWidth")), _int(ql.get("MaxHeight"))
                entry = {
                    "index": idx, "width": w, "height": h,
                    "resolution": classify_resolution(w, h),
                    "bandwidth": bw, "codecs": codecs, "fourcc": fourcc,
                    "stream": sname,
                }
                out["video"].append(entry)
            elif stype == "audio":
                out["audio"].append({
                    "index": idx, "lang": slang, "bandwidth": bw,
                    "codecs": codecs, "fourcc": fourcc,
                    "channels": _int(ql.get("Channels")),
                    "sampling_rate": _int(ql.get("SamplingRate")),
                    "stream": sname,
                })
            elif stype == "text":
                out["subtitles"].append({
                    "index": idx, "lang": slang, "stream": sname,
                })

    def _vrank(v):
        r = (v.get("resolution") or {}).get("rank") or 0
        return (r, v.get("bandwidth") or 0)

    out["video"].sort(key=_vrank, reverse=True)
    out["audio"].sort(key=lambda a: a.get("bandwidth") or 0, reverse=True)
    return out
