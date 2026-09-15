"""Row 807: an outcome with NO outcome key is the third state, not a clean run.

``tests/_run_context.outcome`` promises "never a clean-looking empty record":
a reporter that cannot say what happened yields ``recorded: False`` with
``counts`` and ``failed`` NULL. Measured on the base before a line was written:
that promise held only for a reporter with no ``stats`` mapping at all. A
reporter whose ``stats`` was ``{}`` -- or carried only the ``""`` key pytest
files outcome-less reports under, which the loop skips by design -- came back
``{"recorded": True, "counts": {}, "failed": []}``: byte-for-byte the record of
a run in which nothing failed. "I looked and nothing failed" and "I never got
to look" lead to opposite actions (row 753, clause 3), and this record said the
first while meaning the second.

WHAT THIS GATE PINS. Both shapes take the no-stats branch. The one shape that
is genuinely observed -- at least one outcome key -- keeps its ``recorded:
True`` record with the reporter's own counts, so the correction cannot have
widened the UNKNOWN branch into the observed one.

PRODUCTION REACH, measured and stated rather than assumed: ``conftest.
_write_run_context`` calls ``outcome`` only when worker chains exist, i.e.
after at least one item began, so a live master reporter has an outcome key by
then. The defect is in the function's contract, which the row names as the
diagnostic to close; this file is the pin on that contract.
"""

import pathlib
import sys

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tests"))

import _run_context as rc                                        # noqa: E402


class _Report:
    def __init__(self, nodeid):
        self.nodeid = nodeid


class _Reporter:
    """A terminal reporter with exactly the surface the seam reads."""

    def __init__(self, stats):
        self.stats = stats


def _unknown(record):
    return (record["recorded"], record["counts"], record["failed"])


@pytest.mark.parametrize(
    "stats",
    [
        pytest.param({}, id="empty-stats"),
        pytest.param({"": [_Report("tests/test_x.py::test_setup_only")]},
                     id="only-the-outcome-less-key"),
    ],
)
def test_stats_with_no_outcome_key_is_unknown_not_a_clean_run(stats):
    # The fixture built the shape it claims: the "" key really is populated
    # in the second case, so a skip of it is a skip of something.
    assert sum(len(v) for v in stats.values()) == len(stats)
    record = rc.outcome(_Reporter(stats))
    assert _unknown(record) == (False, None, None), record
    assert "never observed" in record["why"] or "no outcome" in record["why"], record


def test_a_reporter_with_one_outcome_key_is_still_recorded_with_its_own_counts():
    """Negative control: the observed shape must NOT take the UNKNOWN branch."""
    stats = {"": [_Report("tests/test_x.py::a")],
             "passed": [_Report("tests/test_x.py::a"), _Report("tests/test_x.py::b")]}
    record = rc.outcome(_Reporter(stats))
    assert record["recorded"] is True, record
    assert record["counts"] == {"passed": 2}, record   # exactly the two reports, "" not counted
    assert record["failed"] == []


def test_no_stats_mapping_still_takes_the_no_stats_branch():
    """The pre-existing branch is untouched: no ``stats`` at all stays UNKNOWN."""
    record = rc.outcome(object())
    assert _unknown(record) == (False, None, None), record
