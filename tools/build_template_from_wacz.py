#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# extraction_core consolidation (step 3): route this builder's shared URL/value/segment
# derivation through the single canonical, pure, stdlib-only core. _network_patterns and
# _manifest_resolutions are imported from there (proven byte-identical by
# tests/test_extraction_core.py + the characterization golden); the local duplicates and
# their manifest regexes are removed. Force repo root onto sys.path so the cross-package
# import resolves when btw is run as a bare CLI from tools/ (mirrors tools/_probe_lib.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from bulk_downloader.extraction_core import (  # noqa: E402
    network_patterns as _network_patterns,
    manifest_resolutions as _manifest_resolutions,
)
# v3.66.762: derive the redaction sentinel from its canonical home instead of a
# hand-kept "kept in sync" literal (DERIVE-AUDIT). The edge
# build_template_from_wacz -> capture_artifact_redact already exists (redact_artifact
# is imported below), so this adds no new import edge.
from bulk_downloader.capture_artifact_redact import PLACEHOLDER as _SCRUBBED  # noqa: E402


RES_RE = re.compile(r"(?<!\d)(4320|2160|1440|1080|720|540|480|360|240)p(?!\d)", re.I)


# ── Builder-side supplemental recognition (extraction_core stays byte-identical) ──
# extraction_core recognizes reptyle-shaped media (VP9_/AVC_/...p + the
# download-resolution API). Sites like wowgirls stream direct MP4 renditions whose
# resolution is encoded as ``x{res}`` with an optional FPS/quality variant
# (``/stream/{slug}/{name}x1080_30FPS.mp4``) and have NO separate download API.
# These helpers catch that scheme and merge it into the draft's network_discovery.
# They live here (non-frozen builder), never in extraction_core.

# Known resolution heights (mirrors extraction_core's rendition set; we cannot edit
# the core, so the constant is restated here for the builder's own recognition).
_DIRECT_RES = (144, 240, 288, 360, 480, 540, 576, 720, 960, 1080, 1280, 1440,
               1920, 2160, 2560, 2880, 3840, 4096, 4320)
_DIRECT_MP4_RE = re.compile(
    r"[x_-](?P<res>" + "|".join(str(r) for r in sorted(_DIRECT_RES, reverse=True)) +
    # optional trailing p/P (e.g. VIXEN_{id}_1080P.mp4, {name}-720p.mp4) — R3
    r")[pP]?(?:_(?P<variant>\d{1,3}FPS|[A-Za-z]{1,6}))?\.mp4(?:$|[?#])", re.I)
# URLs extraction_core already handles — skip so we never double-emit / clobber it.
_CORE_MEDIA_RE = re.compile(r"/(?:VP9|AVC)_\d+\.mp4$|\.(?:m3u8|mpd)$", re.I)

# ── Corpus-driven supplemental recognizers (v3.66.197, non-guard) ─────────────
# The first real capture corpus (11 sites) showed extraction_core + the x{res}
# recognizer cover only 1/11 — the real download targets are flagged by
# site-agnostic SIGNALS the builder wasn't reading: Content-Disposition,
# ranged video/mp4 on a dedicated download host, generalized res-in-filename, and
# HLS .ts rendition structure. All emit templated, signing-free patterns only.
_LARGE_MEDIA_BYTES = 50_000_000   # a real download body, not an ad/preview fragment
# dedicated download / delivery hosts: cdn-download.*, *-dl.com, dl.* …
_DL_HOST_RE = re.compile(
    r"(?:^|[.\-])(?:cdn-)?download(?:[.\-]|$)|-dl[.\-]|(?:^|\.)dl[.\-]", re.I)
# a resolution token embedded in a path segment, e.g. …_1080p_… / …-720p-…
_RES_IN_PATH_RE = re.compile(r"(?<!\d)(\d{3,4})[pP](?=[_\-./]|$)")


def _har_get(headers, name: str):
    """Case-insensitive lookup over the loader's HAR-style response_headers
    (a list of {"name","value"} dicts). Returns the value or None."""
    if not isinstance(headers, list):
        return None
    nl = name.lower()
    for h in headers:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == nl:
            return h.get("value")
    return None


def _to_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _ts_rendition_pattern(path: str):
    """Templatize an HLS .ts segment path into a rendition pattern (NOT a
    contentless manifest stub). Variable segments — names, numeric indices,
    short alnum markers (v1/a1) — are parametrized; known-resolution tokens
    become {resolution}p. No slug/title/index literal survives."""
    leaf = path.rsplit("/", 1)[-1]
    if not leaf.lower().endswith(".ts"):
        return None
    stem = leaf[:-3]
    out = []
    for tok in re.split(r"([_\-.])", stem):
        if tok in ("_", "-", ".", ""):
            out.append(tok)
            continue
        mres = re.fullmatch(r"(\d{3,4})[pP]", tok)
        if mres and int(mres.group(1)) in _DIRECT_RES:
            out.append("{resolution}p")
        elif re.fullmatch(r"\d+", tok):
            out.append("{n}")
        elif re.fullmatch(r"[A-Za-z]+\d+", tok):   # v1, a1, seg2 …
            out.append("{seg}")
        else:
            out.append("{name}")                   # never the raw slug/title
    return ".../" + "".join(out) + ".ts"


# ── R5 helpers: signed direct-media / JWPlayer recognition ─────────────────
# Detection only — these recognize the SHAPE of a signed/JWPlayer media target
# so the draft is classified correctly. The signed URL itself is short-lived and
# is NEVER persisted (patterns below strip the query); it must be captured live
# via the runtime player path (Pick → Test → Promote).
_SIGNING_QUERY_KEYS = frozenset({
    "token", "__token__", "hdnts", "hdnea", "expires", "expire", "exp",
    "signature", "sig", "policy", "key-pair-id", "keypairid", "hash", "st",
    "x-amz-signature", "x-amz-credential", "x-amz-security-token", "awsaccesskeyid",
})
_JWPLAYER_HOST_MARKERS = ("jwplayer", "jwplatform", "jwpcdn", "jwpsrv")
_JWPLAYER_PATH_MARKERS = ("/v2/media/", "/v2/playlists/")


def _has_signing_query(qs) -> bool:
    """True iff the query string carries signing params (token/expiry/sig)."""
    if not qs:
        return False
    try:
        from urllib.parse import parse_qsl
        keys = {k.lower() for k, _ in parse_qsl(str(qs), keep_blank_values=True)}
    except Exception:
        keys = {kv.split("=", 1)[0].lower() for kv in str(qs).split("&") if kv}
    if keys & _SIGNING_QUERY_KEYS:
        return True
    low = str(qs).lower()              # akamai-style compound token value
    return "~hmac=" in low or ("exp=" in low and "hmac" in low)


def _is_jwplayer_target(host, path) -> bool:
    """True iff the URL is a JWPlayer media/playlist target (host or path)."""
    h = (host or "").lower()
    pp = (path or "").lower()
    if any(m in h for m in _JWPLAYER_HOST_MARKERS):
        return True
    return any(m in pp for m in _JWPLAYER_PATH_MARKERS)


def _jwplayer_player_markers(html: str) -> dict:
    """Structure-only JWPlayer container + play-button selectors (no values)."""
    out: dict = {}
    if not html:
        return out
    if ("jwplayer" in html) or ("jw-video" in html) or ("jw-flag-" in html):
        out["container"] = ".jwplayer"
    if (("jw-icon-display" in html) or ("jw-icon-playback" in html)
            or ("jw-display-icon-container" in html)):
        out["play_button"] = ".jw-icon-display, .jw-icon-playback"
    return out


# ── R6: CDN-fronting topology for JWPlayer delivery (row 120 / JW-TMPL) ────
# THE TOPOLOGY IS THE SUBJECT, NOT A DESCRIPTION. R5 above already recognises
# the signed / JWPlayer media SHAPE, but it cannot answer the question row 120
# actually asks -- is the player or the media served from an akamai, cloudflare
# or cloudfront-fronted host? Measured over the 21 ultrafilms captures (14
# sessions) that question read 0 of 21: every one was JWPlayer-bearing with a
# member-only entitlement call and signed renditions, and NONE served a player
# or media asset from such a host. The only CDN host anywhere was
# `cdn.jsdelivr.net`, a JS-library CDN, and the row's ruling does not count it.
# `_LIBRARY_CDN_SUFFIXES` below encodes that ruling so the same input keeps
# reading 0 rather than drifting to 1 the day jsdelivr answers with a `cf-ray`.
#
# TWO INDEPENDENT TELLS, BECAUSE ONE IS NOT ENOUGH. Akamai and cloudfront are
# usually visible in the HOST (`*.akamaized.net`, `*.cloudfront.net`), but
# cloudflare fronting is host-invisible by design -- a site behind cloudflare
# keeps its own name -- so host matching alone would report cloudflare as absent
# on every site that uses it. Response headers carry the second tell
# (`cf-ray`, `akamai-grn`, `x-amz-cf-id`, `server:`). Both are recorded in
# `cdn_evidence` so a reader can tell which one fired.
#
# SCOPED TO PLAYER AND MEDIA ASSETS, WHICH IS THE WHOLE DIFFERENCE BETWEEN A
# RECOGNISER AND A YES-MACHINE. A news page carries dozens of ad-tech and
# consent requests on cloudflare and cloudfront; "some request touched a CDN" is
# true of nearly every page on the web and answers nothing. `cdn_fronted` means
# one thing only: at least one JWPlayer PLAYER asset or one VIDEO/AUDIO/MANIFEST
# asset was served from a CDN-fronted host. Captions are counted separately and
# are never sufficient alone; tracker hosts are excluded via the same
# subdomain-boundary matcher `reject_noise` uses.
#
# A7: an input that cannot be measured returns status "unknown" with
# `cdn_fronted` / `jwplayer_present` as None. It never returns False, because a
# False here would read as a measured absence of CDN fronting -- which is
# exactly the false zero this row was parked on.
_CDN_HOST_SUFFIXES = (
    ("akamai", (".akamaized.net", ".akamaihd.net", ".akamai.net",
                ".akamaiedge.net", ".akamaistream.net", ".edgesuite.net",
                ".edgekey.net", ".akamaitechnologies.com")),
    ("cloudfront", (".cloudfront.net",)),
    ("cloudflare", (".cloudflarestream.com", ".cdn.cloudflare.net")),
)
# Library CDNs deliver third-party JS, not the site's own player/media tier.
# The row's ruling names jsdelivr explicitly; the neighbours share its shape.
_LIBRARY_CDN_SUFFIXES = (
    ".jsdelivr.net", ".unpkg.com", "cdnjs.cloudflare.com", ".jquery.com",
    ".bootstrapcdn.com", ".googleapis.com", ".gstatic.com",
)
_CDN_HEADER_NAMES = (
    ("cloudflare", ("cf-ray", "cf-cache-status", "cf-request-id", "cf-apo-via")),
    ("cloudfront", ("x-amz-cf-id", "x-amz-cf-pop")),
)
_CDN_SERVER_VALUES = (
    ("cloudfront", "cloudfront"),
    ("cloudflare", "cloudflare"),
    ("akamai", "akamai"),
)
# JWPlayer PLAYER assets: the bundle/module scripts and the versioned dirs that
# carry them. Distinct from `_is_jwplayer_target`, which R5 uses to pick media
# and whose output is pinned by the recognizer corpus -- this one is additive
# and feeds only the topology block.
_JW_PLAYER_PATH_RE = re.compile(
    r"/jwplayer(?:[.\-][^/]*)?\.js(?:$|[?#])"     # jwplayer.js, jwplayer.core.controls.js
    r"|/jwpsrv\.js(?:$|[?#])"
    r"|/jwplayer[/\-]"                            # /jwplayer/jwplayer-8.30.1/...
    r"|/jw/\d+/",                                 # /jw/8/...
    re.I)
_JW_ENTITLEMENT_HOST_RE = re.compile(
    r"(?:^|\.)entitlements\.(?:jwplayer|jwplatform)\.com$", re.I)
_MEDIA_CT_RE = re.compile(
    r"^(?:video|audio)/|mpegurl|dash\+xml|/x-mpegurl", re.I)
_MEDIA_EXT_RE = re.compile(
    r"\.(?:m3u8|mpd|mp4|m4s|m4v|ts|webm|ogv|mov|mkv|f4m|f4v|flv)(?:$|[?#])", re.I)
_CAPTION_CT_RE = re.compile(r"^text/vtt|^application/x-subrip|^text/srt", re.I)
_CAPTION_EXT_RE = re.compile(r"\.(?:vtt|srt|ttml)(?:$|[?#])", re.I)


def _hdr_value(headers, name: str):
    """Case-insensitive header lookup that tolerates every shape a capture has
    carried: the HAR list of {"name","value"} dicts, a plain {name: value} dict,
    and the list-of-pairs form used by some fixtures. `_har_get` above handles
    only the first, and silently returns None for the other two -- which would
    make every header-derived CDN tell read as absent."""
    nl = name.lower()
    if isinstance(headers, dict):
        for k, v in headers.items():
            if str(k).lower() == nl:
                return v
        return None
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict):
                if str(h.get("name", "")).lower() == nl:
                    return h.get("value")
            elif isinstance(h, (list, tuple)) and len(h) == 2:
                if str(h[0]).lower() == nl:
                    return h[1]
    return None


def _hdr_names(headers):
    """Every response-header NAME, lowercased. Names only -- never values."""
    if isinstance(headers, dict):
        return [str(k).lower() for k in headers]
    if isinstance(headers, list):
        out = []
        for h in headers:
            if isinstance(h, dict):
                out.append(str(h.get("name", "")).lower())
            elif isinstance(h, (list, tuple)) and len(h) == 2:
                out.append(str(h[0]).lower())
        return out
    return []


def _is_library_cdn(host: str) -> bool:
    h = (host or "").lower()
    return any(h == s.lstrip(".") or h.endswith(s) for s in _LIBRARY_CDN_SUFFIXES)


