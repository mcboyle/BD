"""Row 954: W3C-DOM-EVENT-DISPATCHER-FOR-ACCESSIBLE-UI-COMPONENTS

Tests for bulk_downloader/dom_event_dispatcher.py.
Verifies:
(1) Standard DOM event sequence ordering (focus, pointer, click)
(2) Event propagation and default prevention bubbling
(3) Target state change verification
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    import bulk_downloader.dom_event_dispatcher as _mod
    from bulk_downloader.dom_event_dispatcher import (
        AT_TARGET,
        BUBBLING_PHASE,
        CAPTURING_PHASE,
        DOMEventDispatcher,
        EventRecord,
        add_event_listener,
        dispatch_click_sequence,
        dispatch_event,
        dispatch_focus_sequence,
        dispatch_input_sequence,
    )
except ImportError:
    _mod = None
    AT_TARGET = BUBBLING_PHASE = CAPTURING_PHASE = None
    DOMEventDispatcher = None
    EventRecord = None
    add_event_listener = None
    dispatch_click_sequence = None
    dispatch_event = None
    dispatch_focus_sequence = None
    dispatch_input_sequence = None

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Behavioral RED
# ---------------------------------------------------------------------------

def test_behavioral_red_no_conformant_event_dispatch():
    """Behavioral RED: without the dispatcher, there is no W3C-compliant
    event sequence for programmatic interaction with form controls."""
    w3c_click_events = ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]
    assert len(w3c_click_events) == 5

    if dispatch_click_sequence is None:
        dispatched = []
        assert len(dispatched) == 5, (
            f"BEHAVIORAL RED: no conformant event dispatch pipeline exists; "
            f"0 of 5 W3C click-sequence events dispatched (assert 0 == 5)"
        )

    target = _make_target("button", {"id": "submit-btn"})
    records = dispatch_click_sequence(target)
    event_types = [r.event_type for r in records]
    for evt in w3c_click_events:
        assert evt in event_types, f"Missing {evt} in click sequence"


# ---------------------------------------------------------------------------
# (1) Standard DOM event sequence ordering
# ---------------------------------------------------------------------------

def test_click_sequence_order():
    """Click sequence follows W3C UIEvents order."""
    assert dispatch_click_sequence is not None
    target = _make_target("button", {"id": "btn1"})
    records = dispatch_click_sequence(target)
    types = [r.event_type for r in records]

    expected_order = ["pointerover", "pointerenter", "mouseover", "mouseenter",
                      "pointermove", "mousemove",
                      "pointerdown", "mousedown",
                      "focus", "focusin",
                      "pointerup", "mouseup",
                      "click"]
    for i, evt in enumerate(expected_order):
        assert evt in types, f"Missing {evt}"
    # Verify relative ordering of critical events
    assert types.index("pointerdown") < types.index("mousedown")
    assert types.index("mousedown") < types.index("focus")
    assert types.index("pointerup") < types.index("mouseup")
    assert types.index("mouseup") < types.index("click")


def test_focus_sequence_order():
    """Focus sequence dispatches focusin and focus in correct order."""
    assert dispatch_focus_sequence is not None
    target = _make_target("input", {"type": "text", "name": "email"})
    records = dispatch_focus_sequence(target)
    types = [r.event_type for r in records]
    assert types == ["focus", "focusin"], types
    assert types.index("focus") < types.index("focusin")


def test_input_sequence_dispatches_key_events():
    """Input sequence dispatches input and change events."""
    assert dispatch_input_sequence is not None
    target = _make_target("input", {"type": "text", "name": "search"})
    records = dispatch_input_sequence(target, value="hello")
    types = [r.event_type for r in records]
    assert "input" in types
    assert "change" in types


def test_focus_precedes_focusin_in_every_sequence():
    """UIEvents 5.2.2: focus (non-bubbling) fires before focusin (bubbling)
    in the click, focus, and input sequences. Kills a focus/focusin swap."""
    assert dispatch_click_sequence is not None
    seqs = {
        "click": dispatch_click_sequence(_make_target("button", {"id": "f1"})),
        "focus": dispatch_focus_sequence(_make_target("input", {"name": "f2"})),
        "input": dispatch_input_sequence(_make_target("input", {"name": "f3"}), value="x"),
    }
    assert len(seqs) == 3
    checked = 0
    for name, records in seqs.items():
        types = [r.event_type for r in records]
        assert types.count("focus") == 1 and types.count("focusin") == 1, (name, types)
        assert types.index("focus") + 1 == types.index("focusin"), (name, types)
        checked += 1
    assert checked == 3
    click_types = [r.event_type for r in seqs["click"]]
    assert click_types == _CLICK_ORDER, click_types


# ---------------------------------------------------------------------------
# (2) Event propagation and default prevention bubbling
# ---------------------------------------------------------------------------

def test_event_bubbling_propagation():
    """Events that bubble have bubbles=True; non-bubbling events don't."""
    assert dispatch_click_sequence is not None
    target = _make_target("a", {"href": "/page2"})
    records = dispatch_click_sequence(target)
    bubbling_events = {"click", "mousedown", "mouseup", "pointerdown",
                       "pointerup", "mouseover", "mousemove", "pointermove",
                       "pointerover", "focusin"}
    non_bubbling = {"focus", "pointerenter", "mouseenter"}

    for r in records:
        if r.event_type in bubbling_events:
            assert r.bubbles is True, f"{r.event_type} must bubble"
        if r.event_type in non_bubbling:
            assert r.bubbles is False, f"{r.event_type} must not bubble"


