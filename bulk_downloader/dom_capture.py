"""DOM + behavioral capture (A-T2) — production-grade capture.

Builds on A-T1's :class:`~bulk_downloader.session_capture.SessionCapture`
(network + cookies) by adding the streams a production capture needs:

  * **rrweb-style DOM event log** — full-snapshot + incremental
    mutations + input/scroll/mouse events, timestamp-synchronized with
    the network log. (We record the event envelopes; the actual DOM
    serialization happens browser-side via rrweb and is handed to us.)
  * **CSS-class PII redaction at capture time** — fields annotated
    ``.bd-mask`` / ``.bd-block`` (and the rrweb-native ``.rr-mask`` /
    ``.rr-block`` / ``.rr-ignore``) have their text/value redacted as the
    event is recorded, mirroring PostHog/rrweb's developer-annotation
    model. Redaction is capture-time so PII never reaches disk.
  * **frame_path** — every event carries its iframe lineage
    (``["main", "player_iframe"]``) so a later consumer knows which
    frame an action occurred in (multi-frame support).
  * **storage deltas** — snapshot local/session storage at start, then
    record deltas; full state at any timestamp is the snapshot plus
    applied deltas (avoids missing within-session changes).
  * **response-body capture policy** — :func:`should_capture_body`
    enforces the 1 MiB cap and the content-type video-exclusion (a
    capture file storing 4 GB of video bytes is pointless; the file URL
    is what matters).
  * **chunking** — :class:`CaptureChunker` rolls events into chunks on a
    time or count boundary with ``continuation_of`` links, so a long
    session streams to bounded-size pieces.

Posture: record + redact + chunk only — detect-and-surface-risk. No
replay, no reconstruction, no evasion. (``--use-system-chrome`` CDP
attach for TLS-fingerprint-coherent capture is an operator/live-test
concern handled by the live driver, not this pure core.)
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .session_capture import SessionCapture, _iso, _now_ms

# rrweb incremental-source numeric ids we care to name (subset of the
# rrweb spec — others pass through as their integer source).
RR_MUTATION = 0
RR_MOUSE_MOVE = 1
RR_MOUSE_INTERACTION = 2
RR_SCROLL = 3
RR_INPUT = 5

# CSS classes that mark an element's content as PII. bd-* are this
# project's annotations; rr-* are rrweb-native (kept for interop with
# rrweb-instrumented pages).
PII_MASK_CLASSES = ("bd-mask", "rr-mask")      # redact text/value
PII_BLOCK_CLASSES = ("bd-block", "rr-block", "rr-ignore")  # drop subtree

# Body-capture policy.
DEFAULT_BODY_CAP_BYTES = 1024 * 1024  # 1 MiB
_VIDEO_CT_PREFIXES = ("video/", "audio/")
_VIDEO_CT_EXTRA = (
    "application/octet-stream",
    "application/vnd.apple.mpegurl",   # HLS playlist is small; allow
)


def _classes_of(node: Dict[str, Any]) -> List[str]:
    raw = (node.get("attributes") or {}).get("class") or node.get("class") or ""
    if isinstance(raw, list):
        return [str(c).lower() for c in raw]
    return [c.lower() for c in str(raw).split()]


def redact_dom_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """Redact a serialized DOM node in place-safe fashion (returns a new
    dict). A node carrying a block class is replaced by an empty
    placeholder subtree; a mask class has its text/value replaced.

    Recurses into ``childNodes`` so a masked container masks descendants.
    """
    classes = _classes_of(node)
    out = dict(node)

    if any(c in classes for c in PII_BLOCK_CLASSES):
        # Drop the subtree entirely — keep only that an element existed.
        out["childNodes"] = []
        out.pop("textContent", None)
        attrs = dict(out.get("attributes") or {})
        if "value" in attrs:
            attrs["value"] = "<blocked>"
        out["attributes"] = attrs
        out["_bd_redacted"] = "block"
        return out

    masked = any(c in classes for c in PII_MASK_CLASSES)
    if masked:
        if "textContent" in out:
            out["textContent"] = "*" * min(len(str(out["textContent"])), 8)
        attrs = dict(out.get("attributes") or {})
        if "value" in attrs:
            attrs["value"] = "*" * min(len(str(attrs["value"])), 8)
        out["attributes"] = attrs
        out["_bd_redacted"] = "mask"

    # Wave 2 (F2) sink-side input-value redaction — independent of PII class.
    # rrweb does NOT mask type=hidden input values, and the class-gated path
    # above only fires on bd-/rr- classes; without this a Turnstile/session
    # token sitting in a hidden field (or any token-shaped input value) reaches
    # disk in the dom_log. Mask the value of any input that is (A) type=hidden,
    # or (B) carries a secret/token-shaped value. The element and its other
    # attributes (id/name/type) are kept, so attribute-based selector derivation
    # is unaffected. Block path returned early above, so blocked subtrees are
    # already gone; class-masked nodes carry _bd_redacted and are skipped.
    if (out.get("tagName") or "").lower() == "input" and "_bd_redacted" not in out:
        attrs = dict(out.get("attributes") or {})
        v = attrs.get("value")
        if isinstance(v, str) and v:
            from .capture_artifact_redact import _value_findings
            if str(attrs.get("type") or "").lower() == "hidden" or _value_findings(v):
                attrs["value"] = "*" * min(len(v), 8)
                out["attributes"] = attrs
                out["_bd_redacted"] = "input_value"

    children = out.get("childNodes")
    if isinstance(children, list):
        out["childNodes"] = [redact_dom_node(c) if isinstance(c, dict) else c
                             for c in children]
    return out


def should_capture_body(content_type: Optional[str], size: Optional[int],
                        *, cap: int = DEFAULT_BODY_CAP_BYTES):
    """Body-capture decision.

    Returns ``(capture: bool, truncate_to: int|None, reason: str)``.
      * video/audio (and octet-stream) bodies → not captured (the URL is
        what synthesis needs, not 4 GB of bytes).
      * bodies over ``cap`` → captured but truncated to ``cap``.
      * otherwise → captured whole.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith(_VIDEO_CT_PREFIXES) or ct in _VIDEO_CT_EXTRA[:1]:
        return False, None, "binary_media_excluded"
    if size is not None and size > cap:
        return True, cap, "truncated_to_cap"
    return True, None, "captured"