def _cdn_vendor_from_host(host: str):
    """Suffix-ANCHORED vendor match. A substring test would call
    `notakamaized.net.evil.example` an akamai host."""
    h = (host or "").lower().split(":")[0]
    if not h or _is_library_cdn(h):
        return None
    for vendor, suffixes in _CDN_HOST_SUFFIXES:
        for s in suffixes:
            if h == s.lstrip(".") or h.endswith(s):
                return vendor
    return None


def _cdn_vendor_from_headers(headers, host: str = ""):
    """Vendor from response-header tells. Required for cloudflare, whose
    fronting leaves the hostname untouched."""
    if _is_library_cdn(host):
        return None
    names = _hdr_names(headers)
    if not names:
        return None
    for name in names:
        if name.startswith("akamai-") or name.startswith("x-akamai-"):
            return "akamai"
    for vendor, tells in _CDN_HEADER_NAMES:
        if any(t in names for t in tells):
            return vendor
    server = str(_hdr_value(headers, "server") or "").lower()
    if server:
        for vendor, needle in _CDN_SERVER_VALUES:
            if needle in server:
                return vendor
    return None


def _is_jwplayer_player_asset(host, path) -> bool:
    """A JWPlayer PLAYER asset -- the script/bundle that renders the player."""
    h = (host or "").lower()
    pp = (path or "")
    if _JW_ENTITLEMENT_HOST_RE.search(h):
        return False                      # classified as the entitlement call
    if any(m in h for m in _JWPLAYER_HOST_MARKERS):
        return True
    return bool(_JW_PLAYER_PATH_RE.search(pp))


def _is_jwplayer_entitlement(host, path) -> bool:
    """The JWPlayer entitlement/authorization call."""
    h = (host or "").lower()
    if _JW_ENTITLEMENT_HOST_RE.search(h):
        return True
    return (any(m in h for m in _JWPLAYER_HOST_MARKERS)
            and "entitlement" in (path or "").lower())


def _asset_kind(path: str, content_type: str) -> str:
    """"media" (video/audio/manifest), "caption", or "".

    THE CONTENT TYPE DECIDES, AND THE EXTENSION ONLY ARBITRATES WHAT IT LEAVES
    OPEN. Both halves of that rule were measured, not assumed. The akamai
    `/out/v1/<ids>/<ids>/...` segment path carries NO extension at all, so an
    extension-only rule reads a real HLS segment as nothing. And an extension
    checked FIRST -- or checked after an `octet-stream` match -- over-reads in
    the other direction: on one recorded page it called `/ramen-overrides.js`
    and an `iconfont.woff2` media, because both are served as octet-stream, and
    `.ts` is TypeScript far more often than it is an MPEG transport stream. So a
    server that named a definite type is believed, and only a missing or
    deliberately ambiguous `octet-stream` type falls through to the extension.
    """
    ct = (content_type or "").strip().lower()
    if ct and "octet-stream" not in ct:
        if _CAPTION_CT_RE.search(ct):
            return "caption"
        return "media" if _MEDIA_CT_RE.search(ct) else ""
    if _CAPTION_EXT_RE.search(path or ""):
        return "caption"
    if _MEDIA_EXT_RE.search(path or ""):
        return "media"
    return ""


def jwplayer_cdn_topology(network_log) -> dict:
    """Row 120's acceptance criterion, measured: is signed JWPlayer delivery
    fronted by akamai / cloudflare / cloudfront?

    Returns a review-only block. Hosts, counts and vendor names only -- no
    signed value, no query string, no header value is ever carried out (F2).
    ``status`` is "measured" or "unknown"; on "unknown" the two verdict fields
    stay None so an unread archive can never present as a measured absence.
    """
    out = {
        "status": "unknown",
        "unknown_reason": None,
        "entries_total": 0,
        "entries_examined": 0,
        "tracker_filter": "unavailable",
        "jwplayer_present": None,
        "cdn_fronted": None,
        "jwplayer_player_assets": 0,
        "jwplayer_player_hosts": [],
        "jwplayer_entitlement_calls": 0,
        "jwplayer_entitlement_hosts": [],
        "media_assets": 0,
        "media_hosts": [],
        "caption_assets": 0,
        "signed_media_assets": 0,
        "signed_and_cdn_fronted_media_assets": 0,
        "cdn_vendors": [],
        "cdn_fronted_hosts": [],
        "cdn_fronted_player_assets": 0,
        "cdn_fronted_media_assets": 0,
        "cdn_fronted_caption_assets": 0,
        "cdn_evidence": [],
        "excluded_tracker_hosts": [],
    }
    if not isinstance(network_log, list):
        out["unknown_reason"] = (
            "network_log is %s, not a list" % type(network_log).__name__)
        return out
    entries = [e for e in network_log if isinstance(e, dict)]
    if not entries:
        out["unknown_reason"] = (
            "network_log carried zero request entries; a zero read out of an "
            "unread or wrongly-parsed archive is not a measured absence")
        return out

    out["entries_total"] = len(entries)
    # An unavailable tracker filter is RECORDED, not silently skipped: without
    # it an ad video on a cloudflare host could read as CDN-fronted media, so a
    # reader has to be able to see that the filter did not run.
    try:
        from bulk_downloader import honeypot_score as _hp
        out["tracker_filter"] = "honeypot_score"
    except Exception:
        _hp = None

    player_hosts, ent_hosts, media_hosts = set(), set(), set()
    vendors, fronted_hosts, evidence, trackers = set(), set(), set(), set()
    for e in entries:
        url = e.get("url")
        if not isinstance(url, str) or not url:
            continue
        try:
            p = urlparse(url)
        except Exception:
            continue
        host, path = (p.netloc or ""), (p.path or "")
        if not host:
            continue
        if _hp is not None:
            try:
                if _hp._host_matches_tracker(host):
                    trackers.add(host)
                    continue
            except Exception:
                pass
        rh = e.get("response_headers")
        ct = str(_hdr_value(rh, "content-type") or "")
        kind = _asset_kind(path, ct)
        is_ent = _is_jwplayer_entitlement(host, path)
        is_player = (not is_ent) and _is_jwplayer_player_asset(host, path)
        if not (is_ent or is_player or kind):
            continue

        out["entries_examined"] += 1
        if is_ent:
            out["jwplayer_entitlement_calls"] += 1
            ent_hosts.add(host)
        if is_player:
            out["jwplayer_player_assets"] += 1
            player_hosts.add(host)
        signed = _has_signing_query(p.query or "")
        if kind == "media":
            out["media_assets"] += 1
            media_hosts.add(host)
            if signed:
                out["signed_media_assets"] += 1
        elif kind == "caption":
            out["caption_assets"] += 1

        by_host = _cdn_vendor_from_host(host)
        by_hdr = _cdn_vendor_from_headers(rh, host)
        vendor = by_host or by_hdr
        if not vendor:
            continue
        # An entitlement call is a JWPlayer control-plane call, not the player
        # or the media, so it records the vendor but never satisfies the
        # criterion on its own -- same rule as a caption.
        vendors.add(vendor)
        fronted_hosts.add(host)
        if by_host:
            evidence.add("host_suffix")
        if by_hdr:
            evidence.add("response_header")
        if is_player:
            out["cdn_fronted_player_assets"] += 1
        if kind == "media":
            out["cdn_fronted_media_assets"] += 1
            if signed:
                out["signed_and_cdn_fronted_media_assets"] += 1
        elif kind == "caption":
            out["cdn_fronted_caption_assets"] += 1

    out["status"] = "measured"
    out["jwplayer_player_hosts"] = sorted(player_hosts)
    out["jwplayer_entitlement_hosts"] = sorted(ent_hosts)
    out["media_hosts"] = sorted(media_hosts)
    out["cdn_vendors"] = sorted(vendors)
    out["cdn_fronted_hosts"] = sorted(fronted_hosts)
    out["cdn_evidence"] = sorted(evidence)
    out["excluded_tracker_hosts"] = sorted(trackers)
    out["jwplayer_present"] = bool(
        out["jwplayer_player_assets"] or out["jwplayer_entitlement_calls"])
    out["cdn_fronted"] = bool(
        out["cdn_fronted_player_assets"] or out["cdn_fronted_media_assets"])
    return out


def _supplemental_media_patterns(network_log: list) -> dict:
    """Recognize the download target via site-agnostic SIGNALS the core misses.

    Returns templated, signing-free media patterns plus resolutions, media hosts,
    variants, and the download_signals that fired. Never returns api patterns — a
    direct-download site has no download API, and we do not invent one.

    Recognizers (all non-guard, all templated output):
      R1  Content-Disposition: attachment        → strongest cross-site signal
      R2  ranged (206 / Content-Range) video/mp4 on a dedicated-DL host (or large body)
      R3  generalized resolution-in-filename      (…{delim}{res}[pP]?…\\.mp4)
      R4  HLS .ts rendition structure             (not a contentless manifest stub)
    """
    media_patterns: set = set()
    resolutions: set = set()
    media_hosts: set = set()
    variants: set = set()
    signals: set = set()
    for e in network_log or []:
        if not isinstance(e, dict):
            continue
        url = e.get("url")
        if not isinstance(url, str):
            continue
        try:
            p = urlparse(url)
        except Exception:
            continue
        path = p.path or ""
        host = p.netloc or ""
        if _CORE_MEDIA_RE.search(path):
            continue  # extraction_core already derives a pattern for this

        rh = e.get("response_headers")
        ct = (_har_get(rh, "content-type") or "").lower()
        dispo = (_har_get(rh, "content-disposition") or "").lower()
        crange = _har_get(rh, "content-range")
        clen = _to_int(_har_get(rh, "content-length"))
        is_mp4 = path.lower().endswith(".mp4") or "mp4" in ct
        is_ts = path.lower().endswith(".ts")
        ranged = str(e.get("response_status")) == "206" or bool(crange)
        large = clen is not None and clen > _LARGE_MEDIA_BYTES
        matched = False

        # R3 (+ legacy x{res}): resolution-from-filename
        m = _DIRECT_MP4_RE.search(path)
        if m:
            resolutions.add(int(m.group("res")))
            var = m.group("variant")
            if var:
                variants.add(var.upper())
            if host:
                media_hosts.add(host)
            # Templated suffix only — never the slug/name/query (no signed material).
            media_patterns.add(
                ".../x{resolution}" + ("_{variant}" if var else "") + ".mp4")
            signals.add("resolution-in-name")
            matched = True
        # R4: HLS .ts rendition structure
        elif is_ts:
            tmpl = _ts_rendition_pattern(path)
            if tmpl:
                media_patterns.add(tmpl)
                if host:
                    media_hosts.add(host)
                for r in _RES_IN_PATH_RE.findall(path):
                    if int(r) in _DIRECT_RES:
                        resolutions.add(int(r))
                signals.add("hls-rendition")
                matched = True

        # R1: Content-Disposition: attachment ⇒ download target
        if "attach" in dispo:
            signals.add("content-disposition")
            if host:
                media_hosts.add(host)
            if not matched and is_mp4:
                media_patterns.add(".../{download}.mp4")
                matched = True
        # R2: dedicated-DL-host (or large body) + ranged video/mp4
        if is_mp4 and ranged and (_DL_HOST_RE.search(host) or large):
            signals.add("ranged-mp4")
            if host:
                media_hosts.add(host)
            if not matched:
                media_patterns.add(".../{download}.mp4")
                matched = True

        # R5: signed direct-media (JWPlayer / akamai/cloudflare). A media
        # response whose query is signed (token/expiry/signature) — or a
        # JWPlayer playlist/media target — that none of R1–R4 caught. The
        # signed URL is short-lived: we emit only a SIGNING-FREE template
        # (query stripped) and the signal, never the signed value. Detection
        # only; the actual media is captured live via the runtime player path.
        signed_q = _has_signing_query(p.query or "")
        jw = _is_jwplayer_target(host, path)
        if jw:
            signals.add("jwplayer")
            if host:
                media_hosts.add(host)
        if (is_mp4 or jw) and (signed_q or jw):
            if signed_q:
                signals.add("signed-media")
            if host:
                media_hosts.add(host)
            if is_mp4 and not matched:
                media_patterns.add(".../{signed}.mp4")
                matched = True
    return {
        "media_patterns": sorted(media_patterns),
        "resolutions": sorted(resolutions, reverse=True),
        "media_hosts": sorted(media_hosts),
        "media_variants": sorted(variants),
        "download_signals": sorted(signals),
    }


def _merge_supplemental_media(network: dict, network_log: list) -> dict:
    """Union the supplemental direct-stream recognition into the core's output.

    Only touches media_patterns / resolutions_seen (+ adds media_hosts/variants
    provenance). api_patterns / observed_api_hosts are left exactly as the core
    produced them — we never fabricate an API for a direct-stream site.
    """
    sup = _supplemental_media_patterns(network_log)
    if not sup["media_patterns"] and not sup["resolutions"]:
        return network
    mp = set(network.get("media_patterns") or [])
    mp.update(sup["media_patterns"])
    network["media_patterns"] = sorted(mp)
    rs = set(network.get("resolutions_seen") or [])
    rs.update(sup["resolutions"])
    network["resolutions_seen"] = sorted(rs, reverse=True)
    if sup["media_hosts"]:
        network["media_hosts"] = sorted(
            set(network.get("media_hosts") or []) | set(sup["media_hosts"]))
    if sup["media_variants"]:
        network["media_variants"] = sup["media_variants"]
    if sup.get("download_signals"):
        network["download_signals"] = sup["download_signals"]
    network["supplemental_media"] = True
    return network


# ── Modal-scoped row-selector mining from the rrweb dom_log ───────────────────
_ROW_ROLES = {"menuitem", "menuitemradio", "option", "row", "listitem"}

