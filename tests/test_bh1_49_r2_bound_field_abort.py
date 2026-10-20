"""BH1-49 r2: a lost binding aborts the whole credential entry.

Each per-character check of BoundField (connected, same document, document,
frame and main-frame origin, the value read back, the handle's own errors)
stops the entry with the BOUND_FIELD_LOST reason: no retry, no re-resolve of
the Locator, no next match or selector, no page.keyboard fallback.  The
refusal callback admits everything here, so BoundField is the only guard.
r4: the Handle double plays BoundField._WRITE (one in-realm write per
character); it has no typing surface at all.
"""
import pytest

from bulk_downloader import synthetic_input_scheduler
from bulk_downloader.login_impl import _common

BD_GATE_SCOPE = "module"
BOUND_FIELD_LOST = "credential entry aborted: bound field lost"  # the abort reason prefix
LOGIN = "https://www.site.test/login"
FOREIGN = "https://evil.test/login"
ORIGIN = "https://www.site.test"  # location.origin of the login document
TYPED_FIXTURE = "fixture-0123"  # public, 12-character fixture
BREAK_AT = 6  # refusal checks: 1-3 around the clear and click, 4.. one per key


class Page:
    def __init__(self):
        self.url = LOGIN
        self.main_frame = Frame(LOGIN)
        self.keyboard = self
        self.queried, self.resolved, self.handles = [], [], []
        self.keyboard_keys = []

    def type(self, char, **kwargs):
        self.keyboard_keys.append(char)

    def press(self, key, **kwargs):
        self.keyboard_keys.append(key)

    def locator(self, selector):
        self.queried.append(selector)
        return Matches(self)


class Frame:
    def __init__(self, url):
        self.url, self.detached = url, False

    def is_detached(self):
        return self.detached


class Matches:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        pass

    def count(self):
        return 2

    def nth(self, index):
        self.page.resolved.append(index)
        return Field(self.page)


class Field:
    """The Locator: no typing surface; only resolves to a Handle."""

    def __init__(self, page):
        self.page = page

    def wait_for(self, **kwargs):
        pass

    def fill(self, value):
        assert value == "", "BH1_49_R2_LOCATOR_FILL: the secret went through Locator.fill"

    def click(self, **kwargs):
        raise AssertionError("BH1_49_R2_LOCATOR_CLICK: the Locator, not the handle, was clicked")

    def element_handle(self, **kwargs):
        handle = Handle(self.page)
        self.page.handles.append(handle)
        return handle


class Handle:
    def __init__(self, page):
        self.page = page
        self.frame = Frame(LOGIN)
        self.connected = self.same_doc = True
        self.origin = ORIGIN
        self.broken = None
        self.value, self.typed, self.changes = "", [], 0

    def wait_for_element_state(self, state, **kwargs):
        pass

    def fill(self, value, **kwargs):
        raise AssertionError("BH1_49_R4_HANDLE_FILL: ElementHandle.fill is focus plus keys")

    def click(self, **kwargs):
        pass

    def owner_frame(self):
        return self.frame

    def evaluate(self, script, arg=None):
        if self.broken == "handle":
            raise RuntimeError("Execution context was destroyed")
        if "sameDoc" in script:
            return {"connected": self.connected, "sameDoc": self.same_doc, "origin": self.origin}
        if isinstance(arg, dict):  # BoundField._WRITE, checks first, in one call
            if not self.connected:
                return {"lost": "isConnected", "detail": ""}
            if not self.same_doc:
                return {"lost": "ownerDocument", "detail": ""}
            if self.origin != arg["origin"]:
                return {"lost": "document origin", "detail": self.origin}
            if arg["before"] is not None and self.value != arg["before"]:
                return {"lost": "value", "detail": "changed under the entry"}
            if arg["value"] is not None and self.broken != "write_dropped":
                self.value = arg["value"]
                if arg["data"]:
                    self.typed.append(arg["data"])
            if arg["value"] is None or arg.get("change"):
                self.changes += 1
            return {"value": self.value}
        return self.connected

    def type(self, *args, **kwargs):
        raise AssertionError("BH1_49_R4_HANDLE_KEY: ElementHandle.type in the credential path")

    press = focus = type


def detach(page, h):
    h.connected = False


