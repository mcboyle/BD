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
from typing import Any, Dict, List, Optional, Sequence, Union


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
    ) -> Dict[str, Any]:
        """Execute scheduled actions against a real or mock Playwright page."""
        start_time = time.monotonic()
        executed = 0
        chars_typed = 0

        for act in schedule.actions:
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
                if act.char and hasattr(page, "keyboard") and hasattr(page.keyboard, "type"):
                    page.keyboard.type(act.char, delay=act.delay_ms)
                    chars_typed += 1
            elif act.action_type == InputActionType.PRESS_KEY:
                if act.key and hasattr(page, "keyboard") and hasattr(page.keyboard, "press"):
                    page.keyboard.press(act.key)
            elif act.action_type == InputActionType.PAUSE:
                if real_time_sleep and act.delay_ms > 0:
                    time.sleep(act.delay_ms / 1000.0)

            executed += 1

        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        return {
            "ok": True,
            "actions_executed": executed,
            "characters_typed": chars_typed,
            "elapsed_ms": round(elapsed_ms, 2),
            "estimated_ms": round(schedule.total_estimated_duration_ms, 2),
        }


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
