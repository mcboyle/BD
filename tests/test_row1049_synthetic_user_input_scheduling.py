"""Row 1049: Synthetic User Input Scheduling for Web Forms.

Provides natural, human-like cadence scheduling for form filling, keystroke
variance, inter-field delays, and mouse focus events to defeat bot-detection
heuristics and enterprise WAF timing analyses.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import synthetic_input_scheduler
except ImportError:
    synthetic_input_scheduler = None


class _MockElementLocator:
    def __init__(self, selector: str):
        self.selector = selector
        # One entry per click: the timeout the CALLER passed (None = none passed).
        # No numeric default: test_v3_66_1222's census reads a constant `timeout=`
        # default as a budget in SECONDS ('DEFAULT:click' 1500 was the T66' CI red),
        # and recording the argument asserts what the product sent, not the fake.
        self.click_timeouts: list = []
        self.cleared = False
        self.filled_text = ""

    def click(self, timeout=None):
        self.click_timeouts.append(timeout)

    def fill(self, val):
        self.filled_text = val
        if val == "":
            self.cleared = True


class _MockKeyboard:
    def __init__(self):
        self.typed_chars: list[tuple[str, float]] = []
        self.pressed_keys: list[str] = []

    def type(self, text, delay=0):
        self.typed_chars.append((text, delay))

    def press(self, key):
        self.pressed_keys.append(key)


class _MockPlaywrightPage:
    def __init__(self):
        self.keyboard = _MockKeyboard()
        self.locators: dict[str, _MockElementLocator] = {}

    def locator(self, selector):
        if selector not in self.locators:
            self.locators[selector] = _MockElementLocator(selector)
        return self.locators[selector]


def test_positive_control_existing_try_fill_baseline():
    """Verify test harness integrity and positive baseline probe in login_impl._common."""
    from bulk_downloader.login_impl._common import _try_fill
    assert callable(_try_fill), "Existing _try_fill missing on base"


def test_synthetic_input_scheduler_capability_implemented():
    """Verify bulk_downloader.synthetic_input_scheduler module and core symbols exist."""
    assert synthetic_input_scheduler is not None, (
        "Row 1049 capability missing: Synthetic User Input Scheduling for Web Forms "
        "not implemented in bulk_downloader.synthetic_input_scheduler"
    )
    from bulk_downloader.synthetic_input_scheduler import (
        SyntheticInputScheduler,
        InputProfile,
        InputActionType,
        InputAction,
        InputSchedule,
        schedule_synthetic_form_input,
    )
    assert issubclass(InputProfile, object)
    assert issubclass(InputActionType, object)
    assert issubclass(InputSchedule, object)
    assert callable(schedule_synthetic_form_input)
    sched = SyntheticInputScheduler()
    assert hasattr(sched, "schedule_form_fill")
    assert hasattr(sched, "schedule_field_input")
    assert hasattr(sched, "execute_schedule")


def test_input_profile_timing_distributions():
    """Verify timing distributions respect profile semantics (careful > natural > rapid)."""
    assert synthetic_input_scheduler is not None, "capability missing"
    from bulk_downloader.synthetic_input_scheduler import SyntheticInputScheduler, InputProfile

    sched_rapid = SyntheticInputScheduler(profile=InputProfile.RAPID_BURST, seed=42)
    sched_natural = SyntheticInputScheduler(profile=InputProfile.HUMAN_NATURAL, seed=42)
    sched_careful = SyntheticInputScheduler(profile=InputProfile.CAREFUL_ENTRY, seed=42)

    text = "TestingCadence123"
    delays_rapid = [sched_rapid.sample_keystroke_delay(c) for c in text]
    delays_natural = [sched_natural.sample_keystroke_delay(c) for c in text]
    delays_careful = [sched_careful.sample_keystroke_delay(c) for c in text]

    avg_rapid = sum(delays_rapid) / len(delays_rapid)
    avg_natural = sum(delays_natural) / len(delays_natural)
    avg_careful = sum(delays_careful) / len(delays_careful)

    assert avg_rapid < avg_natural < avg_careful, (
        f"Expected rapid ({avg_rapid}) < natural ({avg_natural}) < careful ({avg_careful})"
    )

    # Verify non-uniform variance across keystrokes (anti-periodic signal)
    unique_delays = len(set(delays_natural))
    assert unique_delays > 5, "Keystroke delays must have natural non-uniform variance"


def test_schedule_form_fill_generation():
    """Verify schedule generation for multi-field forms creates appropriate actions and transitions."""
    assert synthetic_input_scheduler is not None, "capability missing"
    from bulk_downloader.synthetic_input_scheduler import (
        SyntheticInputScheduler,
        InputActionType,
    )

    sched = SyntheticInputScheduler()
    form = [
        ("input#username", "alice_operator"),
        ("input#password", "SecretPassword456!"),
    ]
    schedule = sched.schedule_form_fill(form, submit_selector="button[type=submit]")

    assert schedule.field_count == 2
    assert schedule.keystroke_count == len("alice_operator") + len("SecretPassword456!")
    assert schedule.total_estimated_duration_ms > 500

    # Ensure actions include focus clicks, character keystrokes, inter-field pause, and submit click
    action_types = [a.action_type for a in schedule.actions]
    assert InputActionType.CLICK in action_types
    assert InputActionType.TYPE_CHAR in action_types
    assert InputActionType.PAUSE in action_types

    # Find the submit action at the end
    last_action = schedule.actions[-1]
    assert last_action.action_type == InputActionType.CLICK
    assert last_action.selector == "button[type=submit]"


def test_schedule_execution_on_mock_page():
    """Verify executing schedule drives mock Playwright page keyboard and locator APIs."""
    assert synthetic_input_scheduler is not None, "capability missing"
    from bulk_downloader.synthetic_input_scheduler import (
        SyntheticInputScheduler,
        InputProfile,
    )

    sched = SyntheticInputScheduler(profile=InputProfile.RAPID_BURST)
    page = _MockPlaywrightPage()

    form = [("input#user", "john"), ("input#pin", "9876")]
    schedule = sched.schedule_form_fill(form, submit_selector="#submit-btn")

    report = sched.execute_schedule(page, schedule)
    assert report["ok"] is True
    assert report["actions_executed"] == len(schedule.actions)
    assert report["characters_typed"] == 8

    # Each field is focused, and the submit button pressed, by exactly ONE click,
    # and the executor passes no timeout of its own: execute_schedule calls
    # loc.click() bare, so the page's own default governs (recorded as None).
    assert {sel: loc.click_timeouts for sel, loc in page.locators.items()} == {
        "input#user": [None], "input#pin": [None], "#submit-btn": [None]}
    # CLEAR ran for both fields (loc.fill("")) and never for the submit button.
    assert [s for s, loc in page.locators.items() if loc.cleared] == ["input#user", "input#pin"]

    # Assert keyboard typed all characters
    typed_string = "".join(ch for ch, _ in page.keyboard.typed_chars)
    assert typed_string == "john9876"


def test_convenience_functional_interface():
    """Verify schedule_synthetic_form_input convenience functional interface."""
    assert synthetic_input_scheduler is not None, "capability missing"
    from bulk_downloader.synthetic_input_scheduler import (
        schedule_synthetic_form_input,
        InputSchedule,
    )

    form_dict = {"input#query": "BulkDownloader search"}
    sched = schedule_synthetic_form_input(form_dict, profile="stealth_jitter")
    assert isinstance(sched, InputSchedule)
    assert sched.field_count == 1
    assert sched.keystroke_count == len("BulkDownloader search")


def test_login_impl_common_integration():
    """Verify bulk_downloader.login_impl._common integrates synthetic input scheduling."""
    assert synthetic_input_scheduler is not None, "capability missing"
    from bulk_downloader.login_impl import _common

    assert hasattr(_common, "get_input_scheduler"), (
        "login_impl._common missing get_input_scheduler integration"
    )
    sched = _common.get_input_scheduler()
    assert sched is not None


# ── Rebuild r2 (refutes N6-A / P2-B, E1-E2 + NOTE) ──────────────────────────
# A recording fake page shaped like tests/test_login_honeypot.py's, so the
# real _try_fill (clear, click-to-focus, type) is driven end to end.

class _RecKeyboard:
    def __init__(self, page, fail_at=None):
        self._page = page
        self.delays = []
        self._fail_at = fail_at

    def type(self, ch, delay=0):
        if self._fail_at is not None and len(self.delays) == self._fail_at:
            self._fail_at = None
            raise RuntimeError("keystroke dropped")
        self.delays.append((ch, delay))
        if self._page.focused is not None:
            self._page.focused.typed += ch


class _RecField:
    def __init__(self):
        self._page = None
        self.typed = ""
        self.click_timeouts = []  # what the caller passed to click(timeout=), per click

    def get_attribute(self, name):
        return None

    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 100, "height": 20}

    def wait_for(self, state="visible", timeout=None):
        pass

    def fill(self, value):
        if value == "":
            self.typed = ""

    def click(self, timeout=None):
        self.click_timeouts.append(timeout)
        self._page.focused = self


class _RecGroup:
    def __init__(self, elems):
        self._elems = elems

    @property
    def first(self):
        return self._elems[0]

    def count(self):
        return len(self._elems)

    def nth(self, idx):
        return self._elems[idx]


class _RecPage:
    def __init__(self, fail_at=None):
        self.field = _RecField()
        self.field._page = self
        self.focused = None
        self.keyboard = _RecKeyboard(self, fail_at=fail_at)

    def locator(self, sel):
        return _RecGroup([self.field])


_SEL = "input#user"


def _seeded(monkeypatch, calls=None):
    from bulk_downloader.login_impl import _common
    from bulk_downloader.synthetic_input_scheduler import SyntheticInputScheduler

    class _Spy(SyntheticInputScheduler):
        def execute_schedule(self, page, schedule, **kw):
            if calls is not None:
                calls.append(len(schedule.actions))
            return super().execute_schedule(page, schedule, **kw)

    monkeypatch.setattr(_common, "get_input_scheduler", lambda: _Spy(seed=1049))
    return _common


def test_e1_try_fill_types_through_the_scheduler_plan(monkeypatch):
    calls = []
    _common = _seeded(monkeypatch, calls)
    page = _RecPage()
    ok, used = _common._try_fill(page, [_SEL], "frank", "username")
    assert (ok, used, page.field.typed) == (True, _SEL, "frank")
    assert calls == [5], f"E1: _try_fill must execute the scheduler's 5-keystroke plan once, executed {calls}"
    # The focus click is _try_fill's own, once, with its Playwright budget in
    # MILLISECONDS (1500 = 1.5 s); the scheduler's field plan adds no second click.
    assert page.field.click_timeouts == [1500], page.field.click_timeouts


def test_e2_keyboard_delays_come_from_the_scheduler_range(monkeypatch):
    _common = _seeded(monkeypatch)
    page = _RecPage()
    value = "abcdefghij" * 4
    assert _common._try_fill(page, [_SEL], value, "username")[0] is True
    delays = [d for _, d in page.keyboard.delays]
    assert len(delays) == 40
    out = [d for d in delays if not 85.0 <= d <= 135.0]
    assert out == [], f"E2: lowercase keystroke delays outside HUMAN_NATURAL 85-135ms: {out}"


def test_e2_uppercase_adds_hesitation_at_the_keyboard(monkeypatch):
    _common = _seeded(monkeypatch)
    page = _RecPage()
    assert _common._try_fill(page, [_SEL], "ABCDEFGHIJ" * 4, "username")[0] is True
    delays = [d for _, d in page.keyboard.delays]
    assert len(delays) == 40
    out = [d for d in delays if not 105.0 <= d <= 180.0]
    assert out == [], f"E2: uppercase delays must carry 20-45ms hesitation (105-180ms): {out}"


def test_e2_inter_field_pause_comes_from_the_scheduler(monkeypatch):
    _common = _seeded(monkeypatch)
    slept = []
    monkeypatch.setattr(_common.time, "sleep", slept.append)
    _common._inter_field_pause()
    assert len(slept) == 1 and 0.25 <= slept[0] <= 0.5, f"E2: inter-field pause {slept} not from scheduler 250-500ms"


def test_e2_form_fill_schedule_has_one_pause_between_two_fields():
    from bulk_downloader.synthetic_input_scheduler import InputActionType, SyntheticInputScheduler

    plan = SyntheticInputScheduler(seed=7).schedule_form_fill([("#u", "ab"), ("#p", "cd")])
    pauses = [a for a in plan.actions if a.action_type == InputActionType.PAUSE]
    assert len(pauses) == 1 and 250.0 <= pauses[0].delay_ms <= 500.0
    kinds = [a.action_type for a in plan.actions]
    first_p = kinds.index(InputActionType.PAUSE)
    assert kinds[:first_p].count(InputActionType.TYPE_CHAR) == 2, "the pause sits between the two fields"


def test_e2_login_submit_pauses_between_username_and_password():
    """The product form fill (submit.py) takes the scheduler's pause between the two fields."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "bulk_downloader/login_impl/submit.py").read_text(encoding="utf-8")
    u = src.index('_try_fill(page,uf_candidates,username,"username")')
    p = src.index('_try_fill(page,pf_candidates,password,"password")')
    between = src[u:p]
    assert between.count("_inter_field_pause()") == 1, "E2: submit.py must call _inter_field_pause() between the fields"