# Modal-scope class stems. WORD-BOUNDARY tokens match compounds (modal-dialog /
# modal-content are real modals). EXACT tokens match a standalone class only:
# lightbox/fancybox/colorbox are library names and popup/overlay are common
# innocent SUFFIXES (loading-overlay, image-popup, video-lightbox-trigger are NOT
# modal containers), so requiring an exact class token there kills the
# false-positive modal scoping (MOD-tokens / over-scope fix).
_MODAL_WB_TOKENS = ("modal", "dialog", "drawer", "popover")
_MODAL_EXACT_TOKENS = ("lightbox", "fancybox", "colorbox", "popup", "overlay")


def _modal_scope_token(attrs: dict):
    """A modal/dialog scope prefix for `attrs`, or None. The returned token is
    chosen to satisfy both template_normalize._MODAL_RE and selector_lint's
    scoped-selector check (so derived rows pass the safety gate)."""
    a = attrs or {}
    role = str(a.get("role") or "").lower()
    if role == "dialog":
        return '[role="dialog"]'
    if a.get("aria-modal"):
        return '[aria-modal="true"]'
    low = str(a.get("class") or "").lower()
    if "ant-modal" in low:
        return ".ant-modal"
    if "muidialog" in low:
        return ".MuiDialog-root"
    for tok in _MODAL_WB_TOKENS:
        if re.search(r"\b" + tok + r"\b", low):
            return "." + tok
    cls = low.split()
    for tok in _MODAL_EXACT_TOKENS:
        if tok in cls:
            return "." + tok
    return None


def _modal_scope_tokens(attrs: dict) -> list:
    """ALL modal/dialog scope prefixes that apply to ``attrs`` (a container often
    carries both ``role="dialog"`` and ``.ant-modal``; the gold templates emit a
    row family under each). Specific scopes (role=dialog / ant-modal / MuiDialog)
    take precedence; the generic fallback fires ONLY when no specific scope
    matched — word-boundary for modal/dialog/drawer/popover (compounds are real
    modals) and EXACT class token for lightbox/fancybox/colorbox/popup/overlay
    (so loading-overlay / image-popup are not mistaken for modals)."""
    a = attrs or {}
    toks: list = []
    role = str(a.get("role") or "").lower()
    low = str(a.get("class") or "").lower()
    classes = low.split()
    if role == "dialog":
        toks.append('[role="dialog"]')
    if "ant-modal" in classes:
        toks.append(".ant-modal")
    if any("muidialog" in c for c in classes):
        toks.append(".MuiDialog-root")
    if not toks:
        for tok in _MODAL_WB_TOKENS:
            if re.search(r"\b" + tok + r"\b", low):
                toks.append("." + tok)
        for tok in _MODAL_EXACT_TOKENS:
            if tok in classes:
                toks.append("." + tok)
    if not toks:
        single = _modal_scope_token(a)
        if single:
            toks.append(single)
    return toks


_RES_TEXT_RE = re.compile(r"\b(4320|2160|1440|1080|720|540|480|360|240)\b")


def _node_text(node: dict) -> str:
    """Direct text of an element node (rrweb text children, type==3)."""
    out = []
    for c in (node.get("childNodes") or []):
        if isinstance(c, dict) and c.get("type") == 3:
            out.append(str(c.get("textContent") or ""))
    return " ".join(out)


def _row_part(node: dict):
    """A stable row-shaped selector fragment for a serialized node, or None."""
    tag = str(node.get("tagName") or "").lower()
    a = node.get("attributes") or {}
    role = str(a.get("role") or "").lower()
    if tag in ("li", "a", "button", "tr") and role in _ROW_ROLES:
        return f'{tag}[role="{role}"]'
    if tag == "a" and "download" in a:
        return "a[download]"
    if tag in ("li", "a", "button") and role:
        return f'{tag}[role="{role}"]'
    return None


# Recursion-depth guard for the serialized-node walkers (deep-DOM). The builder
# ingests an operator-chosen WACZ; a fresh capture.json is already truncated to
# <=250 DOM levels by the export-redaction cap, but an old/external capture could
# nest deeper and blow Python's recursion limit. 400 is above any real DOM and
# the truncated-capture ceiling, so real builds are unchanged; only a
# pathological tree is cut off.
_MAX_NODE_DEPTH = 400


def _walk_node(node, fn, _depth=0):
    if not isinstance(node, dict) or _depth > _MAX_NODE_DEPTH:
        return
    fn(node)
    for c in (node.get("childNodes") or []):
        _walk_node(c, fn, _depth + 1)


# ── A6-1: observed-workflow + trigger derivation from the capture timeline ───
# The capture records the real interaction sequence (rrweb MouseInteraction
# clicks, the mutation that opens a modal, and the download-resolution API
# response). Mining that ORDERED stream lets the builder self-describe the
# workflow and propose the modal-OPEN trigger (the element clicked immediately
# before a modal appears) — instead of leaving both to hand-editing. Structure
# only: emits step labels + a selector SHAPE, never URLs/values (and the whole
# draft still passes the redact_artifact chokepoint).

# rrweb IncrementalSource: 0=Mutation, 2=MouseInteraction. MouseInteractionType
# 2=Click. Captures vary, so detection is defensive.
def _parent_index(dom_log: list) -> dict:
    """{child_id: parent_id} over the serialized trees. Lets a click that landed
    on a text/icon node (rrweb records the deepest target) resolve UP to its
    nearest ancestor element. full_snapshot uses childNodes hierarchy;
    incremental adds carry an explicit ``parentId``."""
    par: dict = {}

    def walk(node, pid):
        if not isinstance(node, dict):
            return
        nid = node.get("id")
        if isinstance(nid, int) and isinstance(pid, int):
            par[nid] = pid
        for c in (node.get("childNodes") or []):
            walk(c, nid if isinstance(nid, int) else pid)
    for ev in dom_log or []:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if isinstance(d.get("node"), dict):
            walk(d["node"], None)
        for a in (d.get("adds") or []):
            if isinstance(a, dict) and isinstance(a.get("node"), dict):
                walk(a["node"], a.get("parentId"))
    return par


def _click_selector(cid, idx: dict, parents: dict, max_up: int = 6):
    """Resolve a click target id to a selector. rrweb often records the click on
    a non-element (the text/icon under the cursor); when the target itself does
    not resolve, walk up to the NEAREST ancestor ELEMENT. Returns
    ``(selector, walked)`` where ``walked`` is True if an ancestor (not the
    target itself) supplied the selector — review-only signal that it is
    container/icon-level, not a labelled control. Returns ``(None, False)`` if
    nothing in the bounded chain resolves."""
    cur, hops = cid, 0
    while isinstance(cur, int) and hops <= max_up:
        sel = _selector_for_element(idx.get(cur))
        if sel:
            return sel, (hops > 0)
        cur = parents.get(cur)
        hops += 1
    return None, False


def _index_serialized_nodes(dom_log: list) -> dict:
    """{node_id: node} over every serialized node tree in the dom_log
    (full_snapshot data.node + incremental adds[].node)."""
    idx: dict = {}

    def add(n):
        if isinstance(n, dict) and isinstance(n.get("id"), int):
            idx.setdefault(n["id"], n)
    for ev in dom_log or []:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if isinstance(d.get("node"), dict):
            _walk_node(d["node"], add)
        for a in (d.get("adds") or []):
            if isinstance(a, dict) and isinstance(a.get("node"), dict):
                _walk_node(a["node"], add)
    return idx


_GENERIC_CLASS = re.compile(
    r"^(active|open|show|hidden|visible|selected|disabled|focus|hover|"
    r"d-flex|d-block|row|col|container|wrapper|content|inner|outer|"
    r"card|item|box|tile|thumb|thumbnail|cell|media|panel|list|grid|"
    r"block|flex|clearfix|btn|button|link|icon|label|text|title|"
    r"image|img|wrap|main)$", re.I)

# js-/state/framework utility classes — never a stable content anchor.
_UTILITY_CLASS = re.compile(
    r"^(?:js|is|has|u|ng|v|x|aos)-|^(?:fade|sr-only|visually-hidden)$", re.I)


def _pick_class(class_attr) -> str:
    """Most-distinctive class from a class attribute, or None (SEL-class).
    Skips generic content/layout words and js-/state/utility classes; prefers a
    hyphen/underscore-segmented class, then a longer one (more specific),
    stable-first on ties — so ``card video-thumbnail`` yields ``video-thumbnail``
    rather than the generic first class ``card``."""
    best = None
    best_score = None
    for c in str(class_attr or "").split():
        if not c or _GENERIC_CLASS.match(c) or _UTILITY_CLASS.match(c):
            continue
        score = (("-" in c or "_" in c), len(c))
        if best_score is None or score > best_score:
            best, best_score = c, score
    return best


def _selector_for_element(node: dict):
    """A stable selector SHAPE for a serialized element node, or None.
    Preference: #id > tag.distinctive-class > tag[role] > tag. Generic
    state/layout classes are skipped (they are not stable anchors); when an
    element carries only generic classes we fall through to role/tag rather than
    emit a too-broad ``tag.generic`` selector."""
    if not isinstance(node, dict) or node.get("type") != 2:
        return None
    tag = str(node.get("tagName") or "").lower()
    if not tag:
        return None
    a = node.get("attributes") or {}
    nid = a.get("id")
    if isinstance(nid, str) and nid and not re.search(r"\d{3,}", nid):
        return f"#{nid}"                      # an id with a long digit run is likely volatile
    cls = _pick_class(a.get("class"))
    if cls:
        return f"{tag}.{cls}"
    role = str(a.get("role") or "").lower()
    if role:
        return f'{tag}[role="{role}"]'
    return tag


def _is_click(ev: dict) -> bool:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return ev.get("type") == "incremental" and d.get("source") == 2 and d.get("type") == 2


def _adds_open_modal(ev: dict) -> bool:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if ev.get("type") != "incremental" or not d.get("adds"):
        return False
    hit = {"v": False}
    for a in (d.get("adds") or []):
        if isinstance(a, dict) and isinstance(a.get("node"), dict):
            _walk_node(a["node"], lambda n: hit.__setitem__(
                "v", hit["v"] or bool(_modal_scope_token(n.get("attributes")))))
    return hit["v"]


def _looks_like_download_api(url: str) -> bool:
    base = (url or "").split("?", 1)[0].lower()
    return any(k in base for k in ("download", "resolution", "/dl/", "manifest", ".m3u8", ".mpd"))


def _derive_workflow_from_action_timeline(action_timeline: list, cap: dict):
    """Wave B: derive ordered observed steps + a trigger candidate from the
    operator-recorded ``action_timeline`` (inspect_pick entries persisted into
    the WACZ by dom_capture). This is the RELIABLE click->effect source the
    rrweb ``dom_log`` derivation lacks — rrweb frequently records no
    MouseInteraction clicks, so the dom_log timeline "never fires on real data".

    Structural ONLY: each entry already carries a selector/role (structure) and
    an effect (request kinds/counts) with values redacted at capture time; no
    URL or value crosses this boundary. The trigger candidate is the click whose
    effect first produced a manifest/segment/direct-media response (reusing the
    capture-time ``inspect_pick.verify_summary`` resolution). Returns ``None``
    when no action_timeline is present (caller falls back to the dom_log path);
    never overwrites the stronger structural ``trigger`` field."""
    tl = [e for e in (action_timeline or []) if isinstance(e, dict)]
    if not tl:
        return None
    v = {}
    try:
        from bulk_downloader import inspect_pick as _ip  # lazy: repo root on sys.path
        v = _ip.verify_summary(tl, cap if isinstance(cap, dict) else {}, recorded_clicks=len(tl))
    except Exception:
        v = {}
    steps = []
    for e in tl:
        eff = e.get("effect") or {}
        steps.append(
            "interact: {role} ({sel}) -> req:{r} manifest:{m} seg:{s} direct:{d}".format(
                role=(e.get("role") or "element"),
                sel=(e.get("selector") or "unresolved target"),
                r=int(eff.get("req_count") or 0), m=int(eff.get("manifest") or 0),
                s=int(eff.get("segments") or 0), d=int(eff.get("direct_media") or 0)))
    trig = (v or {}).get("trigger_selector")
    return {
        "derived_steps": steps,
        "trigger_candidate": trig,
        "trigger_evidence": ("operator click whose effect first produced media "
                             "(action_timeline)") if trig else None,
        "source": "action_timeline",
        "verify": {k: (v or {}).get(k) for k in
                   ("tier", "checks", "warnings", "gap_count", "action_count")},
    }


# The redaction sentinel is DERIVED from the canonical source (see the module-level
# import of PLACEHOLDER as _SCRUBBED near the extraction_core import) -- a scrubbed
# selector from an OLD capture must not be treated as a real selector.


def _generalize_attr_value(sel: str) -> str:
    """Drop the VALUE from each quoted attribute predicate, keeping the attribute
    PRESENCE, so a per-rendition row anchor matches the whole family:
    ``a.ct_dl_button[data-framerate="60fps"]`` -> ``a.ct_dl_button[data-framerate]``.
    Selectors with no quoted attribute value are returned unchanged."""
    if not isinstance(sel, str):
        return sel
    return re.sub(r'\[\s*([A-Za-z_:][\w:.\-]*)\s*=\s*"[^"]*"\s*\]', r'[\1]', sel)


def _autoplay_contaminated(entry: dict, network_log) -> bool:
    """REC-3: True when a download-affordance click's ``direct_media`` is the
    player's autoplay/preview stream carrying over the correlation window rather
    than a download the click actually triggered.

    Prefers the capture-side attribution stamped on the effect (v3.66.302+):
    ``autoplay and not fresh_download``. For a LEGACY capture whose effect
    predates that attribution, back-computes it from the passed ``network_log``
    + the entry's click ``ts`` (reusing inspect_pick.media_attribution). With no
    signal at all the guard never fabricates a rejection (returns False)."""
    eff = entry.get("effect") or {}
    if "autoplay" in eff or "fresh_download" in eff:
        return bool(eff.get("autoplay")) and not bool(eff.get("fresh_download"))
    ts = entry.get("ts")
    if network_log and isinstance(ts, int):
        try:
            from bulk_downloader import inspect_pick as _ip
            att = _ip.media_attribution(network_log, ts)
            return bool(att.get("autoplay")) and not bool(att.get("fresh_download"))
        except Exception:
            return False
    return False


