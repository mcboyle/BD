"""bulk_downloader.takeover -- MOD-1 remote captcha-takeover security helpers.

Pure, dependency-free primitives shared by the takeover routes (A-2 input
injection). Kept OUT of app.py / the blueprint so they are unit-testable without
Flask and add no import edge to the captcha-api blueprint (coupling-neutral: the
blueprint reaches these via captcha_relay, never directly).

Three primitives, each defending one A-2 threat:
  * validate_input_event  -- T5 out-of-bounds / escape-page: an ALLOWLIST of the
    CDP Input subset the operator may inject. Anything else (navigation,
    arbitrary dispatch, clipboard) is rejected. Fail-closed on malformed input.
  * redact_input_for_audit -- T8 audit: insertText carries the solved-challenge
    text; it must be HASHED (never stored raw) before it reaches audit_log.
  * InputRateBucket        -- T4 flood: a per-sid token bucket bounding the
    input rate so a compromised/abusive viewer cannot flood the CDP channel.
"""
from __future__ import annotations

import hashlib
import time
from typing import Dict


# The ONLY CDP Input event types an operator may inject during a takeover.
# Deliberately excludes navigation (Page.navigate), raw dispatch, clipboard
# (clientCutText), file transfer, and anything not in this set. See A-2 T5/T6.
_ALLOWED_INPUT_TYPES = frozenset({
    "mousePressed",
    "mouseReleased",
    "mouseMoved",
    "keyDown",
    "keyUp",
    "insertText",
})

# Mouse events must carry finite, non-negative integer coordinates. Coordinate
# bounds against the actual viewport are enforced at the route (which knows the
# session geometry); here we reject the structurally invalid (missing/negative/
# non-numeric) so nothing malformed reaches the CDP channel.
_MOUSE_TYPES = frozenset({"mousePressed", "mouseReleased", "mouseMoved"})


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_input_event(event: object) -> bool:
    """True iff ``event`` is a dict describing an allowlisted CDP Input event
    that is structurally well-formed. Fail-closed: any non-dict, unknown type,
    or malformed field returns False (never raises)."""
    if not isinstance(event, dict):
        return False
    etype = event.get("type")
    if etype not in _ALLOWED_INPUT_TYPES:
        return False
    if etype in _MOUSE_TYPES:
        x, y = event.get("x"), event.get("y")
        if not (_is_num(x) and _is_num(y)):
            return False
        if x < 0 or y < 0:
            return False
    if etype == "insertText":
        if not isinstance(event.get("text"), str):
            return False
    if etype in ("keyDown", "keyUp"):
        # A key event needs at least one of key / code / text to be meaningful;
        # reject a bare {"type": "keyDown"} that carries no key.
        if not any(isinstance(event.get(k), str) and event.get(k)
                   for k in ("key", "code", "text")):
            return False
    return True


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def redact_input_for_audit(event: object) -> dict:
    """Return an audit-safe copy of ``event`` with any human-typed text HASHED,
    never carried in the clear. insertText.text and a keyDown/keyUp key/text are
    replaced with a length + short hash. Non-text fields (coords, type) are kept.
    Always returns a dict (fail-closed to a minimal record on malformed input)."""
    if not isinstance(event, dict):
        return {"type": "invalid"}
    safe: Dict[str, object] = {}
    for k, v in event.items():
        if k in ("text", "key") and isinstance(v, str):
            safe[k] = {"len": len(v), "hash": _hash_text(v)}
        else:
            safe[k] = v
    return safe


