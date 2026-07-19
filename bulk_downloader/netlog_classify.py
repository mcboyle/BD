"""Network-log classifier — v3.66.34 (option 3).

Reads a bd-recon capture's ``network_log`` and reports what media it
contains: HLS/DASH manifests, segment streams, direct media files, and
crucially WHETHER each is signed/short-lived or a plain unsigned URL.

Design line (this is the whole point of the module):

  * This module DESCRIBES signed streams — it does not reassemble them.
    Signed HLS/DASH segments on member sites carry short-lived signing
    tokens (key=, end=<expiry>, limit=, policy=, signature=) precisely
    so they can't be replayed outside the authenticated player. Handing
    those back as fetch-ready candidates so a downloader could stitch
    the stream is defeating that signing. We don't do it. Signed media
    is reported as a classification, with its expiry surfaced so the
    operator knows what they're looking at.

  * Only GENUINELY UNSIGNED media (a plain .mp4 with no signing markers,
    e.g. an openly-served trailer/preview) is passed through as an
    actual download candidate. The classifier separates these for free.

So the output has two clearly separated parts:
    report      — everything found, with signed/unsigned status
    candidates  — only the unsigned subset, safe to treat as downloads

Input is the parsed recon-capture dict (or just its network_log list).
No network access; operates on already-captured data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

from . import drm_detect


# ── Signing markers ─────────────────────────────────────────────────
# Query/path tokens that indicate a signed, access-controlled URL.
# Presence of ANY of these (or the scrub placeholder, which means a
# token WAS there and got redacted) marks the URL as signed.
#
# The general branch anchors each token on a boundary char + trailing '='.
# AWS SigV4 query params are hyphenated (``X-Amz-Signature=``,
# ``X-Amz-Credential=``, ``X-Amz-Date=``, …): the token sits after a '-'
# (not a boundary char) and ``x-amz-`` itself is followed by a word, not
# '=', so the general branch misses every SigV4 URL (v3.66.54 finding via
# the C-T2 adversarial body test). The dedicated ``x-amz-[a-z-]+=`` branch
# below recognises the whole hyphenated family WITHOUT loosening the general
# boundary class — so it adds no false positives on unrelated hyphenated
# params (e.g. ``some-end=``). Over-flagging toward "signed" would be the
# conservative direction anyway, but this stays targeted.
_SIGN_MARKER = re.compile(
    r"(?:(^|[/?&,])"
    r"(key|sig|signature|token|expires?|end|limit|policy|hmac|"
    r"st|hash|credential|x-amz-|keypair|md5)"
    r"=)"
    r"|(?:[/?&]x-amz-[a-z-]+=)",
    re.I,
)
_SCRUB_PLACEHOLDER = "<scrubbed>"

# Expiry-bearing markers we can surface a human-readable window from.
_EXPIRY_MARKER = re.compile(r"(?:^|[/?&,])(?:end|expires?|st)=(\d{9,13})", re.I)

# ── Media classification by URL / content-type ──────────────────────
_EXT_HLS_MANIFEST = (".m3u8",)
_EXT_DASH_MANIFEST = (".mpd",)
_EXT_HLS_SEGMENT = (".ts", ".m4s")
_EXT_DIRECT = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".mp3", ".m4a",
               ".aac", ".ogg")

_CT_HLS = ("application/vnd.apple.mpegurl", "application/x-mpegurl",
           "audio/mpegurl")
_CT_DASH = ("application/dash+xml",)
_CT_SEGMENT = ("video/mp2t", "video/iso.segment")
_CT_DIRECT = ("video/mp4", "video/webm", "video/quicktime",
              "audio/mpeg", "audio/mp4", "audio/aac")

# DRM markers (these are never candidates regardless of signing).
_DRM_MARKER = re.compile(
    r"(widevine|playready|fairplay|clearkey|\.wvm\b|cenc|"
    r"licenseUrl|drmtoday|/license)",
    re.I,
)

KIND_HLS_MANIFEST = "hls_manifest"
KIND_DASH_MANIFEST = "dash_manifest"
KIND_HLS_SEGMENT = "hls_segment"
KIND_DIRECT = "direct_media"
KIND_UNKNOWN = "unknown"


@dataclass
class MediaItem:
    url: str
    kind: str
    content_type: Optional[str] = None
    signed: bool = False
    expiry_epoch: Optional[int] = None
    drm: bool = False
    status: Optional[int] = None
    note: str = ""
    drm_category: Optional[str] = None

    def as_dict(self):
        return {
            "url": self.url, "kind": self.kind,
            "content_type": self.content_type, "signed": self.signed,
            "expiry_epoch": self.expiry_epoch, "drm": self.drm,
            "status": self.status, "note": self.note,
            "drm_category": self.drm_category,
        }


@dataclass
class CaptureReport:
    host: Optional[str] = None
    items: List[MediaItem] = field(default_factory=list)

    # Convenience tallies
    @property
    def hls_manifests(self):
        return [i for i in self.items
                if i.kind == KIND_HLS_MANIFEST]

    @property
    def dash_manifests(self):
        return [i for i in self.items
                if i.kind == KIND_DASH_MANIFEST]

    @property
    def segments(self):
        return [i for i in self.items if i.kind == KIND_HLS_SEGMENT]

    @property
    def signed_items(self):
        return [i for i in self.items if i.signed]

    @property
    def unsigned_media(self):
        """Genuinely unsigned, non-DRM media — the only pass-through
        candidates. Segments are excluded even if unsigned: a lone
        segment isn't a usable download and reassembling the set is the
        declined path."""
        return [i for i in self.items
                if not i.signed and not i.drm
                and i.kind in (KIND_DIRECT,)]

    def candidates(self):
        """Download candidates = unsigned direct media only."""
        return [i.url for i in self.unsigned_media]

    def summary(self) -> str:
        parts = [
            f"host={self.host}",
            f"hls_manifests={len(self.hls_manifests)}",
            f"dash_manifests={len(self.dash_manifests)}",
            f"segments={len(self.segments)}",
            f"signed={len(self.signed_items)}",
            f"unsigned_media={len(self.unsigned_media)}",
        ]
        drm = sum(1 for i in self.items if i.drm)
        if drm:
            parts.append(f"drm={drm}")
        return " ".join(parts)

    def as_dict(self):
        return {
            "host": self.host,
            "summary": self.summary(),
            "items": [i.as_dict() for i in self.items],
            "candidates": self.candidates(),
        }


def _content_type_of(entry: dict) -> Optional[str]:
    rh = entry.get("response_headers")
    if isinstance(rh, dict):
        for k, v in rh.items():
            if str(k).lower() == "content-type":
                return str(v).split(";")[0].strip().lower()
    elif isinstance(rh, list):
        for h in rh:
            if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-type":
                return str(h.get("value", "")).split(";")[0].strip().lower()
    return None


def _is_signed(url: str) -> bool:
    if not url:
        return False
    if _SCRUB_PLACEHOLDER in url:
        # A token was present and got redacted by the scrubber → signed.
        return True
    return _SIGN_MARKER.search(url) is not None


def _expiry_of(url: str) -> Optional[int]:
    m = _EXPIRY_MARKER.search(url or "")
    if not m:
        return None
    try:
        val = int(m.group(1))
    except (ValueError, TypeError):
        return None
    # Normalize ms → s if it looks like milliseconds.
    if val > 10_000_000_000:
        val //= 1000
    return val


def _classify_kind(url: str, ct: Optional[str]) -> str:
    u = (url or "").lower()
    path = urlparse(u).path
    if ct in _CT_HLS or any(path.endswith(e) for e in _EXT_HLS_MANIFEST) or "/media=hls" in u:
        # /media=hls without .m3u8 ext is still a manifest request shape
        if any(path.endswith(e) for e in _EXT_HLS_SEGMENT):
            return KIND_HLS_SEGMENT
        return KIND_HLS_MANIFEST
    if ct in _CT_DASH or any(path.endswith(e) for e in _EXT_DASH_MANIFEST):
        return KIND_DASH_MANIFEST
    if ct in _CT_SEGMENT or any(path.endswith(e) for e in _EXT_HLS_SEGMENT):
        return KIND_HLS_SEGMENT
    if ct in _CT_DIRECT or any(path.endswith(e) for e in _EXT_DIRECT):
        return KIND_DIRECT
    return KIND_UNKNOWN


def _looks_like_media(url: str, ct: Optional[str]) -> bool:
    if ct and (ct in _CT_HLS or ct in _CT_DASH or ct in _CT_SEGMENT
               or ct in _CT_DIRECT):
        return True
    u = (url or "").lower()
    path = urlparse(u).path
    if any(path.endswith(e) for e in
           _EXT_HLS_MANIFEST + _EXT_DASH_MANIFEST
           + _EXT_HLS_SEGMENT + _EXT_DIRECT):
        return True
    # Segment-template shapes without a clean extension (beeg-style)
    if "/media=hls" in u or "/data=" in u and "/key=" in u:
        return True
    return False


def classify_network_log(capture, *, host: Optional[str] = None) -> CaptureReport:
    """Classify the media in a recon capture's network_log.

    `capture` may be the full capture dict (we read .network_log and
    .host) or a bare network_log list.
    """
    if isinstance(capture, dict):
        nl = capture.get("network_log") or []
        host = host or capture.get("host")
    else:
        nl = capture or []

    report = CaptureReport(host=host)
    seen = set()
    for entry in nl:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or ""
        ct = _content_type_of(entry)
        if not _looks_like_media(url, ct):
            continue
        if url in seen:
            continue
        seen.add(url)

        kind = _classify_kind(url, ct)
        signed = _is_signed(url)
        drm = bool(_DRM_MARKER.search(url))
        # Structural category (detection only): a URL marker resolves to cdm-drm;
        # the richer HLS/DASH/key-system classification lives in drm_detect and is
        # used wherever a manifest body or EME key-system is available.
        drm_category = (drm_detect.classify_protection(url=url)["category"]
                        if drm else drm_detect.CAT_NONE)
        expiry = _expiry_of(url) if signed else None

        note = ""
        if drm:
            note = "DRM-protected; not a candidate"
        elif signed:
            note = "signed/short-lived; reported only, not reassembled"
        elif kind == KIND_HLS_SEGMENT:
            note = "unsigned segment; not a standalone candidate"
        elif kind == KIND_DIRECT:
            note = "unsigned direct media; pass-through candidate"

        report.items.append(MediaItem(
            url=url, kind=kind, content_type=ct, signed=signed,
            expiry_epoch=expiry, drm=drm, drm_category=drm_category,
            status=entry.get("response_status"), note=note))

    return report
