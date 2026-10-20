"""Synthetic User Input Scheduling for Web Forms.

Schedules natural, human-like cadence for web form interactions, keystroke
variance, inter-field delays, and mouse focus transitions to prevent anti-bot
classification and heuristic timing fingerprinting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import random
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Union


class InputProfile(str, Enum):
    """Cadence profiles for synthetic user interactions."""
    HUMAN_NATURAL = "human_natural"
    CAREFUL_ENTRY = "careful_entry"
    RAPID_BURST = "rapid_burst"
    STEALTH_JITTER = "stealth_jitter"


class InputActionType(str, Enum):
    """Types of synthetic interaction actions."""
    CLICK = "click"
    TYPE_CHAR = "type_char"
    PAUSE = "pause"
    PRESS_KEY = "press_key"
    CLEAR = "clear"


@dataclass
class InputAction:
    """Individual scheduled interaction step."""
    action_type: InputActionType
    selector: Optional[str] = None
    char: Optional[str] = None
    key: Optional[str] = None
    delay_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type.value if isinstance(self.action_type, InputActionType) else str(self.action_type),
            "selector": self.selector,
            "char": self.char,
            "key": self.key,
            "delay_ms": round(self.delay_ms, 2),
            "metadata": self.metadata,
        }


@dataclass
class InputSchedule:
    """Sequential interaction schedule for form filling and navigation."""
    actions: List[InputAction] = field(default_factory=list)
    profile: InputProfile = InputProfile.HUMAN_NATURAL
    total_estimated_duration_ms: float = 0.0
    field_count: int = 0
    keystroke_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile.value if isinstance(self.profile, InputProfile) else str(self.profile),
            "total_estimated_duration_ms": round(self.total_estimated_duration_ms, 2),
            "field_count": self.field_count,
            "keystroke_count": self.keystroke_count,
            "actions": [a.to_dict() for a in self.actions],
        }


BOUND_FIELD_LOST = "credential entry aborted: bound field lost"


class BoundFieldLost(RuntimeError):
    """BH1-49 r2: the checked element can no longer be proven to be the one
    the next key would reach.  The whole credential entry stops: no retry,
    no re-resolve, no Locator fallback."""

    def __init__(self, check: str, detail: Any = ""):
        self.check = check
        tail = f": {str(detail)[:120]}" if detail else ""
        super().__init__(f"{BOUND_FIELD_LOST} ({check}{tail})")


def _origin(url: str) -> tuple:
    from urllib.parse import urlsplit
    parts = urlsplit(url or "")
    return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port)


class BoundField:
    """BH1-49: the checked element as an ElementHandle, resolved ONCE.

    A Locator re-resolves on every action, so its keys followed a navigation
    or an nth() shift (r1 SEC F1/F2).  A handle stays on its element and dies
    with its document.  r4: no keys at all.  Keystrokes are routed by focus,
    and any check-then-press leaves a window in which page script moves focus
    (r3 SEC A1), so each character is written by ONE in-realm evaluate on the
    handle: the element is connected, in its realm's document of the recorded
    origin, holds exactly the characters written so far, and then gets the
    next one through the native value setter plus an 'input' event.  Checks
    and write share one JS turn, so no page script runs between them (the
    field's own listeners run after the write, and the value is read back
    after them).  Nothing depends on focus (so a field in a shadow root is
    no different, r3 SEC S1).  Events are
    isTrusted=false, the accepted trade-off.  Every failure, the handle's own
    errors included, raises BoundFieldLost."""

    # One call per write: checks and write share a single JS turn.  Returns
    # {lost: check, detail} or the value read back after the event.
    _WRITE = """(el, a) => {
        const lost = (check, detail) => ({lost: check, detail: detail === undefined ? '' : String(detail)});
        if (!el.isConnected) return lost('isConnected');
        if (el.ownerDocument !== document) return lost('ownerDocument');
        if (location.origin !== a.origin) return lost('document origin', location.origin);
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
            : el instanceof HTMLInputElement ? HTMLInputElement.prototype : null;
        if (!proto) return lost('not a text field', el.tagName);
        if (a.before !== null && el.value !== a.before) return lost('value', 'changed under the entry');
        if (a.value !== null && el.value !== a.value) {  // clearing an empty field fires nothing, as Delete would not
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, a.value);
            try { el.setSelectionRange(a.value.length, a.value.length); } catch (e) {}
            el.dispatchEvent(new InputEvent('input', {data: a.data, inputType: a.inputType, bubbles: true}));
        }
        if (a.value === null || a.change) el.dispatchEvent(new Event('change', {bubbles: true}));
        if (!el.isConnected) return lost('isConnected');
        return {value: el.value};
    }"""
    def _sleep(self, seconds: float) -> None:
        """The scheduled delay as the page's own wait, as keyboard.type(delay)
        held the key: time.sleep would also stop this client from taking
        the page's events (a navigation the page started stays unseen)."""
        wait = getattr(self.page, "wait_for_timeout", None)
        if wait is None:
            time.sleep(seconds)
            return
        try:
            wait(seconds * 1000.0)
        except Exception as e:
            raise BoundFieldLost("handle", e)

    def __init__(self, page: Any, handle: Any):
        self.page = page
        self.handle = handle
        self.written = ""
        self.length: Optional[int] = None  # the entry's length, when the caller knows it
        self.changed = False
        try:
            self.frame = handle.owner_frame()
            self.frame_origin = _origin(self.frame.url)
            self.main_origin = _origin(page.main_frame.url)
            state = handle.evaluate("el => ({connected: el.isConnected,"
                                    " sameDoc: el.ownerDocument === document, origin: location.origin})")
        except Exception as e:
            raise BoundFieldLost("bind", e)
        if not state.get("connected") or not state.get("sameDoc"):
            raise BoundFieldLost("bind", "element not connected")
        self.origin = state.get("origin")

    @classmethod
    def bind(cls, page: Any, locator: Any) -> Optional["BoundField"]:
        """Resolve ``locator`` once, before the click, so the clicked and
        the typed-into element are one.  None only for objects without
        ``element_handle`` (test doubles; a Playwright Locator has it)."""
        if not hasattr(locator, "element_handle"):
            return None
        try:
            handle = locator.element_handle(timeout=1500)
        except Exception as e:
            raise BoundFieldLost("bind", e)
        if handle is None:
            raise BoundFieldLost("bind", "no element")
        return cls(page, handle)

    def verify(self) -> None:
        """The frames as seen from outside the realm: not detached, and the
        frame and main-frame origins are the recorded ones."""
        try:
            if self.frame.is_detached() or _origin(self.frame.url) != self.frame_origin:
                raise BoundFieldLost("frame origin", self.frame.url)
            if _origin(self.page.main_frame.url) != self.main_origin:
                raise BoundFieldLost("main-frame origin", self.page.main_frame.url)
        except BoundFieldLost:
            raise
        except Exception as e:
            raise BoundFieldLost("handle", e)

    def _write(self, before: Optional[str], value: Optional[str], data: Optional[str],
               input_type: str, change: bool = False) -> None:
        self.verify()
        try:
            res = self.handle.evaluate(self._WRITE, {"origin": self.origin, "before": before,
                                                     "value": value, "data": data,
                                                     "inputType": input_type, "change": change})
        except Exception as e:
            raise BoundFieldLost("handle", e)
        if res.get("lost"):
            raise BoundFieldLost(res["lost"], res.get("detail"))
        # Never the value itself in the reason: it is the credential.
        if res.get("value") != (before if value is None else value):
            raise BoundFieldLost("value", f"{len(str(res.get('value')))} characters after the write")

    def clear(self) -> None:
        """Empty the field in-realm (ElementHandle.fill('') is focus plus a
        Delete key press)."""
        self._write(None, "", None, "deleteContentBackward")
        self.written = ""

    def type_char(self, char: str, delay: float) -> None:
        """Write ONE character in-realm, then wait the scheduled delay; the
        field must read back as exactly the characters written so far.
        The delay follows the write, where keyboard.type(char, delay) held
        the key, so a page that submits on input has the same time to
        navigate before the caller looks at it.  The last character
        (``length``) carries 'change' in the same JS turn, so that
        navigation cannot run before it."""
        value = self.written + char
        last = len(value) == self.length
        self._write(self.written, value, char, "insertText", change=last)
        self.written, self.changed = value, last
        if delay > 0:
            self._sleep(delay / 1000.0)

    def finish(self) -> None:
        """'change' after the last character: sent with it when ``length``
        was known, else one more write under the same checks."""
        if not self.changed:
            self._write(self.written, None, None, "")


