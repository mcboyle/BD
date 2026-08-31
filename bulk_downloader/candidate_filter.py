"""candidate_filter — gate template download candidates on clear site-provided
media/download signals, and reject obvious non-download links.

The template flow can otherwise mistake a normal page link for the intended
download control: a generic ``a[href]`` / ``[href]`` selector resolving to the
homepage or a nav link yields a bad ``download.bin``. This module is the safety
gate. A URL-bearing candidate is accepted only when it carries at least one
positive signal:

  * media file extension      (.mp4 / .m3u8 / .mpd / .webm / .ts / ...)
  * manifest URL              (HLS .m3u8, DASH .mpd, /hls/, /dash/, /manifest)
  * download path             (/download, /dl, /get, /stream, /videoplayback ...)
  * resolution label          (1080p, 4K, 2160, ...)
  * known reusable API pattern (/api/.../video|media|download, videoplayback ...)

...and none of the hard rejections fire:

  * generic href-only selector (``a[href]`` / ``[href]`` / ``a`` / ``*``)
  * homepage link              (path ``/`` or ``/index.*`` with no media signal)
  * nav / header / footer       link
  * search / settings / login / logout link
  * share / favorite / comment / vote / like button
  * unrelated external-service link (analytics / social / auth, not a media CDN)

URL-less buttons whose text/classes name a quality/download/format menu are kept
as *triggers* (they reveal the real download controls) — they are not rejected
for lacking a media URL, but the same nav/account/social rejections still apply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

# ── positive signals ────────────────────────────────────────────────────
MEDIA_EXT_RE = re.compile(
    r"\.(mp4|m4v|mov|mkv|webm|ts|m4s|m4a|mp3|aac|flac|wav|avi|flv|wmv|"
    r"ogg|ogv|3gp|mpg|mpeg|m3u8|mpd)(\?|#|$)", re.I)
MANIFEST_RE = re.compile(
    r"\.(m3u8|mpd)(\?|#|$)|/master\.m3u8|/playlist\.m3u8|/manifest\b|"
    r"format=m3u8|[?&]hls\b|/hls/|/dash/", re.I)
DOWNLOAD_PATH_RE = re.compile(
    r"/(?:download|dl|get|getfile|getvideo|fetch|stream|streaming|"
    r"videoplayback|media|content|file|attachment)s?(?:/|\?|#|$)|"
    r"[?&](?:download|dl)=", re.I)
RESOLUTION_LABEL_RE = re.compile(
    r"\b\d{3,4}\s*p\b|\b(?:4k|8k|2k|uhd|qhd|fhd)\b|"
    r"(?<![\w])(?:240|360|480|540|576|720|1080|1440|2160|4320)(?![\w])", re.I)
API_PATTERN_RE = re.compile(
    r"/api/[^?\s]*(?:video|media|download|stream|playback|source|manifest|"
    r"hls|dash|qualit|render)|/(?:playback|get_media|get_video|videoplayback|"
    r"media_source|source)\b|googlevideo\.com/videoplayback", re.I)

# ── generic, intent-free selectors ──────────────────────────────────────
GENERIC_SELECTOR_RE = re.compile(r"^\s*(?:a\s*)?(?:\[href\])?\s*$|^\s*\*\s*$", re.I)

# ── negative signals ─────────────────────────────────────────────────────
_NAV_RE = re.compile(
    r"\b(?:nav|navbar|navigation|menu-?bar|topbar|top-?nav|site-?header|"
    r"site-?footer|masthead|breadcrumbs?|footer|header)\b", re.I)
_ACCOUNT_RE = re.compile(
    r"\b(?:log[\s-]?in|sign[\s-]?in|signin|log[\s-]?out|sign[\s-]?out|signout|"
    r"logout|register|sign[\s-]?up|signup|settings?|preferences?|"
    r"my[\s-]?account|account|dashboard)\b", re.I)
_SEARCH_RE = re.compile(r"\b(?:search|find|filter|sort|query)\b", re.I)
_SOCIAL_RE = re.compile(
    r"\b(?:share|tweet|retweet|facebook|whatsapp|telegram|reddit|pinterest|"
    r"favou?rite|bookmark|watch[\s-]?later|upvote|downvote|vote|like|dislike|"
    r"comments?|reply|report|flag|subscribe|follow|embed|copy[\s-]?link)\b", re.I)
_EXTERNAL_SERVICE_HOSTS = re.compile(
    r"(?:google-?analytics|googletagmanager|doubleclick|google-?syndication|"
    r"facebook\.|fbcdn|twitter\.|t\.co|instagram\.|accounts\.google|"
    r"gravatar|disqus|paypal|patreon|discord|linkedin\.|tiktok\.|"
    r"snapchat\.|pinterest\.)", re.I)
_HASH_OR_JS_RE = re.compile(r"^\s*(?:#|javascript:|mailto:|tel:|about:)", re.I)
# Known navigation / listing / account URL *paths* that are never a download.
# Plural listings + search/account/commerce paths (per the nav-rejection spec):
# /movies, /models, /series, /search, /settings, /logout, /categories, /deals,
# and friends. Deliberately NOT singular content paths like /movie/<id>/... —
# reptyle's real media lives under /movie/<id>/download-resolution/<res>, which
# also carries a strong download signal and so is never rejected here anyway.
_NAV_PATH_RE = re.compile(
    r"^/(?:movies|videos|models|actors|stars|performers|series|seasons|"
    r"episodes|categories|category|tags|genres|collections|playlists|"
    r"channels|studios|sites|search|browse|explore|discover|trending|"
    r"popular|latest|newest|top|recommended|deals|offers|pricing|plans|"
    r"upgrade|premium|store|shop|cart|checkout|settings|preferences|"
    r"account|profile|dashboard|billing|membership|favou?rites|history|"
    r"watchlist|login|signin|logout|signout|register|signup|home|index)"
    r"(?:/|\?|#|$)", re.I)
# A URL signal strong enough to OVERRIDE a nav-path match (real media/download).
# Resolution-label alone is intentionally NOT strong: a nav URL can carry a
# stray number ("/top-100").
_STRONG_URL_SIGNALS = frozenset(
    {"media_extension", "manifest_url", "download_path", "api_pattern"})


def _is_nav_path(url: str) -> bool:
    if not url:
        return False
    try:
        return bool(_NAV_PATH_RE.match(urlsplit(url).path or "/"))
    except Exception:
        return False
_TRIGGER_TEXT_RE = re.compile(
    r"\b(?:download|quality|qualit|resolution|choose|select|format|version|"
    r"render|get\s+video|hd|4k|8k|1080|720|480)\b", re.I)


@dataclass
class Verdict:
    """Result of classifying one candidate. ``kind`` is ``download`` (a real
    URL-bearing media/download control), ``trigger`` (a quality/download menu
    opener with no URL), or ``rejected``."""
    accepted: bool
    kind: str
    positive_signals: List[str] = field(default_factory=list)
    rejections: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "kind": self.kind,
            "positive_signals": list(self.positive_signals),
            "rejections": list(self.rejections),
            "reason": self.reason,
        }


def _registrable(host: str) -> str:
    """The registrable domain of `host`.

    v3.66.1018: delegates to bulk_downloader.registrable_domain. The
    previous last-two-labels join returned the PUBLIC SUFFIX for any
    multi-part one -- co.uk for www.bbc.co.uk -- so two unrelated
    registrants read as one domain. Local import: registrable_domain is
    a leaf (urlparse only), and this keeps the module's import surface
    unchanged for callers that never reach this function.
    """
    from .registrable_domain import registrable_domain
    return registrable_domain(host)


def _same_site(host: str, page_host: str) -> bool:
    return _registrable(host) == _registrable(page_host)


def _is_homepage(url: str) -> bool:
    if not url:
        return False
    s = urlsplit(url)
    path = (s.path or "").rstrip("/")
    return path in ("", "/index", "/index.html", "/index.php", "/home", "/default")


def positive_signals(url: str = "", text: str = "", classes: str = "") -> List[str]:
    """The site-provided media/download signals present on a candidate."""
    u = url or ""
    txt = f"{text or ''} {classes or ''}"
    sigs: List[str] = []
    if MEDIA_EXT_RE.search(u):
        sigs.append("media_extension")
    if MANIFEST_RE.search(u):
        sigs.append("manifest_url")
    if DOWNLOAD_PATH_RE.search(u):
        sigs.append("download_path")
    if API_PATTERN_RE.search(u):
        sigs.append("api_pattern")
    if RESOLUTION_LABEL_RE.search(u) or RESOLUTION_LABEL_RE.search(txt):
        sigs.append("resolution_label")
    return sigs


def classify(*, url: Optional[str] = None, text: str = "", classes: str = "",
             ancestor_text: str = "", selector: Optional[str] = None,
             tag: str = "", page_host: Optional[str] = None) -> Verdict:
    """Classify one candidate. See module docstring for the accept/reject rules."""
    url = (url or "").strip()
    text = text or ""
    classes = classes or ""
    ancestor = ancestor_text or ""
    sel = (selector or "").strip()

    has_url = bool(url) and not _HASH_OR_JS_RE.match(url)
    pos = positive_signals(url, text, classes)

    rej: List[str] = []
    chrome_blob = f"{classes} {ancestor} {sel}"           # structural context
    text_blob = f"{text} {classes} {ancestor} {sel}"      # everything
    if _NAV_RE.search(chrome_blob):
        rej.append("nav/header/footer")
    if _ACCOUNT_RE.search(text_blob):
        rej.append("search/settings/login/logout")
    if _SEARCH_RE.search(f"{text} {classes} {sel}") and not _TRIGGER_TEXT_RE.search(text_blob):
        rej.append("search/filter")
    if _SOCIAL_RE.search(text_blob):
        rej.append("share/favorite/comment/vote")
    if has_url:
        host = urlsplit(url).netloc
        # v3.66.555 (F-CORE_BD15-01): a download candidate whose host is a non-public IP
        # literal (RFC1918 / loopback / link-local-metadata / RFC6598 CGNAT / reserved) is
        # an SSRF vector -- the transport extracted-URL path would fetch it WITH the session
        # cookies (incl. BD's own loopback API). Reject it via the canonical per-IP predicate.
        # Literal only: no DNS in this hot per-candidate path; the fetch boundary resolves
        # hostnames. Unlike the hooks, an internal download candidate is never legitimate.
        _iphost = urlsplit(url).hostname or ""
        try:
            import ipaddress as _ipaddr
            _addr = _ipaddr.ip_address(_iphost)
        except ValueError:
            _addr = None
        if _addr is not None:
            from bulk_downloader.provider_resolve_impl._common import _classify_ip
            _ip_ok, _ = _classify_ip(_addr, _iphost)
            if not _ip_ok:
                rej.append("internal/non-public host")
        if host and page_host and not _same_site(host, page_host):
            if _EXTERNAL_SERVICE_HOSTS.search(host) or not pos:
                rej.append("external/unrelated link")
    if sel and GENERIC_SELECTOR_RE.match(sel) and not pos:
        rej.append("generic href-only selector")
    if has_url and _is_homepage(url) and not pos:
        rej.append("homepage link")
    # Navigation/listing/account path → reject unless a strong media/download
    # URL signal overrides (resolution-label alone is not strong).
    if has_url and _is_nav_path(url) and not _STRONG_URL_SIGNALS.intersection(pos):
        if "homepage link" not in rej:
            rej.append("navigation URL")

    # Decision -------------------------------------------------------------
    if has_url:
        if rej:
            return Verdict(False, "rejected", pos, rej, "; ".join(rej))
        if pos:
            return Verdict(True, "download", pos, [], "download: " + ", ".join(pos))
        return Verdict(False, "rejected", pos, ["no download signal"],
                       "no media/download/resolution/manifest/api signal")

    # No URL → only a quality/download menu *trigger* is useful.
    if _TRIGGER_TEXT_RE.search(f"{text} {classes}") and not rej:
        return Verdict(True, "trigger", ["menu/quality trigger"], [],
                       "quality/download menu trigger")
    return Verdict(False, "rejected", pos, rej or ["no url, no trigger signal"],
                   "; ".join(rej or ["no url, no trigger signal"]))


def best_url(cand: Dict[str, Any]) -> str:
    """Pick the most download-relevant URL field from an extractor candidate."""
    for k in ("href", "data_href", "data_url", "data_src", "data_download",
              "url", "data_video"):
        v = (cand.get(k) or "").strip()
        if v:
            return v
    return ""


def classify_candidate(cand: Dict[str, Any], *,
                       page_host: Optional[str] = None,
                       selector: Optional[str] = None) -> Verdict:
    """Convenience wrapper over :func:`classify` for the dict shape produced by
    ``template_extractor._walk_for_candidates``."""
    return classify(
        url=best_url(cand),
        text=cand.get("text", ""),
        classes=cand.get("classes", "") or cand.get("classlist", ""),
        ancestor_text=cand.get("ancestor_text", ""),
        selector=selector,
        tag=cand.get("tag", ""),
        page_host=page_host,
    )


def filter_candidates(cands: List[Dict[str, Any]], *,
                      page_host: Optional[str] = None
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Partition extractor candidates into (kept, rejected). Each gets a
    ``filter_verdict`` dict; rejected ones also carry ``reject_reason``."""
    kept, rejected = [], []
    for c in cands:
        v = classify_candidate(c, page_host=page_host)
        c = dict(c)
        c["filter_verdict"] = v.to_dict()
        if v.accepted:
            kept.append(c)
        else:
            c["reject_reason"] = v.reason
            rejected.append(c)
    return kept, rejected


