"""The module-wipe census must read CODE, not prose -- including its own.

@1067, backlog row 91. `_module_wipe_leakers()` in
tests/test_v3_66_1034_guards_survive_a_module_wipe.py decided "this file
restores its wipe" by regexing the WHOLE file text. 1034 deletes
`bulk_downloader.*` from sys.modules and never restores -- that is its declared
job -- and it scored SAFE, because the restore pattern appears twice inside its
own text: the regex SOURCE LITERAL that defines the pattern, and an assertion
MESSAGE quoting the idiom it recommends.

MEASURED at v3.66.1066: census 13, budget 13, and the ONE leaker causing live
failures (the CSRF 403s of ledger item 48) ABSENT from its own list. The gate
built to police this class could not see the instance sitting inside it.

TWO INDEPENDENT AGENTS' CLASSIFIERS WERE FOOLED BY THE SAME PROSE during the
investigation that found it, so the trap is live rather than theoretical.

WHAT THIS CUT DOES NOT DO. It does not stop 1034 leaking. That leak is
deliberate and is item 48's subject; making the census honest is the
prerequisite, because until it counts correctly every number in this area is
suspect. The budget therefore RISES by exactly one, and that +1 is the census
gaining sight of its own file -- not a new leak.
"""

import importlib
import sys
from pathlib import Path

import pytest

from python_source import mentions_only, python_code_only

_REPO = Path(__file__).resolve().parent.parent
_RATCHET_REL = "tests/test_v3_66_1034_guards_survive_a_module_wipe.py"
_RATCHET = _REPO / _RATCHET_REL


@pytest.fixture(scope="module")
def ratchet():
    sys.path.insert(0, str(_REPO / "tests"))
    return importlib.import_module(
        "test_v3_66_1034_guards_survive_a_module_wipe")


def test_the_stripper_removes_a_string_literal_but_keeps_the_call():
    """The instrument's own control, on synthetic input.

    Without this, a helper that returned the raw text would make every
    assertion below pass while proving nothing.
    """
    import tempfile
    src = (
        'PATTERN = "sys.modules.update("\n'
        '# a comment mentioning sys.modules.update(\n'
        'def f(saved):\n'
        '    sys.modules.update(saved)\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(src)
        tmp = fh.name
    code = python_code_only(tmp)
    assert code.count("sys.modules.update(") == 1, (
        f"expected exactly the CALL to survive, got {code.count('sys.modules.update(')}"
    )
    assert "a comment mentioning" not in code
    assert "PATTERN" in code, "the assignment target must survive"


def test_the_ratchet_file_only_mentions_the_restore_idiom(ratchet):
    """The precondition, asserted before the verdict: 1034 really does talk
    about restoring without doing it. If that ever stops being true this test
    is measuring something else."""
    assert mentions_only(_RATCHET, "sys.modules.update("), (
        "1034 now CALLS sys.modules.update() in code -- it may have started "
        "restoring, in which case this test's premise is gone and the census "
        "expectation below needs re-deriving rather than adjusting"
    )


def test_the_census_sees_the_ratchets_own_file(ratchet):
    """THE DEFECT, stated as the rule that would have caught it."""
    leakers = ratchet._module_wipe_leakers()
    assert _RATCHET_REL in leakers, (
        f"{_RATCHET_REL} deletes bulk_downloader.* from sys.modules and never "
        f"restores, yet its own census does not list it. The predicate is "
        f"reading prose as code: its regex literal and an assertion message "
        f"both contain the restore idiom. Census: {sorted(leakers)}"
    )


def test_the_budget_admits_the_newly_visible_file(ratchet):
    """The ratchet must not fail merely because it can now see itself."""
    leakers = ratchet._module_wipe_leakers()
    assert len(leakers) <= ratchet._LEAK_BUDGET, (
        f"census is {len(leakers)} against budget {ratchet._LEAK_BUDGET}. "
        f"Making the predicate honest revealed a file it could not previously "
        f"see; the budget must account for it in the SAME cut."
    )


def test_the_census_still_excludes_a_genuine_restorer(ratchet):
    """OVER-SENSITIVITY CONTROL, and it is the half that matters.

    A 'fix' that simply stopped matching restores would list every wiper,
    satisfy the test above, and destroy the census. At least one file that
    genuinely restores in CODE must still be absent.
    """
    leakers = set(ratchet._module_wipe_leakers())
    known_restorer = "tests/test_v3_66_1021_log_reinit_replaces.py"
    if not (_REPO / known_restorer).exists():
        pytest.skip("the known restorer has been renamed; re-derive one")
    code = python_code_only(_REPO / known_restorer)
    assert "sys.modules.update(" in code or "saved_modules" in code, (
        "the chosen control does not actually restore in code -- pick another"
    )
    assert known_restorer not in leakers, (
        f"{known_restorer} restores its wipe in CODE and is still reported as "
        f"a leaker -- the predicate has become useless in the other direction"
    )
