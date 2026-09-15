"""Row 770 -- the login fill path must skip honeypot decoy fields.

project-knowledge/IMPROVEMENT_BACKLOG.md row 770: real sites (ultrafilms,
wowgirls, pegasproductions) plant a decoy ``input[type=text]`` ahead of the
real username field in DOM order -- ``tabindex=-1``, ``aria-hidden="true"``,
off-screen positioning, or a hiding inline style.

The subject is ``bulk_downloader/login_impl/_common.py::_try_fill``, which is
the single seam behind BOTH fill paths the row names: ``submit.py`` calls it
for the username (``_try_fill(page, uf_candidates, ...)``) and again for the
password. The row's text points at ``submit.py:79-102``; measured at
900c08fc the fill logic lives in ``_common._try_fill`` and submit.py only
calls it, so the fix belongs here and covers both callers at once.

Pre-fix, ``_try_fill`` resolved ``page.locator(sel).first`` and handed it to
Playwright's ``wait_for(state="visible")``. That check is layout-aware but
knows nothing about ``tabindex`` or ``aria-hidden``, and it accepts an
off-screen element (a non-empty box at a negative coordinate) and an
``opacity:0`` element as visible. ``.first`` also means that when a selector
matches BOTH the decoy and the real field, only the first DOM match is ever
considered -- so a decoy planted before the real field wins by construction.

This file fakes just enough of the Playwright surface (``locator()``,
``.first``/``.count()``/``.nth(i)``, ``get_attribute``, ``bounding_box``,
``wait_for``, ``fill``, ``click``, ``keyboard.type``) to drive ``_try_fill``
against fixtures where a decoy sits before the real field in the SAME
selector match set -- the DOM shape the register row describes.
"""

BD_GATE_SCOPE = "module"

import ast
from pathlib import Path

import pytest

from bulk_downloader.login_impl import _common
from bulk_downloader.login_impl import submit as _submit


class _FakeKeyboard:
    def __init__(self, page):
        self._page = page

    def type(self, ch, delay=0):
        if self._page.focused is not None:
            self._page.focused.typed += ch


class _Elem:
    def __init__(self, name, attrs=None, box=None, visible=True, page=None):
        self._page = page
        self.name = name
        self.attrs = attrs or {}
        self.box = box if box is not None else {
            "x": 0, "y": 0, "width": 100, "height": 20}
        self.visible = visible
        self.typed = ""
        self.dom_set_value = None

    def get_attribute(self, attr_name):
        return self.attrs.get(attr_name)

    def bounding_box(self):
        return self.box

    def wait_for(self, state="visible", timeout=None):
        if state == "visible" and not self.visible:
            raise TimeoutError(f"{self.name}: not visible")

    def fill(self, value):
        if value == "":
            self.typed = ""
        else:
            self.dom_set_value = value

    def click(self, timeout=None):
        self._page.focused = self


class _Empty:
    """What Playwright gives you for a selector that matches nothing: the
    resolved ``.first`` handle raises on ANY wait_for, attached included."""

    def wait_for(self, state="visible", timeout=None):
        raise TimeoutError("no element matched")


class _LocatorGroup:
    def __init__(self, elems):
        self._elems = elems

    @property
    def first(self):
        return self._elems[0] if self._elems else _Empty()

    def count(self):
        return len(self._elems)

    def nth(self, idx):
        return self._elems[idx]


class _FakePage:
    """selector -> ordered list of _Elem, mimicking DOM match order."""

    group_cls = _LocatorGroup

    def __init__(self, mapping=None):
        self._mapping = dict(mapping or {})
        self.focused = None
        self.keyboard = _FakeKeyboard(self)
        for elems in self._mapping.values():
            for el in elems:
                el._page = self

    def locator(self, sel):
        return self.group_cls(self._mapping.get(sel, []))


SELECTOR = "form input[type='text']"


def _page(*elems):
    page = _FakePage()
    page._mapping = {SELECTOR: list(elems)}
    for el in elems:
        el._page = page
    return page


def _decoy_tabindex():
    # ultrafilms: input#website_url tabindex=-1, otherwise a normal text input.
    return _Elem("decoy-tabindex", attrs={"tabindex": "-1", "id": "website_url"})