# Page-level media evidence is deliberately three-state. Candidate strings can
# prove presence, but strings that do not match a recognizer cannot prove
# absence: a blob-backed <video>, an iframe/player shell, or an unrendered JS
# application may expose no ordinary media URL at all. The only absent state we
# currently know how to prove is the row-399 shape -- a fully rendered page at a
# /gallery/ URL with a real photo-gallery DOM and no possible player surface.
PAGE_MEDIA_PRESENT = "present"
PAGE_MEDIA_CONFIRMED_ABSENT = "confirmed_absent"
PAGE_MEDIA_UNKNOWN = "unknown"

NO_VIDEO_ON_PAGE_MESSAGE = (
    "No video on this page -- the rendered page is a confirmed photo gallery "
    "with no video or player surface. There is no download control to fire; "
    "this is not a broken selector.")

_PHOTO_GALLERY_PATH_RE = re.compile(r"(?:^|/)gallery(?:/|$)", re.I)
_PAGE_MEDIA_COUNT_FIELDS = (
    "affordance_count",
    "gallery_marker_count",
    "photo_count",
    "possible_media_count",
    "media_url_count",
    "pending_shell_count",
)


def classify_page_media_snapshot(snapshot: Dict[str, Any],
                                 page_url: str = "") -> str:
    """Classify a structured DOM snapshot as present, confirmed absent, or
    unknown.

    Absence needs positive photo-gallery evidence; it is never inferred from
    unrecognised affordance text. Missing/malformed counts, an incomplete DOM,
    a non-gallery URL, or a shell with no rendered photos all remain UNKNOWN.
    Any possible media/player surface is PRESENT for this decision's purpose:
    it prevents the no-video diagnosis even when its eventual media URL is
    opaque or blob-backed.
    """
    if not isinstance(snapshot, dict):
        return PAGE_MEDIA_UNKNOWN

    counts: Dict[str, int] = {}
    for field_name in _PAGE_MEDIA_COUNT_FIELDS:
        value = snapshot.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return PAGE_MEDIA_UNKNOWN
        counts[field_name] = value

    if (counts["possible_media_count"] > 0
            or counts["media_url_count"] > 0):
        return PAGE_MEDIA_PRESENT
    if counts["pending_shell_count"] > 0:
        return PAGE_MEDIA_UNKNOWN
    if snapshot.get("ready_state") != "complete":
        return PAGE_MEDIA_UNKNOWN

    effective_url = snapshot.get("location_url") or page_url or ""
    if not isinstance(effective_url, str):
        return PAGE_MEDIA_UNKNOWN
    if not effective_url.lower().startswith(("http://", "https://")):
        effective_url = page_url if isinstance(page_url, str) else ""
    try:
        path = urlsplit(effective_url).path or ""
    except Exception:
        return PAGE_MEDIA_UNKNOWN
    if not _PHOTO_GALLERY_PATH_RE.search(path):
        return PAGE_MEDIA_UNKNOWN

    if (counts["affordance_count"] <= 0
            or counts["gallery_marker_count"] <= 0
            or counts["photo_count"] <= 0):
        return PAGE_MEDIA_UNKNOWN
    return PAGE_MEDIA_CONFIRMED_ABSENT


