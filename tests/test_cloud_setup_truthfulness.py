"""scripts/cloud-setup.sh must report what actually happened.

The provisioning report opens with "Read this before trusting any test result."
A next session is told, in writing, to treat it as authority. That makes every
inaccuracy in it a CLAUDE.md 0 defect rather than a cosmetic one: a row that
says OK when the step did not happen, or names the wrong cause for a failure,
is a gate reporting clean over a denominator that excludes its subject.

These gates assert on BEHAVIOUR -- the functions are extracted from the script
and executed -- not on substrings. A substring test cannot tell a real
diagnostic from a hardcoded one, which is precisely the bug in `step`.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CLOUD_SETUP = REPO_ROOT / "scripts" / "cloud-setup.sh"

# The seven release-guard files, and where their authoritative SHAs live.
# CLAUDE.md's table is the operator-declared record; cloud-setup.sh keeps a
# private copy. Two copies of a number that must agree is a denominator that
# drifts, and the copy nobody re-derives is the one that goes stale.
GUARD_PATHS = (
    "bulk_downloader/extraction_core.py",
    "bulk_downloader/session_capture.py",
    "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py",
    "bulk_downloader/capture_bodies.py",
    "tools/capture_session.py",
    "tools/build_release.py",
)


def _script() -> str:
    return CLOUD_SETUP.read_text(encoding="utf-8")


def _extract_function(name: str) -> str:
    """Return the bash source of `name`, from its header to the closing brace.

    Extracting and executing the real function is what makes these tests
    behavioural. Asserting over the whole file's text would let a hardcoded
    string satisfy a gate meant to check a computed one.
    """
    source = _script()
    header = re.search(rf"^{re.escape(name)}\(\)\s*\{{", source, re.M)
    assert header, f"{name}() not found in {CLOUD_SETUP} -- anchor stale"
    tail = source[header.start():]
    close = re.search(r"^\}", tail, re.M)
    assert close, f"no column-0 closing brace for {name}() -- anchor stale"
    return tail[: close.end()]


def _run_bash(snippet: str, *, cwd: Path | None = None, env: dict | None = None,
              timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True, text=True, cwd=str(cwd) if cwd else None,
        env=env, timeout=timeout,
    )


# --------------------------------------------------------------------------
# 1. The private guard-SHA copy must not go stale.
# --------------------------------------------------------------------------

def test_guard_pins_in_cloud_setup_match_the_files_on_disk():
    """A stale pin makes the provisioner cry wolf on an intact tree.

    CLAUDE.md 0's inverse: a gate that fires on identity gets switched off, so
    over-sensitivity is a soundness bug, not a safe default. This hashes the
    real files rather than comparing cloud-setup.sh's copy to CLAUDE.md's copy
    -- comparing the two copies to each other would pass if both went stale
    together.
    """
    source = _script()
    pins = dict(re.findall(r'"([^"]+\.py)"\s*:\s*"([0-9a-f]{16})"', source))
    assert pins, "no guard pins found in cloud-setup.sh -- anchor stale"

    wrong = []
    for path, pinned in sorted(pins.items()):
        target = REPO_ROOT / path
        assert target.is_file(), f"cloud-setup.sh pins a nonexistent file: {path}"
        actual = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
        if actual != pinned:
            wrong.append(f"{path}: pinned {pinned}, actual {actual}")

    assert not wrong, (
        "cloud-setup.sh's guard pins have drifted from the tree:\n  "
        + "\n  ".join(wrong)
        + "\nThe provisioner would report a guard mismatch on an intact tree."
    )


def test_every_guard_file_is_pinned_so_the_denominator_contains_the_subject():
    """A pin list missing a guard reports OK for a file it never examined."""
    source = _script()
    pinned = set(re.findall(r'"([^"]+\.py)"\s*:\s*"[0-9a-f]{16}"', source))
    missing = [p for p in GUARD_PATHS if p not in pinned]
    assert not missing, (
        f"cloud-setup.sh's guard step cannot see these guard files: {missing}"
    )


# --------------------------------------------------------------------------
# 2. step()'s optional branch must report the real cause.
# --------------------------------------------------------------------------

def test_optional_step_failure_reports_the_real_diagnostic():
    """The WARN row must carry the command's own output, not a fixed sentence.

    `step` currently writes "absent; dependent work cannot run" for EVERY
    optional failure. That sentence is true for an uninstalled package and
    false for everything else -- a drifted guard, a syntax error, a 403. The
    one report a next session is told to trust then names the wrong cause,
    which is worse than naming none.
    """
    harness = textwrap.dedent(
        """
        set -uo pipefail
        REPORT="$PWD/report.md"; : > "$REPORT"
        CORE_FAILED=0
        %s
        %s
        step "probe" optional bash -c 'echo GUARD_DRIFT_SENTINEL_9f3a >&2; exit 7'
        cat "$REPORT"
        """
    ) % (_extract_function_row(), _extract_function("step"))

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_bash(harness, cwd=Path(tmp))

    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    assert "GUARD_DRIFT_SENTINEL_9f3a" in proc.stdout, (
        "the optional WARN row discarded the command's real output and "
        f"substituted a fixed string.\nRow written:\n{proc.stdout}"
    )


def test_optional_step_failure_does_not_assert_a_cause_it_did_not_observe():
    """"absent" is a specific claim; step() cannot know it is true."""
    harness = textwrap.dedent(
        """
        set -uo pipefail
        REPORT="$PWD/report.md"; : > "$REPORT"
        CORE_FAILED=0
        %s
        %s
        step "probe" optional bash -c 'echo REAL_CAUSE_HERE >&2; exit 7'
        cat "$REPORT"
        """
    ) % (_extract_function_row(), _extract_function("step"))

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_bash(harness, cwd=Path(tmp))

    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    assert "absent; dependent work cannot run" not in proc.stdout, (
        "step() still hardcodes a cause it did not observe. Every optional "
        "failure -- guard drift, syntax error, HTTP 403 -- is reported as an "
        "absent capability."
    )


def _extract_function_row() -> str:
    """`row` is a one-liner (`row(){ ...; }`), so the brace extractor misses it."""
    for line in _script().splitlines():
        if line.startswith("row()"):
            return line
    raise AssertionError("row() not found in cloud-setup.sh -- anchor stale")


# --------------------------------------------------------------------------
# 3. find_repo must not guess.
# --------------------------------------------------------------------------

def test_find_repo_refuses_rather_than_searching_the_filesystem(tmp_path):
    """With no named probe matching, find_repo must FAIL, not go hunting.

    The bounded `find /` fallback ranks candidates by path depth and takes the
    shallowest. On any host that has ever run the test suite, /tmp is full of
    two- and three-file pytest fixtures containing bulk_downloader/__init__.py,
    and those are SHALLOWER than the real checkout. Measured on this host: 70
    candidates, and the winner was a 3-file fixture directory.

    Provisioning against a fixture is not a degraded success. Every subsequent
    step reports OK about the wrong tree.
    """
    empty_home = tmp_path / "home"
    empty_home.mkdir()
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()

    harness = textwrap.dedent(
        """
        set -uo pipefail
        MARKER="bulk_downloader/__init__.py"
        %s
        if out="$(find_repo)"; then
          echo "RETURNED:$out"
        else
          echo "REFUSED"
        fi
        """
    ) % _extract_function("find_repo")

    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(empty_home),
        "PWD": str(cwd),
    }
    proc = _run_bash(harness, cwd=cwd, env=env, timeout=300)

    assert "REFUSED" in proc.stdout, (
        "find_repo returned a path when no named probe matched:\n"
        f"{proc.stdout}\n"
        "A filesystem search cannot distinguish the real checkout from a "
        "leftover test fixture, and it silently prefers the shallower one."
    )


def test_repo_ambiguity_warning_is_not_dead_code():
    """A WARN that cannot fire is indistinguishable from no WARN at all.

    BD_REPO_CANDIDATES was assigned INSIDE find_repo, which is invoked as
    `REPO="$(find_repo)"` -- a command substitution, i.e. a subshell. The
    assignment never reached the parent, so the guard at the report site always
    read the `:-1` default and the ambiguity row could never be emitted. The
    script looked like it handled multiple checkouts; it could not.
    """
    source = _script()
    if "BD_REPO_CANDIDATES" not in source:
        pytest.skip("BD_REPO_CANDIDATES removed entirely -- no dead branch left")

    body = _extract_function("find_repo")
    assigns_in_subshell = "BD_REPO_CANDIDATES=" in body
    assert not assigns_in_subshell, (
        "BD_REPO_CANDIDATES is assigned inside find_repo(), which runs in a "
        "command-substitution subshell; the value cannot escape, so the "
        "ambiguity WARN that reads it is unreachable."
    )


# --------------------------------------------------------------------------
# 4. The report must date itself against the tree, not the wall clock.
# --------------------------------------------------------------------------

def test_report_header_records_the_tree_it_was_generated_against():
    """A timestamp alone cannot answer "is this still true?".

    The report on this host was seven days old and asserted a version the tree
    had long since moved past -- while instructing the reader to trust it. A
    consumer needs the version and commit to decide staleness; without them
    the honest answer is UNKNOWN, and unknown is a third state that fails.
    """
    source = _script()
    assert "generated_against_version" in source, (
        "the report header records no tree version, so a reader cannot tell a "
        "current report from one written against a different tree"
    )
    assert "generated_against_commit" in source, (
        "the report header records no commit, so staleness is unknowable"
    )


def test_report_records_how_the_repo_was_located():
    """Which checkout was provisioned is load-bearing and must not be inferred."""
    source = _script()
    assert "repo location" in source or "located via" in source, (
        "the report never says HOW the checkout was found, so a reader cannot "
        "tell a BD_REPO-directed run from a lucky $PWD"
    )


# --------------------------------------------------------------------------
# 5. No repo is UNKNOWN, and unknown fails.
# --------------------------------------------------------------------------

def test_missing_repo_does_not_exit_zero():
    """Exiting 0 having provisioned nothing is a false READY.

    The script's own verdict text calls this case "a sequencing fact, not a
    failure" and exits 0. But the caller cannot distinguish that exit from a
    successful provision, and the report it wrote says APP DEFERRED in prose a
    machine never reads.
    """
    source = _script()
    tail = source[source.rindex("exit "):]
    assert 'exit "$CORE_FAILED"' not in tail or "HAVE_REPO" in tail, (
        "the final exit is CORE_FAILED alone, so a run that found no checkout "
        "and installed nothing exits 0, indistinguishable from success"
    )