class DomCapture(SessionCapture):
    """SessionCapture + DOM/behavioral event stream.

    Adds ``dom_log`` (rrweb-style events) alongside ``network_log``,
    sharing the same millisecond clock so the two streams are
    correlatable. ``frame_path`` defaults to ``["main"]``.
    """

    def __init__(self, *, url: Optional[str] = None, redact: bool = True):
        super().__init__(url=url, redact=redact)
        self.dom_log: List[Dict[str, Any]] = []
        self._dom_seq = 0
        self.dom_snapshots: List[Dict[str, Any]] = []
        self.storage_snapshot: Dict[str, Any] = {}
        self.storage_deltas: List[Dict[str, Any]] = []
        # Track-F Wave A — resolved observational action->effect timeline
        # (selectors = structure; effects = request kinds/counts). Populated by
        # the capture driver from inspect_pick at finish; persisted into the
        # WACZ via to_capture_dict for later template review. No values stored.
        self.action_timeline: List[Dict[str, Any]] = []
        self.page_context["capture_kind"] = "dom+network"

    # ── DOM events ────────────────────────────────────────────────
    def record_dom_event(self, *, source: int, data: Any = None,
                         frame_path: Optional[List[str]] = None,
                         timestamp: Optional[int] = None,
                         is_full_snapshot: bool = False,
                         event_type: Optional[str] = None) -> Dict[str, Any]:
        """Append one rrweb-style DOM event. If the event carries a
        serialized node/snapshot (``data["node"]`` or ``data["adds"]``)
        and redaction is on, PII-annotated nodes are redacted before
        storage.

        ``event_type`` overrides the full/incremental label and is used for
        rrweb **Meta** events (``event_type="meta"``) — viewport (``width``/
        ``height``) plus URL/navigation (``href``) metadata. For a Meta event
        under redaction the URL query/fragment is stripped so no token-in-URL
        reaches disk; the path and viewport are kept. When ``event_type`` is
        ``None`` (the default) the existing full_snapshot/incremental behaviour
        is unchanged."""
        ts = timestamp if timestamp is not None else _now_ms()
        # Meta-only: strip URL query/fragment under redaction (token hygiene).
        if (event_type == "meta" and self.redact and isinstance(data, dict)
                and data.get("href")):
            href = str(data["href"])
            data = {**data, "href": href.split("?", 1)[0].split("#", 1)[0]}
        if self.redact and isinstance(data, dict):
            data = self._redact_event_data(data)
        ev = {
            "dom_seq": self._dom_seq,
            "timestamp": ts,
            "iso": _iso(ts),
            "type": event_type or ("full_snapshot" if is_full_snapshot else "incremental"),
            "source": source,
            "frame_path": list(frame_path or ["main"]),
            "data": data,
        }
        self._dom_seq += 1
        self.dom_log.append(ev)
        return ev

    def _redact_event_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(data)
        if isinstance(out.get("node"), dict):
            out["node"] = redact_dom_node(out["node"])
        # mutation 'adds' carry serialized nodes
        if isinstance(out.get("adds"), list):
            out["adds"] = [
                {**a, "node": redact_dom_node(a["node"])}
                if isinstance(a, dict) and isinstance(a.get("node"), dict)
                else a
                for a in out["adds"]
            ]
        # input events: redact the text value unconditionally if the
        # target is masked (caller marks via data["_masked"]).
        if out.get("_masked") and "text" in out:
            out["text"] = "*" * min(len(str(out["text"])), 8)
        return out

    def record_dom_snapshot(self, image_data_url: str, *,
                            label: Optional[str] = None,
                            timestamp: Optional[int] = None) -> Dict[str, Any]:
        """Append a snapdom DOM->image snapshot (a PNG ``data:`` URL).

        CAVEAT: a rendered raster can contain visible PII as pixels (the
        class-based redaction that protects ``dom_log`` does NOT apply to an
        image). This is therefore opt-in and is never taken automatically on a
        logged-in capture — the caller is responsible for only snapshotting
        non-sensitive views (or DOM where sensitive nodes are already
        blocked). The rrweb event log remains the redacted source of textual
        DOM state."""
        ts = timestamp if timestamp is not None else _now_ms()
        snap = {
            "timestamp": ts,
            "iso": _iso(ts),
            "label": label,
            "image": image_data_url,
        }
        self.dom_snapshots.append(snap)
        return snap

    # ── storage deltas ────────────────────────────────────────────
    def snapshot_storage(self, local: Optional[dict] = None,
                         session: Optional[dict] = None) -> None:
        """Snapshot localStorage + sessionStorage at a point in time.

        Records **key presence + structure**; under redaction every VALUE is
        replaced with the scrub placeholder (web storage routinely holds tokens),
        so no raw value / token / secret is persisted. Keys are kept — they are
        structural and non-sensitive. Redaction is applied **sink-side** here, so
        a caller that passes raw page storage still cannot write a raw value to
        disk (mirrors :meth:`record_storage_delta` and the dom_log redaction)."""
        self.storage_snapshot = {
            "local_storage": self._redact_storage_map(local),
            "session_storage": self._redact_storage_map(session),
            "at": _now_ms(),
        }

    def _redact_storage_map(self, m: Optional[dict]) -> Dict[str, Any]:
        m = dict(m or {})
        if not self.redact:
            return m  # dev/bd_dev_inspect path keeps raw, consistent with the rest
        from .capture_redact import PLACEHOLDER
        return {str(k): PLACEHOLDER for k in m}

    def record_storage_delta(self, *, area: str, key: str,
                             new_value: Optional[str],
                             timestamp: Optional[int] = None) -> Dict[str, Any]:
        """Record a single storage change. Values are dropped to the
        placeholder under redaction — storage routinely holds tokens."""
        from .capture_redact import PLACEHOLDER
        ts = timestamp if timestamp is not None else _now_ms()
        delta = {
            "timestamp": ts,
            "area": area,           # "local" | "session"
            "key": key,
            "new_value": (PLACEHOLDER if (self.redact and new_value is not None)
                          else new_value),
            "removed": new_value is None,
        }
        self.storage_deltas.append(delta)
        return delta

    def storage_at(self, ts: int) -> Dict[str, Dict[str, Any]]:
        """Reconstruct storage state at/just-before ``ts`` from the
        snapshot + applied deltas (deltas with timestamp <= ts)."""
        state = {
            "local": dict(self.storage_snapshot.get("local_storage", {})),
            "session": dict(self.storage_snapshot.get("session_storage", {})),
        }
        for d in self.storage_deltas:
            if d["timestamp"] > ts:
                break
            bucket = state["local"] if d["area"] == "local" else state["session"]
            if d["removed"]:
                bucket.pop(d["key"], None)
            else:
                bucket[d["key"]] = d["new_value"]
        return state

    # ── output ────────────────────────────────────────────────────
    def to_capture_dict(self) -> Dict[str, Any]:
        out = super().to_capture_dict()
        out["dom_log"] = list(self.dom_log)
        out["dom_log_count"] = len(self.dom_log)
        if self.dom_snapshots:
            out["dom_snapshots"] = list(self.dom_snapshots)
            out["dom_snapshot_count"] = len(self.dom_snapshots)
        out["storage_snapshot"] = self.storage_snapshot
        out["storage_deltas"] = list(self.storage_deltas)
        if self.action_timeline:
            out["action_timeline"] = list(self.action_timeline)
            out["action_timeline_count"] = len(self.action_timeline)
        return out

    def record_action(self, entry: Dict[str, Any]) -> None:
        """Append one resolved observational action entry (from
        :mod:`bulk_downloader.inspect_pick`). Structure + kinds/counts only —
        the entry is already redacted; this never stores attribute values."""
        if isinstance(entry, dict):
            self.action_timeline.append(entry)


