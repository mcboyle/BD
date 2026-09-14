"""Row 788 -- the row 708 transform control is not a regression mutant."""
from __future__ import annotations

import json
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_ROOT = Path(__file__).resolve().parent.parent
_PRIMARY = _ROOT / "tests/mutants/row708_no_nav_login_is_not_success.json"
_CONTROL = _ROOT / "tests/mutants/row708_control_no_nav_login_is_not_success_transform_control.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_row708_control_is_its_own_declared_control_spec():
    """Nine behavioral regressions stay primary; M10 is one expected escape."""
    primary = _read(_PRIMARY)
    regressions = primary["mutants"]

    assert len(regressions) == 9, (
        "row 708's regression battery must contain exactly nine mutants; "
        "the transform control belongs in its own declared control spec")
    assert all(mutant["direction"] == "regression" for mutant in regressions)
    assert not any("transform control" in mutant["label"].lower()
                   for mutant in regressions)

    control = _read(_CONTROL)
    assert control["control_spec"] is True
    assert control["band"] == [
        "tests/test_row708_no_nav_login_is_not_success.py::"
        "test_transform_control_only_imports_the_seam"
    ]
    assert len(control["mutants"]) == 1
    mutant = control["mutants"][0]
    assert mutant["label"].startswith("M10 transform control")
    assert mutant["catcher"] == control["band"][0]
