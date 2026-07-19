"""dom_recorder — browser-side DOM capture wiring (rrweb + snapdom).

`dom_capture.DomCapture` defines an rrweb-style ingest API
(:meth:`record_dom_event`) but nothing produced those events: the capture
session recorded network (via :func:`session_capture.capture_via_cdp`) and
left ``dom_log`` empty. This module closes that gap. It injects the vendored
rrweb bundle into the capture page, starts ``rrweb.record`` with the project's
PII-redaction classes, and forwards each emitted event into ``record_dom_event``
— the same shape the rest of the pipeline (``selector_learning``,
``capture_synth``) already consumes. It also exposes a snapdom-based DOM→image
snapshot helper.

Mirrors the :func:`session_capture.capture_via_cdp` contract: the caller owns
the Playwright ``page`` and is responsible for navigating/driving it; this only
wires the listeners. Like ``capture_via_cdp``, the live injection path is not
exercised by the unit suite (it needs a real browser); the pure mapping logic
(:func:`rrweb_to_record_kwargs`) and the ingest/redaction it delegates to are
unit-tested with synthetic rrweb events.

Vendored assets live in ``bulk_downloader/vendor`` (see ``vendor/VENDOR.md``):
rrweb 2.0.1 (``window.rrweb``) and @zumer/snapdom 2.12.8 (``window.snapdom``).
Both are self-contained global-attaching bundles so they can be injected with
``page.add_script_tag`` after load (no module loader exists in page scope).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from .session_capture import _now_ms

# rrweb event-type ids (top-level event.type, distinct from the incremental
# data.source ids named in dom_capture.RR_*).
_RR_FULL_SNAPSHOT = 2
_RR_INCREMENTAL = 3
_RR_META = 4  # viewport + URL/navigation metadata (data: href/width/height)

_VENDOR = Path(__file__).resolve().parent / "vendor"
_RRWEB_JS = _VENDOR / "rrweb" / "rrweb.min.js"
_SNAPDOM_JS = _VENDOR / "snapdom" / "snapdom.js"


@lru_cache(maxsize=None)
def _asset(path: Path) -> str:
    """Read a vendored bundle once. Raises a clear error if it is missing
    (vendoring is part of the package; a missing file is a build problem)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:  # pragma: no cover - build integrity
        raise FileNotFoundError(
            f"vendored capture asset missing: {path} "
            f"(expected under bulk_downloader/vendor; see vendor/VENDOR.md)"
        ) from e
    if not text.strip():  # pragma: no cover
        raise ValueError(f"vendored capture asset is empty: {path}")
    return text


def rrweb_js() -> str:
    """The vendored rrweb UMD bundle (defines ``window.rrweb``)."""
    return _asset(_RRWEB_JS)


def snapdom_js() -> str:
    """The vendored snapdom bundle (defines ``window.snapdom``)."""
    return _asset(_SNAPDOM_JS)


