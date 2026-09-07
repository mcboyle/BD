"""Row 771: comma-bearing interstitial controls remain safe and dismissible."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from bulk_downloader import interstitial, templates


BD_GATE_SCOPE = "repo-wide"


@dataclass
class _Control:
    label: str


class _Locator:
    def __init__(self, page, control):
        self._page = page
        self._control = control

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._control in self._page.controls

    def count(self):
        return int(self.is_visible())

    def wait_for(self, **_kwargs):
        if not self.is_visible():
            raise TimeoutError("control is absent")

    def inner_text(self, **_kwargs):
        return self._control.label

    def get_attribute(self, _name, **_kwargs):
        return None

    def click(self, **_kwargs):
        self.wait_for()
        self._page.clicked.append(self._control.label)
        self._page.controls.remove(self._control)


class _ListLocator:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.controls)

    def nth(self, index):
        return _Locator(self._page, self._page.controls[index])


class _FixturePage:
    def __init__(self, labels):
        self.controls = [_Control(label) for label in labels]
        self.clicked = []
        self.url = "https://fixture.invalid/en/interstitial"

    def locator(self, selector):
        if selector == interstitial.GENERIC_CONTROL_SELECTOR:
            return _ListLocator(self)
        labels = re.findall(r":has-text\('([^']+)'\)", selector)
        for control in self.controls:
            if any(label.casefold() in control.label.casefold() for label in labels):
                return _Locator(self, control)
        return _Locator(self, _Control(""))

    def wait_for_load_state(self, **_kwargs):
        return None


def _dismiss(page, raw):
    return interstitial.dismiss_gates(
        page, raw, timeout_ms=1, site_appear_ms=0, settle_s=0,
        sleep=lambda _seconds: None)


def test_gamma_kosmos_dismisses_two_ordered_comma_walls():
    raw = templates.get("gamma_kosmos")["config_defaults"]["dismiss_selectors_login"]
    page = _FixturePage([
        "NO, THANKS",
        "No Thanks. Continue to Members Area",
    ])

    # The fixture is the real two-wall shape: both controls exist and are
    # visible before dismissal, and the template supplies a nonempty selector.
    assert raw
    assert [control.label for control in page.controls] == [
        "NO, THANKS", "No Thanks. Continue to Members Area"]
    assert len(page.controls) == 2

    actions = _dismiss(page, raw)

    assert page.clicked == [
        "NO, THANKS", "No Thanks. Continue to Members Area"], actions
    assert len(page.clicked) == 2
    cleared = [action for action in actions if action["outcome"] == "cleared"]
    assert [action["label"] for action in cleared] == page.clicked
    assert [action["source"] for action in cleared] == ["site", "site"]


def test_generic_interstitial_accepts_comma_but_not_vague_or_denied_controls():
    comma_page = _FixturePage(["No, Thanks"])
    comma_actions = _dismiss(comma_page, "")
    assert comma_page.clicked == ["No, Thanks"]
    assert sum(action["outcome"] == "cleared" for action in comma_actions) == 1

    denied_labels = ("Decline", "No", "Exit", "No thanks, cancel my subscription")
    assert len(denied_labels) == 4
    for label in denied_labels:
        page = _FixturePage([label])
        actions = _dismiss(page, "")
        assert page.controls and page.controls[0].label == label
        assert page.clicked == [], (label, actions)
        assert sum(action["outcome"] == "cleared" for action in actions) == 0


def test_clear_gates_interstitial_tier_clears_comma_spelling():
    page = _FixturePage(["No, Thanks"])
    cleared = interstitial.clear_gates(page, sleep=lambda _seconds: None)

    assert page.clicked == ["No, Thanks"]
    assert cleared == ["interstitial: cleared via a:has-text('No, Thanks')"]
    assert len(cleared) == 1


def test_declared_decline_is_refused_before_a_click():
    page = _FixturePage(["Decline"])
    actions = _dismiss(page, "button:has-text('Decline')")

    assert page.clicked == []
    assert [action["outcome"] for action in actions] == ["refused"]
    assert actions[0]["reason"].startswith("denylisted label matched 'decline'")


def test_gamma_template_is_optional_on_dfxtra_without_a_click():
    raw = templates.get("gamma_kosmos")["config_defaults"]["dismiss_selectors_login"]
    page = _FixturePage([])
    actions = _dismiss(page, raw)

    assert raw and not page.controls
    assert page.clicked == []
    assert len(actions) == 0


def test_import_only_transform_control():
    assert callable(interstitial.dismiss_gates)


def test_login_wall_lookup_shape_uses_an_empty_default():
    source = Path(__file__).parents[1] / "bulk_downloader" / "login_impl" / "submit.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
             and len(node.args) == 2
             and all(isinstance(arg, ast.Constant) for arg in node.args)
             and [arg.value for arg in node.args] == ["dismiss_selectors_login", ""]]
    assert len(calls) == 1, "login must resolve the declared wall selector once with a safe empty default"
    wall_assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "_wall"
                                for target in node.targets)]
    assert len(wall_assignments) == 1
    value = wall_assignments[0].value
    assert isinstance(value, ast.BoolOp) and isinstance(value.values[0], ast.Call), (
        "the declared wall lookup must be the first evaluated assignment value")