def _download_trigger_from_timeline(action_timeline: list, network_log=None):
    """The REAL download trigger from the operator-recorded action_timeline,
    as ``(generalized_selector, tag)`` or ``None``.

    The DOM-structure derivation (`_derive_download_trigger`) deliberately
    excludes ``<a download>`` rows while hunting a separate modal *opener*, so a
    site whose download anchors ARE the rows (e.g. wowgirls'
    ``a.ct_dl_button[data-framerate=…]``) gets no trigger and falls through to a
    generic ``a[download]``. Here we pick the download-link step whose effect
    produced direct media and GENERALIZE its attribute value to a family
    selector. The caller places it by tag: a ``<button>`` is an opener; an
    ``<a>`` is the download anchor itself. Structure-only — a CSS selector
    shape, never a URL/value; a scrubbed selector from an old capture is
    skipped.

    REC-3 autoplay-window guard (v3.66.302): a candidate whose direct media is
    the player's autoplay/preview stream (``autoplay and not fresh_download``)
    is skipped, so an autoplay site no longer seeds a false trigger from a click
    that merely overlapped media already in flight. ``network_log`` (optional)
    lets the guard back-compute the attribution for legacy captures whose effect
    predates it."""
    best = None  # ((direct, -order), selector, tag)
    for i, e in enumerate(action_timeline or []):
        if not isinstance(e, dict):
            continue
        sel = e.get("selector")
        if not isinstance(sel, str) or not sel or sel == _SCRUBBED:
            continue
        role = str(e.get("role") or "").lower()
        eff = e.get("effect") or {}
        direct = int(eff.get("direct_media") or 0)
        is_dl = ("download" in role) or ("download" in sel.lower())
        if is_dl and direct >= 1:
            if _autoplay_contaminated(e, network_log):
                continue  # autoplay stream, not a click-triggered download
            key = (direct, -i)  # strongest direct-media effect, earliest on ties
            if best is None or key > best[0]:
                best = (key, _generalize_attr_value(sel), str(e.get("tag") or "").lower())
    return (best[1], best[2]) if best else None


def _login_email_from_timeline(action_timeline: list):
    """The credential (email/username) input selector from the action_timeline
    login steps — used only when the static-HTML path missed it (e.g. the field
    is ``#user-email`` with a non-email type/name). Structure-only; a submit
    button is never returned, and a scrubbed selector is skipped."""
    for e in action_timeline or []:
        if not isinstance(e, dict):
            continue
        sel = e.get("selector")
        role = str(e.get("role") or "").lower()
        if not isinstance(sel, str) or not sel or sel == _SCRUBBED:
            continue
        low = sel.lower()
        if "submit" in low or "button" in low:
            continue
        looks_input = low.startswith("input") or low.startswith("#") or "[name=" in low
        is_creds = ("email" in low) or ("user" in low) or ("login" in low and "input" in low)
        if role == "login/submit" and looks_input and is_creds:
            return sel
    return None


def _derive_workflow_timeline(dom_log: list, network_log: list) -> dict:
    """Derive ordered observed steps + a modal-open trigger candidate from the
    capture timeline. Returns {derived_steps, trigger_candidate, trigger_evidence}.
    All structural — no URLs or values are emitted."""
    idx = _index_serialized_nodes(dom_log)
    # ordered DOM events (stable: keep capture order, break ties by index)
    dom = [e for e in (dom_log or []) if isinstance(e, dict)]
    steps: list = []
    trigger = None
    evidence = None
    last_click = None  # (node_id, position)

    if any(e.get("type") == "meta" for e in dom):
        steps.append("navigate: page loaded (meta)")
    saw_snapshot = False
    for i, e in enumerate(dom):
        t = e.get("type")
        if t == "full_snapshot" and not saw_snapshot:
            saw_snapshot = True
            steps.append("render: initial DOM snapshot")
        elif _is_click(e):
            cid = (e.get("data") or {}).get("id")
            last_click = (cid, i)
            sel = _selector_for_element(idx.get(cid)) if isinstance(cid, int) else None
            steps.append(f"interact: click ({sel or 'unresolved target'})")
        elif _adds_open_modal(e):
            steps.append("modal: a dialog/modal opened")
            # the click immediately preceding this open is the trigger candidate
            if trigger is None and last_click is not None:
                sel = _selector_for_element(idx.get(last_click[0]))
                if sel:
                    trigger = sel
                    evidence = "element clicked immediately before the modal opened"

    # network side: did a download-resolution / manifest response occur?
    nl = [e for e in (network_log or []) if isinstance(e, dict)]
    if any(_looks_like_download_api(e.get("url") or "") for e in nl):
        steps.append("network: download-resolution / manifest response observed")
    if any(str(e.get("type") or "").lower() == "media" for e in nl):
        steps.append("network: media rendition fetched")

    # A6.2 passive-capture fallback: rrweb rarely serializes a modal-open, so the
    # modal-anchored heuristic above "never fires on real data". When no trigger
    # was anchored, correlate the recorded clicks with the network effect they
    # produced — the click IMMEDIATELY preceding the first download-resolution /
    # manifest response is the trigger candidate. This mirrors the
    # action_timeline path's "first click whose effect produced media" rule,
    # computed offline from click+response timestamps. CONSERVATIVE: fires ONLY
    # when such a response exists AND a timestamped click precedes it, so it
    # never fabricates a trigger on a site that produced no download signal.
    # Structural only (a resolved selector); no URL or value is emitted. Review-
    # only — never overwrites the stronger structural `trigger` field, and the
    # action_timeline path still wins for captures that recorded operator picks.
    if trigger is None:
        dl_ts = sorted(e.get("timestamp") for e in nl
                       if _looks_like_download_api(e.get("url") or "")
                       and isinstance(e.get("timestamp"), int))
        click_ev = [e for e in dom
                    if _is_click(e) and isinstance(e.get("timestamp"), int)]
        if dl_ts and click_ev:
            preceding = [e for e in click_ev if e["timestamp"] < dl_ts[0]]
            if preceding:
                anchor = max(preceding, key=lambda e: e["timestamp"])
                cid = (anchor.get("data") or {}).get("id")
                sel, walked = (_click_selector(cid, idx, _parent_index(dom_log))
                               if isinstance(cid, int) else (None, False))
                if sel:
                    trigger = sel
                    evidence = ("click immediately preceding the first "
                                "download-resolution/manifest response "
                                "(effect-correlated; no operator pick recorded"
                                + ("; resolved to nearest clickable ancestor"
                                   if walked else "") + ")")

    return {"derived_steps": steps, "trigger_candidate": trigger,
            "trigger_evidence": evidence}


def _derive_download_trigger(dom_log: list):
    """A6-1: derive the modal-OPEN download trigger from DOM STRUCTURE — the
    interactive element carrying a download affordance that lives OUTSIDE the
    download modal (the opener), not the ``a[download]`` row link inside it.

    Reads structure, not a click->open timeline: captures here record snapshots
    + mutations but frequently NOT MouseInteraction clicks, so a timeline-only
    derivation never fires on real data. Prefers an attribute affordance
    (data-tooltip / aria-label / title ~ "download", as the gold templates use)
    over a class affordance. Returns a stable selector SHAPE or None; secret-free.
    """
    best = [None]   # (rank, selector)

    def _affordance(a):
        low = {str(k).lower(): str(v) for k, v in (a or {}).items()}
        for key in ("data-tooltip", "aria-label", "title"):
            if "download" in low.get(key, "").lower():
                return ("attr", key)
        if re.search(r"\bdownload", low.get("class", ""), re.I):
            return ("class", None)
        return None

    def _sel(tag, a, kind):
        if kind[0] == "attr":
            return f'{tag}[{kind[1]}*="download" i]'
        return _selector_for_element({"type": 2, "tagName": tag, "attributes": a})

    def visit(node, in_modal):
        if not isinstance(node, dict):
            return
        a = node.get("attributes") or {}
        child_in_modal = in_modal or bool(_modal_scope_token(a))
        tag = str(node.get("tagName") or "").lower()
        role = str(a.get("role") or "").lower()
        # opener candidates are buttons/role=button (or a NON-download anchor),
        # and must live OUTSIDE the modal (the rows live inside it)
        interactive = tag == "button" or role == "button" or (tag == "a" and "download" not in a)
        if interactive and not in_modal:
            aff = _affordance(a)
            if aff:
                rank = 0 if aff[0] == "attr" else 1
                sel = _sel(tag, a, aff)
                if sel and (best[0] is None or rank < best[0][0]):
                    best[0] = (rank, sel)
        for c in (node.get("childNodes") or []):
            visit(c, child_in_modal)

    for ev in dom_log or []:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if isinstance(d.get("node"), dict):
            visit(d["node"], False)
        for ad in (d.get("adds") or []):
            if isinstance(ad, dict) and isinstance(ad.get("node"), dict):
                visit(ad["node"], False)
    return best[0][1] if best[0] else None


_GENERIC_DL_RE = re.compile(
    r"download|\bdl\b|\.mp4|\.mkv|\.mov|\.webm|\.m4v|\.ts", re.I)
_GENERIC_RES_RE = re.compile(
    r"\d{3,4}\s*p\b|\d{3,4}\s*[x\u00d7]\s*\d{3,4}|\b(?:4k|2k|8k|hd|fhd|uhd|qhd)\b",
    re.I)


def _stable_class_sig(tag: str, class_attr: str) -> str | None:
    """tag + sorted stable classes — mirrors element_pick.stableClasses' rules
    (len>=2, [A-Za-z][\\w-]*, drop pure-hash). None when no stable class."""
    out = []
    for c in (class_attr or "").split():
        if len(c) < 2:
            continue
        if not re.match(r"^[A-Za-z][\w-]*$", c):
            continue
        if re.match(r"^[a-f0-9]{8,}$", c, re.I):
            continue
        out.append(c)
    if not out:
        return None
    return tag.lower() + "".join("." + c for c in sorted(out))


def _generic_row_selectors_from_html(html: str) -> list:
    """OFFLINE fallback: derive the dominant repeating, download-shaped tile
    signature from captured HTML when no modal/timeline rows were found.

    Mirrors the live element_pick.bdAutoRowGroups ranking, but offline has NO
    computed layout, so it cannot score visibility or auto-scope to a responsive
    container (the live picker is the visibility-aware path). Heuristic +
    review-only: this only ever feeds a draft candidate via setdefault — nothing
    is enabled.
    """
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []          # [tag, sig, dl_flag, [text...]]
            self.groups: dict = {}   # sig -> {"count": n, "dl": bool}

        def handle_starttag(self, tag, attrs):
            ad = {k: (v or "") for k, v in attrs}
            sig = _stable_class_sig(tag, ad.get("class", ""))
            href = " ".join((ad.get("href", ""), ad.get("data-href", ""),
                             ad.get("data-url", ""), ad.get("data-src", "")))
            dl = ("download" in ad) or bool(_GENERIC_DL_RE.search(href))
            self.stack.append([tag, sig, dl, []])

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

        def handle_data(self, data):
            if self.stack:
                self.stack[-1][3].append(data)

        def handle_endtag(self, tag):
            idx = None
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    idx = i
                    break
            if idx is None:
                return
            node = self.stack.pop(idx)
            _t, sig, dl, txt = node
            if not sig:
                return
            text = " ".join(txt)
            if _GENERIC_RES_RE.search(text):
                dl = True
            g = self.groups.setdefault(sig, {"count": 0, "dl": False})
            g["count"] += 1
            g["dl"] = g["dl"] or dl

    p = _P()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        return []
    # Keep signatures that REPEAT (>=2) and are download-shaped; rank by count.
    ranked = sorted(
        (sig for sig, g in p.groups.items() if g["count"] >= 2 and g["dl"]),
        key=lambda s: -p.groups[s]["count"],
    )
    return ranked


def _modal_row_selectors_from_dom(dom_log: list, *, cap: int = 14) -> list:
    """Derive modal-scoped row-selector CANDIDATES from a recorded modal.

    Walks rrweb full-snapshot nodes and incremental adds; for each detected
    modal/dialog container, emits ``<modal-scope> <row-part>`` for row shapes
    that REPEAT inside it (>=2 occurrences → a real list, not a one-off). These
    are candidates only — the normalizer's modal-scoped + lint-safe gate decides
    whether to keep them; nothing is auto-enabled.
    """
    found: list = []
    seen: set = set()

    def scan_tree(root):
        modals = []
        _walk_node(root, lambda n: modals.append(n)
                   if _modal_scope_tokens(n.get("attributes")) else None)
        for mnode in modals:
            tokens = _modal_scope_tokens(mnode.get("attributes"))
            counts: dict = {}          # repeating role/[download] row parts
            href_kinds: set = set()    # download / download-resolution anchor hrefs
            res_set: set = set()       # resolution-button text values
            class_rows: dict = {}      # repeating class-anchored content rows (SEL-row)
            disc_rows: dict = {}       # discriminating download-anchor rows
                                       # (a.<class>[<data-attr>]) — modal-scoped,
                                       # keeps the per-rendition discriminator the
                                       # generic a[download] collapse drops.

            def rowscan(n, _m=mnode):
                if n is _m or not isinstance(n, dict):
                    return
                rp = _row_part(n)
                if rp:
                    counts[rp] = counts.get(rp, 0) + 1
                tag = str(n.get("tagName") or "").lower()
                a = n.get("attributes") or {}
                if tag == "a":
                    href = str(a.get("href") or "").lower()
                    is_dl_anchor = ("download" in a) or ("download" in href)
                    if is_dl_anchor:
                        cls = _pick_class(a.get("class"))
                        # a stable data-* attribute distinguishes renditions
                        # (data-framerate / data-quality / data-res …); keep the
                        # attribute PRESENCE only (never its value).
                        datks = sorted(k for k in a.keys()
                                       if str(k).lower().startswith("data-"))
                        pref = [k for k in datks if re.search(
                            r"frame|fps|quality|res|format|bitrate|type|label", k, re.I)]
                        dk = (pref or datks or [None])[0]
                        if cls and dk:
                            key = f"a.{cls}[{dk}]"
                            disc_rows[key] = disc_rows.get(key, 0) + 1
                    if "download-resolution" in href:
                        href_kinds.add('a[href*="download-resolution" i]')
                    elif "download" in href:
                        href_kinds.add('a[href*="download" i]')
                    elif href:
                        # SEL-row: a repeated content link with a stable
                        # distinctive class (no role / no download attr) is a
                        # listing row. Stays modal-scoped → lint-safe.
                        cls = _pick_class(a.get("class"))
                        if cls:
                            key = f"a.{cls}"
                            class_rows[key] = class_rows.get(key, 0) + 1
                if tag in ("button", "a"):
                    for r in _RES_TEXT_RE.findall(_node_text(n)):
                        res_set.add(r)
            _walk_node(mnode, rowscan)

            # class-stable families, emitted under EACH applicable modal scope.
            # Discriminating download-anchor rows go FIRST (most specific); a
            # single such anchor is meaningful (it's a download link), so they are
            # not gated on the >=2 "repeats" rule the generic class rows use.
            parts: list = [k for k, c in sorted(disc_rows.items(), key=lambda x: -x[1])]
            parts += [rp for rp, c in sorted(counts.items(), key=lambda x: -x[1]) if c >= 2]
            parts += sorted(href_kinds)
            parts += [k for k, c in sorted(class_rows.items(), key=lambda x: -x[1]) if c >= 2]
            parts += [f'button:has-text("{r}")' for r in
                      sorted(res_set, key=lambda x: -int(x))]
            for tok in tokens:
                for rp in parts:
                    sel = f"{tok} {rp}"
                    if sel not in seen:
                        seen.add(sel)
                        found.append(sel)

    for ev in dom_log or []:
        d = ev.get("data") or {}
        if isinstance(d.get("node"), dict):
            scan_tree(d["node"])
        for a in (d.get("adds") or []):
            if isinstance(a, dict) and isinstance(a.get("node"), dict):
                scan_tree(a["node"])
        if len(found) >= cap:
            break
    return found[:cap]