def test_default_prevention_cancelable():
    """Cancelable events can have their default prevented."""
    assert DOMEventDispatcher is not None
    dispatcher = DOMEventDispatcher()
    target = _make_target("button", {"id": "del-btn"})

    prevented_events = []
    def on_event(record):
        if record.event_type == "click":
            record.prevent_default()
            prevented_events.append(record)

    records = dispatcher.dispatch_click(target, listener=on_event)
    assert len(prevented_events) == 1
    assert prevented_events[0].default_prevented is True


def test_non_cancelable_event_ignores_prevent():
    """Non-cancelable events ignore preventDefault calls."""
    assert DOMEventDispatcher is not None
    dispatcher = DOMEventDispatcher()
    target = _make_target("input", {"type": "text"})

    records = dispatcher.dispatch_focus(target)
    focus_records = [r for r in records if r.event_type == "focus"]
    assert len(focus_records) >= 1
    focus_records[0].prevent_default()
    assert focus_records[0].default_prevented is False


def test_ancestor_listener_receives_bubbling_click_not_focus():
    """(2) Propagation: a bubbling 'click' on a child reaches an ancestor
    listener in BUBBLING_PHASE with current_target == ancestor; the
    non-bubbling 'focus' never reaches the ancestor."""
    assert add_event_listener is not None
    root, form, button = _make_tree()
    seen = []
    for evt in ("click", "focus", "focusin"):
        add_event_listener(root, evt, lambda r: seen.append(
            (r.event_type, r.current_target is root, r.target is button, r.event_phase)))
    assert len(root["_listeners"]) == 3

    records = dispatch_click_sequence(button)
    assert len(records) == 13
    assert seen == [
        ("focusin", True, True, BUBBLING_PHASE),
        ("click", True, True, BUBBLING_PHASE),
    ], seen
    assert [t for t, *_ in seen].count("focus") == 0
    # after dispatch the record is reset per DOM spec
    click = [r for r in records if r.event_type == "click"][0]
    assert click.current_target is None and click.event_phase == 0


def test_capture_then_target_then_bubble_exact_order():
    """(2) Propagation: capture root->target, AT_TARGET (capture listeners
    then bubble listeners), then bubble target->root."""
    assert dispatch_event is not None
    root, mid, leaf = _make_tree()
    calls = []
    for name, node in (("root", root), ("mid", mid), ("leaf", leaf)):
        for cap in (False, True):
            add_event_listener(node, "click", (lambda n, c: lambda r: calls.append((n, c, r.event_phase)))(name, cap), capture=cap)
    assert sum(len(n["_listeners"]) for n in (root, mid, leaf)) == 6

    rec = dispatch_event(leaf, EventRecord("click", bubbles=True, cancelable=True))
    assert rec.target is leaf
    assert calls == [
        ("root", True, CAPTURING_PHASE),
        ("mid", True, CAPTURING_PHASE),
        ("leaf", True, AT_TARGET),
        ("leaf", False, AT_TARGET),
        ("mid", False, BUBBLING_PHASE),
        ("root", False, BUBBLING_PHASE),
    ], calls
    assert len(calls) == 6

    calls.clear()
    dispatch_event(leaf, EventRecord("focus", bubbles=False, cancelable=False))
    assert calls == [], "non-bubbling focus must not fire click listeners"


