"""extraction_core — canonical URL/value/segment derivation primitives.

PHASE 1, STEP 2 of the extraction_core consolidation (see EXTRACTION_CORE_DESIGN.md
§3/§4). This module is **additive and pure** (stdlib only, fully sandbox-testable) and
is the single source of truth for the four derivation concerns currently duplicated
across the three draft producers:

  1. URL-segment roles      — split_segments / is_addressable / segment_role
  2. pattern derivation      — value_shape / segment_regex / derive_pattern / network_patterns
  3. identity / rendition    — manifest_resolutions / recorded_rendition_ok
  4. confidence floor        — DEFERRED to step 5 (see note below)

**Nothing imports this module yet — by construction it changes no behavior.** The
functions here are byte-faithful copies of the current canonical implementations;
routing the producers through them (so the duplicates and the hand-synced private
imports disappear) is steps 3–5, each gated behind the characterization suite plus a
promote byte-stability check. Until then the originals remain in place and authoritative,
and `tests/test_extraction_core.py` proves each function here is equivalent to its
producer counterpart on the frozen golden corpus.

Provenance of each copied symbol (the "canonical base" per §4 step 2):
  - split_segments        ← capture_template._segments
  - is_addressable        ← capture_workbench._segment_is_addressable
  - segment_role          ← capture_workbench._segment_role  (canonical)
  - value_shape           ← capture_synth.classify_value     (re-exported, not copied)
  - segment_regex         ← capture_workbench._segment_regex
  - derive_pattern        ← capture_workbench._derive_pattern (+ DraftPattern, _slug)
  - network_patterns      ← build_template_from_wacz._network_patterns
  - manifest_resolutions  ← build_template_from_wacz._manifest_resolutions
  - recorded_rendition_ok ← capture_template._is_recorded_rendition (closure lifted to
                            a module function: its free var `slots_meta` becomes an
                            explicit parameter; RENDITION_ROLE → RENDITION)
  - IDENTITY / RENDITION  ← capture_workbench.IDENTITY / RENDITION

`DraftPattern` is intentionally duplicated with capture_workbench for now: workbench
still constructs its own until step 5 points its privates at this core. The two are
field-identical; `derive_pattern`'s output is compared structurally in the tests so the
type identity does not matter pre-routing. When step 5 lands, DraftPattern relocates
here and workbench imports it (cycle-free).

**decision_confidence is DEFERRED to step 5.** §3 lists it in the proposed API, but the
design (§4 step 5, §5) quarantines all confidence work to the gated step "where numbers
can move", and workbench._decision_confidence is not a pure derivation primitive — it
post-processes an assembled DetectorDraft plus its computed flow/stability graphs
(_upstream_assumptions, _assumptions_for_recommendation, _BAND_ORDER, _OBSERVED_BASES).
It is also not covered by the shipped characterization fixture, so there is no frozen
golden to prove a faithful copy against. Pulling it (and its subtree) in here now would
be the scope creep CAPTURE_REFACTOR_STRATEGY.md flags as the primary risk. It will be
added when step 5 is greenlit, behind the characterization + promote byte-stability gate.

Custom-runner note: pure/stdlib so it imports cleanly without the venv.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlparse

# value_shape is the canonical shape classifier — re-exported (NOT copied) so it stays
# identical to the live implementation by object identity. ← capture_synth.classify_value
from .capture_synth import classify_value

value_shape = classify_value  # public name per EXTRACTION_CORE_DESIGN.md §3

__all__ = [
    "IDENTITY", "RENDITION", "DraftPattern",
    "split_segments", "is_addressable", "segment_role",
    "value_shape", "classify_value", "segment_regex", "derive_pattern",
    "network_patterns", "manifest_resolutions", "recorded_rendition_ok",
]


# ── constants (today scattered as workbench / capture_template privates) ──────────
IDENTITY = "identity"      # per-title content key (co-varies with title)
RENDITION = "rendition"    # resolution/quality selection (constant per title)

# Shape labels that denote an addressable content identifier. ← capture_workbench
_ID_SHAPES = ("uuid", "sha256", "md5", "id", "filename")

# A path segment is an addressable content identifier candidate (worth an extraction
# pattern) rather than a structural literal if it carries an id/filename shape, looks
# hash-like, or simply contains a digit. Pure lowercase words are structural literals.
_STRUCTURAL_WORD = re.compile(r"^[A-Za-z]{1,12}$")
_HEXISH = re.compile(r"^[0-9a-fA-F]{6,}$")

# Resolution / quality / fps tokens that mark a path segment as a rendition descriptor
# rather than an opaque identity key. Matched against the filename STEM (extension
# stripped) and the raw segment. ← capture_workbench._RENDITION_SIGNAL
_RENDITION_SIGNAL = re.compile(
    r"(?:\d{2,5}\s*[xX]\s*\d{2,5})"       # 1280x720, 3840x2160, 5568x3132
    r"|(?:\b\d{3,4}[pi]\b)"               # 720p, 1080p, 2160p, 480i
    r"|(?:\b(?:[2348]k|uhd|qhd|fhd|hd|sd)\b)"  # 4k, 8k, uhd, hd, sd ...
    r"|(?:\bfps\b)|(?:\d{1,4}\s*fps)"     # 60fps, fps (digits bounded: fps is 1-4 digits, avoids O(n^2) backtracking on a long-digit segment)
    r"|(?:\b\d{3,5}\s*kbps\b)"            # 4500kbps (bitrate renditions)
    # a BARE resolution width/height suffix (_480, _3840). Matched only against the
    # KNOWN resolution set after a _/-/x delimiter, so it does not fire on arbitrary
    # numbers (ids, segment indices).
    r"|(?:[_x-](?:144|240|288|360|480|540|576|720|960|1080|1280|1440|1920|"
    r"2160|2560|2880|3840|4096|4320|5760|7680)(?=[_x.]|$))",
    re.IGNORECASE)

# Resolution-height regexes for network/manifest derivation.
# ← build_template_from_wacz.RES_RE / _HLS_RES_RE / _MPD_RES_RE
RES_RE = re.compile(r"(?<!\d)(4320|2160|1440|1080|720|540|480|360|240)p(?!\d)", re.I)
_HLS_RES_RE = re.compile(r"RESOLUTION\s*=\s*\d+\s*x\s*(\d+)", re.I)
_MPD_RES_RE = re.compile(r"""height\s*=\s*["'](\d+)["']""", re.I)


@dataclass
class DraftPattern:
    """A candidate deep_detect provider-id extraction pattern.

    ``regex`` extracts a STABLE identifier. Signing material never appears
    here — it is represented by ``opaque_slots`` on the draft instead.
    ← capture_workbench.DraftPattern
    """
    key: str
    regex: str
    sample_shape: str
    confidence: str
    rationale: str


# ── 1. URL-segment roles ──────────────────────────────────────────────────────────
def split_segments(url: str) -> Tuple[str, List[str]]:
    """(netloc, [path segments]). ← capture_template._segments"""
    sp = urlsplit(url)
    path_parts = [p for p in sp.path.split("/") if p != ""]
    return sp.netloc, path_parts


def is_addressable(seg: str) -> bool:
    """Segment carries addressable identity (not a fixed path word).
    ← capture_workbench._segment_is_addressable"""
    if not seg:
        return False
    shape = classify_value(seg)
    if shape in _ID_SHAPES:
        return True
    if _HEXISH.match(seg) and any(c.isdigit() for c in seg):
        return True
    if any(c.isdigit() for c in seg) and not _STRUCTURAL_WORD.match(seg):
        return True
    return False


def segment_role(seg: str) -> str:
    """Classify an *addressable* path segment as an IDENTITY key or a
    RENDITION key. Pure shape inference — there is no variation evidence in a
    same-title capture pair, so we read the segment's internal structure: a
    resolution/quality/fps descriptor is a rendition member; anything else
    addressable is the per-title identity key. ← capture_workbench._segment_role
    """
    shape = classify_value(seg)
    stem = seg.rsplit(".", 1)[0] if "." in seg else seg
    if _RENDITION_SIGNAL.search(stem) or _RENDITION_SIGNAL.search(seg):
        return RENDITION
    # A filename-shaped segment with no resolution/quality signal is ambiguous;
    # default it to rendition only if it is filename-shaped AND its stem is not
    # itself id-shaped (opaque/hex/uuid). Otherwise it is an identity key.
    if shape == "filename":
        stem_shape = classify_value(stem)
        if stem_shape in ("uuid", "sha256", "md5", "id"):
            return IDENTITY  # e.g. "<uuid>.mp4" — the id IS the title key
        # filename-shaped, non-id stem, no resolution signal: treat as a
        # rendition-ish descriptor (a named asset variant), still not identity.
        return RENDITION
    return IDENTITY


# ── 2. pattern derivation ───────────────────────────────────────────────────────────
def _slug(name: str) -> str:
    """Canonicalize a param/segment name into a pattern key, matching the
    deep_detect convention of stripping the disambiguating suffix so query
    and attr variants merge (``media_id_url`` + ``media_id_attr`` ->
    ``media_id``). ← capture_workbench._slug"""
    base = re.sub(r"_(url|attr|embed|short|player|v)$", "",
                  name.strip().lower())
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or "id"


def segment_regex(seg: str) -> str:
    """Char-class regex for a segment's shape. ← capture_workbench._segment_regex"""
    shape = classify_value(seg)
    if shape == "filename":
        return r"[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,6}"
    if shape == "uuid":
        return r"[0-9a-fA-F-]{36}"
    if _HEXISH.match(seg):
        return r"[0-9a-fA-F]{%d,}" % min(len(seg), 6)
    if seg.isdigit():
        return r"\d+"
    return r"[A-Za-z0-9._-]+"


def derive_pattern(param: str, value: str, in_path: bool) -> DraftPattern:
    """Draft a deep_detect-style extraction regex for a STABLE identifier.

    The regex captures the value where it appears (query ``key=<cap>`` or a
    path segment), with a character class derived from the value's shape. This
    is a *starting point* for the operator, deliberately conservative.
    ← capture_workbench._derive_pattern
    """
    shape = classify_value(value)
    # Character class by shape — narrow enough to be meaningful, wide enough
    # not to miss a rotated value of the same shape.
    cls = {
        "uuid": r"[0-9a-fA-F-]{36}",
        "sha256": r"[0-9a-fA-F]{64}",
        "md5": r"[0-9a-fA-F]{32}",
        "id": r"\d{1,12}",
        "filename": r"[A-Za-z0-9._-]{1,80}",
    }.get(shape, r"[A-Za-z0-9._-]+")
    key = _slug(param)
    if in_path:
        # Anchor on the literal segment name if the param is a synthetic
        # path slot name we can't anchor on; fall back to a bare segment.
        regex = rf"/({cls})(?:/|$)"
        rationale = (f"path segment holds a {shape}-shaped id; anchored on "
                     f"segment boundary — tighten the prefix to the real "
                     f"path once confirmed")
    else:
        regex = rf"[?&]{re.escape(param)}=({cls})"
        rationale = (f"query param {param!r} carries a {shape}-shaped id; "
                     f"captured on the query key")
    return DraftPattern(key=key, regex=regex, sample_shape=shape,
                        confidence="medium", rationale=rationale)


def network_patterns(network_log: List[dict]) -> dict:
    """host/api/media patterns + resolutions from a capture's network log.
    ← build_template_from_wacz._network_patterns"""
    hosts = Counter()
    api_patterns = set()
    api_hosts = set()
    media_patterns = set()
    resolutions = set()
    statuses = Counter()
    content_types = Counter()

    for entry in network_log:
        url = entry.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue

        p = urlparse(url)
        hosts[p.netloc] += 1
        path = p.path or ""
        statuses[str(entry.get("response_status"))] += 1

        headers = entry.get("response_headers") or []
        for h in headers:
            if str(h.get("name", "")).lower() == "content-type":
                content_types[str(h.get("value", "")).split(";")[0]] += 1

        for r in RES_RE.findall(url):
            resolutions.add(int(r))

        # Save endpoint patterns, not signed URLs.
        m = re.search(r"/api/v\d+/movie/(\d+)/download-resolution/(\d+)", path)
        if m:
            api_patterns.add("/api/v{version}/movie/{movie_id}/download-resolution/{resolution}")
            resolutions.add(int(m.group(2)))
            # record the host that actually served the API call — an observed
            # fact (diagnostic, like top_hosts), surfaced to the reviewer as a
            # hint. Stored under observed_api_hosts (NOT api_host), so the
            # normalizer never auto-builds the api{base} from it.
            if p.netloc:
                api_hosts.add(p.netloc)

        m_avc = re.search(r"/AVC_(\d+)\.mp4$", path, re.I)
        if m_avc:
            media_patterns.add(".../AVC_{resolution}.mp4")
            resolutions.add(int(m_avc.group(1)))
        m_vp9 = re.search(r"/VP9_(\d+)\.mp4$", path, re.I)
        if m_vp9:
            media_patterns.add(".../VP9_{resolution}.mp4")
            resolutions.add(int(m_vp9.group(1)))
        low = path.lower()
        if low.endswith(".mpd"):
            media_patterns.add(".../{manifest}.mpd")
        if low.endswith(".m3u8"):
            media_patterns.add(".../{manifest}.m3u8")
        if low.endswith((".mpd", ".m3u8")):
            # the master manifest lists every rendition even if only one rung
            # was streamed — this is the full quality ladder we want to surface
            resolutions |= manifest_resolutions(entry.get("response_body"), path)

    return {
        "top_hosts": [{"host": h, "count": c} for h, c in hosts.most_common(20)],
        "api_patterns": sorted(api_patterns),
        "observed_api_hosts": sorted(api_hosts),
        "media_patterns": sorted(media_patterns),
        "resolutions_seen": sorted(resolutions, reverse=True),
        "status_counts": dict(statuses),
        "content_type_counts": dict(content_types.most_common(20)),
    }


# ── 3. identity / rendition ─────────────────────────────────────────────────────────
def manifest_resolutions(body: object, path: str) -> set:
    """Pull rendition heights out of an HLS master or DASH MPD body.

    Returns a set of plausible resolution heights (100..8640). Tolerant of
    bytes/str bodies, truncated bodies, and manifests served without the
    file extension (sniffs ``#EXTM3U`` / ``<MPD``).
    ← build_template_from_wacz._manifest_resolutions
    """
    if not body:
        return set()
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8", "replace")
        except Exception:
            return set()
    if not isinstance(body, str):
        return set()
    text = body[:200_000]  # manifests are small; cap defensively
    low = (path or "").lower()
    out: set = set()
    if low.endswith(".m3u8") or "#EXTM3U" in text[:64]:
        out |= {int(h) for h in _HLS_RES_RE.findall(text)}
    if low.endswith(".mpd") or "<MPD" in text[:1024] or "<mpd" in text[:1024]:
        out |= {int(h) for h in _MPD_RES_RE.findall(text)}
    return {n for n in out if 100 <= n <= 8640}


def recorded_rendition_ok(slot_values: Dict[str, str],
                          slots_meta: Dict[str, dict]) -> bool:
    """True if every RENDITION slot with a recorded value matches the observed
    value (i.e. the observed match served the recorded rendition).
    ← capture_template._is_recorded_rendition (closure over `slots_meta`, lifted
    to an explicit parameter; the local `sv` becomes `slot_values`).
    """
    for name, meta in slots_meta.items():
        if (meta.get("role") == RENDITION
                and meta.get("recorded") is not None
                and slot_values.get(name) != meta.get("recorded")):
            return False
    return True


# ── 4. confidence floor ─────────────────────────────────────────────────────────────
# decision_confidence is DEFERRED to step 5 (see module docstring): the design
# quarantines confidence work to the gated step, and workbench._decision_confidence is
# draft post-processing over an assembled DetectorDraft + flow/stability graphs, not a
# pure derivation primitive, and is not covered by the characterization fixture.