_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


def _node_to_html(node: dict, _depth: int = 0) -> str:
    """Serialize an rrweb serialized node (NodeType 2=Element, 3=Text, 0/1/5=
    document/doctype/comment) into an HTML fragment, so the existing
    ``_html_selectors`` regex recognition can read captures that store the DOM
    as serialized nodes (``data.node`` / ``data.adds[].node``) rather than an
    ``html`` string. Structure only — emits tag + attribute shapes; the result
    is a transient local used to derive selector SHAPES and is never persisted
    (build_template returns redact_artifact(draft) regardless). Depth-bounded
    (deep-DOM guard)."""
    if not isinstance(node, dict) or _depth > _MAX_NODE_DEPTH:
        return ""
    ntype = node.get("type")
    if ntype == 3:  # Text
        return str(node.get("textContent") or "")
    children = node.get("childNodes") or []
    if ntype == 2:  # Element
        tag = str(node.get("tagName") or "").lower()
        if not tag:
            return "".join(_node_to_html(c, _depth + 1) for c in children)
        parts = []
        for k, v in (node.get("attributes") or {}).items():
            if v is True or v == "":
                parts.append(str(k))
            elif v is False or v is None:
                continue
            else:
                parts.append(f'{k}="{v}"')
        attr_str = (" " + " ".join(parts)) if parts else ""
        if tag in _VOID_TAGS:
            return f"<{tag}{attr_str}>"
        inner = "".join(_node_to_html(c, _depth + 1) for c in children)
        return f"<{tag}{attr_str}>{inner}</{tag}>"
    # Document / DocType / Comment / unknown — recurse children, drop wrapper.
    return "".join(_node_to_html(c, _depth + 1) for c in children)


_DOWNLOAD_API_RE = re.compile(
    r"download[-_]?resolution|/download(?:/|$)|resolution/\{resolution\}", re.I)

# Broader download-API path shape for the supplemental recognizer: catches
# ``video-download`` / ``get-download`` / ``download`` segments the frozen
# ``_DOWNLOAD_API_RE`` (slash-anchored ``/download``) cannot. Used ONLY when the
# recognizer has positively identified the download-API host, so the default
# (single-host) path of ``_download_api_template`` keeps its exact behaviour.
_SUP_DL_API_RE = re.compile(r"/[\w-]*download(?:/|$)", re.I)


def _merge_supplemental_api(network: dict, network_log: list) -> dict:
    """Builder-side, review-only: recognize a generic download-API host that
    ``extraction_core`` misses (it only flags the reptyle
    ``/api/v{n}/movie/{id}/download-resolution/{res}`` shape). Aylo platforms
    (bangbros/brazzers/realitykings) serve ``GET .../v1/video-download/{id}`` on
    a host distinct from the content CDN — so the core's ``observed_api_hosts``
    is empty AND ``_download_api_template`` bails on >1 host.

    Mines ``network_log`` independently for a download-API path shape and, ONLY
    when exactly ONE download-API host is present (never guess among several),
    records ``download_api_host`` + the templatised path and unions the host into
    ``observed_api_hosts`` / ``api_patterns``. Never fabricates an API for a
    direct/HLS-only site (media/asset URLs are skipped). The result is a
    suggestion only; it is never the runtime base and never enabled.
    extraction_core stays byte-identical.
    """
    dl_hosts: dict = {}  # host -> templatised download path
    for e in (network_log or []):
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        p = urlparse(url)
        path = (p.path or "").rstrip("/")
        if not path or _is_asset_path(path):     # never treat a media file as an API
            continue
        if not _SUP_DL_API_RE.search(path):
            continue
        segs = [s for s in path.split("/") if s]
        out: list = []
        for i, s in enumerate(segs):
            out.append(_param_for_segment(s, segs[i - 1] if i else "")
                       if _is_variable_seg(s) else s)
        dl_hosts.setdefault(p.netloc, "/" + "/".join(out))
    if len(dl_hosts) != 1:                        # zero or ambiguous -> no-op
        return network
    host, tpath = next(iter(dl_hosts.items()))
    network["supplemental_api"] = True
    network["download_api_host"] = host
    aps = set(str(a) for a in (network.get("api_patterns") or []))
    aps.add(tpath)
    network["api_patterns"] = sorted(aps)
    oah = set(str(h) for h in (network.get("observed_api_hosts") or []) if h)
    oah.add(host)
    network["observed_api_hosts"] = sorted(oah)
    return network


def _download_api_template(network: dict):
    """Review-only candidate: combine a SINGLE observed API host with a
    download/resolution-shaped, already-templated relative api_pattern into a
    concrete endpoint a reviewer can confirm. Returns None unless exactly one
    API host was observed AND a download/resolution-shaped pattern exists — it
    never guesses a host and never invents an endpoint. Secret-free: host plus
    extraction_core's templated path (no ids, no query, no signing). The result
    is a suggestion only; it is never the runtime API base and never enabled.

    When ``_merge_supplemental_api`` has positively identified a
    ``download_api_host`` (Aylo-style, multi-host), use THAT host with the
    broader download-shape match — bypassing the single-host bail for the
    identified host only, leaving the default path byte-behaviour-identical."""
    pats = [str(ap) for ap in (network.get("api_patterns") or []) if str(ap).startswith("/")]
    dhost = network.get("download_api_host")
    if dhost:
        for ap in pats:
            if _DOWNLOAD_API_RE.search(ap) or _SUP_DL_API_RE.search(ap):
                return f"https://{dhost}{ap}"
        return None
    hosts = sorted({str(h) for h in (network.get("observed_api_hosts") or []) if h})
    if len(hosts) != 1:
        return None
    for ap in pats:
        if _DOWNLOAD_API_RE.search(ap):
            return f"https://{hosts[0]}{ap}"
    return None


_NUM_SEG_RE = re.compile(r"^\d+$")
# A bare 4-digit segment in 1900-2099 is a static year/archive marker, not an
# id — keep it literal so a date archive path doesn't over-parametrize to a
# phantom {*_id} (NET-year). A non-year numeric (e.g. movie/9) stays variable.
_YEAR_SEG_RE = re.compile(r"^(?:19|20)\d{2}$")
# A path segment is treated as a VARIABLE (templated to {id}) when it is not a
# stable route token: a pure number, an opaque/long token (uuid, long hex, or a
# long slug), or otherwise content-bearing. This keeps content names/ids OUT of
# the derived endpoint patterns (F2 leak-safety) and out of named keys.
_OPAQUE_SEG_RE = re.compile(
    r"^(?:[0-9a-f]{16,}"                      # long hex / sha-ish
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # uuid
    r"|.{24,})$", re.I)                        # any very long segment (slug/title)
# Media/static asset paths are NOT API endpoints — exclude them entirely (their
# filenames can carry content titles, so they must never become endpoint names).
_ASSET_EXT = (".m3u8", ".mpd", ".mp4", ".m4s", ".ts", ".webm", ".mov", ".avi",
              ".mp3", ".aac", ".m4a", ".jpg", ".jpeg", ".png", ".gif", ".webp",
              ".svg", ".ico", ".bmp", ".css", ".js", ".mjs", ".woff", ".woff2",
              ".ttf", ".eot", ".map", ".wasm")


def _is_asset_path(path: str) -> bool:
    return path.lower().rsplit("/", 1)[-1].endswith(_ASSET_EXT)


def _param_for_segment(seg: str, prev: str) -> str:
    """Name a variable path segment from its preceding resource segment:
    ``movie/9`` -> ``movie/{movie_id}``; ``download-resolution/1080`` ->
    ``download-resolution/{resolution}``; an opaque token -> ``{id}``."""
    p = (prev or "").lower()
    if _NUM_SEG_RE.match(seg) and "resolution" in p:
        return "{resolution}"
    if not p:
        return "{id}"
    base = (p.rstrip("s") or p).replace("-", "_")
    return "{%s_id}" % base


def _is_variable_seg(seg: str) -> bool:
    if _YEAR_SEG_RE.match(seg):
        return False  # static year/archive marker, not an id (NET-year)
    return bool(_NUM_SEG_RE.match(seg) or _OPAQUE_SEG_RE.match(seg))


def _derive_api(network_log: list, network: dict):
    """A6-1: derive a CONCRETE runtime ``api.base`` + named endpoints from the
    observed API requests.

    Guard-free by construction: ``extraction_core`` only surfaces the
    ``observed_api_hosts`` hint + a single templated pattern (it deliberately
    never builds ``api.base``). Here, in the enrichment layer, we rebuild the
    concrete base/endpoints from the raw ``network_log`` — but only when the core
    recognised EXACTLY ONE api host, so we never guess a host (same conservatism
    as ``_download_api_template``). Secret-free: host + parametrised path only
    (numeric segments templated to ``{param}``; query stripped; no ids/tokens).

    Returns ``{"base": "...", "<name>": "/rel/{param}/path", ...}`` or ``None``.
    """
    hosts = sorted({str(h) for h in (network.get("observed_api_hosts") or []) if h})
    if len(hosts) != 1:
        return None
    host = hosts[0]
    paths: list = []
    seen: set = set()
    for e in (network_log or []):
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        p = urlparse(url)
        if p.netloc != host:
            continue
        path = (p.path or "").rstrip("/")
        if not path or _is_asset_path(path):     # API endpoints only — no media/static
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    if not paths:
        return None
    parametrised: list = []
    for path in paths:
        segs = [s for s in path.split("/") if s]
        out: list = []
        first_var = None
        for i, s in enumerate(segs):
            if _is_variable_seg(s):
                out.append(_param_for_segment(s, segs[i - 1] if i else ""))
                if first_var is None:
                    first_var = i
            else:
                out.append(s)
        parametrised.append((out, first_var if first_var is not None else len(out)))
    # base = scheme://host + leading STATIC segments common to all paths, stopping
    # one segment before the earliest first-variable (that segment owns the first
    # id and so starts the named endpoint, e.g. base=/api/v1, ep=/movie/{id}/…).
    cut = max(min(fv for _, fv in parametrised) - 1, 0)
    base_segs: list = []
    for i in range(cut):
        col = {segs[i] for segs, _ in parametrised if i < len(segs)}
        if len(col) == 1:
            base_segs.append(next(iter(col)))
        else:
            break
    base = "https://" + host + (("/" + "/".join(base_segs)) if base_segs else "")
    api = {"base": base}
    n_base = len(base_segs)
    used: dict = {}
    for segs, _ in parametrised:
        rel = "/" + "/".join(segs[n_base:])
        name = next((s.replace("-", "_") for s in reversed(segs[n_base:])
                     if not s.startswith("{")), "endpoint")
        if name in api and api[name] != rel:
            used[name] = used.get(name, 0) + 1
            name = "%s_%d" % (name, used[name])
        api.setdefault(name, rel)
    return api


def _nodes_to_html(dom_log: list) -> str:
    """Concatenated HTML view of every serialized-node tree in the dom_log:
    full-snapshot ``data.node`` AND interaction-added ``data.adds[].node`` (so
    modals/menus/buttons opened after the initial snapshot are seen)."""
    chunks = []
    for ev in dom_log or []:
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") or {}
        node = d.get("node")
        if isinstance(node, dict):
            chunks.append(_node_to_html(node))
        for a in (d.get("adds") or []):
            if isinstance(a, dict) and isinstance(a.get("node"), dict):
                chunks.append(_node_to_html(a["node"]))
    return "\n".join(chunks)


# Zip-bomb ceiling for the capture.json member read out of a .wacz (rec #2). A
# real capture.json is far below this; a larger declared member is refused unread.
_MAX_CAPTURE_JSON_BYTES = 256 * 1024 * 1024