def _decoy_aria_hidden():
    # wowgirls: "Website (do not fill)", aria-hidden. The real site carries
    # tabindex=-1 too; it is left off here so the parametrised case pins the
    # aria-hidden signal on its own rather than the first signal in the chain.
    return _Elem("decoy-aria-hidden",
                 attrs={"aria-hidden": "true", "id": "hp_field"})


def _decoy_offscreen():
    return _Elem("decoy-offscreen", attrs={"id": "trap"},
                 box={"x": -9999, "y": 0, "width": 1, "height": 1})


def _decoy_style_opacity():
    # opacity:0 is the case Playwright's own is_visible() calls VISIBLE and
    # the bounding box calls on-screen. Only the shipped HONEYPOT_CSS_HIDDEN
    # vocabulary rejects it.
    return _Elem("decoy-opacity", attrs={"id": "hp2", "style": "opacity: 0"})


def _decoy_hidden_attr():
    # pegasproductions: hidden input[name=login...].
    return _Elem("decoy-hidden-attr", attrs={"name": "login_hp", "hidden": ""})


def _decoy_type_hidden():
    # pegasproductions ships the decoy as an input whose TYPE is hidden, which
    # is a different attribute from the bare `hidden` attribute above: a
    # name-keyed selector ("input[name='login']") still matches it, so it can
    # still be the first member of a match set and must still be walked past.
    return _Elem("decoy-type-hidden",
                 attrs={"name": "login_hp2", "type": "hidden"})


def _real_field():
    return _Elem("real-username", attrs={"id": "username"})


def _typed(*elems):
    """Exact-count helper: how many fixture elements received typed text."""
    return [e for e in elems if e.typed]


# --------------------------------------------------------------------------
# RED (pre-fix): a decoy ahead of the real field in the SAME match set.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("make_decoy,value,signal", [
    (_decoy_tabindex, "alice", "tabindex=-1"),
    (_decoy_aria_hidden, "bob", 'aria-hidden="true"'),
    (_decoy_offscreen, "carol", "off-screen box"),
    (_decoy_style_opacity, "dana", "style~opacity:0"),
    (_decoy_hidden_attr, "erin", "hidden-attr"),
    (_decoy_type_hidden, "fran", "type=hidden"),
])
def test_decoy_before_real_field_is_skipped(make_decoy, value, signal):
    """The decoy must go unfilled and the real field must receive the value.

    Exact failing assertion at base 900c08fc (tabindex case):
        AssertionError: honeypot filter did not skip the decoy:
        decoy.typed='alice' real.typed=''
        assert '' == 'alice'
    """
    decoy = make_decoy()
    real = _real_field()
    page = _page(decoy, real)

    ok, used = _common._try_fill(page, [SELECTOR], value, "username")

    assert ok is True, used
    assert real.typed == value, (
        f"honeypot filter did not skip the decoy: decoy.typed={decoy.typed!r} "
        f"real.typed={real.typed!r}")
    assert decoy.typed == "", f"decoy field was filled: {decoy.typed!r}"
    # Exact count over the WHOLE fixture, not over a one-element list:
    # exactly one of the two elements is written to.
    assert len(_typed(decoy, real)) == 1
    # The signal is the one we think it is, not an accidental second one.
    assert _common._is_honeypot_field(decoy) == (True, signal)
    assert _common._is_honeypot_field(real) == (False, "")


def test_decoy_signal_is_read_from_the_shipped_vocabulary():
    """The style rule must be the SHIPPED deep_detect vocabulary, not a
    private restatement of it. Positive control: a pattern that exists only
    in HONEYPOT_CSS_HIDDEN (clip-path) is rejected; negative control: a
    harmless inline style is not."""
    from bulk_downloader.deep_detect.login import HONEYPOT_CSS_HIDDEN

    assert "clip-path:inset(100%)" in HONEYPOT_CSS_HIDDEN, (
        "fixture premise gone: this pattern no longer ships")
    clipped = _Elem("clipped", attrs={"style": "clip-path: inset(100%)"})
    assert _common._is_honeypot_field(clipped) == (
        True, "style~clip-path:inset(100%)")

    plain = _Elem("plain", attrs={"style": "margin-top: 4px"})
    assert _common._is_honeypot_field(plain) == (False, "")