class BoundSelector(str):
    """BH1-49: a selector string that also carries its BoundField.  It still
    compares, hashes and serialises as the plain selector."""

    def __new__(cls, selector: str, field: BoundField):
        obj = super().__new__(cls, selector)
        obj.field = field
        return obj


def type_char(page: Any, selector: Any, char: str, delay: float) -> bool:
    """Type one character; written in-realm by the selector's BoundField
    when it carries one (raises BoundFieldLost, never falls back), else
    through ``page.keyboard`` (only objects without ``element_handle``).
    True when a character was sent."""
    field = getattr(selector, "field", None)
    if field is not None:
        field.type_char(char, delay)
        return True
    if hasattr(page, "keyboard") and hasattr(page.keyboard, "type"):
        page.keyboard.type(char, delay=delay)
        return True
    return False


class SyntheticInputScheduler:
    """Generates and executes realistic, non-periodic synthetic user interactions."""

    def __init__(
        self,
        profile: Union[str, InputProfile] = InputProfile.HUMAN_NATURAL,
        seed: Optional[int] = None,
    ):
        if isinstance(profile, str):
            try:
                self.profile = InputProfile(profile.lower())
            except ValueError:
                self.profile = InputProfile.HUMAN_NATURAL
        else:
            self.profile = profile

        self._rng = random.Random(seed)

    def sample_keystroke_delay(self, char: str) -> float:
        """Sample a realistic keystroke delay in milliseconds according to active profile."""
        if self.profile == InputProfile.RAPID_BURST:
            base = self._rng.uniform(40.0, 75.0)
        elif self.profile == InputProfile.CAREFUL_ENTRY:
            base = self._rng.uniform(140.0, 200.0)
        elif self.profile == InputProfile.STEALTH_JITTER:
            base = self._rng.uniform(60.0, 180.0)
        else:  # HUMAN_NATURAL
            base = self._rng.uniform(85.0, 135.0)

        # Subtle hesitations for complex characters
        hesitation = 0.0
        if char.isupper() or (not char.isalnum() and not char.isspace()):
            hesitation = self._rng.uniform(20.0, 45.0)
        elif char.isspace():
            hesitation = self._rng.uniform(15.0, 35.0)

        return round(base + hesitation, 2)

    def sample_inter_field_delay(self) -> float:
        """Sample pause between distinct form input fields."""
        if self.profile == InputProfile.RAPID_BURST:
            return self._rng.uniform(150.0, 300.0)
        elif self.profile == InputProfile.CAREFUL_ENTRY:
            return self._rng.uniform(400.0, 800.0)
        return self._rng.uniform(250.0, 500.0)

    def schedule_field_input(
        self,
        selector: str,
        value: str,
        *,
        clear_first: bool = True,
        click_to_focus: bool = True,
    ) -> List[InputAction]:
        """Generate scheduled actions for focusing, clearing, and typing a field value."""
        actions: List[InputAction] = []

        if click_to_focus:
            actions.append(
                InputAction(
                    action_type=InputActionType.CLICK,
                    selector=selector,
                    delay_ms=self._rng.uniform(100.0, 250.0),
                    metadata={"step": "focus_field"},
                )
            )

        if clear_first:
            actions.append(
                InputAction(
                    action_type=InputActionType.CLEAR,
                    selector=selector,
                    delay_ms=self._rng.uniform(50.0, 100.0),
                    metadata={"step": "clear_field"},
                )
            )

        for ch in value:
            d = self.sample_keystroke_delay(ch)
            actions.append(
                InputAction(
                    action_type=InputActionType.TYPE_CHAR,
                    selector=selector,
                    char=ch,
                    delay_ms=d,
                )
            )

        return actions

    def schedule_form_fill(
        self,
        form_entries: Sequence[tuple[str, str]],
        *,
        submit_selector: Optional[str] = None,
    ) -> InputSchedule:
        """Construct full input schedule across multiple form fields with natural transitions."""
        all_actions: List[InputAction] = []
        total_duration = 0.0
        field_count = len(form_entries)
        keystroke_count = 0

        for i, (selector, value) in enumerate(form_entries):
            field_actions = self.schedule_field_input(selector, value)
            all_actions.extend(field_actions)
            keystroke_count += len(value)

            # Add inter-field pause between fields
            if i < field_count - 1:
                pause_d = self.sample_inter_field_delay()
                all_actions.append(
                    InputAction(
                        action_type=InputActionType.PAUSE,
                        delay_ms=pause_d,
                        metadata={"step": "inter_field_transition"},
                    )
                )

        if submit_selector:
            # Pre-submit pause
            pre_submit_pause = self._rng.uniform(300.0, 600.0)
            all_actions.append(
                InputAction(
                    action_type=InputActionType.PAUSE,
                    delay_ms=pre_submit_pause,
                    metadata={"step": "pre_submit_hesitation"},
                )
            )
            all_actions.append(
                InputAction(
                    action_type=InputActionType.CLICK,
                    selector=submit_selector,
                    delay_ms=self._rng.uniform(100.0, 200.0),
                    metadata={"step": "submit_click"},
                )
            )

        total_duration = sum(a.delay_ms for a in all_actions)

        return InputSchedule(
            actions=all_actions,
            profile=self.profile,
            total_estimated_duration_ms=total_duration,
            field_count=field_count,
            keystroke_count=keystroke_count,
        )

    def execute_schedule(
        self,
        page: Any,
        schedule: InputSchedule,
        *,
        real_time_sleep: bool = False,
        refusal: Optional[Callable[[], Optional[str]]] = None,
    ) -> Dict[str, Any]:
        """Execute actions, stopping on a refusal before any page mutation."""
        def refused():
            try:
                return refusal() if refusal is not None else None
            except Exception:
                return "input refused: could not verify the current page"

        start_time = time.monotonic()
        executed = 0
        chars_typed = 0
        why = None

        for act in schedule.actions:
            if act.action_type != InputActionType.PAUSE:
                why = refused()
                if why:
                    break
            if act.action_type == InputActionType.CLICK:
                if act.selector and hasattr(page, "locator"):
                    loc = page.locator(act.selector)
                    if hasattr(loc, "click"):
                        loc.click()
                elif hasattr(page, "click") and act.selector:
                    page.click(act.selector)
            elif act.action_type == InputActionType.CLEAR:
                if act.selector and hasattr(page, "locator"):
                    loc = page.locator(act.selector)
                    if hasattr(loc, "fill"):
                        loc.fill("")
            elif act.action_type == InputActionType.TYPE_CHAR:
                try:
                    if act.char and type_char(page, act.selector, act.char, act.delay_ms):
                        chars_typed += 1
                except BoundFieldLost as e:
                    why = str(e)
                    break
            elif act.action_type == InputActionType.PRESS_KEY:
                if act.key and hasattr(page, "keyboard") and hasattr(page.keyboard, "press"):
                    page.keyboard.press(act.key)
            elif act.action_type == InputActionType.PAUSE:
                if real_time_sleep and act.delay_ms > 0:
                    time.sleep(act.delay_ms / 1000.0)

            executed += 1

        # The last key/click can navigate too; do not report a completed fill
        # and let the caller submit or continue on that new document.
        why = why or refused()
        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        report: Dict[str, Any] = {
            "ok": not bool(why),
            "actions_executed": executed,
            "characters_typed": chars_typed,
            "elapsed_ms": round(elapsed_ms, 2),
            "estimated_ms": round(schedule.total_estimated_duration_ms, 2),
        }
        if why:
            report["refusal"] = why
        return report


def schedule_synthetic_form_input(
    form_data: Union[Dict[str, str], Sequence[tuple[str, str]]],
    *,
    profile: str = "human_natural",
    submit_selector: Optional[str] = None,
) -> InputSchedule:
    """Functional convenience interface for generating input schedule."""
    scheduler = SyntheticInputScheduler(profile=profile)
    entries: List[tuple[str, str]] = (
        list(form_data.items()) if isinstance(form_data, dict) else list(form_data)
    )
    return scheduler.schedule_form_fill(entries, submit_selector=submit_selector)
