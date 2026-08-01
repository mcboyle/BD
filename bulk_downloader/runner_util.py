"""runner_util -- the leaf "kernel" for the runner.py decomposition (cut 1).

Genuinely-leaf module functions plus the shared constants/ledger that the
SiteRunner mixins (and core) reference, lifted verbatim out of runner.py so a
mixin can import them WITHOUT a `from .runner import X` cycle. This module is
imported by all and imports nothing back from runner. Pure code motion --
behavior unchanged from v3.66.396.
"""
import sys
import threading
import time
from datetime import datetime, timezone


# Canonical creation-time default (paired with DEFAULT_MAX_CONCURRENT, which
# stays in runner.py with the worker-coupled concurrency cap).
DEFAULT_MIN_RESOLUTION = 1080


# v3.43.70: BD event-kind → apprise event-type mapping. Used in
# log_event() to fan out interesting events to apprise. Only the
# events with mappings here generate notifications; everything else
# stays as in-memory log entries.
#
# Keys are the `kind` arg passed to log_event() throughout the runner.
# Values are the canonical apprise event constants. New event kinds
# can be added to this map without touching log_event() itself.
_BD_TO_APPRISE_EVENT: dict = {
    # Successful downloads (each one is one batch entry)
    "done":             "download_done",
    "aylo_done":        "download_done",
    "vixen_done":       "download_done",
    "dl8_done":         "download_done",
    "jsonapi_done":     "download_done",
    # Failures
    "fail":             "download_failed",
    "aylo_extract_failed":   "download_failed",
    "vixen_extract_failed":  "download_failed",
    "dl8_extract_failed":    "download_failed",
    "aylo_hls_failed":  "download_failed",
    "vixen_hls_failed": "download_failed",
    "dl8_mp4_failed":   "download_failed",
    # Captcha / auth — high priority, per-event by default
    "captcha":          "captcha",
    "captcha_pending":  "captcha",
    "auth_required":    "auth_required",
    # Disk pressure
    "disk_full":        "disk_full",
    "disk_low":         "disk_full",
    # Queue lifecycle
    "queue_empty":      "queue_empty",
    "queue_paused":     "queue_paused",
    "queue_resumed":    "queue_resumed",
}


def _ts(): return datetime.now().strftime("%H:%M:%S")


def _ts_iso():
    """The date-comparable sibling of `_ts()`.

    `_ts()` is HH:MM:SS because that is what the queue UI renders; it carries
    no date, so comparing it against a "%Y-%m-%d" prefix is False for every
    possible pair of values. Every day-window consumer filters on THIS field
    instead (app.py:3912, app_dashboard.py:66 and :203, app_queue.py:229).

    LOCAL, deliberately: all four consumers compare against a LOCAL
    `time.strftime("%Y-%m-%d")`, so a UTC stamp here would land on the wrong
    day near midnight on a non-UTC host. (runner_queue.py:106 copies sqlite
    `ts_updated`, which db.py stamps UTC -- that mismatch is a separate,
    separately-filed item and is NOT what this helper is for.)

    One implementation on purpose: this value was inlined at runner.py:1658 and
    three more producers wrote none at all. Three copies is a denominator that
    drifts, and the copy nobody updates is the one that ships.
    """
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _utc_iso_to_local_iso(s: str) -> str:
    """Re-render a UTC "%Y-%m-%dT%H:%M:%S" stamp in LOCAL time, same format.

    The queue table's `ts_updated` is written by SQLite
    strftime('%Y-%m-%dT%H:%M:%S','now'), which is UTC. Every day-window
    consumer compares it against a LOCAL time.strftime("%Y-%m-%d"). Copying it
    verbatim into `ts_iso` on restart therefore compared two different clocks.

    MEASURED, not theoretical: on a host at America/Los_Angeles a job completed
    17:05 local was rehydrated as 2026-08-01T00:05:02 and tested against
    today_iso=2026-07-31 -- False. No midnight involved; it simply vanished
    from the count. Sweeping 24 hourly instants per zone: Tokyo loses 9 of 24,
    Kiritimati 14, Los_Angeles 7, UTC 0. West-of-UTC zones also gain jobs from
    "tomorrow"; east-of-UTC only lose.

    Converted HERE and not in db.py on purpose. `ts_updated` has non-day-window
    consumers that build UTC cutoffs (storage_tier.py:310, cost_economics.py:185)
    and db.py:1792 uses it as a monotonic cursor, so stamping 'localtime' would
    give the column mixed semantics and break a DST fall-back. `ts_iso` has
    exactly four readers and all four want LOCAL.

    Historic rows need no migration: the column has ALWAYS been UTC, so
    converting on read is correct for old and new rows alike.

    An unparseable value is returned UNCHANGED rather than blanked -- that is a
    no-op relative to the previous behaviour, so the fix cannot make a row that
    used to count stop counting. Empty stays empty. Never fall back to today:
    test_cut40's G4 pins that as the seductive wrong fix.
    """
    if not s:
        return ""
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return s
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def _resolve_safe(v):
    """NEW-7 (v3.66.43): resolve a credential value, returning "" on any
    failure — NEVER the literal "@cred:..." reference. The runner treats
    "" as "no credentials configured", which produces a clean error path
    instead of typing the ref string into a login form (401, confusing)
    or crashing the worker thread when resolve_password raises."""
    if not v:
        return ""
    try:
        from .secrets_store import resolve_password
    except Exception:
        if isinstance(v, str) and v.startswith("@cred:"):
            import logging
            logging.getLogger("bulk_downloader.runner").warning(
                "secrets_store unavailable; a '@cred:' reference treated as "
                "missing credentials")
            return ""
        return v  # legacy plaintext passthrough
    try:
        resolved = resolve_password(v)
    except Exception as e:
        import logging
        logging.getLogger("bulk_downloader.runner").warning(
            "resolve_password raised %s for %s; treating as missing",
            type(e).__name__,
            "a '@cred:' reference" if isinstance(v, str) and v.startswith("@cred:")
            else "a plaintext value")
        return ""
    return resolved or ""