# Browser-side bootstrap: start rrweb.record, emitting each event into an
# in-page buffer (``window.__bd_dom_buf``) that Python drains on an interval and
# at finish (see :func:`drain_dom_events`). It honours the project's redaction
# classes (bd-* are ours, rr-* are rrweb-native); redaction here is
# belt-and-suspenders — the Python side (DomCapture._redact_event_data)
# re-redacts serialized nodes too.
#
# v3.66.165: emit no longer calls a Playwright-exposed binding. On some
# renderers rrweb's emit closure could not call the exposed name
# (``TypeError: this._global[this._globalBindingName] is not a function``),
# silently dropping every event and leaving ``dom_log`` empty. Buffering in-page
# and draining over ``page.evaluate`` sidesteps the binding entirely. The
# bootstrap is also armed AFTER load (via ``add_script_tag``; see
# :func:`arm_dom_recorder`), not at document-start, so the library is already
# present when it runs — no setTimeout retry is needed, and arming record() over
# a not-yet-built SPA DOM (which SIGTRAPped the renderer) no longer happens.
_BOOTSTRAP = r"""
(function () {
  if (window.__bd_rrweb_started) return;
  if (!Array.isArray(window.__bd_dom_buf)) window.__bd_dom_buf = [];
  if (typeof window.__bd_dom_dropped !== "number") window.__bd_dom_dropped = 0;
  if (!(window.rrweb && typeof window.rrweb.record === "function")) return;
  window.__bd_rrweb_started = true;
  try {
    window.rrweb.record({
      emit: function (event) {
        try {
          var buf = window.__bd_dom_buf;
          // Safety valve: never let the in-page buffer grow without bound
          // between Python drains — drop (counted) rather than OOM the tab.
          if (buf.length >= 200000) { window.__bd_dom_dropped++; return; }
          buf.push(event);
        } catch (e) {}
      },
      // bd-* + rr-* PII annotations. RegExp form tests the className string.
      maskTextClass: /bd-mask|rr-mask/,
      blockClass: /bd-block|rr-block/,
      ignoreClass: /bd-ignore|rr-ignore/,
      // Wave 2 (F2): mask ALL input values, not just type=password. rrweb's
      // default (maskAllInputs:false) left email/text/hidden values cleartext in
      // the DOM log — the login-email + hidden Turnstile-input leak. Masks the
      // value only; element + attributes (id/name/type) remain, so attribute-
      // based selector derivation is unaffected. Network-log token redaction is
      // a separate path (capture_artifact_redact / capture_redact).
      maskAllInputs: true,
      recordCanvas: false,
      collectFonts: false,
      sampling: { mousemove: false, scroll: 150 }
    });
  } catch (e) { window.__bd_rrweb_started = false; }
})();
"""


def recorder_script() -> str:
    """Full script to inject: rrweb library + the record bootstrap.

    Injected via ``page.add_script_tag`` AFTER load by :func:`arm_dom_recorder`
    (v3.66.165); re-arming on navigation is driven by the caller's pump loop,
    which re-invokes ``arm_dom_recorder`` once the new document is usable."""
    # NOTE: the explicit ``;`` separator is load-bearing. The vendored rrweb
    # UMD bundle's last statement (``...}))``) has no terminating semicolon,
    # and ``_BOOTSTRAP`` begins with an IIFE ``(function(){...})()``. A bare
    # newline join lets JS ASI parse the boundary as ``}))(function(){...})()``
    # — i.e. it *calls* the bundle's trailing value as a function, throwing
    # "(intermediate value)(...) is not a function". The bootstrap IIFE is then
    # consumed as that call's argument and never runs, so ``rrweb.record`` is
    # never started and ``dom_log`` stays empty. The ``;`` terminates the
    # bundle's final statement so the bootstrap executes. See
    # tests/test_dom_recorder_asi.py.
    return rrweb_js() + "\n;\n" + _BOOTSTRAP