def _load_capture(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        cap_name = next((n for n in names if n.endswith("archive/capture.json")), None)
        if not cap_name:
            cap_name = next((n for n in names if n.endswith("capture.json")), None)
        if not cap_name:
            raise SystemExit(f"no capture.json found in {path}")
        # Zip-bomb guard: a real capture.json is far below this ceiling; a member
        # declaring more uncompressed bytes is refused unread (zipfile honours the
        # declared size, so a bomb must declare a large size to deliver one).
        if z.getinfo(cap_name).file_size > _MAX_CAPTURE_JSON_BYTES:
            raise SystemExit(
                f"capture.json in {path} declares "
                f"{z.getinfo(cap_name).file_size} bytes (> {_MAX_CAPTURE_JSON_BYTES} "
                f"cap); refusing to load a possible decompression bomb")
        return json.loads(z.read(cap_name))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_query(url: str | None) -> str | None:
    if not url:
        return url
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:
        return url.split("?", 1)[0]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I | re.S) is not None


# ITEM C. An opening <a>/<button>, then optional nested inline markup, then the
# word Download, then optional markup, then the MATCHING close tag. `[^>]*` and
# `<[^>]+>` cannot cross a tag boundary, so a heading sitting between two
# controls cannot bridge them into a false match.
_DL_INTERACTIVE_RE = {
    tag: re.compile(
        r"<%s\b[^>]*>(?:\s|<[^>]+>)*download(?:\s|<[^>]+>)*</%s>" % (tag, tag),
        re.I | re.S,
    )
    for tag in ("a", "button")
}


# ── D++ cut 4 (Layer E): noise / verdict / scoring / safety ─────────────────
# These live builder-side (Option A): reusing the core noise lists
# (honeypot_score's subdomain-boundary tracker matcher + bad_terms) is
# legitimate here, where build_template already imports bulk_downloader.
# Single source of truth -> no duplicated term list to drift.

def reject_noise(network_log) -> dict:
    """First-class NOT-the-media pass. Explicitly scores down analytics /
    beacons / trackers / ad calls (reusing core honeypot_score's
    subdomain-boundary matcher + bad_terms), so a false-positive media
    candidate is rejected rather than merely absent. F2 — url_shape only.

    Returns ``{rejected: [{url_shape, reason}], hosts: [host]}``.
    """
    try:
        from bulk_downloader import honeypot_score as _hp
    except Exception:
        _hp = None
    try:
        from bulk_downloader import bad_terms as _bt
    except Exception:
        _bt = None

    rejected, hosts, seen = [], set(), set()
    for e in (network_log or []):
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        shape = _strip_query(url)
        if shape in seen:
            continue
        host = urlparse(url).netloc
        reason = None
        if _hp is not None and _hp._host_matches_tracker(host):
            reason = "tracker_host"
        elif _bt is not None and _bt.contains_bad_term(urlparse(url).path):
            reason = f"bad_term:{_bt.first_bad_term(urlparse(url).path)}"
        if reason:
            rejected.append({"url_shape": shape, "reason": reason})
            hosts.add(host)
            seen.add(shape)
    return {"rejected": rejected, "hosts": sorted(hosts)}


def classify(*, framework, protocol, protection, aux=None, noise=None,
             selectors=None) -> dict:
    """Honest site-type verdict. Consumes the four axes + cut-3 protection:
    DRM -> not_downloadable; anti-bot / signed-URL -> pick_test_promote
    (the signature expires and the session is gated, so only a runtime
    Pick->Test->Promote path is honest); clean progressive/manifest with
    selectors -> auto_template.

    Returns ``{site_type, downloadable, requires_runtime_capture,
    recommended_path, reasons}``.
    """
    protection = protection or {}
    protocol = protocol or {}
    selectors = selectors or {}
    reasons = []
    primary = protocol.get("primary")
    drm = bool(protection.get("drm"))
    schemes = (protection.get("signing") or {}).get("schemes") or []
    anti_bot = protection.get("anti_bot") or []

    if framework == "iframe_embed":
        return {"site_type": "iframe_embed", "downloadable": False,
                "requires_runtime_capture": False,
                "recommended_path": "not_downloadable",
                "reasons": ["cross-origin iframe; DOM empty by design"]}

    if drm:
        return {"site_type": "drm_protected", "downloadable": False,
                "requires_runtime_capture": False,
                "recommended_path": "not_downloadable",
                "reasons": ["DRM/EME present — no standalone replayable template"]}

    if schemes or anti_bot:
        if schemes:
            reasons.append("signed-URL scheme(s): " + ",".join(sorted(schemes)))
            st = "signed_" + sorted(schemes)[0]
        else:
            st = ("hls_manifest" if primary == "hls"
                  else "dash_manifest" if primary == "dash"
                  else "direct_progressive" if primary == "progressive"
                  else "unknown")
        if anti_bot:
            reasons.append("anti-bot: " + ",".join(sorted(anti_bot)))
        reasons.append("signature expires / session-gated -> runtime capture")
        return {"site_type": st, "downloadable": True,
                "requires_runtime_capture": True,
                "recommended_path": "pick_test_promote", "reasons": reasons}

    # clean (no drm / signing / anti-bot)
    st = ("hls_manifest" if primary == "hls"
          else "dash_manifest" if primary == "dash"
          else "direct_progressive" if primary == "progressive"
          else "iframe_embed" if primary is None and not selectors.get("player")
          else "unknown")
    has_player = bool(selectors.get("player"))
    if st in ("direct_progressive", "hls_manifest", "dash_manifest") and has_player:
        return {"site_type": st, "downloadable": True,
                "requires_runtime_capture": False,
                "recommended_path": "auto_template",
                "reasons": reasons or ["clean media + recovered player selectors"]}
    return {"site_type": st, "downloadable": bool(primary),
            "requires_runtime_capture": True,
            "recommended_path": "pick_test_promote",
            "reasons": reasons or ["thin recovery — verify via runtime"]}


def confidence_rubric(*, selectors=None, renditions=None, aux=None,
                      selectors_resolve=None) -> dict:
    """Confidence weighted by what was RECOVERED (ladder + trigger + controls +
    tracks + selector-resolve rate), not by "a pattern exists"."""
    selectors = selectors or {}
    renditions = renditions or []
    aux = aux or {}
    sr = selectors_resolve or {}
    factors = {}
    score = 0.0
    player = selectors.get("player") or {}
    if player.get("container"):
        score += 0.20; factors["container"] = True
    if player.get("play_button"):
        score += 0.15; factors["play_button"] = True
    if selectors.get("quality") or selectors.get("download"):
        score += 0.15; factors["quality_or_download"] = True
    if len(renditions) >= 2:
        score += 0.25; factors["rendition_ladder"] = len(renditions)
    elif renditions:
        score += 0.10; factors["rendition_ladder"] = len(renditions)
    if aux.get("captions") or aux.get("audio"):
        score += 0.10; factors["tracks"] = True
    checked = sr.get("checked") or 0
    resolved = sr.get("resolved") or 0
    if checked:
        rate = resolved / checked
        score += 0.15 * rate
        factors["selector_resolve_rate"] = round(rate, 2)
    score = round(min(score, 1.0), 3)
    band = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
    return {"score": score, "band": band, "factors": factors}


def selectors_resolve(selectors, html) -> dict:
    """Template self-test: dry-run the derived selector SHAPES against the
    captured HTML and count how many plausibly resolve. Heuristic (no full CSS
    engine in the sandbox) — checks for the tag / #id / .class / [attr] token
    presence — so the draft carries a resolve count instead of shipping
    unverified selectors."""
    html = html or ""
    low = html.lower()
    checked = resolved = 0
    unresolved = []

    def _resolves(sel: str) -> bool:
        s = str(sel or "").strip()
        if not s:
            return False
        ok = True
        # attribute selectors e.g. [aria-label*="play" i] — require the value present
        for ma in re.finditer(r'\[([\w-]+)\s*[*^$~|]?=\s*["\']?([^"\'\]\s]+)', s):
            ok = ok and (ma.group(2).lower() in low)
        # every .class token must be present
        for cls in re.findall(r'\.([\w-]+)', s):
            ok = ok and (cls.lower() in low)
        # every #id token must be present
        for idv in re.findall(r'#([\w-]+)', s):
            ok = ok and (f'id="{idv.lower()}"' in low or f"id='{idv.lower()}'" in low)
        # leading tag (if any) must be present
        mt = re.match(r'^([a-z][\w-]*)', s, re.I)
        if mt:
            ok = ok and (("<" + mt.group(1).lower()) in low)
        # if the selector had no recognizable token at all, fall back to substring
        if not (re.search(r'[\[.#]', s) or mt):
            return s.lower() in low
        return ok

    for group, val in (selectors or {}).items():
        if isinstance(val, dict):
            for k, sv in val.items():
                if isinstance(sv, str):
                    checked += 1
                    if _resolves(sv):
                        resolved += 1
                    else:
                        unresolved.append(f"{group}.{k}")
        elif isinstance(val, str):
            checked += 1
            if _resolves(val):
                resolved += 1
            else:
                unresolved.append(group)
    return {"checked": checked, "resolved": resolved, "unresolved": unresolved}


def gold_merge_guard(out_path, new_draft) -> dict:
    """Never let a thin auto-draft overwrite a selector-rich reviewed gold.
    If ``out_path`` exists and the existing template has MORE leaf selectors
    than the incoming draft, REFUSE the overwrite (the caller should write the
    incoming draft beside it as ``<name>.incoming.json`` for diff review).

    Returns ``{blocked: bool, existing_selectors, incoming_selectors, reason}``.
    """
    import os as _os

    def _leaf_count(tpl):
        sel = (tpl or {}).get("selectors") or {}
        n = 0
        for v in sel.values():
            n += len(v) if isinstance(v, dict) else 1
        return n

    if not _os.path.exists(str(out_path)):
        return {"blocked": False, "existing_selectors": 0,
                "incoming_selectors": _leaf_count(new_draft),
                "reason": "no existing template"}
    try:
        existing = json.loads(open(str(out_path), encoding="utf-8").read())
    except Exception:
        return {"blocked": False, "existing_selectors": 0,
                "incoming_selectors": _leaf_count(new_draft),
                "reason": "existing unreadable"}
    e, i = _leaf_count(existing), _leaf_count(new_draft)
    enabled = existing.get("template_status") in ("enabled", "reviewed")
    blocked = (e > i) or (enabled and e >= i)
    return {"blocked": blocked, "existing_selectors": e, "incoming_selectors": i,
            "reason": ("richer reviewed gold present" if blocked
                       else "incoming is not thinner")}


# ── interstitial recognition (v3.66.1017, item E) ────────────────────────────
# A captured template could not express an interstitial at all: _html_selectors
# emitted login / quality / download and no dismissal vocabulary, so the Gamma
# "Skip this page" wall could only ever be hand-written into
# site_templates/_data_players.py. 15.79 measured it as "zero dismiss vocabulary
# in the WACZ pipeline".
#
# THE CLASSIFICATION IS ADVISORY, and the reason is measured rather than
# cautious: 15.79 records that in the real corpus "No thanks" marks both true
# post-login interstitials AND ordinary upsell modals. No regex separates those
# -- the words are identical and only the surrounding flow differs. So this
# proposes a bucket, the draft stays draft_requires_review, and a human decides.
#
# WHERE A PHRASE IS AMBIGUOUS IT GOES TO per_page, because the two mistakes cost
# differently. A wall selector misfiled as per-page still fires; it just pays its
# timeout on every URL. A per-page selector misfiled as a wall STOPS FIRING on
# the pages that needed it -- a consent gate that never gets dismissed. The
# default follows the cheaper mistake.
_DISMISS_TAG_RE = re.compile(r"<(a|button)\b([^>]*)>(.*?)</\1\s*>",
                             re.I | re.S)
_TAG_STRIP_RE = re.compile(r"<[^>]*>")
_CLASS_ATTR_RE = re.compile(r'class=["\']([^"\']*)["\']', re.I)

# A wall is recognised by its DESTINATION, not by politeness. "Continue" alone
# is ordinary pagination and must never match.
_WALL_PHRASES = ("continue to members", "continue to the members",
                 "members area", "skip this page", "skip for now")
# Consent / cookie / age. These can appear on ANY content page, so they are
# per-URL by definition -- an age gate placed in the wall bucket would stop
# firing exactly where it is needed.
_PER_PAGE_PHRASES = ("i agree", "i accept", "accept cookies", "accept all",
                     "allow cookies", "got it", "18 or older", "i am 18",
                     "over 18", "21 or older", "enter site", "no thanks",
                     "maybe later")
_SKIP_CLASS_RE = re.compile(r"skip[-_]?page", re.I)


def _dismiss_text(inner: str) -> str:
    """The visible text of an element, whitespace-normalised."""
    return " ".join(_TAG_STRIP_RE.sub(" ", inner).split())


def _dismiss_selector_for(tag: str, text: str, classes: str) -> str | None:
    """A STRUCTURAL selector for this control, or None if it cannot be written.

    Text-scoped rather than href-scoped on purpose: an href carries query
    strings and signed tokens, and the builder's standing guardrail is that
    capture-derived values never reach a durable draft. Text does not.
    """
    cls = _SKIP_CLASS_RE.search(classes or "")
    if cls:
        # Prefer the stable class when the markup offers one -- it survives
        # copy changes and localisation, which a text match does not.
        for c in (classes or "").split():
            if _SKIP_CLASS_RE.search(c):
                return "%s.%s" % (tag.lower(), c)
    if not text or len(text) > 60:
        return None
    if "'" in text and '"' in text:
        return None                      # unquotable; skip rather than mangle
    q = '"' if "'" in text else "'"
    return "%s:has-text(%s%s%s)" % (tag.lower(), q, text, q)


