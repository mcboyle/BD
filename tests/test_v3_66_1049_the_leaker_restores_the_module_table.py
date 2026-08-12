"""The sys.modules leaker must leave the module table as it found it.

ITEM 48, SECOND MECHANISM. `tests/test_v3_66_1021_log_reinit_replaces.py`
deletes every `bulk_downloader.*` entry from `sys.modules` in its
`restored_logger` fixture's `finally`. Any test module that holds an
IMPORT-TIME binding -- `from bulk_downloader import db` -- keeps the pre-wipe
module object while the code under test re-imports a fresh one. Two live copies
of one module with independent module-level state, so a victim's `audit()`
reads a different database than the victim seeded.

WHY THE FIXTURE IS THE RIGHT PLACE. Its own docstring already promises to
"Leave the ambient logger exactly as found", and it keeps that promise
scrupulously for the stdlib logger -- handlers, filters, level, propagate all
saved and restored. The module table is state this suite churns exactly as
hard, and it was the one thing not put back. The fix is the promise the
fixture already makes, applied to the state it already disturbs.

THE MEASURED BLAST RADIUS, so nobody reads this cut as bigger than it is. An
AST census over all 1312 collected test files at bb37142 found 503 modules
holding a module-scope `bulk_downloader` binding used inside a function body.
Sampling 105 of them against this ONE leaker flipped 14 green->red (13.3%,
Wilson 95% CI 8.1-21.2%), i.e. roughly 41-107 modules would actually manifest.
A further 393 modules import only inside functions and are structurally immune
-- 0 of 20 sampled broke, which is the control that makes the predicate
believable in both directions.

THIS CUT FIXES ONE LEAKER, NOT THE CLASS. Runtime measurement over the 20
non-conftest package-killers found 11 that genuinely orphan module objects.
This file's subject is the one with a deterministic repro. The other ten are
the same idiom away from the same repair, and that is a follow-up, not a claim
made here.

WHY NOT THE OBVIOUS FIX. Generalising the @1034 conftest guard from three
registered names to all of them was designed and then REFUTED by measurement:
against a leaker that deletes and immediately re-imports at teardown, the guard
observes no absence, classifies the damage as a deliberate swap, and the same
four failures reproduce with the guard installed
(`BD-HANDBACK: tracked=13 handed-back=0 swapped-left-alone=10`). A guard that
watches the damage and files it as a decision is section 0's defect wearing the
uniform of a fix.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEAKER = REPO / "tests" / "test_v3_66_1021_log_reinit_replaces.py"
# The documented victim from the register's deterministic repro. It holds
# `from bulk_downloader import db, library_final` at module scope.
VICTIM = REPO / "tests" / "test_v3_66_915_audit_caps_are_one_window.py"

# -p no:randomly is load-bearing: the defect is ORDER-dependent, so a shuffled
# run can put the victim first and observe nothing. Pinning the order is what
# makes this test deterministic rather than a coin flip.
_ARGS = ("-q", "-p", "no:randomly")


def _run(paths) -> subprocess.CompletedProcess:
    """Always run from the repo root, and never with BD_INSTALL_DIR set.

    An inherited BD_INSTALL_DIR is preferred by db._resolve_db_path over the
    working directory, so every test in the child would share ONE database and
    the per-test isolation conftest.py provides is defeated -- measured at 89
    false failures. capture.sh refuses outright when it is set; this does the
    same by popping it out of the child's environment.
    """
    import os

    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)          # POP, not "refrain from setting"
    env["BD_DISABLE_KEEPALIVE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", *[str(p) for p in paths], *_ARGS],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=600,
    )


def test_the_repro_files_are_both_present():
    """Denominator first. If either path has moved, every assertion below would
    pass over a pytest that collected nothing -- a gate reporting OK because it
    examined nothing, which is worse than no gate."""
    assert LEAKER.is_file(), f"leaker missing at {LEAKER}"
    assert VICTIM.is_file(), f"victim missing at {VICTIM}"


def test_the_victim_passes_alone():
    """THE CONTROL, and it is not ceremony.

    Without it, a red result in the pair below is equally explained by "the
    leaker orphaned the victim's bindings" and by "the victim is simply
    broken". Only one of those is this cut's subject, and changing one variable
    at a time is the difference between a measurement and an argument.
    """
    r = _run([VICTIM])
    assert r.returncode == 0, (
        "the victim does not pass in isolation, so the paired run below cannot "
        f"attribute anything to the leaker.\nrc={r.returncode}\n{r.stdout[-3000:]}"
    )


def test_the_leaker_does_not_orphan_the_victims_bindings():
    """The gate. Leaker first, victim second, one process, fixed order."""
    r = _run([LEAKER, VICTIM])
    assert r.returncode == 0, (
        "running the sys.modules leaker before the victim turned the victim "
        "red, so the leaker left the module table wiped and the victim's "
        "import-time bindings point at orphaned module objects.\n"
        f"rc={r.returncode}\n{r.stdout[-4000:]}"
    )
