"""Row 673: the reviewed-to-learned probe adapter ships ONCE, in the package.

The row said the adapter "ships only inside a test". While verifying it this
cut measured the tree and found THREE copies, not the two the row names:
``tests/test_row126_reptyle_selectors_resolve_on_recorded_dom.py``,
``tests/test_row455_reviewed_template_against_a_live_dom.py`` and
``tests/test_row671_reviewed_template_selectors_are_enumerated.py``. That is
why this gate DERIVES ITS POPULATION FROM THE TREE at check time instead of
judging a hard-coded pair of paths: a hard-coded pair would have reported the
tree clean while a third copy sat two files away, which is the missing
denominator CLAUDE.md A7 warns every fix reproduces.

The signature is STRUCTURAL, not a name. Row 455's copy is called ``_probe``
and row 126's is ``_probe_template``, so a name search finds one and misses the
other; row 671's has no function of its own at all -- it is a dict literal
passed straight into a call. What every copy DOES share is the shape it
builds: a mapping with a ``learned`` key whose value carries the three reviewed
groups the verifier measures.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TESTS = _REPO / "tests"
_TEMPLATE = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"

# The three reviewed groups a probe carries. A mapping under ``learned`` that
# names all three IS the composition; two of three is some other structure and
# is deliberately outside the denominator (see the near-miss control below).
_PROBE_GROUPS = frozenset({"download", "login", "player"})

_ADAPTER = "template_to_selector_probe"


def _literal_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def probe_copy_lines(source: str) -> list[int]:
    """Every line in ``source`` that BUILDS a reviewed selector probe inline."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict) or "learned" not in _literal_keys(node):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "learned"
                and isinstance(value, ast.Dict)
                and _PROBE_GROUPS <= _literal_keys(value)
            ):
                found.append(node.lineno)
    return sorted(found)


def _adapter_call_count(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _ADAPTER
    )


def _population() -> list[Path]:
    """Every Python file under ``tests/``, read off the tree at check time."""
    return sorted(_TESTS.rglob("*.py"))


# --- the detector is measured before it is trusted ------------------------

_COPY_SAMPLE = '''
def _probe():
    return {
        "id": "sample",
        "learned": {
            "download": d,
            "login": {"user_field": a},
            "player": {"player_selectors": [b]},
        },
    }
'''

_IMPORT_SAMPLE = '''
def _probe():
    from bulk_downloader.template_assist import template_to_selector_probe

    return template_to_selector_probe(_template(), "sample")
'''

_NEAR_MISS_SAMPLE = '''
report = {"learned": {"download": d, "login": l}}
'''

_NESTED_SAMPLE = '''
adapted = enumerate_template_selectors({
    "id": "sample",
    "learned": {"download": d, "login": l, "player": p},
})
'''


def test_the_copy_detector_is_not_vacuous_and_does_not_match_the_import():
    # A gate that finds nothing is indistinguishable from a clean tree, so the
    # detector states its own denominator first: one hit on a copy, one hit on
    # a copy that is only ever a call argument (row 671's shape), zero on the
    # shipped-adapter import that replaces them, and zero on a ``learned``
    # mapping that names only two of the three reviewed groups.
    assert probe_copy_lines(_COPY_SAMPLE) == [3], probe_copy_lines(_COPY_SAMPLE)
    assert probe_copy_lines(_NESTED_SAMPLE) == [2], probe_copy_lines(_NESTED_SAMPLE)
    assert probe_copy_lines(_IMPORT_SAMPLE) == []
    assert probe_copy_lines(_NEAR_MISS_SAMPLE) == []
    assert _adapter_call_count(_IMPORT_SAMPLE) == 1
    assert _adapter_call_count(_COPY_SAMPLE) == 0
    # A file the detector cannot parse must RAISE rather than read as clean:
    # the collection below turns that into UNMEASURED, never into a pass.
    with pytest.raises(SyntaxError):
        probe_copy_lines("def _probe(:\n")


def test_the_probe_adapter_ships_in_the_package_and_composes_the_reviewed_groups():
    from bulk_downloader.template_assist import template_to_selector_probe

    assert _TEMPLATE.is_file(), f"precondition: reviewed template absent: {_TEMPLATE}"
    template = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    assert template.get("host") == "app.reptyle.com", template.get("host")

    probe = template_to_selector_probe(template, "row673_shipped_adapter")
    assert probe["id"] == "row673_shipped_adapter"
    assert set(probe["learned"]) == set(_PROBE_GROUPS), sorted(probe["learned"])

    # NONZERO SEAM: the composed block carries the reviewed template's OWN
    # selectors, not empty containers. An adapter that returned empty groups
    # would satisfy the shape assertion above and measure nothing.
    login = template["selectors"]["login"]
    player = template["selectors"]["player"]
    assert probe["learned"]["login"] == {
        "user_field": login["email"],
        "pass_field": login["password"],
        "submit_btn": login["submit"],
    }
    assert probe["learned"]["player"]["player_selectors"] == [
        player["container"], player["play_button"]
    ]
    download = probe["learned"]["download"]
    assert len(download["row_selectors"]) == 4, download["row_selectors"]
    assert len(download["trigger_selectors"]) == 10, download["trigger_selectors"]


def test_no_file_under_tests_carries_its_own_copy_of_the_probe_adapter():
    population = _population()
    assert len(population) > 0, f"precondition: no Python files under {_TESTS}"

    carriers: list[str] = []
    unreadable: list[str] = []
    for path in population:
        try:
            source = path.read_text(encoding="utf-8")
            lines = probe_copy_lines(source)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            # A file the gate cannot parse is UNMEASURED, never clean (A2).
            unreadable.append(f"{path.relative_to(_REPO)}: {type(exc).__name__}: {exc}")
            continue
        carriers.extend(
            f"{path.relative_to(_REPO)}:{line}" for line in lines
        )

    assert unreadable == [], unreadable
    assert carriers == [], (
        "these files build the reviewed selector probe inline instead of "
        "importing bulk_downloader.template_assist.template_to_selector_probe: "
        + ", ".join(carriers)
    )


def test_every_reviewed_probe_caller_under_tests_goes_through_the_shipped_adapter():
    callers = {
        str(path.relative_to(_REPO)): _adapter_call_count(
            path.read_text(encoding="utf-8")
        )
        for path in _population()
    }
    named = sorted(name for name, count in callers.items() if count)
    assert named == [
        "tests/test_row126_reptyle_selectors_resolve_on_recorded_dom.py",
        "tests/test_row455_reviewed_template_against_a_live_dom.py",
        "tests/test_row671_reviewed_template_selectors_are_enumerated.py",
        "tests/test_row673_reviewed_probe_adapter_ships_once.py",
    ], named
    assert [callers[name] for name in named] == [1, 1, 1, 1], named


def test_row673_transform_control_import_only():
    """Imports the shipped adapter without composing anything through it.

    Band for ``row673_adapter_ships_once_transform_control.json``: a transform
    of the adapter that this test cannot observe MUST ESCAPE it.
    """
    from bulk_downloader import template_assist

    assert callable(template_assist.template_to_selector_probe)
    assert callable(template_assist.selector_group)