def rrweb_to_record_kwargs(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map one rrweb event envelope to :meth:`DomCapture.record_dom_event`
    kwargs, or ``None`` for events that carry nothing we retain.

    rrweb ``event.type`` 2 = full snapshot, 3 = incremental (whose
    ``data.source`` is the incremental-source id named by ``dom_capture.RR_*``),
    4 = **Meta** (viewport ``width``/``height`` + ``href`` URL/navigation),
    which is retained as an ``event_type="meta"`` record. Types 0/1 (load) and
    5/6 (custom/plugin) carry no DOM/metadata we use and still map to ``None``.
    Redaction of serialized nodes (and Meta URL hygiene) is handled downstream
    by ``record_dom_event``.
    """
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    ts = event.get("timestamp")
    if etype == _RR_FULL_SNAPSHOT:
        return {"source": -1, "data": event.get("data"),
                "is_full_snapshot": True, "timestamp": ts}
    if etype == _RR_INCREMENTAL:
        data = event.get("data") or {}
        src = data.get("source", -1)
        try:
            src = int(src)
        except (TypeError, ValueError):
            src = -1
        return {"source": src, "data": data,
                "is_full_snapshot": False, "timestamp": ts}
    if etype == _RR_META:
        d = event.get("data") or {}
        meta = {k: d.get(k) for k in ("href", "width", "height") if k in d}
        return {"source": -1, "data": meta, "event_type": "meta",
                "is_full_snapshot": False, "timestamp": ts}
    return None


def attach_dom_recorder(page, capture, *, redact: bool = True):
    """Register a Playwright ``page`` for rrweb DOM capture.

    v3.66.165: recording is no longer armed here. The previous implementation
    installed the recorder via ``add_init_script`` (document-start) and emitted
    through a ``expose_binding`` callback. On heavy SPAs (e.g. reptyle) that
    combination failed two ways: arming ``rrweb.record`` at document-start, over
    a not-yet-built DOM, SIGTRAPped the renderer; and even when it started,
    rrweb's emit closure could not call the exposed binding name (``TypeError:
    this._global[this._globalBindingName] is not a function``), so every event
    was dropped and ``dom_log`` stayed empty.

    The fix splits arming and transport out to two caller-driven steps:
    :func:`arm_dom_recorder` (post-load, via ``add_script_tag``) and
    :func:`drain_dom_events` (pulls the in-page buffer over ``page.evaluate``).
    capture_session's pump loop calls both on every wired page on an interval
    and at finish. This registrar now only emits the offline-assets log line
    once; ``capture``/``redact`` are accepted for call-site symmetry with
    :func:`session_capture.capture_via_cdp` (which still wires at attach time).
    """
    _log_local_assets_once()
    return capture


# Page-side probes used by arm/drain. Kept as module constants so tests can
# assert on them and so the strings are defined once.
_STARTED_JS = "!!window.__bd_rrweb_started"
_READYSTATE_JS = "document.readyState"
# v3.66.170 (H1): also return + reset the in-page drop counter so a buffer that
# hit the 200k safety cap (and silently dropped events) is no longer invisible.
# Atomic within the single page.evaluate: read both, then zero both.
_DRAIN_JS = ("() => { const b = window.__bd_dom_buf || [];"
             " const d = window.__bd_dom_dropped || 0;"
             " window.__bd_dom_buf = []; window.__bd_dom_dropped = 0;"
             " return { events: b, dropped: d }; }")


def _arm_dom_recorder_impl(page) -> bool:
    """Core arming logic (best-effort, never raises). Public entry point is
    :func:`arm_dom_recorder`, which also tracks the failure streak (H2)."""
    try:
        if page.evaluate(_STARTED_JS):
            return True
    except Exception:
        return False
    try:
        if page.evaluate(_READYSTATE_JS) not in ("interactive", "complete"):
            return False
    except Exception:
        return False
    try:
        page.add_script_tag(content=recorder_script())
    except Exception:
        return False
    try:
        return bool(page.evaluate(_STARTED_JS))
    except Exception:
        return False


# v3.66.170 (H2): a persistent arm failure (page CSP blocking add_script_tag, a
# page that never reaches readyState interactive/complete, or a torn-down page)
# otherwise yields an empty dom_log with NO signal — indistinguishable from "the
# page genuinely had no DOM events." Track a consecutive-failure streak and warn
# ONCE past a threshold so a silently-empty capture has a stated cause. A
# success resets the streak. Self-contained; does not touch the caller's pump.
_ARM_FAIL_STREAK = 0
_ARM_FAIL_WARN_AT = 5
_ARM_WARNED = False


def _note_arm_result(ok: bool) -> None:
    global _ARM_FAIL_STREAK, _ARM_WARNED
    if ok:
        _ARM_FAIL_STREAK = 0
        return
    _ARM_FAIL_STREAK += 1
    if _ARM_FAIL_STREAK >= _ARM_FAIL_WARN_AT and not _ARM_WARNED:
        _ARM_WARNED = True
        sys.stderr.write(
            "  dom_recorder: WARNING rrweb.record could not be armed after "
            f"{_ARM_FAIL_STREAK} consecutive attempts — DOM capture will be "
            "empty. Likely a page CSP blocking add_script_tag, a page that "
            "never reached readyState interactive/complete, or a torn-down page "
            "(independent of the masking/transport path)\n")


def arm_dom_recorder(page) -> bool:
    """Idempotently start ``rrweb.record`` on a *loaded* ``page``, emitting into
    the in-page ``window.__bd_dom_buf`` buffer (drained by
    :func:`drain_dom_events`). Returns True iff recording is active; best-effort,
    never raises.

    Arms only when the document is usable and via ``add_script_tag`` (a real
    top-level script) AFTER load — never ``add_init_script`` at document-start,
    which SIGTRAPs the renderer on heavy SPAs. Idempotent via
    ``window.__bd_rrweb_started``: a no-op once recording is active, and it
    re-arms after a navigation (which resets that flag and the buffer on the new
    document). Injection can be blocked by a page CSP or a torn-down page; in
    that case it returns False and the next pump tick retries — and a persistent
    failure streak is surfaced once (H2)."""
    ok = _arm_dom_recorder_impl(page)
    _note_arm_result(ok)
    return ok


def drain_dom_events(page, capture) -> int:
    """Pull buffered rrweb events out of ``page`` and feed them into ``capture``
    via the same :func:`rrweb_to_record_kwargs` -> ``record_dom_event`` path the
    old binding used (so downstream mapping/redaction is unchanged). Returns the
    count ingested. Best-effort; never raises.

    This is the replacement transport for the Playwright ``expose_binding`` emit
    that failed on some renderers: the page buffers events into
    ``window.__bd_dom_buf`` and Python drains that buffer (atomic splice) on an
    interval and once more at finish.
    """
    try:
        result = page.evaluate(_DRAIN_JS)
    except Exception:
        return 0
    # v3.66.170 (H1): _DRAIN_JS returns {events, dropped}. Tolerate a bare list
    # too, in case a page armed before this upgrade is drained mid-session.
    if isinstance(result, dict):
        batch = result.get("events") or []
        dropped = int(result.get("dropped") or 0)
    else:
        batch = result or []
        dropped = 0
    if dropped:
        _note_dom_dropped(dropped)
    if not batch:
        return 0
    n = 0
    for event in batch:
        try:
            kwargs = rrweb_to_record_kwargs(event)
            if kwargs is not None:
                capture.record_dom_event(**kwargs)
                n += 1
        except Exception:
            # A single malformed event must never crash the capture.
            pass
    return n


# v3.66.170 (H1): a non-zero drop count means the in-page buffer hit its 200k
# safety cap between drains and shed events — the persisted dom_log is therefore
# truncated, not complete. Surface it: accumulate a session total (exposed via
# get_status) and warn once so a truncated capture is never silent.
_DOM_DROPPED_TOTAL = 0
_DOM_DROPPED_WARNED = False


def _note_dom_dropped(dropped: int) -> None:
    global _DOM_DROPPED_TOTAL, _DOM_DROPPED_WARNED
    _DOM_DROPPED_TOTAL += int(dropped)
    if not _DOM_DROPPED_WARNED:
        _DOM_DROPPED_WARNED = True
        sys.stderr.write(
            "  dom_recorder: WARNING in-page DOM buffer hit its cap and dropped "
            f"{_DOM_DROPPED_TOTAL} event(s) — the captured dom_log is TRUNCATED, "
            "not complete (drain the buffer more frequently if this recurs)\n")


_LOCAL_LOGGED = False


def using_local_assets() -> bool:
    """True iff both DOM-capture bundles are vendored on disk. When True, an
    approved capture session records rrweb DOM + snapdom snapshots entirely from
    the local copies — offline, no remote CDN. The asset readers raise rather
    than fall back to a CDN, so this is the project's no-CDN guarantee."""
    return _RRWEB_JS.is_file() and _SNAPDOM_JS.is_file()