def _check_video_magic_bytes(data: bytes) -> str:
    """v3.43.0: identify a video container from its first bytes.

    Used by teach Test Download to confirm the URL produces real video
    bytes rather than an HTML error page, login redirect, or empty
    response. Returns one of "mp4", "mkv", "webm", "flv", "mpegts",
    "avi", "mov", "unknown". Conservative — only reports a hit on
    actual container magic. "unknown" means "don't commit unless the
    operator explicitly overrides."

    Magic byte references:
      - MP4/MOV: bytes 4-8 == 'ftyp' (ISO base media format)
      - MKV/WebM: starts with 0x1A45DFA3 (EBML header)
      - FLV: starts with b'FLV\\x01'
      - MPEG-TS: starts with 0x47 sync byte at intervals of 188
      - AVI: starts with b'RIFF' and bytes 8-12 == b'AVI '
    """
    if not data or len(data) < 16:
        return "unknown"
    # ISO BMFF (MP4/MOV): the first box is 'ftyp' at offset 4.
    if data[4:8] == b"ftyp":
        # Subtype tells MP4 vs MOV. Both are mp4 family.
        subtype = data[8:12]
        if subtype in (b"qt  ", b"moov"):
            return "mov"
        return "mp4"
    # Matroska / WebM share the EBML header magic
    if data[:4] == b"\x1A\x45\xDF\xA3":
        # Look for 'webm' doctype within the first 512 bytes
        if b"webm" in data[:512]:
            return "webm"
        return "mkv"
    # FLV
    if data[:4] == b"FLV\x01":
        return "flv"
    # AVI
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "avi"
    # MPEG-TS: sync byte 0x47 at offsets 0, 188, 376
    if (data[0] == 0x47 and len(data) >= 377
            and data[188] == 0x47 and data[376] == 0x47):
        return "mpegts"
    # MP4 fragments may not start with 'ftyp' — they might start with
    # 'styp' or 'moof'. Treat those as mp4 too.
    if data[4:8] in (b"styp", b"moof", b"sidx"):
        return "mp4"
    return "unknown"