# --------------------------------------------------------------------------
# THE ENUMERATION'S EDGE -- decoys the six signals do NOT catch.
#
# These record a DEFEAT, not a success. _is_honeypot_field is an enumeration
# of spellings, not a visibility oracle: a decoy spelled outside it is filled
# with the operator's secret, `skipped` empty and no diagnostic. The subject's
# docstring names this surface; these fixtures MEASURE it against the shipped
# code so the claim cannot rot into a comment. Widening a signal to catch one
# of these cases is a row of its own -- and it will break the case here, which
# is the point: the edge cannot move without someone re-stating where it is.
# --------------------------------------------------------------------------

def _decoy_class_hidden():
    """Case A: the hiding lives in a stylesheet rule keyed on the class, so
    there is no inline `style` attribute for _signal_css_hidden to read and
    the box stays positive. The widest gap: the shipped deep_detect rule
    reads class and id hints, this Playwright-side rule does not."""
    return _Elem("decoy-class-hidden",
                 attrs={"id": "website_url", "class": "visually-hidden"})


def _decoy_zero_size_box():
    """Case B: a 1x1 box at POSITIVE coordinates. _signal_offscreen_box tests
    the SIGN of x/y and never the extent, so a decoy collapsed in place
    rather than pushed off-screen is invisible to it."""
    return _Elem("decoy-zero-size", attrs={"id": "hp_zero"},
                 box={"x": 12, "y": 340, "width": 0, "height": 0})


def _decoy_tabindex_minus_two():
    """Case C: `tabindex="-2"` -- still unreachable by keyboard, still not a
    field a human tabs into, but the comparison is against the literal "-1"
    the fixture sites ship, not against "negative"."""
    return _Elem("decoy-tabindex-2",
                 attrs={"tabindex": "-2", "id": "website_url2"})


@pytest.mark.parametrize("make_decoy", [
    pytest.param(_decoy_class_hidden, id="stylesheet-class-hidden"),
    pytest.param(_decoy_zero_size_box, id="zero-size-box-positive-coords"),
    pytest.param(_decoy_tabindex_minus_two, id="tabindex-other-than-minus-1"),
])
def test_an_unenumerated_decoy_defeats_the_filter_and_is_filled(make_decoy):
    """A decoy outside the six enumerated spellings IS FILLED today.

    This is the harm, recorded: on a live site the operator's password goes
    into the decoy. Compare test_decoy_before_real_field_is_skipped, which is
    the same fixture shape for an ENUMERATED spelling and asserts the
    opposite outcome -- the pair is what makes this a measured edge rather
    than an untested assumption.
    """
    decoy = make_decoy()
    real = _real_field()
    page = _page(decoy, real)

    ok, used = _common._try_fill(page, [SELECTOR], "heidi", "username")

    # No signal fires: the filter does not see a decoy here at all.
    assert _common._is_honeypot_field(decoy) == (False, ""), (
        "the enumeration has been WIDENED to catch this spelling: update the "
        "EVASION SURFACE section of _is_honeypot_field's docstring and move "
        "this case into test_decoy_before_real_field_is_skipped")
    assert ok is True, used
    assert decoy.typed == "heidi", (
        f"fixture premise gone: the decoy was not the filled element "
        f"(decoy.typed={decoy.typed!r} real.typed={real.typed!r})")
    assert real.typed == "", (
        f"the REAL field was filled, so this is no longer the walk's first "
        f"match: real.typed={real.typed!r}")
    # Exactly one element written to, and it is the wrong one.
    assert len(_typed(decoy, real)) == 1
    # And the caller is told NOTHING that distinguishes this from a clean
    # fill: the success return is the bare selector, byte-identical to what
    # test_negative_control_no_decoy_fills_the_only_field gets back. There is
    # no `skipped` list on the success path at all -- that string is built
    # only in the failure branch -- so nothing downstream can notice.
    assert used == SELECTOR, used


# --------------------------------------------------------------------------
# Negative controls.
# --------------------------------------------------------------------------