class CaptureChunker:
    """Rolls a stream of events into bounded chunks.

    A new chunk starts when either the event count reaches
    ``max_events`` or the span since the chunk's first event reaches
    ``max_span_ms``. Each chunk after the first carries
    ``continuation_of`` = the prior chunk's id, so the sequence can be
    reassembled. Streaming: ``add`` returns a finalized chunk (or None)
    so a caller can write-and-discard rather than hold everything.
    """

    def __init__(self, *, max_events: int = 10000,
                 max_span_ms: int = 5 * 60 * 1000,
                 capture_id: Optional[str] = None):
        self.max_events = max_events
        self.max_span_ms = max_span_ms
        self.capture_id = capture_id or f"cap_{int(time.time())}"
        self._chunk_index = 0
        self._buf: List[Dict[str, Any]] = []
        self._first_ts: Optional[int] = None
        self._prev_chunk_id: Optional[str] = None

    def _chunk_id(self) -> str:
        return f"{self.capture_id}_{self._chunk_index:04d}"

    def add(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ts = event.get("timestamp")
        if ts is None:
            ts = _now_ms()
        if self._first_ts is None:
            self._first_ts = ts
        over_count = len(self._buf) >= self.max_events
        over_span = (ts - self._first_ts) >= self.max_span_ms
        finalized = None
        if self._buf and (over_count or over_span):
            finalized = self._flush()
        self._buf.append(event)
        return finalized

    def _flush(self) -> Dict[str, Any]:
        chunk = {
            "chunk_id": self._chunk_id(),
            "continuation_of": self._prev_chunk_id,
            "event_count": len(self._buf),
            "events": self._buf,
        }
        self._prev_chunk_id = chunk["chunk_id"]
        self._chunk_index += 1
        self._buf = []
        self._first_ts = None
        return chunk

    def finalize(self) -> Optional[Dict[str, Any]]:
        """Flush any remaining buffered events as a final chunk."""
        if self._buf:
            return self._flush()
        return None