# ── AF1 confidence tier (v3.66.785) ──────────────────────────────────────────
# A mechanical triage layer over classify(): turn the binary accept/reject verdict
# into a three-way tier so A-DISCO (level-4 enumeration) can auto-queue only the
# unambiguous downloads and route everything uncertain to the operator.
#
# THE INVARIANT: the tier is derived from the verdict classify() already produced
# over EVERY candidate -- so the denominator is every classified candidate, not a
# pre-filtered subset. Only a strong-signal download is 'high'. Anything
# accepted-but-weak (a resolution-label-only "download", or a URL-less menu
# trigger) is 'review' -- it FAILS TO REVIEW, never silently to auto-queue. A
# stray "1080p" in a URL must never become an autonomous download.
TIER_HIGH = "high"        # auto-queue-safe: an unambiguous media/download URL
TIER_REVIEW = "review"    # uncertain: surface to the operator, never auto-queue
TIER_REJECT = "reject"    # clearly not the target: drop

# Score bands -- coarse + explicit so the thresholds below are self-evident. The
# strong-signal set is _STRONG_URL_SIGNALS (single-sourced above), so "strong"
# means the same thing here as in the nav-override rule.
_SCORE_STRONG_MULTI = 1.0    # download carrying 2+ strong signals
_SCORE_STRONG_ONE = 0.9      # download carrying exactly 1 strong signal
_SCORE_WEAK_ACCEPTED = 0.5   # accepted but weak (resolution-only download / edge)
_SCORE_TRIGGER = 0.4         # URL-less quality/download menu opener
_SCORE_REJECT = 0.0          # not accepted