def _dismiss_selectors(html: str) -> dict:
    """Recognise interstitial controls, split into the two runtime scopes.

    Returns ``{"login_wall": [...], "per_page": [...]}`` with empty buckets
    omitted. Both feed v3.66.1016's config keys of the same shape via
    ``capture_login_wire.apply_draft_dismiss_selectors``.
    """
    wall: list = []
    per_page: list = []
    for m in _DISMISS_TAG_RE.finditer(html or ""):
        tag, attrs, inner = m.group(1), m.group(2) or "", m.group(3) or ""
        text = _dismiss_text(inner)
        low = text.lower()
        classes = ""
        cm = _CLASS_ATTR_RE.search(attrs)
        if cm:
            classes = cm.group(1)

        is_wall = bool(_SKIP_CLASS_RE.search(classes))
        if not is_wall:
            is_wall = any(p in low for p in _WALL_PHRASES)
        if not is_wall and "no thanks" in low and "continue" in low:
            # "No Thanks. Continue" -- the decline half of a wall. A BARE
            # "no thanks" is deliberately not enough (15.79: it marks upsells
            # too) and falls through to per_page below.
            is_wall = True

        sel = _dismiss_selector_for(tag, text, classes)
        if not sel:
            continue
        if is_wall:
            if sel not in wall:
                wall.append(sel)
        elif any(p in low for p in _PER_PAGE_PHRASES):
            if sel not in per_page:
                per_page.append(sel)

    out = {}
    if wall:
        out["login_wall"] = wall
    if per_page:
        out["per_page"] = per_page
    return out


def _html_selectors(html: str) -> dict:
    selectors: dict[str, object] = {}

    # Login/auth selectors. Do not include CAPTCHA/Turnstile fields.
    login = {}
    # An input id CONTAINING "email" (e.g. id="user-email") is the credential
    # field even when the bare type/name isn't literally "email" — match it by id
    # first so real forms (#user-email) aren't dropped.
    _m_eid = re.search(r'<input[^>]+id=["\']([\w-]*email[\w-]*)["\']', html, re.I)
    if _m_eid:
        login["email"] = f'input#{_m_eid.group(1)}'
    elif _has(r'<input[^>]+name=["\']email["\']', html):
        login["email"] = 'input[name="email"]'
    elif _has(r'<input[^>]+type=["\']email["\']', html):
        login["email"] = 'input[type="email"]'
    # Username fallback: some sites use a plain username/login credential field
    # with NO email token (e.g. bangbros #username), which the email-only
    # matchers above miss. Fill the canonical credential slot from a username/
    # login id or name — anchored so a *-password id is never grabbed.
    if "email" not in login:
        _m_uid = re.search(
            r'<input[^>]+id=["\'](username|user_name|userid|user|login|loginname)["\']',
            html, re.I)
        if _m_uid:
            login["email"] = f'input#{_m_uid.group(1)}'
        elif _has(r'<input[^>]+name=["\'](username|user|login|userid)["\']', html):
            login["email"] = 'input[name="username"]'

    if _has(r'<input[^>]+id=["\']password["\']', html):
        login["password"] = "input#password"
    elif _has(r'<input[^>]+name=["\']password["\']', html):
        login["password"] = 'input[name="password"]'
    elif _has(r'<input[^>]+type=["\']password["\']', html):
        login["password"] = 'input[type="password"]'

    if _has(r'<button[^>]+type=["\']submit["\'][^>]*name=["\']submit["\']', html) or _has(r'<button[^>]+name=["\']submit["\'][^>]*type=["\']submit["\']', html):
        login["submit"] = 'button[type="submit"][name="submit"]'
    elif _has(r'<button[^>]+type=["\']submit["\']', html):
        login["submit"] = 'button[type="submit"]'

    if login:
        selectors["login"] = login

    # Player selectors are produced by the registry (player_recognition +
    # player_families), merged into ``selectors`` in build_template. The former
    # inline player={} chain (container + play_button) duplicated that registry
    # and diverged on the play/play_button key; it has been folded into the
    # registry (D++ Layer A consolidation) so there is one source of truth.

    quality = {}
    if "Open the video quality settings menu" in html:
        quality["open_menu"] = '[aria-label="Open the video quality settings menu"]'
    elif "theo-settings-control-menu" in html:
        quality["open_menu"] = ".theo-settings-control-menu"

    qualities = sorted({int(x) for x in RES_RE.findall(html)}, reverse=True)
    aria_res = sorted({
        int(x)
        for x in re.findall(r'aria-label=["\']Set video quality to\s+(\d+)p["\']', html, re.I)
    }, reverse=True)
    if aria_res:
        quality["select_resolution_template"] = '[aria-label="Set video quality to {resolution}"]'
        quality["available_resolutions"] = aria_res
    elif qualities:
        quality["available_resolutions"] = qualities

    if quality:
        selectors["quality"] = quality

    download = {}
    # Stable UI hints only; exact signed links are intentionally not saved.
    if _has(r'aria-label=["\'][^"\']*download[^"\']*["\']', html):
        download["button_hint"] = '[aria-label*="Download" i]'
    elif _has(r'title=["\'][^"\']*download[^"\']*["\']', html):
        download["button_hint"] = '[title*="Download" i]'
    else:
        # ITEM C. This branch used to be `_has(r'>\s*Download\s*<', html)`
        # emitting a bare `text=/Download/i`. That matched ANY element whose
        # text is "Download" -- `<h3>Download</h3>` included -- and the UNSCOPED
        # selector then resolves to the heading at runtime even when a real
        # control exists on the page. Measured on a VIP4K reconstruction:
        # promotion_ready True on a trigger that clicks a title.
        #
        # BOTH HALVES ARE NEEDED. Gating on an interactive element alone still
        # leaves a selector a later heading can capture; scoping alone still
        # emits a hint for a page carrying no control at all.
        #
        # COST, STATED HONESTLY: a site whose download control is a
        # <div>/<span> with a JS click handler now emits no hint and reads
        # not_green. That is a real loss. It is the right trade -- the
        # alternative is a FALSE GREEN, and a false green sends a worker at a
        # page that will never download.
        _tags = [t for t in ("a", "button") if _DL_INTERACTIVE_RE[t].search(html)]
        if _tags:
            download["button_hint"] = ", ".join(
                '%s:has-text("Download")' % t for t in _tags)

    if download:
        selectors["download"] = download

    return selectors


def _anti_bot_summary(fp: object) -> dict:
    if not isinstance(fp, dict):
        return {"present": bool(fp), "vendors": [], "challenge_count": 0}
    vendors = fp.get("vendors") or fp.get("anti_bot_vendors") or []
    if isinstance(vendors, str):
        vendors = [vendors]
    challenges = fp.get("challenges") or []
    reasons = Counter()
    statuses = Counter()
    if isinstance(challenges, list):
        for c in challenges:
            if not isinstance(c, dict):
                continue
            reason = str(c.get("reason", "unknown"))
            # Do not persist raw URLs, signed query strings, or data URLs.
            reason = re.sub(r"data:[^;]+;[^ ]+", "data-url", reason)
            reasons[reason] += 1
            statuses[str(c.get("status"))] += 1
    return {
        "present": bool(fp),
        "vendors": vendors,
        "challenge_count": len(challenges) if isinstance(challenges, list) else 0,
        "reason_counts": dict(reasons),
        "status_counts": dict(statuses),
    }


