"""Row 1031 -- an accessible confirmation-checkbox selector and interaction handler.

MEASURED ON BASE bc1544b7, not assumed:

    interstitial.CHECKED_CONSENT_SELECTOR                 -> "input[type='checkbox']:checked"
    grep -rl 'checkbox' bulk_downloader/ tools/ --include=*.py -> 16 files, 3 of which roll
      their own locator: interstitial.py, runner_challenge.py, login_impl/submit.py
    every one of those three is CSS over a NATIVE <input type=checkbox>; none reads aria-checked
    positive control, same probe shape: 'turnstile' -> 41 files

The consequence is the row's reason, and it is a money one. ``_prechecked_billing_consent``
(row 762) refuses a gate click when recurring-charge language sits beside a CHECKED consent box,
and its own docstring says an unmeasurable box is UNKNOWN because "unavailable evidence is never
permission to spend money". But a consent box rendered the accessible way --
``<span role="checkbox" aria-checked="true">``, the standard custom-widget pattern -- is not an
``<input>``, so the native selector counts ZERO and the function returns ``("clear", "")``.

That is worse than the unknown the module fails closed on: the box was never read, yet the answer
claims it was measured and found clear. The deliverable is a selector that can see the accessible
shapes, and a handler that toggles them by their accessible state rather than by ``.check()``.
"""
from __future__ import annotations

import importlib
import re

from bulk_downloader import interstitial

# A real-money safety boundary, like row 762 beside it: it must run on every PR.
BD_GATE_SCOPE = "repo-wide"

CHARGE_TEXT = ("By checking this box you agree to add Premium Plus to your membership.\n"
               "You will be charged $29.97 every 30 days until you cancel.")

# Four ways a page can say "this box is ticked". The first is the only one base could see.
NATIVE_CHECKED = {"tag": "input", "type": "checkbox", "checked": True}
NATIVE_CLEAR = {"tag": "input", "type": "checkbox", "checked": False}
ARIA_CHECKED = {"tag": "span", "role": "checkbox", "aria-checked": "true"}
ARIA_CLEAR = {"tag": "span", "role": "checkbox", "aria-checked": "false"}
ARIA_SWITCH_ON = {"tag": "button", "role": "switch", "aria-checked": "true"}


def _a11y():
    """Import the subject lazily: on base this raises inside ONE test, not at collection."""
    return importlib.import_module('bulk_downloader.a11y_checkbox')


# ── a tiny DOM the selector is actually applied to ───────────────────────────────
# The test never hand-counts: it asks the page to MATCH the shipped selector string, so a
# selector that cannot express "aria-checked=true" cannot pass by being handed the right number.

_SIMPLE = re.compile(r"""^\s*(?P<tag>[a-z]+|\*)?
      (?:\[(?P<attr>[\w-]+)(?:\s*=\s*'?"?(?P<val>[^\]'"]+)'?"?)?\])*
      (?P<pseudo>:checked)?\s*$""", re.X)


def _matches_one(node, part):
    part = part.strip()
    tag_match = re.match(r'^([a-z]+|\*)?', part)
    tag = tag_match.group(1)
    if tag and tag != '*' and node.get('tag') != tag:
        return False
    for attr, val in re.findall(r"\[([\w-]+)(?:\s*=\s*['\"]?([^\]'\"]+)['\"]?)?\]", part):
        have = node.get(attr)
        if have is None:
            return False
        if val and str(have) != val:
            return False
    if ':checked' in part and not node.get('checked'):
        return False
    return True


class _Locator:
    def __init__(self, page, selector):
        self._page, self._selector = page, selector

    def count(self):
        self._page.reads += 1
        if self._page.error is not None:
            raise self._page.error
        parts = [p for p in self._selector.split(',') if p.strip()]
        return sum(1 for node in self._page.nodes
                   if any(_matches_one(node, p) for p in parts))


class _Page:
    def __init__(self, nodes, text=CHARGE_TEXT, error=None):
        self.nodes, self.text, self.error, self.reads = list(nodes), text, error, 0

    def locator(self, selector):
        return _Locator(self, selector)

    def inner_text(self, *_a, **_k):
        return self.text


# ── 1. the row's reason, asserted against code that exists on base ───────────────

def test_a_prechecked_aria_consent_box_is_not_reported_as_a_clear_page():
    page = _Page([ARIA_CHECKED])
    verdict, detail = interstitial._prechecked_billing_consent(page)
    assert verdict == "refuse", (
        "a consent box rendered as role=checkbox aria-checked=true beside "
        f"{'$29.97 every 30 days'!r} produced {verdict!r} ({detail!r}); the selector "
        f"{interstitial.CHECKED_CONSENT_SELECTOR!r} only matches a native <input>, so the box "
        "was never read and the gate click would accept the charge")


