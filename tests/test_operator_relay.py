"""Row 937: REMOTE-OPERATOR-VERIFICATION-RELAY-AND-INTERACTIVE-BRIDGE.

Acceptance criteria:
(1) session handover to relay bridge
(2) remote input pass-through
(3) automated resumption upon clearance
"""
from __future__ import annotations

import threading
import time
import pytest

from bulk_downloader.operator_relay import (
    OperatorRelayBridge,
    RelaySession,
    RelayStatus,
    DEFAULT_CONSOLE_PORT,
)

BD_GATE_SCOPE = "module"


def test_session_handover_to_relay_bridge():
    bridge = OperatorRelayBridge(port=6080)
    session = bridge.handover(
        session_id="sess-test-01",
        url="https://challenges.example.org/verify",
        viewport=(1280, 720),
        site_id="example_site",
    )

    assert isinstance(session, RelaySession)
    assert session.session_id == "sess-test-01"
    assert session.url == "https://challenges.example.org/verify"
    assert session.viewport == (1280, 720)
    assert session.status == RelayStatus.BRIDGED
    assert session.console_url == f"http://127.0.0.1:{DEFAULT_CONSOLE_PORT}/?session=sess-test-01"
    
    # Exact-count assertion
    active = bridge.list_active_sessions()
    assert len(active) == 1
    assert active[0].session_id == "sess-test-01"


def test_remote_input_pass_through():
    bridge = OperatorRelayBridge(port=6080)
    bridge.handover(
        session_id="sess-test-02",
        url="https://challenges.example.org/verify",
        viewport=(1280, 720),
    )

    dispatched_events = []
    bridge.register_input_sink("sess-test-02", lambda ev: dispatched_events.append(ev))

    valid_events = [
        {"type": "mouseMoved", "x": 100, "y": 200},
        {"type": "mousePressed", "x": 100, "y": 200, "button": "left"},
        {"type": "mouseReleased", "x": 100, "y": 200, "button": "left"},
        {"type": "keyDown", "key": "Tab"},
        {"type": "keyUp", "key": "Tab"},
        {"type": "insertText", "text": "481516"},
    ]

    for ev in valid_events:
        ok = bridge.dispatch_input("sess-test-02", ev)
        assert ok is True

    # Exact-count assertion: all 6 events forwarded
    assert len(dispatched_events) == 6
    session = bridge.get_session("sess-test-02")
    assert session is not None
    assert session.input_count == 6


def test_automated_resumption_upon_clearance():
    bridge = OperatorRelayBridge(port=6080)
    resumption_signal = threading.Event()
    
    def on_resume(session: RelaySession):
        resumption_signal.set()

    bridge.handover(
        session_id="sess-test-03",
        url="https://challenges.example.org/verify",
        on_resume=on_resume,
    )

    session = bridge.get_session("sess-test-03")
    assert session is not None
    assert session.status == RelayStatus.BRIDGED
    assert not resumption_signal.is_set()

    # Operator completes 1-click verification clearance
    cleared = bridge.mark_cleared("sess-test-03", clearance_token="manual-pass-token")
    assert cleared is True
    assert resumption_signal.is_set()

    session_after = bridge.get_session("sess-test-03")
    assert session_after is not None
    assert session_after.status == RelayStatus.RESUMED
    assert session_after.cleared_at is not None
    assert session_after.clearance_token == "manual-pass-token"

    # Bridged sessions count drops to 0 after resumption
    assert len(bridge.list_active_sessions()) == 0


def test_negative_control_input_validation_and_bounds():
    bridge = OperatorRelayBridge(port=6080)
    bridge.handover(
        session_id="sess-test-04",
        url="https://challenges.example.org/verify",
        viewport=(800, 600),
    )

    dispatched_events = []
    bridge.register_input_sink("sess-test-04", lambda ev: dispatched_events.append(ev))

    invalid_events = [
        {"type": "mouseMoved", "x": 900, "y": 200},  # X out of bounds (> 800)
        {"type": "mousePressed", "x": -5, "y": 100},  # negative X
        {"type": "mouseMoved", "x": 100, "y": 650},  # Y out of bounds (> 600)
        {"type": "Page.navigate", "url": "evil.com"},  # disallowed type
        {"type": "executeScript", "script": "alert(1)"},  # disallowed type
        "not-a-dict",  # malformed
    ]

    rejected_count = 0
    for ev in invalid_events:
        ok = bridge.dispatch_input("sess-test-04", ev)
        if not ok:
            rejected_count += 1

    # Exact-count assertion: all 6 invalid events rejected
    assert rejected_count == 6
    assert len(dispatched_events) == 0


def test_negative_control_unknown_session():
    bridge = OperatorRelayBridge(port=6080)

    # Dispatch to unknown session
    assert bridge.dispatch_input("sess-unknown", {"type": "mouseMoved", "x": 10, "y": 10}) is False
    
    # Mark cleared on unknown session
    assert bridge.mark_cleared("sess-unknown") is False
    
    # Non-existent session lookup
    assert bridge.get_session("sess-unknown") is None


def test_input_rate_limiting():
    # Capacity of 5 tokens
    bridge = OperatorRelayBridge(port=6080, rate_limit_burst=5, rate_limit_refill=0.0)
    bridge.handover(
        session_id="sess-test-05",
        url="https://challenges.example.org/verify",
        viewport=(1280, 720),
    )

    results = []
    for i in range(10):
        ev = {"type": "mouseMoved", "x": 10 + i, "y": 10}
        results.append(bridge.dispatch_input("sess-test-05", ev))

    # Exact counts: exactly 5 accepted, exactly 5 throttled
    assert results.count(True) == 5
    assert results.count(False) == 5


def test_session_dismissal():
    bridge = OperatorRelayBridge(port=6080)
    resumption_signal = threading.Event()

    bridge.handover(
        session_id="sess-test-06",
        url="https://challenges.example.org/verify",
        on_resume=lambda s: resumption_signal.set(),
    )

    dismissed = bridge.dismiss("sess-test-06", reason="operator_declined")
    assert dismissed is True
    assert not resumption_signal.is_set()

    session = bridge.get_session("sess-test-06")
    assert session is not None
    assert session.status == RelayStatus.DISMISSED
    assert session.dismissal_reason == "operator_declined"
    assert len(bridge.list_active_sessions()) == 0


def test_custom_console_port_configuration():
    bridge = OperatorRelayBridge(port=7080)
    session = bridge.handover(
        session_id="sess-test-07",
        url="https://challenges.example.org/verify",
    )
    assert session.console_url == "http://127.0.0.1:7080/?session=sess-test-07"