def _log_local_assets_once() -> None:
    global _LOCAL_LOGGED
    if _LOCAL_LOGGED:
        return
    _LOCAL_LOGGED = True
    if using_local_assets():
        sys.stderr.write("  dom_recorder: using local vendored rrweb + snapdom "
                         "(offline, no CDN)\n")
    else:
        sys.stderr.write("  dom_recorder: WARNING vendored rrweb/snapdom missing "
                         "under bulk_downloader/vendor — DOM capture will fail "
                         "(no CDN fallback by design; see vendor/VENDOR.md)\n")


# snapdom v2 attaches window.snapdom(el, opts) -> object with .toPng()/.toCanvas().
_SNAPDOM_SNAPSHOT = r"""
(async function () {
  try {
    if (typeof window.snapdom !== "function") return null;
    var el = document.documentElement || document.body;
    var r = await window.snapdom(el, { fast: true, embedFonts: false });
    if (r && typeof r.toCanvas === "function") {
      var c = await r.toCanvas();
      return c.toDataURL("image/png");
    }
    if (r && typeof r.toPng === "function") {
      var img = await r.toPng();
      return img && img.src ? img.src : null;
    }
    return null;
  } catch (e) { return null; }
})()
"""


# v3.66.170 (H3): bound a single snapdom data URL. A full-documentElement PNG of
# a pathological page can be enormous; storing it unbounded bloats the capture
# and can OOM downstream. Over the cap, drop the snapshot (return None) and warn
# once. ~12M chars of base64 ≈ ~9MB decoded — generous for a real page snapshot.
_SNAPSHOT_MAX_CHARS = 12_000_000
_SNAPSHOT_WARNED = False