def test_note_keystroke_failure_is_not_retyped_into_the_same_field(monkeypatch):
    _common = _seeded(monkeypatch)
    page = _RecPage(fail_at=2)
    ok, info = _common._try_fill(page, [_SEL], "frank", "username")
    assert page.field.typed == "fr", (
        f"NOTE: a dropped keystroke must not retype the value into the same field; field holds {page.field.typed!r}")
    assert ok is False


def test_rec_page_control_fills_like_the_honeypot_fixture(monkeypatch):
    """Positive control: the recording page reaches the keyboard loop (typed text lands in the field)."""
    _common = _seeded(monkeypatch)
    page = _RecPage()
    assert _common._try_fill(page, [_SEL], "ok", "username") == (True, _SEL)
    assert [c for c, _ in page.keyboard.delays] == ["o", "k"]


# ---- W9-A bounce (N6-A REFUTE 23:58Z, E3 timing seams): each seam below survived a
# deletion mutant on tree 85a6409e; seeded RNG keeps every draw deterministic.

def _samples(profile, char, n=200):
    from bulk_downloader.synthetic_input_scheduler import SyntheticInputScheduler
    s = SyntheticInputScheduler(profile=profile, seed=1049)
    return [s.sample_keystroke_delay(char) for _ in range(n)]


