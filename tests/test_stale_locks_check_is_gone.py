"""check_stale_locks is deleted. This keeps it deleted, and says why.

The check WARNed on `*.lock` files older than 6h across the download and
capture roots, on the theory that a crashed process leaves one behind. Nothing
in this tree has ever written a `*.lock` file, so the theory was never true.

WHAT IT ACTUALLY FOUND. `captures_root` falls back to PROJECT_ROOT when the
`capture_store_root` app-config key is absent -- which it is on the box -- so
the rglob descended the whole install tree including venv/ and node_modules/.
Its three hits on test4 were vendored npm `yarn.lock` dependency manifests
inside stale agent worktrees. Dependency manifests, not process locks.
tests/test_gitignore_rules_actually_match.py:63 already names that exact trap
("Ephemeral agent worktrees live under the repository root; rglob descends
into them"); the selftest fell into a hole a sibling test had labelled.

WHY DELETE RATHER THAN RE-POINT. A check whose subject does not exist cannot
fail, so it reported OK forever and then, once the fallback denominator
widened, WARNed about other people's files. Both readings are useless. There
is no BD artifact to re-point it at: storage_tier's exclusive-create
placeholder is dest_path ITSELF (storage_tier.py:209-211), never `.lock`
suffixed, and it is removed inside the same call.

WHAT THIS FILE DOES NOT CLAIM. An earlier draft of the rationale said
"check_orphan_tempfiles already covers BD's real temp artifacts". Measured at
v3.66.843: overstated. That check uses a NON-recursive `base.glob(pat)`
(selftest.py), while crash_recovery.py:141 rglobs `*.part` precisely because
those nest. So a nested `.part` is missed. That gap is real, is filed
separately as canonical backlog row 162, and is NOT what this deletion fixes -- deleting a
check with no subject is right on its own terms, and conflating the two would
let a real gap ride along as though it had been addressed.

THE ASSERTION THAT IS DELIBERATELY ABSENT. A repo-wide "no tracked source
contains a '.lock' literal" gate was designed and then dropped, for two
measured reasons. First, it flags its own file: its fixture and predicate
literals enter the `git ls-files` denominator it defines, so it fails the
moment it is staged -- a gate whose denominator contains itself. Second, the
predicate is not the subject: across 2105 tracked .py the seven non-docstring
`.lock` constants are this check, the housekeeping consumer, two test
fixtures, an assertion MESSAGE, and `plex_deep.py:316`'s `'addedAt.locked'` --
a Plex API field that matches only because `.lock` is a substring of
`.locked`. That is the CLAUDE.md section 1 substring trap. A gate built on it
would fire on correct code.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest  # noqa: F401

from bulk_downloader import selftest

REPO_ROOT = Path(__file__).resolve().parents[1]
SELFTEST_SRC = REPO_ROOT / "bulk_downloader" / "selftest.py"
HOUSEKEEPING_SRC = REPO_ROOT / "bulk_downloader" / "dev_suite" / "housekeeping.py"


def test_the_symbol_is_gone():
    assert not hasattr(selftest, "check_stale_locks"), (
        "bulk_downloader.selftest.check_stale_locks is back. It has no "
        "producer -- nothing in this tree writes a *.lock file -- so it can "
        "only report OK forever or WARN about another program's files. Read "
        "the module docstring here and backlog row 162 before restoring it."
    )


def test_no_selftest_function_globs_for_dot_lock():
    """Structural, so a rename or a re-point cannot satisfy it.

    Scoped to selftest.py on purpose. The repo-wide version of this gate was
    dropped -- see the module docstring; it flagged its own file and its
    predicate matched a Plex field name.
    """
    tree = ast.parse(SELFTEST_SRC.read_text(encoding="utf-8"))
    docstrings = {
        ast.get_docstring(n, clean=False)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef))
    }
    offenders = [
        f"selftest.py:{n.lineno}: {n.value!r}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and ".lock" in n.value
        and n.value not in docstrings
    ]
    assert not offenders, (
        "selftest.py names a .lock pattern outside a docstring:\n  "
        + "\n  ".join(offenders)
        + "\n\nNothing in this tree writes one. If a producer now exists, say "
          "so in the docstring above and the canonical backlog before adding a "
          "check that looks for it."
    )


def test_run_all_surfaces_no_stale_locks_row(tmp_path):
    """Behavioural: the real run_all, over a directory holding a real old lock.

    The second assertion is an anti-vacuity clamp. Without it this test would
    pass if run_all stopped emitting hygiene rows altogether -- a pass for the
    wrong reason, which is the failure mode the whole file is about.
    """
    stale = tmp_path / "runner.lock"
    stale.write_text("", encoding="utf-8")
    old = time.time() - 48 * 3600
    import os
    os.utime(stale, (old, old))

    report = selftest.run_all(
        sites_config_path=None,
        db_path=None,
        cookies_dir=str(tmp_path),
        download_dirs=[str(tmp_path)],
        captures_root=str(tmp_path),
    )
    # The record key is "test", not "name" -- selftest._result() builds
    # {status, test, message, detail, ts}. Guessing "name" here made this
    # assertion die on KeyError during the RED proof: red, but for the wrong
    # reason, and it would have kept dying after the fix landed.
    names = {c["test"] for c in report["checks"]}

    assert "stale_locks" not in names, (
        f"run_all still emits a stale_locks row; saw {sorted(names)}. The "
        f"check was deleted at v3.66.843 -- if this is back, so is the "
        f"registration at selftest.py run_all()."
    )
    assert "orphan_tempfiles" in names, (
        f"ANTI-VACUITY: run_all emitted no orphan_tempfiles row either, so "
        f"the assertion above proved nothing -- it would pass over an empty "
        f"check list. Saw {sorted(names)}."
    )


def test_housekeeping_no_longer_attributes_lock_files_to_storage_tier():
    """The false comment goes, and the true one must arrive in its place.

    Asserting only the absence would be satisfied by deleting the comment
    outright, which loses the measured fact that the branch is a generic
    system-temp reporter. So this pins both directions.
    """
    src = HOUSEKEEPING_SRC.read_text(encoding="utf-8")
    assert "storage_tier creates" not in src, (
        "dev_suite/housekeeping.py still attributes .lock files to "
        "storage_tier. That is false at source: storage_tier's exclusive "
        "placeholder is dest_path itself (storage_tier.py:209-211), never "
        ".lock-suffixed, and it is removed in the same call."
    )
    assert "NOT a BD artifact" in src, (
        "The false storage_tier attribution was removed but nothing replaced "
        "it. Deleting the comment loses the measured fact -- that this branch "
        "reports some OTHER program's lock files -- and the next reader will "
        "re-derive the wrong explanation. Keep a comment that says what is "
        "true."
    )