class InputRateBucket:
    """Per-sid token bucket. ``allow(sid)`` consumes one token, returning False
    when a session's bucket is empty (the flood is throttled). Each sid gets an
    independent bucket lazily; ``refill_per_s`` tokens are added over time up to
    ``capacity``. refill_per_s=0 makes a fixed burst budget (used in tests)."""

    def __init__(self, capacity: int = 30, refill_per_s: float = 10.0,
                 _clock=time.monotonic):
        self.capacity = int(capacity)
        self.refill_per_s = float(refill_per_s)
        self._clock = _clock
        self._state: Dict[str, list] = {}  # sid -> [tokens, last_ts]

    def allow(self, sid: str) -> bool:
        now = self._clock()
        st = self._state.get(sid)
        if st is None:
            st = [float(self.capacity), now]
            self._state[sid] = st
        tokens, last = st
        if self.refill_per_s > 0:
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_s)
        st[1] = now
        if tokens >= 1.0:
            st[0] = tokens - 1.0
            return True
        st[0] = tokens
        return False

    def forget(self, sid: str) -> None:
        """Drop a sid's bucket on teardown (resolved/dismissed) so state does
        not accumulate across sessions."""
        self._state.pop(sid, None)


# ── Per-session screencast/input channel ────────────────────────────────────
#
# Binding-AGNOSTIC transport. The `solving`-state check lives in captcha_relay
# (which owns _pending); this module only moves bytes. A channel holds a bounded
# frame queue (driver -> SSE, newest-wins on overflow so a slow viewer cannot
# back-pressure the capture worker) and an input queue (SSE route -> driver).
import queue as _queue
import threading as _threading

_FRAME_QUEUE_MAX = 4  # small: screencast is newest-wins, not a backlog


class SessionChannel:
    def __init__(self, sid: str, kind: str = "cdp"):
        self.sid = sid
        # MOD-1 C-1: transport tag. "cdp" = SSE screencast + CDP input (the
        # frame/input plumbing below); "vnc" = KasmVNC (uses none of it, but
        # lives in the SAME registry so the cap and the sweep still see it).
        self.kind = kind
        self.frames: "_queue.Queue[str]" = _queue.Queue(maxsize=_FRAME_QUEUE_MAX)
        self.inputs: "_queue.Queue[dict]" = _queue.Queue()
        self.closed = _threading.Event()

    def push_frame(self, frame_b64: str) -> None:
        """Driver pushes a base64 JPEG. Newest-wins: on a full queue, drop the
        oldest rather than block the capture-owning thread."""
        if self.closed.is_set():
            return
        try:
            self.frames.put_nowait(frame_b64)
        except _queue.Full:
            try:
                self.frames.get_nowait()
            except _queue.Empty:
                pass
            try:
                self.frames.put_nowait(frame_b64)
            except _queue.Full:
                pass


_channels: Dict[str, SessionChannel] = {}
_channels_lock = _threading.Lock()
_shared_bucket = InputRateBucket()

# A-5c observability: cumulative count of takeover channels opened since boot.
# Monotonic -- a reconnect that re-opens a channel counts as a new stream (the
# churn signal). bd_takeover_total reads this; bd_takeover_active is the live
# active_channel_count().
_takeover_total = 0


def open_channel(sid: str, kind: str = "cdp") -> SessionChannel:
    """Register (or reuse) a takeover channel for `sid`. MOD-1 C-1: `kind` tags
    the transport ("cdp" | "vnc") so ONE registry spans both -- the concurrency
    cap and the no-orphan sweep count a vnc session even though it uses none of
    the frame/input plumbing. Do not fork the registry (plan 1.3)."""
    global _takeover_total
    with _channels_lock:
        ch = _channels.get(sid)
        if ch is None or ch.closed.is_set():
            ch = SessionChannel(sid, kind=kind)
            _channels[sid] = ch
            _takeover_total += 1  # A-5c: a new stream was opened
        return ch


def get_channel(sid: str):
    with _channels_lock:
        return _channels.get(sid)


def channel_kind(sid: str):
    """MOD-1 C-1: the transport kind of an open channel ("cdp"|"vnc"), or None
    if no such channel. Thread-safe."""
    with _channels_lock:
        ch = _channels.get(sid)
        return ch.kind if ch is not None else None


def close_channel(sid: str) -> None:
    with _channels_lock:
        ch = _channels.pop(sid, None)
    if ch is not None:
        ch.closed.set()
    _shared_bucket.forget(sid)