def test_e3_space_adds_its_own_hesitation():
    """Natural base 85-135ms + space hesitation 15-35ms: every space lands in [100, 170]."""
    d = _samples("human_natural", " ")
    assert min(d) >= 100.0 and max(d) <= 170.0
    assert min(_samples("human_natural", "a")) < 100.0  # control: plain letters do go below 100


def test_e3_stealth_jitter_uses_its_own_range():
    """STEALTH_JITTER draws 60-180ms: wider than natural on both sides."""
    d = _samples("stealth_jitter", "a")
    assert 60.0 <= min(d) < 85.0 and 135.0 < max(d) <= 180.0


def test_e3_inter_field_delay_ranges_per_profile():
    from bulk_downloader.synthetic_input_scheduler import SyntheticInputScheduler
    for profile, lo, hi in [("human_natural", 250, 500), ("rapid_burst", 150, 300), ("careful_entry", 400, 800)]:
        s = SyntheticInputScheduler(profile=profile, seed=1049)
        d = [s.sample_inter_field_delay() for _ in range(100)]
        assert lo <= min(d) and max(d) <= hi, profile
        assert max(d) - min(d) > (hi - lo) / 2, profile  # a real spread, not a constant


def test_e3_real_time_pause_sleeps_the_scheduled_delay(monkeypatch):
    from bulk_downloader import synthetic_input_scheduler as sis

    slept = []
    monkeypatch.setattr(sis.time, "sleep", slept.append)
    sched = sis.SyntheticInputScheduler(seed=1049).schedule_form_fill([("#u", "a"), ("#p", "b")])
    pauses = [a.delay_ms for a in sched.actions if a.action_type == sis.InputActionType.PAUSE]
    assert len(pauses) == 1

    class _Kb:
        def type(self, *_a, **_k):
            pass

    class _Page:
        keyboard = _Kb()

    sis.SyntheticInputScheduler().execute_schedule(_Page(), sched, real_time_sleep=True)
    assert slept == [pauses[0] / 1000.0]
    slept.clear()
    sis.SyntheticInputScheduler().execute_schedule(_Page(), sched)  # default: plan only, no sleep
    assert slept == []


