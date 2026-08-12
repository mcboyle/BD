"""test_v3_66_1034's deliberate wipe must not damage OTHER files.

@1069, ledger item 48 and backlog row 22. The wipe is legitimate -- 1034 exists
to prove the conftest guards survive a sys.modules wipe, and it cannot do that
without wiping. What is NOT legitimate is leaving the module table wiped when
the file finishes, because under `--dist loadfile` whatever pytest schedules
next on that worker inherits it.

THE MEASURED CONSEQUENCE, item 48's second mechanism. bulk_downloader/app.py's
`_csrf_key` is module-level, so a fresh module EXECUTION mints a new one. A
victim that bound `from bulk_downloader.app import app` at COLLECTION time
validates with the OLD key while /api/csrf mints with the NEW one, and every
mutating request 403s. Reproduced deterministically:

    1034 then 780              -> 7 failed, 12 passed
    deselect only the wiper    -> 18 passed, 1 deselected
    reverse the order          -> 19 passed

THE RE-DERIVATION THAT MADE THIS ACTIONABLE (@1068). The static census
over-reports by design and listed 14 files. Measured at RUNTIME with
bd-modwatch in per-file mode: only THREE actually orphan the module table, and
two of those drop exactly `bulk_downloader.push`, which NOTHING binds at import
time -- zero importers, so those two are harmless. Item 48 is one file, not
eleven, not thirteen. Every earlier count was of a static heuristic, not of a
leak.

The fix mirrors what v3.66.1049 did for test_v3_66_1021: save the module table
for the file, let the wipe happen for the in-file assertions, restore on
teardown so the blast radius is the file rather than the worker.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_LEAKER = "tests/test_v3_66_1034_guards_survive_a_module_wipe.py"
_VICTIM = "tests/test_v3_66_780_config_key_write_parity.py"


def _run(*files, extra=()):
    return subprocess.run(
        [sys.executable, "-m", "pytest", *files, "-q", "-p", "no:randomly",
         "--no-header", *extra],
        cwd=_REPO, capture_output=True, text=True, timeout=900,
        env={**_env(), "BD_DISABLE_KEEPALIVE": "1"})


def _env():
    import os
    e = {k: v for k, v in os.environ.items() if k != "BD_INSTALL_DIR"}
    return e


def test_the_victim_passes_alone():
    """PRECONDITION, before any verdict: the victim is healthy by itself.

    Without this, a victim broken for an unrelated reason would make the
    ordering test below look like a leak that is not there.
    """
    r = _run(_VICTIM)
    assert r.returncode == 0, (
        f"the victim does not pass in isolation, so this file cannot attribute "
        f"anything:\n{r.stdout[-1500:]}"
    )


def test_the_wipe_does_not_break_a_later_file_on_the_same_worker():
    """THE DEFECT. One process, leaker first, victim second."""
    r = _run(_LEAKER, _VICTIM)
    assert r.returncode == 0, (
        f"running {_LEAKER} before {_VICTIM} in ONE process broke the victim -- "
        f"the wipe escaped its own file. Under --dist loadfile this is decided "
        f"by the schedule, which is why the failure count looked like noise.\n"
        f"{r.stdout[-2500:]}"
    )


def test_the_leaker_still_does_its_own_job():
    """OVER-SENSITIVITY CONTROL, and it is the half that matters.

    A 'fix' that stopped 1034 wiping at all would satisfy the test above and
    destroy the guard suite it exists to be. The file must still pass on its
    own, wipe included.
    """
    r = _run(_LEAKER)
    assert r.returncode == 0, (
        f"the leaker no longer passes on its own -- the restore has broken the "
        f"wipe it is built around:\n{r.stdout[-2000:]}"
    )


def test_the_module_table_is_restored_by_the_time_the_file_ends(tmp_path):
    """Directly, rather than inferring it from the victim.

    A conftest-level repair elsewhere could make the victim pass while 1034
    still leaves the table wiped, and this file would then certify something it
    does not test.

    THE PROBE LIVES IN tmp_path, NOT IN tests/. The first version wrote it into
    tests/ and unlinked it, and PIN_INDEX's regen -- which globs tests/*.py --
    raced the unlink and died with FileNotFoundError in a parallel band.
    CLAUDE.md section 2a says it plainly: files appearing in tests/ contaminate
    the regen, because tests/ is the DENOMINATOR of several gates. A test that
    briefly creates one is the same defect with a shorter fuse.
    """
    probe = tmp_path / "test_zzzz_1069_probe_after_wipe.py"
    probe.write_text(
        "import sys\n"
        "def test_module_table_is_not_wiped():\n"
        "    live = [m for m in sys.modules if m.startswith('bulk_downloader')]\n"
        "    assert live, (\n"
        "        'the bulk_downloader module table is EMPTY at the start of a "
        "later file -- the wipe escaped its own file')\n",
        encoding="utf-8")
    r = _run(_LEAKER, str(probe))
    assert r.returncode == 0, (
        f"a file running after the leaker sees an empty bulk_downloader "
        f"module table:\n{r.stdout[-1500:]}"
    )