def test_the_native_case_and_the_no_charge_case_are_unchanged():
    """NEGATIVE CONTROL: the row must not turn every page into a refusal."""
    assert interstitial._prechecked_billing_consent(_Page([NATIVE_CHECKED]))[0] == "refuse"
    assert interstitial._prechecked_billing_consent(_Page([ARIA_CLEAR]))[0] == "clear"
    assert interstitial._prechecked_billing_consent(_Page([NATIVE_CLEAR]))[0] == "clear"
    assert interstitial._prechecked_billing_consent(
        _Page([ARIA_CHECKED], text="Welcome back. Your membership is active."))[0] == "clear"
    unknown = interstitial._prechecked_billing_consent(_Page([ARIA_CHECKED], error=RuntimeError("x")))
    assert unknown[0] == "unknown", "an unmeasurable box must still fail closed"


# ── 2. the selector module itself ────────────────────────────────────────────────

def test_the_selector_names_every_accessible_checked_shape():
    a11y = _a11y()
    parts = a11y.CHECKED_CONFIRMATION_SELECTORS
    assert isinstance(parts, tuple) and len(parts) >= 3
    page = _Page([NATIVE_CHECKED, ARIA_CHECKED, ARIA_SWITCH_ON, ARIA_CLEAR, NATIVE_CLEAR])
    assert page.locator(a11y.CHECKED_CONFIRMATION_SELECTOR).count() == 3, (
        "the shipped selector must match the native checked input, the aria checkbox and the "
        "aria switch, and must NOT match the two unchecked ones")


def test_the_selector_is_a_single_css_group_so_it_drops_into_locator():
    a11y = _a11y()
    joined = a11y.CHECKED_CONFIRMATION_SELECTOR
    assert joined == ", ".join(a11y.CHECKED_CONFIRMATION_SELECTORS)
    assert "::" not in joined and ">>" not in joined, "engine-prefixed syntax is not portable CSS"


# ── T66 drop: the module must not add a DP-13 swallowed exception to the ratchet ──────────
def test_the_module_adds_no_swallowed_exception_to_the_defect_ratchet(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    scan = root / "toolchain" / "bin" / "bd-defect-scan"

    def dp13(path):
        out = subprocess.run([sys.executable, str(scan), "--file", str(path), "--json"],
                             capture_output=True, text=True, check=True, cwd=root).stdout
        return [f["line"] for f in json.loads(out) if f["dp"] == "DP-13"]

    # Positive control: the probe can say yes on the exact shape the drop measured.
    control = tmp_path / "control.py"
    control.write_text("def f(n):\n    try:\n        return n.x()\n    except Exception:\n        pass\n"
                       "    return None\n")
    assert dp13(control) == [4], "probe cannot see a pass-only handler"
    assert dp13(root / "bulk_downloader" / "a11y_checkbox.py") == [], (
        "a11y_checkbox swallows an exception (DP-13): defect_DP_total rises above base")


# ── CONTEST-0073: the module ships only what the product calls ───────────────────
def test_every_public_name_in_the_module_has_a_product_caller():
    """RESCOPED (POLICY-0010 s3): the handler half (checkbox_state / count_checked / set_checked /
    CONFIRMATION_SELECTOR(S)) had no caller outside the module, so it was dropped. Whatever the
    module exports must be used by bulk_downloader/ itself, or be the parts the wired selector is
    joined from."""
    from pathlib import Path

    a11y = _a11y()
    pkg = Path(a11y.__file__).resolve().parent
    others = "\n".join(p.read_text(encoding="utf-8") for p in pkg.rglob("*.py")
                       if p.name != "a11y_checkbox.py")
    public = sorted(n for n, v in vars(a11y).items()
                    if not n.startswith("_") and n != "annotations" and not isinstance(v, type(re)))
    # Positive control: the probe sees the one wired name (interstitial imports it).
    assert re.search(r"\bCHECKED_CONFIRMATION_SELECTOR\b", others)
    parts = {"CHECKED_CONFIRMATION_SELECTORS"}  # joined into the wired selector, pinned above
    orphans = [n for n in public if n not in parts and not re.search(rf"\b{n}\b", others)]
    assert orphans == [], f"a11y_checkbox exports with no product caller: {orphans}"
