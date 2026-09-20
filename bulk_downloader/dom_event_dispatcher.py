"""dom_event_dispatcher -- W3C-compliant DOM event dispatch pipeline.

Row 954 (v3.66.1585): programmatic event dispatch conforming to W3C
UIEvents order for focus, pointer, and click sequences on target
element subtrees. Used by integration testing to exercise WAI-ARIA
form controls with conformant event contracts.

Element model: a node is a plain ``dict``.  It may carry ``"parent"``
(another node dict) -- a dict with no parent is a one-node tree.  Per-node
listeners are registered with :func:`add_event_listener` and stored under
``node["_listeners"]``.  Dispatch follows DOM Events: capture phase
root->parent(target) (capture listeners only), AT_TARGET (capture
listeners then bubble listeners), then bubble phase parent(target)->root
(bubble listeners only, and only when ``record.bubbles``).

Default actions (UIEvents / HTML): a canceled ``mousedown`` suppresses
the focus change (no ``focus``/``focusin``, no ``_focused``); a canceled
``click`` suppresses activation (no ``_clicked``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

NONE = 0
CAPTURING_PHASE = 1
AT_TARGET = 2
BUBBLING_PHASE = 3

_LISTENER_KEY = "_listeners"

_BUBBLING_EVENTS = frozenset({
    "click", "dblclick", "mousedown", "mouseup", "mouseover", "mouseout",
    "mousemove", "pointerdown", "pointerup", "pointerover", "pointerout",
    "pointermove", "focusin", "focusout", "input", "change",
    "keydown", "keyup", "keypress",
})

_NON_BUBBLING_EVENTS = frozenset({
    "focus", "blur", "pointerenter", "pointerleave",
    "mouseenter", "mouseleave", "load", "unload",
})

_CANCELABLE_EVENTS = frozenset({
    "click", "dblclick", "mousedown", "mouseup", "mouseover", "mouseout",
    "mousemove", "pointerdown", "pointerup", "pointerover", "pointerout",
    "pointermove", "keydown", "keyup", "keypress", "input",
})


@dataclass
class EventRecord:
    event_type: str
    bubbles: bool = True
    cancelable: bool = True
    default_prevented: bool = False
    target: Optional[dict] = field(default=None, repr=False)
    current_target: Optional[dict] = field(default=None, repr=False)
    event_phase: int = NONE
    propagation_stopped: bool = False
    immediate_propagation_stopped: bool = False

    def __post_init__(self):
        if self.bubbles is None:
            self.bubbles = self.event_type in _BUBBLING_EVENTS
        if self.cancelable is None:
            self.cancelable = self.event_type in _CANCELABLE_EVENTS

    def prevent_default(self) -> None:
        if self.cancelable:
            self.default_prevented = True

    def stop_propagation(self) -> None:
        self.propagation_stopped = True

    def stop_immediate_propagation(self) -> None:
        self.propagation_stopped = True
        self.immediate_propagation_stopped = True


def _make_record(event_type: str, target: Optional[dict] = None) -> EventRecord:
    return EventRecord(
        event_type=event_type,
        bubbles=event_type in _BUBBLING_EVENTS,
        cancelable=event_type in _CANCELABLE_EVENTS,
        target=target,
    )


Listener = Callable[[EventRecord], None]


def add_event_listener(node: dict, event_type: str, fn: Listener, *, capture: bool = False) -> None:
    """Register ``fn`` on ``node`` for ``event_type`` (capture or bubble phase)."""
    if not isinstance(node, dict):
        raise TypeError(f"node must be a dict, got {type(node).__name__}")
    node.setdefault(_LISTENER_KEY, []).append((event_type, fn, bool(capture)))


def _ancestor_path(target: dict) -> List[dict]:
    """target first, root last; raises on a cyclic parent chain."""
    path: List[dict] = []
    seen = set()
    node: Optional[dict] = target
    while node is not None:
        if id(node) in seen:
            raise ValueError("cyclic parent chain in element tree")
        seen.add(id(node))
        path.append(node)
        node = node.get("parent")
    return path


def _invoke(node: dict, record: EventRecord, phases: Tuple[bool, ...]) -> None:
    """Fire node's listeners for record.event_type; phases lists the capture
    flags to run, in order (capture listeners first at target)."""
    listeners = list(node.get(_LISTENER_KEY, ()))
    for want_capture in phases:
        for etype, fn, cap in listeners:
            if record.immediate_propagation_stopped:
                return
            if etype == record.event_type and cap == want_capture:
                fn(record)


def dispatch_event(target: dict, record: EventRecord, *, listener: Optional[Listener] = None) -> EventRecord:
    """Run the DOM dispatch algorithm for ``record`` at ``target``.

    ``listener`` (legacy) fires at the target, AT_TARGET, for every record.
    """
    path = _ancestor_path(target)
    record.target = target
    for node in reversed(path[1:]):  # capture: root -> parent(target)
        if record.propagation_stopped:
            break
        record.current_target, record.event_phase = node, CAPTURING_PHASE
        _invoke(node, record, (True,))
    if not record.propagation_stopped:
        record.current_target, record.event_phase = target, AT_TARGET
        _invoke(target, record, (True, False))
        if listener is not None and not record.immediate_propagation_stopped:
            listener(record)
    if record.bubbles:
        for node in path[1:]:  # bubble: parent(target) -> root
            if record.propagation_stopped:
                break
            record.current_target, record.event_phase = node, BUBBLING_PHASE
            _invoke(node, record, (False,))
    record.current_target, record.event_phase = None, NONE
    return record


_CLICK_SEQUENCE = [
    "pointerover", "pointerenter", "mouseover", "mouseenter",
    "pointermove", "mousemove",
    "pointerdown", "mousedown",
    "focus", "focusin",
    "pointerup", "mouseup",
    "click",
]

_FOCUS_SEQUENCE = ["focus", "focusin"]

_INPUT_SEQUENCE = ["focus", "focusin", "input", "change"]


class DOMEventDispatcher:
    def dispatch_event(self, target: dict, record: EventRecord, *, listener: Optional[Listener] = None) -> EventRecord:
        return dispatch_event(target, record, listener=listener)

    def _run(self, target: dict, event_type: str, listener: Optional[Listener]) -> EventRecord:
        return dispatch_event(target, _make_record(event_type, target), listener=listener)

    def _focusing_steps(self, target: dict, listener: Optional[Listener]) -> List[EventRecord]:
        """HTML focusing steps: update focus state, then fire focus (non-bubbling)
        followed by focusin (bubbling)."""
        target["_focused"] = True
        return [self._run(target, evt, listener) for evt in _FOCUS_SEQUENCE]

    def dispatch_click(self, target: dict, *, listener: Optional[Listener] = None) -> List[EventRecord]:
        records: List[EventRecord] = []
        focus_suppressed = False
        for evt in _CLICK_SEQUENCE:
            if evt in _FOCUS_SEQUENCE:
                if evt == _FOCUS_SEQUENCE[0] and not focus_suppressed:
                    records.extend(self._focusing_steps(target, listener))
                continue
            rec = self._run(target, evt, listener)
            records.append(rec)
            if evt == "mousedown" and rec.default_prevented:
                focus_suppressed = True  # UIEvents: mousedown default action is the focus change
        clicks = [r for r in records if r.event_type == "click"]
        if clicks and not clicks[-1].default_prevented:
            target["_clicked"] = True  # HTML activation behavior, skipped when click is canceled
        return records

    def dispatch_focus(self, target: dict, *, listener: Optional[Listener] = None) -> List[EventRecord]:
        return self._focusing_steps(target, listener)

    def dispatch_input(self, target: dict, *, value: str = "", listener: Optional[Listener] = None) -> List[EventRecord]:
        records = self._focusing_steps(target, listener)
        target["value"] = value  # value is committed before input/change fire
        records.extend(self._run(target, evt, listener) for evt in _INPUT_SEQUENCE if evt not in _FOCUS_SEQUENCE)
        return records


_DEFAULT_DISPATCHER = DOMEventDispatcher()


def dispatch_click_sequence(target: dict) -> List[EventRecord]:
    return _DEFAULT_DISPATCHER.dispatch_click(target)


def dispatch_focus_sequence(target: dict) -> List[EventRecord]:
    return _DEFAULT_DISPATCHER.dispatch_focus(target)


def dispatch_input_sequence(target: dict, *, value: str = "") -> List[EventRecord]:
    return _DEFAULT_DISPATCHER.dispatch_input(target, value=value)