def test_negative_control_no_decoy_fills_the_only_field():
    """No honeypot signal anywhere: the ordinary shape must still be filled.
    Proves the fixture builds a fillable field and that the filter does not
    reject indiscriminately."""
    real = _real_field()
    page = _page(real)

    ok, used = _common._try_fill(page, [SELECTOR], "frank", "username")

    assert ok is True, used
    assert real.typed == "frank"
    assert len(_typed(real)) == 1


def test_negative_control_real_field_first_is_still_the_one_filled():
    """Order control: with the real field FIRST and a decoy behind it, the
    walk must stop at the real field -- proving the fix did not simply
    invert the selection to 'take the last match'."""
    real = _real_field()
    decoy = _decoy_tabindex()
    page = _page(real, decoy)

    ok, used = _common._try_fill(page, [SELECTOR], "grace", "username")

    assert ok is True, used
    assert real.typed == "grace"
    assert decoy.typed == ""
    assert len(_typed(real, decoy)) == 1


def test_walk_fills_the_first_of_two_real_fields():
    """Direction control: with NO decoy anywhere and two fillable matches, the
    walk must stop at the FIRST.

    The order control above cannot say this. Its second element IS a decoy, so
    a walk that ran backwards would skip it and still land on the real field --
    the test passes either way, and nothing else in this file distinguishes a
    forward walk from a backward one. This fixture removes the decoy so only
    the direction is under test."""
    first = _Elem("real-first", attrs={"id": "username"})
    second = _Elem("real-second", attrs={"id": "username-alt"})
    page = _page(first, second)

    ok, used = _common._try_fill(page, [SELECTOR], "mallory", "username")

    assert ok is True, used
    assert first.typed == "mallory"
    assert second.typed == ""
    assert len(_typed(first, second)) == 1


def test_invisible_non_decoy_field_is_skipped_without_a_honeypot_reason():
    """A field that is merely not visible yet (no decoy signal) must still be
    passed over by the visibility wait, and must NOT be reported as a
    honeypot -- the two refusals are different and the message says which."""
    late = _Elem("not-visible", attrs={"id": "username"}, visible=False)
    real = _real_field()
    page = _page(late, real)

    ok, used = _common._try_fill(page, [SELECTOR], "heidi", "username")

    assert ok is True, used
    assert real.typed == "heidi"
    assert late.typed == ""
    assert _common._is_honeypot_field(late) == (False, "")


# --------------------------------------------------------------------------
# Fail-closed, with a distinctive diagnostic.
# --------------------------------------------------------------------------

def test_all_candidates_are_honeypots_reports_failure():
    """If every match for every selector is a decoy, _try_fill must fail
    closed -- never fill the honeypot as a last resort."""
    decoy = _decoy_tabindex()
    page = _page(decoy)

    ok, info = _common._try_fill(page, [SELECTOR], "ivan", "username")

    assert ok is False, info
    assert decoy.typed == ""


def test_failure_message_distinguishes_decoys_from_no_match():
    """'every match was a decoy' and 'nothing matched' are opposite problems
    -- one says fix the selector list, the other says look at the filter --
    so the failure string must not collapse them."""
    decoy_page = _page(_decoy_tabindex())
    ok, decoy_info = _common._try_fill(decoy_page, [SELECTOR], "judy", "username")
    assert ok is False
    assert "honeypot" in decoy_info and "tabindex=-1" in decoy_info, decoy_info

    empty_page = _FakePage({SELECTOR: []})
    ok, empty_info = _common._try_fill(empty_page, [SELECTOR], "judy", "username")
    assert ok is False
    assert "honeypot" not in empty_info, empty_info
    assert decoy_info != empty_info


# --------------------------------------------------------------------------
# Regression guard: the candidate walk must not drop the pre-fix wait.
# --------------------------------------------------------------------------

