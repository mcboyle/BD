"""BH1-49: credential keys go to the field the origin check approved.

(1) page.keyboard types into whatever holds focus when a key lands. Focus
can move (a cross-origin frame grabs it) after the per-key origin check and
before the keystroke, so each character must be bound to the checked match
(r4: written in-realm on its ElementHandle, no key and no focus involved).
(2) Same-brand :port / trailing-dot / http pages are ADMITTED by the row 722
predicate, by design: pinned here, no behaviour change.
"""
import pytest

from bulk_downloader import synthetic_input_scheduler
from bulk_downloader.login_impl import _common, submit

BD_GATE_SCOPE = "module"
LOGIN = "https://www.site.test/login"
TYPED_FIXTURE = "fixture-0123"  # public, 12-character fixture
FIELD, FRAME = "checked-field", "foreign-frame"


class Page:
    """Keys sent through page.keyboard land on whatever is focused."""

    def __init__(self, decoys=0):
        self.url = LOGIN
        self.focused = None
        self.received = {FIELD: [], FRAME: []}
        self.bound = []
        self.decoys = decoys
        self.keyboard = self
        self.main_frame = self

    def type(self, char, **kwargs):
        if self.focused in self.received:
            self.received[self.focused].append(char)

    def locator(self, selector):
        return Matches(self)


class Matches:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def nth(self, index):
        return Field(self.page, index)

    def count(self):
        return self.page.decoys + 1

    def wait_for(self, **kwargs):
        pass


class Field:
    def __init__(self, page, index):
        self.page, self.index = page, index

    def wait_for(self, **kwargs):
        pass

    def fill(self, value):
        assert value == "", "no secret fallback expected after successful focus"

    def click(self, **kwargs):
        self.page.focused = FIELD

    def element_handle(self, **kwargs):
        return Handle(self.page, self.index)


class Handle:
    """BH1-49 r2: the checked match as an ElementHandle (BoundField)."""

    def __init__(self, page, index):
        self.page, self.index = page, index

    def wait_for_element_state(self, state, **kwargs):
        pass

    def fill(self, value, **kwargs):
        raise AssertionError("BH1_49_R4_HANDLE_FILL: ElementHandle.fill is focus plus keys")

    def click(self, **kwargs):
        self.page.focused = FIELD

    def owner_frame(self):
        return self

    @property
    def url(self):
        return self.page.url

    def is_detached(self):
        return False

    def evaluate(self, script, arg=None):
        if "sameDoc" in script:
            return {"connected": True, "sameDoc": True, "origin": "https://www.site.test"}
        # BoundField._WRITE: the element's own value, whatever holds focus
        value = "".join(self.page.received[FIELD])
        if arg["value"] is not None and arg["data"]:
            self.page.bound.append(self.index)
            self.page.received[FIELD].append(arg["data"])
            value += arg["data"]
        return {"value": value}

    def type(self, *args, **kwargs):
        raise AssertionError("BH1_49_R4_HANDLE_KEY: ElementHandle.type in the credential path")

    press = focus = type


@pytest.fixture
def fields(monkeypatch):
    monkeypatch.setattr(synthetic_input_scheduler.BoundField, "_sleep", staticmethod(lambda s: None))
    monkeypatch.setattr(_common, "_is_honeypot_field",
                        lambda loc: (loc.index < loc.page.decoys, "decoy"))
    monkeypatch.setattr(_common, "_is_search_field", lambda loc: (False, ""))


def no_scheduler():
    raise RuntimeError("fixture: plan unavailable")


def fill(page, steal):
    def refusal():
        why = submit._prefill_origin_refusal(page, LOGIN, None)
        if steal:
            page.focused = FRAME  # focus moves after the check, before the key
        return why

    return _common._try_fill(page, ["#secret", "#alternate"], TYPED_FIXTURE,
                             "password", refusal=refusal)


@pytest.mark.parametrize("decoys", [0, 1], ids=["single", "behind-decoy"])
@pytest.mark.parametrize("fallback", [False, True], ids=["scheduler", "plan-fallback"])
@pytest.mark.parametrize("steal", [True, False], ids=["focus-moved", "control"])
def test_keys_reach_only_the_checked_field(monkeypatch, fields, fallback, steal, decoys):
    if fallback:
        monkeypatch.setattr(_common, "get_input_scheduler", no_scheduler)
    page = Page(decoys)
    ok, used = fill(page, steal)
    leaked = page.received[FRAME]
    assert not leaked, f"BH1_49_FOCUS_LEAK: {len(leaked)} chars reached {FRAME}"
    assert (ok, used) == (True, "#secret"), f"BH1_49_FILL: {ok=} {used=}"
    assert "".join(page.received[FIELD]) == TYPED_FIXTURE
    if steal:  # the control's keys reach the field either way
        assert page.bound == [decoys] * len(TYPED_FIXTURE), (
            f"BH1_49_BOUND_MATCH: keys bound to {set(page.bound)}, checked match {decoys}")


SAME_BRAND = [
    "https://www.site.test:8443/account",
    "https://www.site.test./account",
    "http://www.site.test/account",
    "https://members.site.test/account",
]
FOREIGN = [
    "https://unrelated.test/login",
    "https://site.test.evil.test/login",
    "http://unrelated.test:8443/login",
]


class At:
    def __init__(self, url):
        self.url = url


@pytest.mark.parametrize("url", SAME_BRAND)
def test_row_722_admits_same_brand_port_dot_and_http(url):
    assert submit._prefill_origin_refusal(At(url), LOGIN, None) is None, (
        f"BH1_49_ROW722_PIN: same-brand {url} refused; row 722 admits it by design")


@pytest.mark.parametrize("url", SAME_BRAND)
def test_control_same_brand_admission_is_the_row_722_predicate(monkeypatch, url):
    monkeypatch.setattr(submit, "_same_brand_origin", lambda *a: False)
    why = submit._prefill_origin_refusal(At(url), LOGIN, None)
    assert why and "cross-origin" in why, f"BH1_49_ROW722_SOURCE: {url} {why!r}"


@pytest.mark.parametrize("url", FOREIGN)
def test_control_foreign_brand_is_refused(url):
    why = submit._prefill_origin_refusal(At(url), LOGIN, None)
    assert why and "cross-origin" in why, f"BH1_49_ROW722_FOREIGN: {url} admitted"