def test_stop_propagation_at_target_stops_ancestors():
    """(2) Propagation: stopPropagation at the target lets the remaining
    target listeners run but no ancestor bubble listener; stopImmediate-
    Propagation also halts the remaining target listeners; stopPropagation
    in an ancestor's capture listener never reaches the target."""
    assert dispatch_event is not None
    root, mid, leaf = _make_tree()
    calls = []
    add_event_listener(leaf, "click", lambda r: (r.stop_propagation(), calls.append("leaf1")))
    add_event_listener(leaf, "click", lambda r: calls.append("leaf2"))
    add_event_listener(mid, "click", lambda r: calls.append("mid-bubble"))
    add_event_listener(root, "click", lambda r: calls.append("root-bubble"))
    rec = dispatch_event(leaf, EventRecord("click"))
    assert rec.propagation_stopped is True
    assert calls == ["leaf1", "leaf2"], calls
    assert calls.count("mid-bubble") == 0 and calls.count("root-bubble") == 0

    calls.clear()
    leaf["_listeners"].insert(0, ("click", lambda r: (r.stop_immediate_propagation(), calls.append("leaf0")), False))
    dispatch_event(leaf, EventRecord("click"))
    assert calls == ["leaf0"], calls

    calls.clear()
    add_event_listener(root, "click", lambda r: (r.stop_propagation(), calls.append("root-capture")), capture=True)
    legacy = []
    dispatch_event(leaf, EventRecord("click"), listener=lambda r: legacy.append(r.event_type))
    assert calls == ["root-capture"], calls
    assert legacy == []


def test_click_prevent_default_skips_activation():
    """(2)+(3) P1: preventDefault on 'click' cancels the HTML activation
    behavior -- the target must NOT be marked _clicked; the full 13-event
    sequence still dispatches and the focus change still happens."""
    assert DOMEventDispatcher is not None
    dispatcher = DOMEventDispatcher()
    target = _make_target("button", {"id": "p1"})
    prevented = []

    def on_event(record):
        if record.event_type == "click":
            record.prevent_default()
            prevented.append(record)

    records = dispatcher.dispatch_click(target, listener=on_event)
    assert len(records) == 13
    assert len(prevented) == 1 and prevented[0].default_prevented is True
    assert target.get("_clicked") is None, target
    assert target.get("_focused") is True

    ctrl = _make_target("button", {"id": "p1-control"})
    dispatcher.dispatch_click(ctrl)
    assert ctrl.get("_clicked") is True


def test_mousedown_prevent_default_suppresses_focus_change():
    """(2)+(3) P2: preventDefault on 'mousedown' cancels its default action
    (the focus change): no focus/focusin records, target not _focused,
    and the rest of the pointer sequence (pointerup, mouseup, click) continues."""
    assert DOMEventDispatcher is not None
    dispatcher = DOMEventDispatcher()
    root, _form, button = _make_tree()
    root_focusin = []
    add_event_listener(root, "focusin", lambda r: root_focusin.append(r))

    def on_event(record):
        if record.event_type == "mousedown":
            record.prevent_default()

    records = dispatcher.dispatch_click(button, listener=on_event)
    types = [r.event_type for r in records]
    assert len(records) == 11, types
    assert types == [t for t in _CLICK_ORDER if t not in ("focus", "focusin")], types
    assert types.count("focus") == 0 and types.count("focusin") == 0
    assert button.get("_focused") is None, button
    assert button.get("_clicked") is True
    assert len(root_focusin) == 0