class _LateGroup(_LocatorGroup):
    """A selector whose matches attach only once something WAITS on them.

    This is the ordinary slow login form. At 900c08fc the code resolved
    ``.first`` and waited on it, so the form rendered and the fill
    succeeded. Replacing that with a bare ``count()`` -- which resolves
    immediately -- returns 0 for this page and skips the selector, filling
    nothing. The wait is therefore kept, on ``state="attached"`` so that a
    match set whose FIRST member is an invisible decoy still resolves.
    """

    def __init__(self, pending, page):
        super().__init__([])
        self._pending = pending
        self._page = page

    @property
    def first(self):
        if self._elems:
            return self._elems[0]
        return _LateGroup._Waiter(self)

    class _Waiter:
        """Playwright's ``.first`` is a lazy locator: after the wait resolves
        it addresses the first match, so every later call lands on the real
        element. The fake delegates the same way."""

        def __init__(self, group):
            self._group = group

        def wait_for(self, state="visible", timeout=None):
            self._group._page.waits += 1
            self._group._elems = list(self._group._pending)

        def __getattr__(self, name):
            elems = self.__dict__["_group"]._elems
            if not elems:
                raise AttributeError(name)
            return getattr(elems[0], name)


def test_candidate_walk_keeps_the_wait_for_a_late_rendering_form():
    """No decoy here at all -- just an ordinary form that has not rendered
    yet. At 900c08fc this PASSED, because the code waited on ``.first``
    before touching it. Swapping that wait for a bare ``count()`` (which
    resolves immediately, to 0) silently skips the selector and fills
    nothing. This test is a regression guard on that wait, not a RED for
    row 770."""
    real = _real_field()

    page = _FakePage()
    page.waits = 0
    pending = [real]
    real._page = page
    page.locator = lambda sel: (_LateGroup(pending, page)
                                if sel == SELECTOR else _LocatorGroup([]))

    ok, used = _common._try_fill(page, [SELECTOR], "karen", "username")

    assert ok is True, used
    assert page.waits == 1, (
        f"expected exactly 1 wait on the selector, got {page.waits} -- the "
        f"candidate walk dropped the pre-fix wait and would skip any form "
        f"that has not rendered yet")
    assert real.typed == "karen"


# ── Row 770c: THE GUARD IS INSIDE _try_fill, SO THE DISPATCH IS PART OF IT ──
#
# Everything above drives ``_common._try_fill`` directly, and that is where the
# honeypot filter lives. The mechanical self-mutation over the 770b generation
# escaped three times for exactly that reason:
#
#   ESCAPED seam: early-return   _try_fill@bulk_downloader/login_impl/submit.py:89
#   ESCAPED seam: delete-the-call _try_fill@bulk_downloader/login_impl/submit.py:966
#   ESCAPED seam: delete-the-call _try_fill@bulk_downloader/login_impl/submit.py:985
#
# A guard reached through a call protects nothing once the call is gone. Delete
# the username dispatch and login stops filling usernames -- but every test
# above still passes, because every test above calls ``_try_fill`` itself. So
# the three dispatch sites are pinned here, BY CALL SHAPE AND BY REACHABILITY,
# never by line number: a line number would break on the next unrelated edit to
# submit.py and would pin nothing a mutant cannot walk around.


_DISPATCH_SITES = {
    # (enclosing function, candidate-list argument, label argument)
    ("_staged_password_retry", "pf_candidates", "password (after continue)"),
    ("do_login", "uf_candidates", "username"),
    ("do_login", "pf_candidates", "password"),
    # Row 722 (vixen, 2026-09-15): a cleared Cloudflare challenge can swallow
    # the POST and hand back an EMPTY login form; do_login re-fills once
    # through the same filtered seam (tests/test_row722_cloudflare_challenge_
    # before_form.py::test_after_a_cleared_challenge_an_empty_login_form_is_
    # re_submitted_once owns the behaviour). Two more dispatch sites, both
    # inside _try_fill's filter.
    ("do_login", "uf_candidates", "username (re-submit)"),
    ("do_login", "pf_candidates", "password (re-submit)"),
}


def _submit_tree():
    """Parse the SHIPPED submit.py, located through the imported module rather
    than through a path literal, so the gate cannot drift onto a stale copy."""
    src = Path(_submit.__file__).read_text(encoding="utf-8")
    return ast.parse(src), src


def _parents(tree):
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _enclosing_function(node, parents):
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        node = parents.get(node)
    return "<module>"


def _enclosing_statement(node, parents):
    while node is not None and not isinstance(node, ast.stmt):
        node = parents.get(node)
    return node


