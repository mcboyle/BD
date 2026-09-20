"""ROW precollect-guard-base-check (T2, O1055/O1066).

TASK (harness-work/FIX/precollect-guard-base-check/): bd-persist/harness/bd-precollect-guard.sh
CHECK 5 resolved "is the declared base still the tip" only from CANON's own local `main` branch.
CANON's local main can itself be behind a remote-tracking ref this host has already fetched
(origin/main) -- measured: row841 declared base 412cb80e was 9 commits behind origin/main
2c38520a (git merge-base --is-ancestor 412cb80e 2c38520a -> rc=0). When the declared base equals
that stale local main, CHECK 5 fell straight through to OK and printed "declared base is current
main", which was true of the stale local ref and false of the real trunk.

Fix (E1-corrected, review 2026-09-20T19:18Z): before trusting CANON's local main, walk
refs/remotes/*/main in CANON; if local main is an ancestor of (strictly behind) a fetched one,
FLIP to measuring against that truer tip instead (a NOTE, not an abstention) and fall through to
the existing BASE-vs-MAIN NOTE/REFUSE logic. The first cut of this fix downgraded every such case
to UNKNOWN and skipped the comparison outright, making the REFUSE arm unreachable during any
host-lag window -- fixed here. Diverging remote-tracking refs (neither an ancestor of the other)
still report UNKNOWN, since there is genuinely no one tip to measure against.

Fix (E3-corrected, review 2026-09-20T19:37Z): the final OK line's base clause was the fixed
phrase "declared base is current main" even right after CHECK 5 had just printed a NOTE saying
the base is BEHIND the tip (O734.3 clean-merge case) -- a record contradicting the line above it,
and the row's own reported symptom for row841. The OK line's base clause is now conditional: it
only says "current main" when the base equals the measured tip, else it names how far behind and
that the merge was clean. This env-selects the candidate under test (O1066):
BD_PRECOLLECT_GUARD_BASE_CHECK_CANDIDATE.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

DEFAULT_CANDIDATE = "/home/mboyle/bd-persist/harness/bd-precollect-guard.sh"
CANDIDATE = os.environ.get("BD_PRECOLLECT_GUARD_BASE_CHECK_CANDIDATE", DEFAULT_CANDIDATE)
FIXTURE_HARNESS = (
    "/home/mboyle/bd-persist/harness-work/FIX/precollect-guard-base-check/"
    "test_precollect_guard_base_check.sh"
)

pytestmark = pytest.mark.skipif(
    not Path(FIXTURE_HARNESS).is_file(),
    reason=f"fixture harness not present on this host: {FIXTURE_HARNESS}",
)


def test_candidate_guard_exists():
    assert Path(CANDIDATE).is_file(), f"candidate guard not found: {CANDIDATE}"


def test_candidate_guard_never_says_ok_or_unknown_on_a_measurable_stale_base():
    """Positive control (ordinary stale base), the row841-class stale-CANON-main case (must
    flip to measuring against the true tip, not silently UNKNOWN, and the OK line's base clause
    must not then falsely claim "current main"), a negative control (healthy current CANON,
    where "current main" IS the honest phrase), and the E1 fix (host lag STACKED with a real
    conflict against the true trunk must still REFUSE, counted against that true tip) -- run
    inside the shell fixture harness, which builds fully-sandboxed fake git repos and touches
    neither this repo nor BulkDownloader main.
    """
    result = subprocess.run(
        ["bash", FIXTURE_HARNESS, CANDIDATE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "RESULT PASS=12 FAIL=0" in out, out