def resolve_url_attribute(url_attr_raw, row_selectors, matched_selector):
    """Pick the URL attribute to read for a matched selector.

    v3.42.4: a single site config can now cover multiple HTML variants
    by giving each row_selector its own url_attribute. Three accepted
    shapes for `url_attribute`:

      1. str   — single attribute applied to whichever selector matched
                 (legacy behavior, unchanged for existing configs).
      2. list  — parallel to row_selectors; same length, same order.
                 Empty string in any slot means click-and-capture for
                 that selector (no fast-path URL extraction).
      3. dict  — keyed by selector string, value is attribute name.
                 Selectors absent from the dict → click-and-capture.

    Returns the attribute name to read (empty string means fall back to
    the click-and-capture path)."""
    if isinstance(url_attr_raw, str):
        return url_attr_raw or ""
    if isinstance(url_attr_raw, list) and matched_selector:
        try:
            idx = (row_selectors or []).index(matched_selector)
        except ValueError:
            return ""
        if 0 <= idx < len(url_attr_raw):
            return url_attr_raw[idx] or ""
        return ""
    if isinstance(url_attr_raw, dict) and matched_selector:
        return url_attr_raw.get(matched_selector, "") or ""
    return ""


# ─── #3 runtime nav gate ──────────────────────────────────────────────────
# candidate_filter rejection reasons that mean "this URL is navigation /
# account / search / chrome / an unrelated host" — it must NEVER become a
# download (and never reach filename generation / download.bin). A bare
# "no download signal" rejection is intentionally NOT here: a real download
# link on an unknown site may carry no URL signal, so we keep it and let the
# normal scoring / click path handle it (unknown-site fallback stays working).
_RUNTIME_NAV_REJECTIONS = frozenset({
    "homepage link", "navigation URL", "search/settings/login/logout",
    "nav/header/footer", "search/filter", "share/favorite/comment/vote",
    "external/unrelated link",
})
# URL-bearing attributes, in resolution priority, read off a candidate element.
_GATE_URL_ATTRS = ("href", "data-href", "data-url", "data-src",
                   "data-download", "data-video")


def gate_candidate_url(locator, page_url, *, url_attr=None, learned_sel="",
                       text=""):
    """Resolve a download candidate's URL from its element and classify it.

    Returns ``(abs_url, reject_reason)``:
      * ``reject_reason`` is a non-empty string when the resolved URL is a
        homepage / navigation / account / search / unrelated-host link that
        must not become a download — the caller routes to ``needs_review``.
      * ``reject_reason`` is ``""`` when the URL is acceptable, OR when there
        is no resolvable URL on the element (a click-target: the caller falls
        through to ``expect_download``).

    Relative hrefs are resolved against ``page_url`` (so ``href="/"`` on
    ``https://app.reptyle.com/foo`` is recognised as the homepage). Fail-open:
    any internal error yields ``("", "")`` so a classifier/parse bug can never
    cost the operator a real download.
    """
    try:
        attrs = ([url_attr] if url_attr else []) + list(_GATE_URL_ATTRS)
        raw = ""
        for a in attrs:
            if not a:
                continue
            try:
                v = locator.get_attribute(a)
            except Exception:
                v = None
            if v and v.strip():
                raw = v.strip()
                break
        if not raw:
            return ("", "")                       # URL-less click target
        from urllib.parse import urljoin, urlsplit
        abs_url = (raw if raw.startswith(("http://", "https://"))
                   else urljoin(page_url or "", raw))
        page_host = urlsplit(page_url or "").netloc
        from . import candidate_filter as _cf
        verdict = _cf.classify(url=abs_url, text=text or "",
                               selector=learned_sel or "", page_host=page_host)
        if not verdict.accepted:
            hard = _RUNTIME_NAV_REJECTIONS.intersection(verdict.rejections)
            if hard:
                return (abs_url, "; ".join(sorted(hard)))
        return (abs_url, "")
    except Exception:
        return ("", "")                           # fail-open


def _bump_learned_stat(config, key, delta=1):
    """Phase 5.8: increment a counter inside config['learned']['stats'].
    Used to track learned-selector hit/miss rates so we can detect drift
    (site changed) and trigger auto-recovery."""
    if not isinstance(config.get("learned"), dict): return
    stats = config["learned"].setdefault("stats", {})
    stats[key] = (stats.get(key) or 0) + delta

# ─── Phase 8.3: Cross-site bandwidth tracking ─────────────────────────────
# Every chunk written by _http_download bumps a global bytes counter for
# the current second. A background reaper rolls the counter into a fixed
# 3600-entry deque (one entry per second = 1 hour rolling window). The UI
# reads this via /api/stats/bandwidth and renders a chart.
_bw_lock = threading.Lock()
_bw_current = {"second": 0, "bytes": 0}
_bw_history = []  # list of (epoch_second, bytes) tuples, max 3600 entries