def snapshot_dom(page, capture=None, *, label: Optional[str] = None):
    """Capture a snapdom DOM→PNG snapshot of ``page``; store it on ``capture``
    if given. Returns the PNG data URL, or ``None`` on any failure.

    Best-effort and fully guarded — a snapshot failure never disturbs the
    capture. snapdom is injected on demand if not already present. A snapshot
    larger than ``_SNAPSHOT_MAX_CHARS`` is dropped rather than stored (H3).
    """
    global _SNAPSHOT_WARNED
    try:
        present = page.evaluate("typeof window.snapdom === 'function'")
        if not present:
            page.add_script_tag(content=snapdom_js())
        data_url = page.evaluate(_SNAPDOM_SNAPSHOT)
    except Exception:
        return None
    if isinstance(data_url, str) and len(data_url) > _SNAPSHOT_MAX_CHARS:
        if not _SNAPSHOT_WARNED:
            _SNAPSHOT_WARNED = True
            sys.stderr.write(
                "  dom_recorder: WARNING dropping an oversize DOM snapshot "
                f"({len(data_url)} chars > {_SNAPSHOT_MAX_CHARS} cap); not "
                "stored\n")
        return None
    if data_url and capture is not None:
        try:
            capture.record_dom_snapshot(data_url, label=label)
        except Exception:
            pass
    return data_url


def get_status() -> Dict[str, Any]:
    """Introspection for diagnostics: which vendored assets are present, plus
    the v3.66.170 capture-health counters (dropped DOM events at the buffer cap;
    consecutive arm failures)."""
    return {
        "rrweb_present": _RRWEB_JS.is_file(),
        "rrweb_bytes": _RRWEB_JS.stat().st_size if _RRWEB_JS.is_file() else 0,
        "snapdom_present": _SNAPDOM_JS.is_file(),
        "snapdom_bytes": _SNAPDOM_JS.stat().st_size if _SNAPDOM_JS.is_file() else 0,
        "dom_events_dropped": _DOM_DROPPED_TOTAL,
        "arm_fail_streak": _ARM_FAIL_STREAK,
    }