def test_legacy_listener_sees_every_click_record_at_target():
    """The legacy 'listener' kwarg fires once per record, AT_TARGET, with
    current_target == target, for all 13 events (not just 'click')."""
    assert DOMEventDispatcher is not None
    root, _form, button = _make_tree()
    seen = []
    DOMEventDispatcher().dispatch_click(
        button, listener=lambda r: seen.append((r.event_type, r.current_target is button, r.event_phase)))
    assert len(seen) == 13
    assert [t for t, *_ in seen] == _CLICK_ORDER
    assert all(is_target and phase == AT_TARGET for _, is_target, phase in seen), seen


# ---------------------------------------------------------------------------
# (3) Target state change verification
# ---------------------------------------------------------------------------

def test_target_state_updated_after_focus():
    """Target element state reflects focus after dispatch."""
    assert DOMEventDispatcher is not None
    dispatcher = DOMEventDispatcher()
    target = _make_target("input", {"type": "text", "name": "user"})
    assert target.get("_focused") is not True
    dispatcher.dispatch_focus(target)
    assert target.get("_focused") is True


def test_target_state_updated_after_input():
    """Target element value updated after input dispatch."""
    assert DOMEventDispatcher is not None
    dispatcher = DOMEventDispatcher()
    target = _make_target("input", {"type": "text", "name": "q", "value": ""})
    dispatcher.dispatch_input(target, value="search term")
    assert target.get("value") == "search term"


def test_target_click_state():
    """Target records click activation."""
    assert DOMEventDispatcher is not None
    dispatcher = DOMEventDispatcher()
    target = _make_target("button", {"id": "go"})
    dispatcher.dispatch_click(target)
    assert target.get("_clicked") is True


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------

def _assert_pointerdown_precedes_click(types: list) -> None:
    """Order oracle shared by the positive and negative paths."""
    assert "pointerdown" in types and "click" in types, types
    assert types.index("pointerdown") < types.index("click"), (
        f"ORDER VIOLATION: pointerdown must precede click, got {types}")


def test_negative_control_event_sequence_exact_counts(monkeypatch):
    """Negative control: the SAME instrument (dispatch_click_sequence + the
    order oracle) passes on 3 real targets and fails for the intended reason
    when the dispatcher is driven with a deliberately wrong sequence."""
    assert dispatch_click_sequence is not None and _mod is not None

    correct_targets = [_make_target("button", {"id": f"btn-{i}"}) for i in range(3)]
    assert len(correct_targets) == 3
    verified_count = 0
    for t in correct_targets:
        records = dispatch_click_sequence(t)
        assert len(records) == 13
        _assert_pointerdown_precedes_click([r.event_type for r in records])
        verified_count += 1
    assert verified_count == 3, f"Expected 3 verified, got {verified_count}"

    # Deliberately wrong sequence: swap pointerdown and click in the real
    # dispatcher's click table, then run the real instrument on it.
    wrong = list(_mod._CLICK_SEQUENCE)
    i_pd, i_ck = wrong.index("pointerdown"), wrong.index("click")
    wrong[i_pd], wrong[i_ck] = wrong[i_ck], wrong[i_pd]
    assert len(wrong) == 13 and wrong.index("click") < wrong.index("pointerdown")
    monkeypatch.setattr(_mod, "_CLICK_SEQUENCE", wrong)

    bad_target = _make_target("button", {"id": "bad"})
    bad_records = dispatch_click_sequence(bad_target)
    assert len(bad_records) == 13
    with pytest.raises(AssertionError, match=r"ORDER VIOLATION: pointerdown must precede click"):
        _assert_pointerdown_precedes_click([r.event_type for r in bad_records])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLICK_ORDER = ["pointerover", "pointerenter", "mouseover", "mouseenter",
                "pointermove", "mousemove",
                "pointerdown", "mousedown",
                "focus", "focusin",
                "pointerup", "mouseup",
                "click"]


def _make_target(tag: str, attrs: dict) -> dict:
    return {"tag": tag, **attrs}


def _make_tree() -> tuple:
    """root > form > button; returns (root, form, button)."""
    root = _make_target("div", {"id": "root"})
    form = _make_target("form", {"id": "form", "parent": root})
    button = _make_target("button", {"id": "btn", "parent": form})
    assert button["parent"] is form and form["parent"] is root
    return root, form, button