# ---- bd-fixer-B rebuild (ORDERS-0068, T66' 0f6e1a98 CI red): the fakes above
# record click(timeout=) instead of declaring a budget default; below, product
# branches of the row that no test executed before are driven and pinned.

def _no_scheduler():
    raise RuntimeError("scheduler unavailable")


def test_real_factory_drives_try_fill_at_human_natural_milliseconds():
    """The seeded spy above replaces get_input_scheduler; this drives the REAL
    factory: HUMAN_NATURAL (85-135 ms for lowercase), a fresh draw per keystroke,
    a fresh scheduler (fresh RNG) per call."""
    from bulk_downloader.login_impl import _common
    from bulk_downloader.synthetic_input_scheduler import InputProfile

    first = _common.get_input_scheduler()
    assert first.profile is InputProfile.HUMAN_NATURAL
    assert _common.get_input_scheduler() is not first
    page = _RecPage()
    assert _common._try_fill(page, [_SEL], "abcdefghij", "username") == (True, _SEL)
    delays = [d for _, d in page.keyboard.delays]
    assert len(delays) == 10 and all(85.0 <= d <= 135.0 for d in delays), delays
    assert len(set(delays)) > 1, "one delay for every keystroke is the periodic signal v3.65.2 removed"


def test_plan_build_failure_types_once_with_the_phase_15_5_delays(monkeypatch):
    """_type_field_value: when the scheduler cannot build the plan, the base
    uniform(50,150) ms loop types the value exactly once, one call per char."""
    from bulk_downloader.login_impl import _common

    monkeypatch.setattr(_common, "get_input_scheduler", _no_scheduler)
    page = _RecPage()
    assert _common._try_fill(page, [_SEL], "frank", "username") == (True, _SEL)
    assert page.field.typed == "frank"
    delays = [d for _, d in page.keyboard.delays]
    assert len(delays) == 5 and all(50.0 <= d <= 150.0 for d in delays), delays


