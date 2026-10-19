"""og-fu1: the real scheduler must stop at a navigation between keys."""
from types import SimpleNamespace

import pytest

from bulk_downloader.login_impl import _common, submit
from bulk_downloader.synthetic_input_scheduler import (
    InputAction, InputActionType, InputSchedule, SyntheticInputScheduler,
)

BD_GATE_SCOPE = "module"
LOGIN = "https://www.site.test/login"
FOREIGN = "https://unrelated.test/login"
SECRET = "fixture-0123"  # zero-entropy, 12-character fixture


class Page:
    def __init__(self, flip_at=None):
        self.url = LOGIN
        self.flip_at = flip_at
        self.typed = []
        self.writes = []
        self.selectors = []
        self.keyboard = SimpleNamespace(type=self.type, press=self.press)

    def type(self, char, **kwargs):
        self.typed.append((self.url, char))
        self.writes.append((self.url, "type"))
        if len(self.typed) == self.flip_at:
            self.url = FOREIGN

    def press(self, key):
        self.writes.append((self.url, key))

    def locator(self, selector):
        self.selectors.append(selector)
        return Field(self, selector)


class Field:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def nth(self, index):
        assert index == 0
        return self

    def count(self):
        return 1

    def wait_for(self, **kwargs):
        pass

    def fill(self, value):
        assert value == "", "no secret fallback expected after successful focus"
        self.page.writes.append((self.page.url, "clear"))

    def click(self, **kwargs):
        self.page.writes.append((self.page.url, "click"))


@pytest.fixture
def fields(monkeypatch):
    monkeypatch.setattr(_common, "_is_honeypot_field", lambda loc: (False, ""))
    monkeypatch.setattr(_common, "_is_search_field", lambda loc: (False, ""))


def fill(page):
    return _common._try_fill(
        page, ["#secret", "#alternate"], SECRET, "password",
        refusal=lambda: submit._prefill_origin_refusal(page, LOGIN, None))


def no_scheduler():
    raise RuntimeError("fixture: plan unavailable")


@pytest.mark.parametrize("fallback", [False, True], ids=["scheduler", "plan-fallback"])
@pytest.mark.parametrize("flip_at", [1, 4, len(SECRET)], ids=["first", "fourth", "last"])
def test_navigation_between_keys_stops_the_real_fill(monkeypatch, fields, fallback, flip_at):
    if fallback:
        monkeypatch.setattr(_common, "get_input_scheduler", no_scheduler)
    page = Page(flip_at)
    ok, why = fill(page)
    leaked = [char for url, char in page.typed if url == FOREIGN]
    assert not leaked, f"OG_FU1_PER_KEY_LEAK: {len(leaked)} chars after key {flip_at}"
    assert not ok and "cross-origin" in why, f"OG_FU1_REFUSAL_TERMINAL: {ok=} {why=}"
    assert len(page.typed) == flip_at
    assert not [write for url, write in page.writes if url == FOREIGN]
    assert page.selectors == ["#secret"], "refusal must not try another selector"


@pytest.mark.parametrize("fallback", [False, True], ids=["scheduler", "plan-fallback"])
def test_same_origin_types_every_character(monkeypatch, fields, fallback):
    if fallback:
        monkeypatch.setattr(_common, "get_input_scheduler", no_scheduler)
    page = Page()
    assert fill(page) == (True, "#secret")
    assert page.typed == [(LOGIN, char) for char in SECRET]
    assert page.selectors == ["#secret"]


def test_second_selector_attempt_rechecks_origin(monkeypatch, fields):
    page = Page()
    waits = []

    class SecondField(Field):
        def wait_for(self, state=None, **kwargs):
            if state == "visible":
                waits.append(self.selector)
                if self.selector == "#alternate":
                    page.url = FOREIGN

    def locator(selector):
        page.selectors.append(selector)
        return SecondField(page, selector)

    def first_type_fails(char, **kwargs):
        if page.url == LOGIN:
            raise RuntimeError("fixture: first element detached before typing")
        page.type(char, **kwargs)

    page.locator = locator
    page.keyboard.type = first_type_fails
    ok, why = fill(page)
    assert waits == ["#secret", "#alternate"], "premise: reached a second visible attempt"
    assert not page.typed, "OG_FU1_SECOND_SELECTOR: secret typed after second wait navigated"
    assert not ok and "cross-origin" in why
    assert not [write for url, write in page.writes if url == FOREIGN]


@pytest.mark.parametrize("action", [
    InputAction(InputActionType.TYPE_CHAR, char="x"),
    InputAction(InputActionType.PRESS_KEY, key="Enter"),
    InputAction(InputActionType.CLEAR, selector="#next"),
    InputAction(InputActionType.CLICK, selector="#submit"),
], ids=["character", "enter", "clear", "submit-click"])
@pytest.mark.parametrize("raises", [False, True], ids=["foreign", "unverifiable"])
def test_scheduler_refuses_every_mutating_action(action, raises):
    page = Page()
    page.url = FOREIGN

    def refusal():
        if raises:
            raise RuntimeError("fixture: verifier unavailable")
        return submit._prefill_origin_refusal(page, LOGIN, None)

    report = SyntheticInputScheduler().execute_schedule(
        page, InputSchedule(actions=[action]), refusal=refusal)
    assert not page.writes, f"OG_FU1_SCHEDULE_ACTION: {page.writes}"
    assert report["ok"] is False and "refused" in report["refusal"]
    assert report["actions_executed"] == report["characters_typed"] == 0


@pytest.mark.parametrize("flip_at", [1, 4, len(SECRET), None],
                         ids=["first", "fourth", "last", "same-origin"])
def test_form_schedule_stops_before_next_field_or_submit(flip_at):
    page = Page(flip_at)
    scheduler = SyntheticInputScheduler(seed=42)
    schedule = scheduler.schedule_form_fill(
        [("#secret", SECRET), ("#next", "next")], submit_selector="#submit")
    report = scheduler.execute_schedule(
        page, schedule,
        refusal=lambda: submit._prefill_origin_refusal(page, LOGIN, None))
    assert not [write for url, write in page.writes if url == FOREIGN], (
        f"OG_FU1_SCHEDULE_NAVIGATION: {page.writes}")
    if flip_at is None:
        assert report["ok"] is True and "refusal" not in report
        assert report["characters_typed"] == len(SECRET) + 4
        assert report["actions_executed"] == len(schedule.actions)
        assert page.typed == [(LOGIN, char) for char in SECRET + "next"]
        assert "#submit" in page.selectors
    else:
        assert report["ok"] is False and "cross-origin" in report["refusal"]
        assert report["characters_typed"] == flip_at
        assert "#next" not in page.selectors and "#submit" not in page.selectors


@pytest.mark.parametrize("fallback", [False, True], ids=["scheduler", "plan-fallback"])
def test_mid_typing_verifier_error_is_terminal(monkeypatch, fields, fallback):
    if fallback:
        monkeypatch.setattr(_common, "get_input_scheduler", no_scheduler)
    page = Page(4)
    original = submit._prefill_origin_refusal

    def cannot_verify(page, *args):
        if page.url == FOREIGN:
            raise RuntimeError("fixture: current origin unavailable")
        return original(page, *args)

    monkeypatch.setattr(submit, "_prefill_origin_refusal", cannot_verify)
    ok, why = fill(page)
    assert not ok and "could not verify" in why
    assert len(page.typed) == 4
    assert not [write for url, write in page.writes if url == FOREIGN]
    assert page.selectors == ["#secret"]