def push_frame(sid: str, frame_b64: str) -> bool:
    ch = get_channel(sid)
    if ch is None:
        return False
    ch.push_frame(frame_b64)
    return True


def enqueue_input(sid: str, event: dict) -> str:
    """Validate + rate-limit + enqueue a CDP input event for the driver to
    drain. Returns 'ok' | 'invalid' (allowlist/shape) | 'rate' (bucket) |
    'closed' (no live channel). Binding is checked by the caller (which owns
    _pending). Never raises."""
    if not validate_input_event(event):
        return "invalid"
    if not _shared_bucket.allow(sid):
        return "rate"
    ch = get_channel(sid)
    if ch is None or ch.closed.is_set():
        return "closed"
    try:
        ch.inputs.put_nowait(event)
    except _queue.Full:  # pragma: no cover (unbounded queue)
        return "closed"
    return "ok"


def sse_frames(sid: str, heartbeat_s: float = 15.0, idle_max_s: float = 300.0,
               _clock=time.monotonic):
    """Yield SSE-formatted screencast frames for `sid`. Terminates when the
    channel closes OR after `idle_max_s` with no frames (bounded so a leaked
    stream self-reaps; A-5 tightens the idle timeout via config). Emits an
    immediate open comment so a client (and a test) gets a first chunk without
    blocking."""
    yield ": takeover-open\n\n"
    ch = get_channel(sid)
    if ch is None:
        return
    last_frame = _clock()
    last_beat = _clock()
    while not ch.closed.is_set():
        try:
            frame = ch.frames.get(timeout=1.0)
        except _queue.Empty:
            frame = None
        now = _clock()
        if frame is not None:
            last_frame = now
            yield "event: frame\ndata: %s\n\n" % frame
        if now - last_frame >= idle_max_s:
            yield ": takeover-idle-timeout\n\n"
            return
        if now - last_beat >= heartbeat_s:
            yield ": heartbeat %d\n\n" % int(now)
            last_beat = now


def drain_inputs(sid: str, max_n: int = 32) -> list:
    """Pop up to `max_n` queued input events for `sid` (A-4 driver pump). The
    worker thread that owns the solve browser calls this and dispatches each
    event to the CDP session (Playwright thread-affinity). Returns [] when no
    channel or nothing queued. Never raises."""
    ch = get_channel(sid)
    if ch is None:
        return []
    out = []
    for _ in range(max(0, int(max_n))):
        try:
            out.append(ch.inputs.get_nowait())
        except _queue.Empty:
            break
    return out


def active_channel_count(kind: str | None = None) -> int:
    """A-5a: number of open takeover channels == active remote solve sessions.
    Used by the concurrency-cap admission check. MOD-1 C-1: the default (kind
    None) spans BOTH transports so ONE cap governs cdp+vnc (operator attention
    is the shared scarce resource, plan 4.3); pass kind to count one transport.
    Thread-safe."""
    with _channels_lock:
        if kind is None:
            return len(_channels)
        return sum(1 for ch in _channels.values() if ch.kind == kind)


def list_channel_sids(kind: str | None = None) -> list:
    """A-5b: the sids of open channels. The no-orphan sweep's denominator for
    the channel surface -- every open channel, not just the ones the registry
    remembers (A5-R3). MOD-1 C-1: the default (kind None) spans BOTH transports
    so a vnc session is swept like a cdp one; pass kind to snapshot one
    transport. Thread-safe snapshot."""
    with _channels_lock:
        if kind is None:
            return list(_channels.keys())
        return [s for s, ch in _channels.items() if ch.kind == kind]


def takeover_total() -> int:
    """A-5c: cumulative takeover channels opened since boot (monotonic). Backs
    the bd_takeover_total counter."""
    with _channels_lock:
        return _takeover_total


def reset_takeover_total() -> None:
    """Test helper: zero the cumulative counter."""
    global _takeover_total
    with _channels_lock:
        _takeover_total = 0