class _NoTypeKeyboard:
    """A keyboard the executor cannot type with (no .type): each TYPE_CHAR is skipped."""


def test_a_short_execution_fails_the_match_instead_of_reporting_a_fill(monkeypatch):
    """N1-A residual M6: _type_field_value compares the keys the executor ACTUALLY
    typed with len(value). Zero keys typed fails this match, as the base loop's
    AttributeError did -- never (True, sel) over an empty field."""
    _common = _seeded(monkeypatch)
    page = _RecPage()
    page.keyboard = _NoTypeKeyboard()
    assert _common._try_fill(page, [_SEL], "frank", "username") == (
        False, "could not fill username; tried 1 selectors")
    assert (page.field.click_timeouts, page.field.typed) == ([1500], "")  # it reached typing
    with pytest.raises(RuntimeError, match=r"^typed 0 of 5 characters$"):
        _common._type_field_value(page, _SEL, "frank")


def test_inter_field_pause_falls_back_to_the_phase_15_5_range(monkeypatch):
    """_inter_field_pause: if the scheduler cannot run, the base 0.3-0.9 s pause
    (seconds, as time.sleep takes) still happens, exactly once."""
    from bulk_downloader.login_impl import _common

    monkeypatch.setattr(_common, "get_input_scheduler", _no_scheduler)
    slept = []
    monkeypatch.setattr(_common.time, "sleep", slept.append)
    _common._inter_field_pause()
    assert len(slept) == 1 and 0.3 <= slept[0] <= 0.9, f"fallback pause is 0.3-0.9 s: {slept}"


def test_e3_punctuation_hesitates_like_uppercase():
    """The hesitation branch is isupper() OR (not isalnum() and not isspace()):
    punctuation lands in [105, 180] ms; digits take no hesitation."""
    punct = _samples("human_natural", "!")
    assert min(punct) >= 105.0 and max(punct) <= 180.0
    assert max(_samples("human_natural", "7")) <= 135.0  # control: a digit is a plain keystroke


def test_executor_branches_reached_only_by_hand_built_schedules():
    """No plan the scheduler builds today reaches these (no product caller): a page
    without locator() is clicked through page.click, PRESS_KEY presses, an unknown
    profile string falls back to HUMAN_NATURAL, a sequence feeds the functional API."""
    from bulk_downloader.synthetic_input_scheduler import (
        InputAction,
        InputActionType,
        InputProfile,
        InputSchedule,
        SyntheticInputScheduler,
        schedule_synthetic_form_input,
    )

    class _Kb:
        def __init__(self):
            self.pressed = []

        def press(self, key):
            self.pressed.append(key)

    class _Page:
        def __init__(self):
            self.keyboard = _Kb()
            self.clicked = []

        def click(self, selector):
            self.clicked.append(selector)

    page = _Page()
    plan = InputSchedule(actions=[InputAction(InputActionType.CLICK, selector="#go"),
                                  InputAction(InputActionType.PRESS_KEY, key="Enter")])
    report = SyntheticInputScheduler().execute_schedule(page, plan)
    assert (page.clicked, page.keyboard.pressed) == (["#go"], ["Enter"])
    assert (report["actions_executed"], report["characters_typed"]) == (2, 0)

    assert SyntheticInputScheduler(profile="no-such-profile").profile is InputProfile.HUMAN_NATURAL
    sched = schedule_synthetic_form_input([("#a", "xy"), ("#b", "z")], profile="RAPID_BURST",
                                          submit_selector="#s")
    assert (sched.profile, sched.field_count, sched.keystroke_count) == (InputProfile.RAPID_BURST, 2, 3)
    d = sched.to_dict()
    assert d["profile"] == "rapid_burst"
    assert (d["actions"][-1]["action_type"], d["actions"][-1]["selector"]) == ("click", "#s")