def record_bandwidth(n_bytes):
    """Add n_bytes to the current-second counter. Called from inside the
    httpx chunk loop. Cheap: no I/O, single lock acquire, no allocations
    in the hot path."""
    if n_bytes <= 0: return
    now_s = int(time.time())
    with _bw_lock:
        if _bw_current["second"] != now_s:
            # Roll the previous second into history
            if _bw_current["second"] > 0:
                _bw_history.append((_bw_current["second"], _bw_current["bytes"]))
                # Trim to 3600 (1 hour)
                if len(_bw_history) > 3600:
                    del _bw_history[:len(_bw_history) - 3600]
            _bw_current["second"] = now_s
            _bw_current["bytes"] = 0
        _bw_current["bytes"] += n_bytes

def get_bandwidth_history(seconds=3600):
    """Return up to `seconds` of (epoch_second, bytes) data, most recent
    last. Includes the current in-progress second too. Gaps (seconds
    where no data was written) are NOT filled — caller should treat
    missing seconds as zero."""
    cutoff = int(time.time()) - seconds
    with _bw_lock:
        out = [(s, b) for s, b in _bw_history if s >= cutoff]
        if _bw_current["second"] > 0 and _bw_current["second"] >= cutoff:
            out.append((_bw_current["second"], _bw_current["bytes"]))
    return out

# ─── Phase 7.3: Per-selector hit/miss + decay ────────────────────────────
# Phase 5.8 cleared the entire learned.download block when overall miss
# rate got high. That throws away good selectors along with stale ones.
# Per-selector decay keeps the good ones and demotes/drops the bad ones.
#
# Counters live in config['learned'][kind]['_per_selector'] as a dict:
#   {selector_string: {'hits': N, 'misses': N}}
#
# Demotion rules (applied periodically by _maybe_demote_selectors):
#   • A selector with ≥3 consecutive misses (no hits since last bump) gets
#     moved to the END of the role's selector list — still tried, but last.
#   • A selector with ≥6 misses and 0 hits gets DROPPED entirely.
#   • A selector that just hit gets promoted to the FRONT and miss count
#     reset to zero.

def _bump_per_selector(config, kind, role, selector, which, delta=1):
    """Increment per-selector counter for a learned selector's hit/miss
    tracking. `which` is 'hits' or 'misses'.

    Side effects on a 'hits' bump:
      - Resets the miss-streak counter to zero for this selector
      - Promotes the selector to the FRONT of its role's selector list
        in config['learned'][kind][role], so it's tried first next time

    No side effects on 'misses' — demotion is batched into
    _maybe_demote_selectors which is called after wide-scan hits."""
    if not isinstance(config.get("learned"), dict): return
    block = config["learned"].setdefault(kind, {})
    ps = block.setdefault("_per_selector", {})
    rec = ps.setdefault(selector, {"hits":0,"misses":0})
    rec[which] = (rec.get(which) or 0) + delta
    if which == "hits":
        # Reset miss streak — a hit means this selector is still working.
        rec["misses"] = 0
        # Promote to front of the role's selector list.
        sels = block.get(role) or []
        if selector in sels:
            sels.remove(selector); sels.insert(0, selector)
            block[role] = sels

def _maybe_demote_selectors(config, kind, role):
    """Walk the role's selector list and demote/drop based on miss streak.
    Called after a wide-scan hit (when every learned selector for that
    role missed). Only mutates if a change is needed."""
    if not isinstance(config.get("learned"), dict): return
    block = config["learned"].get(kind) or {}
    ps = block.get("_per_selector") or {}
    sels = block.get(role) or []
    if not sels: return
    keep = []; demote = []
    for s in sels:
        rec = ps.get(s, {})
        misses = rec.get("misses", 0) or 0
        hits = rec.get("hits", 0) or 0
        if misses >= 6 and hits == 0:
            sys.stderr.write(f"  decay: dropping selector [{s}] ({misses} misses, 0 hits)\n")
            ps.pop(s, None)
            continue  # drop entirely
        if misses >= 3:
            demote.append(s)
        else:
            keep.append(s)
    new_sels = keep + demote
    if new_sels != sels:
        block[role] = new_sels
