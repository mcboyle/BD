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

    # WHY THE ABSOLUTE RUNGS ARE RE-POINTED INTO A SANDBOX. find_repo's path
    # list gained `/home/*/BD` so that it covers every rung cloud-bootstrap.sh
    # is willing to hand over. On any host that HAS a checkout under /home --
    # this one does, at /home/user/BD -- that rung matches regardless of what
    # the caller intended, so "no named probe matched" becomes unreachable and
    # the refusal this test exists to assert is never exercised. Pointing the
    # absolutes at an empty sandbox restores the condition; every other line of
    # the shipped function, including the whole branch under test, is verbatim.
    # Same remedy as tests/test_cloud_bootstrap_is_thin.py.
    sandbox = tmp_path / "root"
    sandbox.mkdir()
    find_repo_src = _extract_function("find_repo")
    for absolute in ("/workspace", "/repo", "/src", "/app", "/home/*"):
        find_repo_src = find_repo_src.replace(f" {absolute}", f" {sandbox}{absolute}")
    assert str(sandbox) in find_repo_src, (
        "the rewrite did not take, so this test would probe real absolute "
        "paths and could pass or fail on what happens to exist on the host"
    )

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
    ) % find_repo_src

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


# ── the frontend bundle: npm ci is not a build, and exit 0 is not an artifact ──
#
# frontend/dist is gitignored with ZERO tracked files, so `git reset --hard`
# never delivers it and a fresh container has none (CLAUDE.md section 7).
# cloud-setup.sh ran `npm ci`, which installs the toolchain and produces no
# bundle. Two tests then fail and neither names the cause:
#
#   test_v3_66_790_nuitka_config::test_data_dirs_all_exist_in_tree
#       -> "declared data dir does not exist: frontend/dist"
#   test_phase1_root_flip::test_missing_asset_is_404_not_spa_html
#       -> 503, because bulk_downloader/app.py cannot serve an absent bundle
#
# Measured in this container: both fail before `npm run build` and pass after,
# with nothing else changed. They were the last two failures in the whole
# backlog batch that had to be waved away as "environmental" -- so building the
# bundle is also what makes a future occurrence real signal instead of noise.

_FRONTEND_START = "frontend deps"
# The END anchor is the NEXT SECTION's banner, deliberately not one of the
# strings the tests below assert on. Anchoring on `CORE_FAILED=1` would bound
# the block by one of its own subjects, making
# `assert "CORE_FAILED=1" in block` true by construction -- an unfailable
# assertion, which is the failure mode this repo treats as worse than no test.
_FRONTEND_END = "2. browsers"


def _frontend_block() -> str:
    """The lines of cloud-setup.sh that build and verify the bundle.

    Bounded by two CONTENT anchors, not by a fixed width. The original sliced
    `src[start:start + 2000]`, which is the pattern
    tests/test_source_windows_do_not_shift.py ratchets against: a window like
    that silently stops covering its subject the moment unrelated lines are
    added inside it, and the test then passes over a region that no longer
    contains what it asserts on. That regression was introduced by #91 -- this
    file's own cut -- and sat on main for five releases because no band
    included the meta-test that scans every tracked test file.

    Both anchors are strings this file already asserts on, so if either moves
    the assertion below fails loudly and names it, rather than the block
    quietly shrinking past the thing under test.
    """
    src = CLOUD_SETUP.read_text(encoding="utf-8")
    start = src.find(_FRONTEND_START)
    assert start != -1, (
        f"{_FRONTEND_START!r} is gone from cloud-setup.sh; this gate lost its "
        f"subject")
    end = src.find(_FRONTEND_END, start)
    assert end != -1, (
        f"{_FRONTEND_END!r} is gone from cloud-setup.sh, so the frontend block "
        f"has no end anchor and this gate would read to end-of-file")
    return src[start:end]


def test_cloud_setup_builds_the_frontend_bundle():
    """RED. npm ci installs the toolchain; it does not emit frontend/dist."""
    block = _frontend_block()
    # Assert the STEP, not the substring. `npm run build` also appears inside
    # the failure-message string below it, so a bare `in block` check is
    # satisfied by PROSE DESCRIBING the build even when the build itself is
    # gone -- proven by mutation: replacing the step body with `true` left this
    # test green. A description of a thing is not the thing.
    assert re.search(r'step\s+"frontend build".*npm run build', block), (
        "cloud-setup.sh installs the frontend toolchain but never builds the "
        "bundle, so frontend/dist is absent in every provisioned container and "
        "the SPA cannot be served"
    )


def test_the_frontend_bundle_is_verified_by_reading_the_artifact():
    """RED, and the half that matters.

    `tsc -b && vite build` exiting 0 is not the property anyone depends on --
    the property is that the entry point exists. This is the same lesson as
    step [7]'s route_source read-back and the graph pin's check-hash: a
    provisioner that trusts an exit code reports a green host for a container
    that cannot serve a page.
    """
    block = _frontend_block()
    assert "frontend/dist/index.html" in block, (
        "the build step is graded by its exit code alone. Read the artifact "
        "back -- vite can exit 0 having written nothing the app can serve"
    )
    assert "CORE_FAILED=1" in block, (
        "a missing bundle is recorded without failing the provision, so the "
        "report says READY for a container whose asset routes 503"
    )


def test_the_missing_bundle_branch_actually_fails(tmp_path):
    """BEHAVIOURAL. Execute the verification with the artifact absent and
    require it to set CORE_FAILED -- an unfailable branch is the sec0 defect
    this whole file exists to catch."""
    src = CLOUD_SETUP.read_text(encoding="utf-8")
    start = src.find("  if [ -f frontend/dist/index.html ]; then")
    assert start != -1, (
        "the bundle read-back is not in the shape this gate can execute; if it "
        "was reworded, move the anchor rather than deleting the assertion"
    )
    verify = src[start:src.find("fi", start) + 2]

    harness = textwrap.dedent(
        """
        set -uo pipefail
        REPORT="$PWD/report.md"; : > "$REPORT"
        CORE_FAILED=0
        %s
        %s
        echo "CORE_FAILED=$CORE_FAILED"
        """
    ) % (_extract_function_row(), verify)

    proc = _run_bash(harness, cwd=tmp_path)          # no frontend/dist here
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    assert "CORE_FAILED=1" in proc.stdout, (
        "with frontend/dist/index.html absent the provisioner still reported a "
        f"healthy host.\n{proc.stdout}"
    )
