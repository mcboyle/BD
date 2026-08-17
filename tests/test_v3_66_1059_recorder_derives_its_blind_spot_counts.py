"""The socket recorder's blind-spot counts must be measured, not remembered.

@1059, backlog row 34. `_socket_record.BLIND_SPOTS` carried the literals
164/1316/8, measured once at v3.66.1031 and never again. By v3.66.1058 every one
of them had drifted, and nothing could notice: a hardcoded number in a string is
invisible to every gate in this repo, and the string is printed on EVERY pytest
run, so the wrong figure is the most-read number in the suite.

This is the module whose entire subject is that a report which cannot see its
denominator must say so. It was reporting a denominator it could no longer see.

WHY THE ASSERTION COMPARES AGAINST A FRESH MEASUREMENT rather than forbidding
digits. A test that greps for "164" pins the fix to one stale value and goes
green the moment someone types a different wrong number. The property that
matters is agreement with the tree as it is now.

WHY UNKNOWN MUST BE REACHABLE. Section 0: unknown is a third state and it fails.
A deriver that cannot read the tree must say so rather than fall back to a
remembered figure -- a stale number that looks measured is worse than no number.
"""

import re
import subprocess
from pathlib import Path

import pytest

import _socket_record as sr

_REPO = Path(__file__).resolve().parent.parent


def _fresh() -> tuple[int, int, int]:
    """Re-measure independently of the module under test.

    Deliberately NOT importing the module's own helper: a test that calls the
    implementation to check the implementation agrees with itself by
    construction. This walks the tree the same way but in its own code.
    """
    out = subprocess.run(["git", "ls-files", "--", "tests/test*.py"],
                         capture_output=True, text=True, cwd=_REPO, timeout=60)
    files = out.stdout.split()
    spawn = pg = 0
    for rel in files:
        try:
            s = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(k in s for k in ("subprocess.", "Popen", "check_output",
                                "check_call", "os.system")):
            spawn += 1
        if "psycopg" in s or "libpq" in s:
            pg += 1
    return len(files), spawn, pg


@pytest.fixture(scope="module")
def fresh():
    total, spawn, pg = _fresh()
    # Non-empty denominator, before any verdict. A git call that returned
    # nothing would make every "count matches" assertion below compare 0 to 0.
    assert total > 500, f"only {total} tracked test files -- measurement failed"
    assert spawn > 0 and pg > 0, f"spawners={spawn} pg={pg}: predicate found nothing"
    return total, spawn, pg


def test_the_recorder_exposes_its_blind_spots():
    spots = sr.BLIND_SPOTS
    assert spots, "an undeclared blind spot is a gate reporting OK while blind"
    assert isinstance(spots, tuple), type(spots)


def test_the_counts_agree_with_the_tree_as_it_is_now(fresh):
    """THE POINT OF THE CUT."""
    total, spawn, pg = fresh
    joined = " ".join(sr.BLIND_SPOTS)
    numbers = set(re.findall(r"\d+", joined))
    assert str(spawn) in numbers, (
        f"the blind-spot text does not name the CURRENT spawner count "
        f"({spawn}). It reads: {joined!r}. A number measured once and never "
        f"again is printed on every run as though it were current."
    )
    assert str(total) in numbers, (
        f"the blind-spot text does not name the CURRENT denominator ({total})"
    )
    assert str(pg) in numbers, (
        f"the blind-spot text does not name the CURRENT psycopg file count ({pg})"
    )


def test_the_denominator_is_named_beside_the_count(fresh):
    """Section 1: say which denominator a count is over, in the same sentence.

    164/1316 was ambiguous between `tests/test*.py` and `tests/*.py`, which
    differ by 19 files and give spawner counts 10 apart. A bare ratio cannot be
    checked by the next reader.
    """
    # PER LINE, NOT OVER THE JOIN. Asserting against " ".join(...) let a mutant
    # gut one line's denominator while the other line's survived and satisfied
    # the search -- a denominator error in the test for a denominator error.
    counted = [s for s in sr.BLIND_SPOTS if re.search(r"\d", s)]
    assert counted, "no blind-spot line carries a count at all"
    for line in counted:
        assert re.search(r"tests/test\*?\.py|tracked test file", line), (
            f"this line carries a count but names no denominator, so nobody "
            f"can re-derive it: {line!r}"
        )


def test_unknown_is_reachable_when_the_tree_cannot_be_read(monkeypatch):
    """The third state, and it must be REACHABLE -- a branch nothing can reach
    is dead code that reads as a safety feature."""
    def boom(*a, **k):
        raise OSError("git is not available")

    monkeypatch.setattr(sr, "_count_tree", boom, raising=True)
    sr._reset_blind_spot_cache()
    spots = sr.BLIND_SPOTS

    # EVERY line that would carry a count must say UNKNOWN, and NO line may
    # carry a figure. Checking the join let a mutant hardcode one line's number
    # while the other still said UNKNOWN -- the check passed over text that
    # contained both the honest state and the dishonest one.
    for line in spots:
        assert not re.search(r"\b\d{2,}\b", line), (
            f"the tree could not be read, yet this line still prints a "
            f"figure -- a remembered number wearing the clothes of a "
            f"measurement: {line!r}"
        )
    assert any("UNKNOWN" in s.upper() for s in spots), (
        f"nothing says UNKNOWN when the tree cannot be enumerated: {spots!r}"
    )
    sr._reset_blind_spot_cache()


def test_the_derivation_is_cached_and_not_paid_per_call(monkeypatch):
    """Section 2.6: measure the cost of anything paid on every run.

    The summary reads BLIND_SPOTS more than once per process (the empty-harvest
    line and the not-covered line are different call sites), and the derivation
    reads ~1300 files. Uncached, that cost multiplies silently.
    """
    sr._reset_blind_spot_cache()
    calls = {"n": 0}
    real = sr._count_tree

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(sr, "_count_tree", counting, raising=True)
    for _ in range(5):
        _ = sr.BLIND_SPOTS
    assert calls["n"] == 1, (
        f"the tree was walked {calls['n']} times for 5 reads -- the derivation "
        f"is not cached"
    )
    sr._reset_blind_spot_cache()


def test_no_stale_literal_survives_in_the_source():
    """The old numbers must be GONE, including from prose.

    Section 0: a comment is inside the denominator of every gate that reads
    source text -- and the inverse bites here. An explanatory comment that
    spells the retired figure re-creates exactly the thing this cut removed,
    and the next reader cannot tell a live number from a historical one.
    """
    # BOTH files, because the same literal was copied into conftest's own
    # docstring and a check scoped to one file would certify the pair clean.
    for rel in ("tests/_socket_record.py", "tests/conftest.py"):
        src = (_REPO / rel).read_text(encoding="utf-8")
        assert len(src) > 2000, f"BD-GATE-UNRUNNABLE: {rel} did not load"
        for stale in ("1316", "164 of", "164/", "164\n", ": 164"):
            assert stale not in src, (
                f"the retired literal {stale!r} is still in {rel}. Cite the "
                f"mechanism, not the number -- explaining a removal by naming "
                f"the removed thing puts it back."
            )


BD_GATE_SCOPE = "repo-wide"