def build_template(path: Path) -> dict:
    cap = _load_capture(path)
    # capture.json is operator/site data deserialized verbatim. A type-malformed
    # one -- a non-dict root, or a log that is not a list of dict events -- must
    # degrade to an (empty) template rather than raise AttributeError. Normalize
    # the cap-derived collections once, here; a well-formed capture is unaffected.
    if not isinstance(cap, dict):
        cap = {}

    def _dict_events(v):
        """Keep only the dict entries of a list; [] for any non-list value."""
        return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []

    dom_log = _dict_events(cap.get("dom_log"))
    network_log = _dict_events(cap.get("network_log"))

    fulls = [
        e for e in dom_log
        if e.get("type") == "full_snapshot" and isinstance(e.get("html"), str)
    ]

    combined_html = "\n".join(e.get("html", "") for e in fulls)
    # Many captures store the DOM as rrweb serialized nodes (data.node on full
    # snapshots, data.adds[].node on incrementals) with NO `html` string, so the
    # html-string join above is empty. Fold a serialized view of the node trees
    # in — including interaction-added subtrees — so selector recognition sees
    # the real DOM. Additive: old html-string captures keep working unchanged.
    combined_html = (combined_html + "\n" + _nodes_to_html(dom_log)).strip()
    labels = [e.get("label") for e in fulls]

    selectors = _html_selectors(combined_html)
    # v3.66.1017 (item E): the interstitial group. Advisory -- see
    # _dismiss_selectors; the draft stays draft_requires_review and a reviewer
    # decides which bucket is right before it drives anything.
    _dismiss = _dismiss_selectors(combined_html)
    if _dismiss:
        selectors["dismiss"] = _dismiss
    network = _network_patterns(network_log)
    # Builder-side supplemental recognition (extraction_core stays byte-identical):
    # catch direct-stream MP4 renditions (.../{name}x{res}_{variant}.mp4) the core
    # misses, and mine the rrweb dom_log for an opened download/quality modal to
    # derive modal-scoped row-selector candidates (validated by the normalizer).
    network = _merge_supplemental_media(network, network_log)
    network = _merge_supplemental_api(network, network_log)
    # Row 120: the CDN-fronting topology of JWPlayer delivery. Review-only, and
    # deliberately a SEPARATE block rather than a change to R5's signals -- the
    # recognizer corpus pins R5's verdicts and this must not move them. The
    # verdict is additionally folded into download_signals (where a template
    # consumer already looks) ONLY when a player or media asset is actually
    # CDN-fronted, so a page that merely loads a library from a CDN adds nothing.
    _cdn_topology = jwplayer_cdn_topology(network_log)
    if _cdn_topology.get("cdn_fronted"):
        _sig = set(network.get("download_signals") or [])
        _sig.add("cdn-fronted")
        _sig.update("cdn:%s" % v for v in _cdn_topology.get("cdn_vendors") or [])
        network["download_signals"] = sorted(_sig)
    _modal_rows = _modal_row_selectors_from_dom(dom_log)
    if _modal_rows:
        _dl = selectors.get("download") if isinstance(selectors.get("download"), dict) else {}
        _dl.setdefault("row_selectors", _modal_rows)
        selectors["download"] = _dl
    # Review-only: surface the observed download-resolution endpoint as a
    # concrete (templated) candidate. Never the runtime base; never enabled.
    _dl_api = _download_api_template(network)
    if _dl_api:
        _dl = selectors.get("download") if isinstance(selectors.get("download"), dict) else {}
        _dl.setdefault("api_template", _dl_api)
        selectors["download"] = _dl

    # Wave B: prefer the operator-recorded action_timeline (inspect_pick entries
    # persisted by dom_capture) as the click->effect source — it is reliable
    # where the rrweb dom_log derivation below frequently is not. action_timeline
    # wins for trigger_candidate (set first); the dom_log block's setdefault then
    # only fills when no action_timeline was recorded (old captures).
    action_timeline = _dict_events(cap.get("action_timeline"))
    _at = _derive_workflow_from_action_timeline(action_timeline, cap)
    if _at and _at.get("trigger_candidate"):
        _dl = selectors.get("download") if isinstance(selectors.get("download"), dict) else {}
        _dl.setdefault("trigger_candidate", _at["trigger_candidate"])
        selectors["download"] = _dl

    # A6-1: derive the observed workflow + the modal-OPEN trigger from the
    # capture timeline (click -> modal-open -> download-API). Review-only:
    # trigger_candidate is a SHAPE that never overwrites a stronger `trigger`;
    # derived_steps are structural labels. (redact_artifact still applies.)
    _wf = _derive_workflow_timeline(dom_log, network_log)
    if _wf.get("trigger_candidate"):
        _dl = selectors.get("download") if isinstance(selectors.get("download"), dict) else {}
        _dl.setdefault("trigger_candidate", _wf["trigger_candidate"])
        selectors["download"] = _dl
    # A6-1: prefer the DOM-derived modal-OPEN trigger (the download-affordance
    # element outside the modal) over the html-recogniser's a[download] row link,
    # which is a row, not the click target.
    _trig = _derive_download_trigger(dom_log)
    if _trig:
        _dl = selectors.get("download") if isinstance(selectors.get("download"), dict) else {}
        _dl["trigger"] = _trig
        selectors["download"] = _dl

    # When the operator action_timeline recorded a download click that produced
    # direct media, use its GENERALIZED family selector. Placement:
    #   • <a> AND no modal rows were derived → an INLINE direct-link site whose
    #     download anchors ARE the rows (no modal — e.g. wowgirls'
    #     `a.ct_dl_button[data-framerate]` in `div.content_download`). The anchor
    #     IS the row; file it under `row_selectors`. (v3.66.248: previously this
    #     was misfiled as `trigger`, so such sites shipped a seed with a download
    #     link as its "opener" and ZERO rows.)
    #   • otherwise (a <button> opener, OR an <a> on a MODAL site whose
    #     modal-scoped rows are already present) → file under `trigger` via
    #     setdefault. This preserves the v3.66.246 anti-collapse behavior: on a
    #     modal site the discriminating anchor still seeds `trigger` rather than
    #     letting it fall through to a generic `a[download]`, while the
    #     modal-scoped row already lives in `row_selectors`.
    # setdefault never overwrites a real DOM-derived opener or modal-derived rows.
    # Structure-only; a CSS selector shape, never a URL/value.
    _tl = _download_trigger_from_timeline(action_timeline, network_log)
    if _tl:
        _tl_sel, _tl_tag = _tl
        _dl = selectors.get("download") if isinstance(selectors.get("download"), dict) else {}
        if _tl_tag == "a" and not _dl.get("row_selectors"):
            _dl["row_selectors"] = [_tl_sel]
        else:
            _dl.setdefault("trigger", _tl_sel)
        selectors["download"] = _dl

    # v3.66.276: OFFLINE generic-grid fallback. When no modal/timeline/inline
    # rows were derived, mine the captured HTML for the dominant repeating,
    # download-shaped tile signature (mirrors the live bdAutoRowGroups ranking,
    # minus visibility/scoping which offline can't compute). setdefault only —
    # never clobbers a stronger DOM/timeline-derived row set; review-only.
    _generic_rows = _generic_row_selectors_from_html(combined_html)
    if _generic_rows:
        _dl = selectors.get("download") if isinstance(selectors.get("download"), dict) else {}
        if not _dl.get("row_selectors"):
            _dl["row_selectors"] = _generic_rows
            selectors["download"] = _dl

    # Issue (this sweep): the static-HTML login derivation misses a credential
    # field whose id/name/type isn't literally "email" (e.g. #user-email). Fill
    # `login.email` from the operator-recorded login step when absent.
    _tl_email = _login_email_from_timeline(action_timeline)
    if _tl_email:
        _lg = selectors.get("login") if isinstance(selectors.get("login"), dict) else {}
        _lg.setdefault("email", _tl_email)
        selectors["login"] = _lg

    # Issue (this sweep): quality.available_resolutions was the HTML-text subset
    # and could read as a misleadingly partial ladder vs the network-observed
    # renditions. When a quality block already exists, union its ladder with the
    # observed resolutions so the field is honest. (Only extends an existing
    # block — never creates one, so capture `confidence` is unaffected.)
    _q = selectors.get("quality")
    if isinstance(_q, dict) and _q.get("available_resolutions") is not None:
        _seen = {int(r) for r in (network.get("resolutions_seen") or [])}
        if _seen:
            _q["available_resolutions"] = sorted(
                set(_q["available_resolutions"]) | _seen, reverse=True)

    # A6-1: derive a concrete runtime api.base + named endpoints from the
    # observed API requests (enrichment layer; extraction_core stays byte-
    # identical). Gated on the core recognising exactly one api host.
    _api = _derive_api(network_log, network)

    # Wave 168: family-independent recognition. UNION generic selectors only
    # where a brand-derived key is absent (never overwrite); attach review-only
    # recognition metadata. extraction_core untouched; nothing enabled.
    try:
        import player_recognition as _pr
        _iframe_hosts = re.findall(r'<iframe[^>]+src=["\']https?://([^/"\']+)', combined_html, re.I)
        # AI-1 divergence fix (v300): feed storage key NAMES into detect() so the
        # v3.66.171 storage-tell arbitration runs in the draft (it previously ran
        # only in the scorecard/AI-1 path). NAMES only -- F2 (values untouched).
        _ss = cap.get("storage_snapshot")
        _ss = _ss if isinstance(_ss, dict) else {}
        _storage_keys = (list((_ss.get("local_storage") or {}).keys())
                         + list((_ss.get("session_storage") or {}).keys()))
        _script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)', combined_html, re.I)
        _script_srcs += [e.get("url") for e in (network_log or [])
                         if isinstance(e, dict) and isinstance(e.get("url"), str)
                         and e["url"].split("?", 1)[0].endswith(".js")]
        # v3.66.336: operator switch -- when player_struct_tiebreak is enabled in
        # global_config the struct_embed tie-breaker may re-rank a GENUINE 2-way tie
        # toward the high-confidence structural verdict (it never invents a family,
        # never reaches past the top-2, never overrides a storage tell). Default OFF
        # => byte-identical recognition. Fail-open (False) so a config-read glitch
        # never breaks the draft.
        try:
            from bulk_downloader.global_config import get as _gc_get
            _struct_tb = bool(_gc_get("player_struct_tiebreak", False))
        except Exception:  # noqa: BLE001
            _struct_tb = False
        _rec = _pr.detect(combined_html, script_srcs=_script_srcs,
                          iframe_hosts=_iframe_hosts, network=network_log,
                          storage_keys=_storage_keys, struct_tiebreak=_struct_tb)
        for _k, _v in (_rec.get("selectors") or {}).items():
            if _k not in selectors:
                selectors[_k] = _v
            elif isinstance(selectors.get(_k), dict) and isinstance(_v, dict):
                for _sk, _sv in _v.items():
                    selectors[_k].setdefault(_sk, _sv)
        _recognition = {
            "player_family": _rec.get("player_family"),
            "candidates": _rec.get("candidates"),
            "delivery": _rec.get("delivery"),
            "policy": _rec.get("policy"),
            "flags": _rec.get("flags"),
            "concerns": _rec.get("concerns"),
            "api_classes": _rec.get("api_classes"),
            "notes": _rec.get("notes"),
            # Wave 1b: persist the F2-clean platform/workflow hint channel
            # (label/category/note only — query-stripped, no PII/tokens by
            # construction in player_platform_hints). Was computed but dropped.
            "workflow_hints": _rec.get("workflow_hints"),
            "platform_hints": _rec.get("platform_hints"),
        }
        # D++ cuts 1-2: surface the recovered rendition ladder + protocol so a
        # reviewer sees what was recovered (config-seam ladder from the page +
        # the network-derived ladder), which protocols were observed, and what
        # was rejected as poster/segment. Additive, review-only, F2 (every
        # url_shape is already query-stripped by the recognizers).
        _proto = _pr.recognize_protocol(network_log)
        _merged_rends = []
        _seen_us = set()
        for _r in list(_proto.get("renditions") or []) + list(_rec.get("renditions") or []):
            _us = _r.get("url_shape")
            if _us and _us in _seen_us:
                continue
            _seen_us.add(_us)
            _merged_rends.append(_r)
        _recognition["config_seam"] = _rec.get("config_seam")
        _recognition["renditions"] = _merged_rends
        _recognition["protocols"] = _proto.get("protocols")
        _recognition["primary_protocol"] = _proto.get("primary")
        _recognition["ll_hls"] = _proto.get("ll_hls")
        _recognition["resumable"] = _proto.get("resumable")
        _recognition["segments"] = _proto.get("segments")
        _recognition["media_candidates"] = _proto.get("media_candidates")
        _recognition["rejected_media"] = _proto.get("rejected")
        # D++ cut 3 (Layer C): protection posture — signing scheme / token-refresh
        # / anti-bot vendor / captcha / DRM / header preconditions / cookie names.
        # DETECTION ONLY (F2): tags, param NAMES and SHAPES — never a token,
        # signed-URL, or cookie value. The redacted cookie jar (PLACEHOLDER str)
        # is tolerated and yields no cookie names.
        _recognition["protection"] = _pr.recognize_protection(
            network_log, cookies=cap.get("cookies"),
            html=combined_html, script_srcs=_script_srcs)
        # D++ cut 4 (Layer D): auxiliary content (caption/audio/storyboard/
        # chapter tracks + SSAI/ad markers) + the builder-side reject_noise
        # NOT-media pass (Option A: reuses core honeypot/bad_terms). Additive.
        _recognition["tracks"] = _pr.recognize_aux(
            network_log, html=combined_html,
            config_seam=_rec.get("config_seam"))
        _recognition["noise"] = reject_noise(network_log)
    except Exception:
        _recognition = None

    raw_url = cap.get("url") or cap.get("origin") or ""
    host = urlparse(raw_url).netloc or cap.get("host") or "unknown-host"
    origin = cap.get("origin") or ""

    confidence = "low"
    if selectors.get("player") and selectors.get("quality") and network["api_patterns"]:
        confidence = "high"
    elif selectors.get("player") and (selectors.get("quality") or network["resolutions_seen"]):
        confidence = "medium"

    # D++ cut 4 (Layer E): self-test, verdict, refined confidence, provenance.
    _rec_d = _recognition or {}
    _self_test = selectors_resolve(selectors, combined_html)
    _verdict = classify(
        framework=(_rec_d.get("player_family")
                   or ("iframe_embed" if (_rec_d.get("flags") or {}).get("iframe_embed") else None)),
        protocol={"primary": _rec_d.get("primary_protocol")},
        protection=_rec_d.get("protection") or {},
        aux=_rec_d.get("tracks") or {},
        noise=_rec_d.get("noise") or {},
        selectors=selectors)
    _conf = confidence_rubric(
        selectors=selectors, renditions=_rec_d.get("renditions") or [],
        aux=_rec_d.get("tracks") or {}, selectors_resolve=_self_test)
    confidence = _conf["band"]  # refine the coarse band with the rubric
    # provenance: which SIGNAL produced each derived field (names only, F2)
    _prov = {}
    if _rec_d.get("config_seam"):
        _prov["renditions.config_seam"] = "page_config_seam"
    if _rec_d.get("protocols"):
        _prov["renditions.network"] = "network_manifest"
    if (_rec_d.get("protection") or {}).get("signing", {}).get("schemes"):
        _prov["protection.signing"] = "query_param_name_fingerprint"
    if (_rec_d.get("protection") or {}).get("anti_bot"):
        _prov["protection.anti_bot"] = "cookie/header/script_name"
    if (_rec_d.get("tracks") or {}).get("captions"):
        _prov["tracks.captions"] = "html_track/ext_x_media/dash_text"
    if (_rec_d.get("noise") or {}).get("rejected"):
        _prov["noise"] = "honeypot_subdomain_match/bad_terms"
    _prov["verdict"] = "classify(framework,protocol,protection)"

    hosts = sorted({host, urlparse(origin).netloc} - {""})
    url_patterns = []
    for h in hosts:
        safe_h = re.escape(h)
        url_patterns.append(f"^https://{safe_h}/")

    _draft = {
        "schema_version": "bulk_downloader.template_draft.v2",
        "template_status": "draft_requires_review",
        "confidence": confidence,
        "confidence_detail": _conf,
        "verdict": _verdict,
        "selectors_resolve": _self_test,
        "provenance": _prov,
        "source": {
            "capture_file": str(path.name),
            "capture_sha256": _sha256_file(path),
            "captured_at": cap.get("captured_at"),
            "url_no_query": _strip_query(raw_url),
            "origin": origin,
            "host": host,
            "dom_log_count": cap.get("dom_log_count", len(dom_log)),
            "network_log_count": cap.get("network_log_count", len(network_log)),
            "full_snapshot_labels": labels,
        },
        "match": {
            "hosts": hosts,
            "url_patterns": url_patterns,
        },
        "selectors": selectors,
        "network_discovery": network,
        "cdn_topology": _cdn_topology,
        "resolution_priority": [
            r for r in [4320, 2160, 1440, 1080, 720, 540, 480, 360, 240]
            if r in set(network["resolutions_seen"]) or r in set(selectors.get("quality", {}).get("available_resolutions", []))
        ],
        "workflow": {
            "auth": "manual_or_existing_profile",
            "capture_mode": "user_driven",
            "derived_steps": (_at.get("derived_steps") if _at else None) or _wf.get("derived_steps") or [],
            "trigger_evidence": (_at.get("trigger_evidence") if _at else None) or _wf.get("trigger_evidence"),
            # Wave B: which source produced the steps/trigger above —
            # "action_timeline" (operator-recorded clicks, preferred) or
            # "dom_log" (rrweb fallback). `verify` is the advisory finish-time
            # readout (tier/checks/warnings) when an action_timeline was present.
            "source": (_at.get("source") if _at else "dom_log"),
            "verify": (_at.get("verify") if _at else None),
            "recommended_steps": [
                "open page with existing authenticated profile",
                "wait for player container",
                "open quality menu if present",
                "select highest allowed resolution if user requested",
                "trigger site-provided download action when available",
                "watch network for approved download-resolution API response or user-clicked document/video response",
            ],
        },
        "guardrails": [
            "Do not bypass Cloudflare, CAPTCHA, Turnstile, DRM, or paywalls.",
            "Do not save short-lived signed media URLs from captures.",
            "Do not persist cookies, tokens, Authorization headers, or query strings.",
            "Use this only with user-authorized sessions and site-provided playback/download flows.",
            "Ignore captcha/turnstile selectors when generating login templates.",
        ],
        "anti_bot_detection": _anti_bot_summary(cap.get("fingerprint_detection")),
        "recognition": _recognition,  # review-only: player family + delivery + policy (Wave 168)
        "notes": [
            "This is a template seed, not a final template.",
            "Review selectors manually and add tests before enabling automatically.",
        ],
    }
    if _api:
        # Review-only: the CONCRETE derived base + named endpoints, surfaced for
        # the reviewer to accept at promotion — NOT the runtime ``api`` block, so
        # build_api_url stays gated on explicit review and API patterns stay
        # relative until a human confirms the host (v3.66.155 / v3.66.157 gates).
        _draft["api_candidate"] = _api
    # F2 / wave 166: scrub the assembled draft at the single derivation
    # chokepoint, so NO downstream persister (CLI --out below, the build-
    # template route, the normalizer) can write capture-derived secrets to a
    # durable draft/fixture/KB/test. Value-content redaction only — hostnames,
    # counts, status codes, content types, endpoint/media templates, selector
    # shapes, and the capture SHA-256 all survive. (extraction_core untouched.)
    from bulk_downloader.capture_artifact_redact import redact_artifact
    return redact_artifact(_draft)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a sanitized template draft from a BulkDownloader WACZ capture.")
    ap.add_argument("wacz", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    tpl = build_template(args.wacz)
    out = args.out
    if out is None:
        host = (tpl["source"].get("host") or "capture").replace(":", "_")
        host = re.sub(r"[^A-Za-z0-9._-]+", "_", host)
        if host.strip(".") == "":
            host = "capture"
        out = Path("templates/drafts") / f"{host}.template-draft.json"

    out.parent.mkdir(parents=True, exist_ok=True)
    _guard = gold_merge_guard(out, tpl)
    if _guard["blocked"]:
        incoming = out.with_suffix(out.suffix + ".incoming.json")
        incoming.write_text(json.dumps(tpl, indent=2, sort_keys=True), encoding="utf-8")
        print(f"GOLD-MERGE GUARD: refused to overwrite a richer reviewed gold "
              f"({_guard['existing_selectors']} >= {_guard['incoming_selectors']} selectors); "
              f"wrote incoming draft to {incoming} for diff review")
        return 0
    out.write_text(json.dumps(tpl, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out}")
    print(f"confidence: {tpl.get('confidence')}")
    print("selectors:", ", ".join(tpl.get("selectors", {}).keys()) or "none")
    print("api patterns:", len(tpl.get("network_discovery", {}).get("api_patterns", [])))
    print("resolutions:", tpl.get("resolution_priority"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