_TIER_HIGH_MIN = 0.75        # >= -> high (auto-queue)
_TIER_REVIEW_MIN = 0.25      # >= -> review; below -> reject


def confidence_score(verdict: "Verdict") -> float:
    """A coarse [0,1] auto-queue confidence for an already-classified candidate.

    Derived ONLY from the verdict ``classify`` produced -- so the denominator is
    every candidate that was classified, never a pre-filtered subset. ``0.0`` ==
    not accepted (drop); ``~0.5`` == accepted but weak (review); ``>= 0.9`` ==
    strong-signal download (auto-queue). Also ranks within a tier, so A-DISCO's
    per-run enqueue cap can take the highest-confidence candidates first.
    """
    if verdict is None or not getattr(verdict, "accepted", False):
        return _SCORE_REJECT
    pos = set(getattr(verdict, "positive_signals", ()) or ())
    kind = getattr(verdict, "kind", "")
    if kind == "download":
        strong = len(_STRONG_URL_SIGNALS & pos)
        if strong >= 2:
            return _SCORE_STRONG_MULTI
        if strong == 1:
            return _SCORE_STRONG_ONE
        return _SCORE_WEAK_ACCEPTED       # accepted on resolution-label alone
    if kind == "trigger":
        return _SCORE_TRIGGER
    # Accepted but neither download nor trigger: an unrecognised shape. Fail to
    # the review band, never high.
    return _SCORE_WEAK_ACCEPTED


def confidence_tier(verdict: "Verdict") -> str:
    """Triage tier -- ``TIER_HIGH`` | ``TIER_REVIEW`` | ``TIER_REJECT`` -- for an
    already-classified candidate. Only an unambiguous strong-signal download is
    ``high`` (auto-queue); everything accepted-but-weak is ``review``; a rejected
    verdict is ``reject``. Fail-to-review by construction: an unrecognised/edge
    verdict lands in review, never high.
    """
    s = confidence_score(verdict)
    if s >= _TIER_HIGH_MIN:
        return TIER_HIGH
    if s >= _TIER_REVIEW_MIN:
        return TIER_REVIEW
    return TIER_REJECT