def new_document(page, h):
    h.same_doc = False


def frame_navigated(page, h):
    h.frame.url = FOREIGN


def frame_detached(page, h):
    h.frame.detached = True


def main_navigated(page, h):
    page.main_frame.url = FOREIGN


def document_navigated(page, h):
    h.origin = "https://evil.test"


def value_tampered(page, h):
    h.value += "x"


def write_dropped(page, h):
    h.broken = "write_dropped"


def handle_dead(page, h):
    h.broken = "handle"


BREAKS = [
    (detach, "isConnected"), (new_document, "ownerDocument"),
    (frame_navigated, "frame origin"), (frame_detached, "frame origin"),
    (main_navigated, "main-frame origin"), (document_navigated, "document origin"),
    (value_tampered, "value"), (write_dropped, "value"), (handle_dead, "handle"),
]


def no_scheduler():
    raise RuntimeError("fixture: plan unavailable")


@pytest.fixture
def fields(monkeypatch):
    monkeypatch.setattr(synthetic_input_scheduler.BoundField, "_sleep", staticmethod(lambda s: None))
    monkeypatch.setattr(_common, "_is_honeypot_field", lambda loc: (False, ""))
    monkeypatch.setattr(_common, "_is_search_field", lambda loc: (False, ""))


def fill(page, brk=None):
    calls = []

    def refusal():
        calls.append(1)
        if brk and len(calls) == BREAK_AT:
            brk(page, page.handles[0])
        return None

    return _common._try_fill(page, ["#secret", "#alternate"], TYPED_FIXTURE, "password",
                             refusal=refusal)


@pytest.mark.parametrize("fallback", [False, True], ids=["scheduler", "plan-fallback"])
@pytest.mark.parametrize("brk,check", BREAKS, ids=[f"{b.__name__}" for b, _ in BREAKS])
def test_lost_binding_aborts_the_whole_entry(monkeypatch, fields, fallback, brk, check):
    if fallback:
        monkeypatch.setattr(_common, "get_input_scheduler", no_scheduler)
    page = Page()
    ok, why = fill(page, brk)
    assert ok is False and why.startswith(f"{BOUND_FIELD_LOST} ({check}"), (
        f"BH1_49_R2_ABORT: {brk.__name__} gave {ok=} {why=}, want check {check!r}")
    typed = "".join(page.handles[0].typed)
    assert typed == TYPED_FIXTURE[:BREAK_AT - 4], (
        f"BH1_49_R2_KEY_AFTER_ABORT: {typed!r} reached the handle")
    assert (page.queried, page.resolved, len(page.handles)) == (["#secret"], [0], 1), (
        f"BH1_49_R2_RERESOLVED: selectors {page.queried}, nth {page.resolved}, "
        f"{len(page.handles)} handles after the abort")
    assert page.keyboard_keys == [], f"BH1_49_R2_KEYBOARD: {page.keyboard_keys}"


def test_bind_failure_aborts_before_any_key(monkeypatch, fields):
    def no_handle(self, **kwargs):
        raise RuntimeError("Timeout 1500ms exceeded")

    monkeypatch.setattr(Field, "element_handle", no_handle)
    page = Page()
    ok, why = fill(page)
    assert ok is False and why.startswith(f"{BOUND_FIELD_LOST} (bind"), f"BH1_49_R2_BIND: {why=}"
    assert (page.queried, page.resolved, page.keyboard_keys) == (["#secret"], [0], []), (
        f"BH1_49_R2_BIND_RERESOLVED: {page.queried} {page.resolved} {page.keyboard_keys}")


@pytest.mark.parametrize("fallback", [False, True], ids=["scheduler", "plan-fallback"])
def test_control_intact_binding_types_every_key_into_the_handle(monkeypatch, fields, fallback):
    if fallback:
        monkeypatch.setattr(_common, "get_input_scheduler", no_scheduler)
    page = Page()
    ok, used = fill(page)
    assert (ok, used) == (True, "#secret"), f"BH1_49_R2_CONTROL: {ok=} {used=}"
    handle = page.handles[0]
    assert "".join(handle.typed) == handle.value == TYPED_FIXTURE
    assert handle.changes == 1, f"BH1_49_R4_CHANGE: {handle.changes} change events"
    assert page.keyboard_keys == []
