"""bulk_downloader.operator_relay -- REMOTE-OPERATOR-VERIFICATION-RELAY-AND-INTERACTIVE-BRIDGE.

Row 937: Bridges browser canvas viewports to local web console (:6080),
allowing operators 1-click manual verification and resuming headless
automation upon success. 0 site logins touched (Rule 21).

Acceptance criteria:
(1) session handover to relay bridge
(2) remote input pass-through
(3) automated resumption upon clearance
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

DEFAULT_CONSOLE_PORT = 6080

_ALLOWED_INPUT_TYPES: Set[str] = {
    "mousePressed",
    "mouseReleased",
    "mouseMoved",
    "keyDown",
    "keyUp",
    "insertText",
}

_MOUSE_TYPES: Set[str] = {
    "mousePressed",
    "mouseReleased",
    "mouseMoved",
}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_input_event(event: Any) -> bool:
    """Fail-closed validation of CDP/canvas input event structures."""
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
        if not any(isinstance(event.get(k), str) and event.get(k) for k in ("key", "code", "text")):
            return False
    return True


class InputRateBucket:
    """Per-session token bucket bounding remote operator input rate."""

    def __init__(
        self,
        capacity: int = 30,
        refill_per_s: float = 10.0,
        _clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = int(capacity)
        self.refill_per_s = float(refill_per_s)
        self._clock = _clock
        self._state: Dict[str, list] = {}

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
        self._state.pop(sid, None)


class RelayStatus(str, Enum):
    BRIDGED = "bridged"
    CLEARED = "cleared"
    RESUMED = "resumed"
    DISMISSED = "dismissed"


@dataclass
class RelaySession:
    session_id: str
    url: str
    viewport: Tuple[int, int] = (1280, 720)
    site_id: Optional[str] = None
    status: RelayStatus = RelayStatus.BRIDGED
    created_at: float = field(default_factory=time.time)
    cleared_at: Optional[float] = None
    resumed_at: Optional[float] = None
    clearance_token: Optional[str] = None
    dismissal_reason: Optional[str] = None
    input_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    console_url: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == RelayStatus.BRIDGED


class OperatorRelayBridge:
    """Relay bridge coordinating browser session handover, operator remote input,
    and automated resumption of headless automation upon challenge clearance."""

    def __init__(
        self,
        port: int = DEFAULT_CONSOLE_PORT,
        host: str = "127.0.0.1",
        rate_limit_burst: int = 30,
        rate_limit_refill: float = 10.0,
    ) -> None:
        self.port = port
        self.host = host
        self.rate_limit_burst = rate_limit_burst
        self.rate_limit_refill = rate_limit_refill
        self._sessions: Dict[str, RelaySession] = {}
        self._input_sinks: Dict[str, Callable[[dict], None]] = {}
        self._resume_callbacks: Dict[str, Callable[[RelaySession], None]] = {}
        self._rate_buckets: Dict[str, InputRateBucket] = {}
        self._lock = threading.RLock()

    def _resolve_console_url(self, session_id: str) -> str:
        return f"http://{self.host}:{self.port}/?session={session_id}"

    def handover(
        self,
        session_id: str,
        url: str,
        viewport: Tuple[int, int] = (1280, 720),
        site_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        on_resume: Optional[Callable[[RelaySession], None]] = None,
    ) -> RelaySession:
        """Hand over an active browser verification challenge to the relay bridge."""
        with self._lock:
            console_url = self._resolve_console_url(session_id)
            session = RelaySession(
                session_id=session_id,
                url=url,
                viewport=viewport,
                site_id=site_id,
                status=RelayStatus.BRIDGED,
                metadata=dict(metadata or {}),
                console_url=console_url,
            )
            self._sessions[session_id] = session
            if on_resume is not None:
                self._resume_callbacks[session_id] = on_resume
            self._rate_buckets[session_id] = InputRateBucket(
                capacity=self.rate_limit_burst,
                refill_per_s=self.rate_limit_refill,
            )
            return session

    def get_session(self, session_id: str) -> Optional[RelaySession]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_active_sessions(self) -> List[RelaySession]:
        with self._lock:
            return [s for s in self._sessions.values() if s.is_active]

    def list_all_sessions(self) -> List[RelaySession]:
        with self._lock:
            return list(self._sessions.values())

    def register_input_sink(self, session_id: str, sink: Callable[[dict], None]) -> None:
        with self._lock:
            self._input_sinks[session_id] = sink

    def dispatch_input(self, session_id: str, event: Any) -> bool:
        """Validate and dispatch remote operator input to the active session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not session.is_active:
                return False

            # 1. Structural security validation
            if not validate_input_event(event):
                return False

            # 2. Viewport coordinate boundary validation
            if event.get("type") in _MOUSE_TYPES:
                x = event.get("x")
                y = event.get("y")
                max_w, max_h = session.viewport
                if x < 0 or x > max_w or y < 0 or y > max_h:
                    return False

            # 3. Token-bucket rate limiting
            bucket = self._rate_buckets.get(session_id)
            if bucket is not None and not bucket.allow(session_id):
                return False

            # 4. Dispatch to registered sink
            sink = self._input_sinks.get(session_id)
            if sink is not None:
                try:
                    sink(event)
                except Exception:
                    return False

            session.input_count += 1
            return True

    def mark_cleared(
        self,
        session_id: str,
        clearance_token: str = "manual_verification_cleared",
        auto_resume: bool = True,
    ) -> bool:
        """Mark challenge cleared by operator and optionally trigger automated resumption."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not session.is_active:
                return False

            session.status = RelayStatus.CLEARED
            session.cleared_at = time.time()
            session.clearance_token = clearance_token

            if auto_resume:
                return self.resume_automation(session_id)
            return True

    def resume_automation(self, session_id: str) -> bool:
        """Automated resumption of headless automation upon challenge clearance."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if session.status not in (RelayStatus.BRIDGED, RelayStatus.CLEARED):
                return False

            session.status = RelayStatus.RESUMED
            session.resumed_at = time.time()
            callback = self._resume_callbacks.get(session_id)

        # Fire callback outside lock to prevent re-entrancy deadlocks
        if callback is not None:
            try:
                callback(session)
            except Exception:
                pass
        return True

    def dismiss(self, session_id: str, reason: str = "operator_dismissed") -> bool:
        """Operator declines/dismisses challenge; closes session without resumption."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not session.is_active:
                return False

            session.status = RelayStatus.DISMISSED
            session.dismissal_reason = reason
            return True