def _unreachable_because_of(stmt, parents):
    """Return the terminator that makes ``stmt`` dead, or None if it is
    reachable. Walks outward: at every statement list on the way up, a
    preceding sibling that unconditionally returns or raises kills everything
    after it. This is what catches an EARLY-RETURN mutant, which leaves the
    call in the file -- so a gate that only asks 'is the call still here?'
    reports a pass over code that can never run."""
    node = stmt
    while node is not None:
        owner = parents.get(node)
        if owner is None:
            return None
        for field, value in ast.iter_fields(owner):
            if not isinstance(value, list) or node not in value:
                continue
            for sibling in value[:value.index(node)]:
                if isinstance(sibling, (ast.Return, ast.Raise)):
                    return f"{type(sibling).__name__} at line {sibling.lineno}"
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        node = owner


def _dispatch_calls():
    tree, _ = _submit_tree()
    parents = _parents(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_try_fill"):
            continue
        label = None
        if len(node.args) >= 4 and isinstance(node.args[3], ast.Constant):
            label = node.args[3].value
        candidates = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
            candidates = node.args[1].id
        found.append({
            "line": node.lineno,
            "function": _enclosing_function(node, parents),
            "candidates": candidates,
            "label": label,
            "dead": _unreachable_because_of(
                _enclosing_statement(node, parents), parents),
        })
    return found


def test_try_fill_is_imported_by_the_submit_path():
    """The gate below matches ``_try_fill`` by name. If submit.py ever stopped
    importing it from _common, that name could bind to something local and
    every shape assertion would pass over the wrong function."""
    tree, _ = _submit_tree()
    # The BOUND name, not the imported one: ``from ._common import _try_fill as
    # _x`` still has alias.name == "_try_fill" while binding nothing that the
    # shape gate's ``Name.id == "_try_fill"`` match would ever resolve to. An
    # earlier form of this test collected alias.name and PASSED against exactly
    # that rename, so it asserted something its own docstring did not.
    bound = {
        (alias.asname or alias.name): alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "_common"
        for alias in node.names
    }
    assert bound.get("_try_fill") == "_try_fill", (
        f"the name _try_fill in submit.py is not _common._try_fill: the "
        f"._common imports bind {sorted(bound.items())}. The dispatch gate "
        f"below matches calls by that name, so if it binds anything else the "
        f"gate would pass over a function that has no honeypot filter in it")


def test_every_login_fill_goes_through_the_honeypot_filtered_seam():
    """EXACT COUNT, not 'at least'. Five dispatch sites call _try_fill (three
    at row 770, two more re-submit fills at row 722) and the honeypot filter
    is inside it; a sixth fill path added later would bypass the filter
    entirely and must fail here until it is routed through the same seam."""
    calls = _dispatch_calls()
    assert len(calls) == len(_DISPATCH_SITES) == 5, (
        f"expected exactly {len(_DISPATCH_SITES)} _try_fill dispatch sites in submit.py, found "
        f"{len(calls)}: {[(c['function'], c['candidates'], c['label']) for c in calls]}"
        f" -- the honeypot filter lives inside _try_fill, so a fill that does "
        f"not go through it is an unfiltered fill")

    shapes = {(c["function"], c["candidates"], c["label"]) for c in calls}
    assert shapes == _DISPATCH_SITES, (
        f"the login fill dispatch changed shape.\n"
        f"  expected: {sorted(_DISPATCH_SITES)}\n"
        f"  found:    {sorted(shapes)}\n"
        f"Each missing entry is a fill that no longer passes through the "
        f"honeypot filter in _common._try_fill: the username fill, the "
        f"password fill, the staged-login password retry, or one of the "
        f"row-722 post-challenge re-submit fills.")


def test_no_login_fill_dispatch_is_stranded_behind_an_early_return():
    """A delete-the-call mutant removes the call; an EARLY-RETURN mutant leaves
    it sitting after an unconditional return, where it reads as present and can
    never execute. Both lose the filter, so both must fail here."""
    dead = [c for c in _dispatch_calls() if c["dead"]]
    assert dead == [], (
        "a _try_fill dispatch site is unreachable: " + "; ".join(
            f"{c['function']} line {c['line']} "
            f"({c['candidates']}, {c['label']!r}) is dead after {c['dead']}"
            for c in dead
        ) + " -- the call is still in the file, so a presence check would "
            "pass, but that login fill never runs and never reaches the "
            "honeypot filter")
