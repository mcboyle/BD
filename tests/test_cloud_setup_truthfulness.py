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
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CLOUD_SETUP = REPO_ROOT / "scripts" / "cloud-setup.sh"

# This module executes the production cloud provisioner and its emitted
# recovery helper.  It is deliberately pinned into CI even though its subject
# is one script: a READY verdict is a provisioning safety boundary, and a test
# that no pull request runs cannot protect that boundary.
BD_GATE_SCOPE = "module"

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


def _extract_section(start_marker: str, end_marker: str | None = None) -> str:
    """Return a production shell section bounded by exact content markers."""
    source = _script()
    start = source.find(start_marker)
    assert start != -1, (
        f"start marker {start_marker!r} not found in {CLOUD_SETUP} -- "
        "the executable test lost its subject")
    if end_marker is None:
        return source[start:]
    end = source.find(end_marker, start + len(start_marker))
    assert end != -1, (
        f"end marker {end_marker!r} not found after {start_marker!r} -- "
        "the executable test has no bounded subject")
    return source[start:end]


def _extract_heredoc(start_marker: str, delimiter: str) -> str:
    """Extract the exact standalone script body emitted by a heredoc."""
    source = _script()
    start = source.find(start_marker)
    assert start != -1, f"heredoc marker {start_marker!r} is absent"
    start += len(start_marker)
    end_marker = f"\n{delimiter}\n"
    end = source.find(end_marker, start)
    assert end != -1, f"heredoc delimiter {delimiter!r} is absent"
    return source[start:end] + "\n"


# --------------------------------------------------------------------------
# 1. The private guard-SHA copy must not go stale.
# --------------------------------------------------------------------------

def test_guard_pins_in_cloud_setup_match_the_files_on_disk():
    """Cloud setup invokes the production checker instead of copying pins."""
    source = _script()
    pins = dict(re.findall(r'"([^"]+\.py)"\s*:\s*"([0-9a-f]{16})"', source))
    assert not pins
    assert "toolchain/bin/bd-guardcheck --tree \"$PWD\"" in source


def test_every_guard_file_is_pinned_so_the_denominator_contains_the_subject():
    """The canonical manifest, not cloud prose, owns the exact denominator."""
    manifest = json.loads((REPO_ROOT / "guards.json").read_text())
    assert set(manifest["guards"]) == set(GUARD_PATHS)


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
    # Row 338 measured 0.010162s; max(60, ceil(2 * 0.010162)) = 60s.
    proc = _run_bash(harness, cwd=cwd, env=env, timeout=60)

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
#   test_spa_root_routing_contract::test_missing_asset_is_404_not_spa_html
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


# --------------------------------------------------------------------------
# Row 341: READY requires the GUI-parity artifact that defines the inventory.
# --------------------------------------------------------------------------

_GUI_PARITY_START = (
    "# ==================================================== 7c. reconcile inventories"
)
_GUI_PARITY_END = "# ================================================================ 8. runtime"
_VERDICT_START = "# =============================================================== 12. verdict"


def _run_gui_parity_verdict(tmp_path: Path, mode: str):
    """Execute the real GUI-parity and final-verdict blocks.

    The generator fixture is an executable Python program, not a replacement
    for the shell under test.  It can exit zero without writing, emit either
    provenance state, or fail after a prior valid artifact was planted.  Thus
    each arm reaches the production ``step`` and read-back decisions.
    """
    work = tmp_path / mode
    (work / "tools").mkdir(parents=True)
    (work / "venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, work / "venv" / "bin" / "python")
    generator = work / "tools" / "gui_parity_inventory.py"
    generator.write_text(textwrap.dedent(
        """
        import json
        import os
        from pathlib import Path

        mode = os.environ["PROBE_GUI_MODE"]
        with Path("generator.log").open("a", encoding="utf-8") as log:
            log.write(mode + "\\n")
        if mode == "generator-fails-with-stale-live":
            raise SystemExit(7)
        if mode != "missing":
            out = Path("reports/gui_parity_inventory.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            route_source = "live url_map" if mode == "live" else "endpoint catalog"
            out.write_text(json.dumps({"route_source": route_source}), encoding="utf-8")
        """), encoding="utf-8")

    inventory = work / "reports" / "gui_parity_inventory.json"
    existed_before = inventory.exists()
    if mode == "generator-fails-with-stale-live":
        inventory.parent.mkdir(parents=True)
        inventory.write_text(
            json.dumps({"route_source": "live url_map"}), encoding="utf-8")

    harness = textwrap.dedent(
        """
        set -uo pipefail
        REPORT="$PWD/report.md"; : > "$REPORT"
        CORE_FAILED=0
        HAVE_REPO=1
        REPO="$PWD"
        START=$(date +%%s)
        %s
        %s
        %s
        %s
        """
    ) % (
        _extract_function_row(),
        _extract_function("step"),
        _extract_section(_GUI_PARITY_START, _GUI_PARITY_END),
        _extract_section(_VERDICT_START),
    )
    env = dict(os.environ)
    env["PROBE_GUI_MODE"] = mode
    proc = _run_bash(harness, cwd=work, env=env)

    calls = (work / "generator.log").read_text(encoding="utf-8").splitlines()
    assert calls == [mode], (
        f"the production generator step fired {len(calls)} times, expected "
        f"exactly once: {calls}")
    return proc, inventory, existed_before, (work / "report.md").read_text(
        encoding="utf-8")


def _proc_context(proc: subprocess.CompletedProcess[str]) -> str:
    return f"\nreturncode={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def test_gui_parity_generator_exit_zero_without_artifact_is_incomplete(tmp_path):
    """RED: exit zero is not evidence that the inventory was generated."""
    proc, inventory, existed_before, report = _run_gui_parity_verdict(
        tmp_path, "missing")
    assert not existed_before, "the missing-artifact fixture started with an artifact"
    assert not inventory.exists(), (
        "the no-output generator unexpectedly created the inventory; this no "
        "longer exercises the demonstrated absent-artifact state")
    assert proc.returncode == 1, (
        "cloud-setup returned success after the generator exited zero without "
        "reports/gui_parity_inventory.json; it advertised READY with the "
        "load-bearing artifact absent" + _proc_context(proc))
    assert "## VERDICT: INCOMPLETE" in report
    assert "=== READY" not in proc.stdout


def test_gui_parity_live_inventory_remains_ready(tmp_path):
    """Healthy control: a parsed, app-derived inventory still earns READY."""
    proc, inventory, existed_before, report = _run_gui_parity_verdict(
        tmp_path, "live")
    assert not existed_before
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    assert payload["route_source"] == "live url_map"
    assert proc.returncode == 0, _proc_context(proc)
    assert "## VERDICT: READY" in report
    assert "inventory route source | OK" in report, (
        "the healthy artifact was never read back, so READY still rests only "
        "on the generator's exit code")


def test_gui_parity_catalog_fallback_is_incomplete(tmp_path):
    """Negative control: valid JSON from the wrong route source is not good."""
    proc, inventory, existed_before, report = _run_gui_parity_verdict(
        tmp_path, "catalog")
    assert not existed_before
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    assert payload["route_source"] == "endpoint catalog"
    assert proc.returncode == 1, (
        "catalog-derived inventory was accepted as app-derived evidence" +
        _proc_context(proc))
    assert "## VERDICT: INCOMPLETE" in report


def test_gui_parity_generator_failure_cannot_reuse_stale_live_artifact(tmp_path):
    """The generator command itself is core even when old output looks valid."""
    proc, inventory, existed_before, report = _run_gui_parity_verdict(
        tmp_path, "generator-fails-with-stale-live")
    assert not existed_before, "fixture bookkeeping must precede the stale plant"
    assert json.loads(inventory.read_text(encoding="utf-8"))["route_source"] == (
        "live url_map")
    assert proc.returncode == 1, (
        "the failed generation attempt was demoted to WARN and a stale artifact "
        "laundered the run into READY" + _proc_context(proc))
    assert "gui-parity inventory | **FAILED** | exit 7" in report
    assert "## VERDICT: INCOMPLETE" in report


# --------------------------------------------------------------------------
# Row 341: the advertised bd-provision recovery must build and read back SPA.
# --------------------------------------------------------------------------

_PROVISION_HEREDOC = "cat > \"$BIN/bd-provision\" <<'PROV'\n"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _run_bd_provision(tmp_path: Path, npm_mode: str):
    work = tmp_path / npm_mode
    repo = work / "repo"
    bindir = work / "bin"
    repo.joinpath("bulk_downloader").mkdir(parents=True)
    repo.joinpath("bulk_downloader", "__init__.py").write_text(
        '__version__ = "3.66.999"\n', encoding="utf-8")
    repo.joinpath("frontend").mkdir()
    repo.joinpath("scripts", "lib").mkdir(parents=True)
    repo.joinpath("scripts", "lib", "system_deps.sh").write_text(textwrap.dedent(
        """
        bd_playwright_engines() {
          if [ "$1" = core ]; then echo chromium; else echo firefox; fi
        }
        """), encoding="utf-8")
    repo.joinpath("requirements.txt").write_text("# fixture\n", encoding="utf-8")
    repo.joinpath("venv", "bin").mkdir(parents=True)
    bindir.mkdir(parents=True)

    helper = work / "bd-provision"
    _write_executable(
        helper, _extract_heredoc(_PROVISION_HEREDOC, "PROV"))

    control_log = work / "control.log"
    _write_executable(bindir / "python3.12", textwrap.dedent(
        """#!/bin/bash
        echo "system-python $*" >> "$PROBE_CONTROL_LOG"
        exit 0
        """))
    _write_executable(repo / "venv" / "bin" / "pip", textwrap.dedent(
        """#!/bin/bash
        echo "pip $*" >> "$PROBE_CONTROL_LOG"
        exit 0
        """))
    _write_executable(repo / "venv" / "bin" / "python", textwrap.dedent(
        """#!/bin/bash
        echo "venv-python $*" >> "$PROBE_CONTROL_LOG"
        if [ "${1:-}" = "-c" ]; then echo 3.66.999; fi
        exit 0
        """))
    _write_executable(bindir / "npm", textwrap.dedent(
        """#!/bin/bash
        echo "npm $*" >> "$PROBE_CONTROL_LOG"
        if [ "$PROBE_NPM_MODE" = ci-fails ] && [ "${1:-}" = ci ]; then
          exit 7
        fi
        if [ "${1:-}" = run ] && [ "${2:-}" = build ]; then
          if [ "$PROBE_NPM_MODE" = build-fails ]; then exit 9; fi
          if [ "$PROBE_NPM_MODE" = healthy ] || [ "$PROBE_NPM_MODE" = ci-fails ]; then
            mkdir -p "$PROBE_REPO/frontend/dist"
            printf '<!doctype html>\n' > "$PROBE_REPO/frontend/dist/index.html"
          fi
        fi
        exit 0
        """))

    index = repo / "frontend" / "dist" / "index.html"
    assert not index.exists(), "the fresh-checkout fixture already has a SPA bundle"
    env = dict(os.environ)
    env.update({
        "PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin",
        "PROBE_CONTROL_LOG": str(control_log),
        "PROBE_NPM_MODE": npm_mode,
        "PROBE_REPO": str(repo),
    })
    proc = subprocess.run(
        [str(helper), str(repo)], capture_output=True, text=True, env=env,
        cwd=work, timeout=60)
    controls = control_log.read_text(encoding="utf-8").splitlines()
    npm_calls = [line.removeprefix("npm ") for line in controls
                 if line.startswith("npm ")]
    assert npm_calls and npm_calls[0].startswith("ci "), (
        f"the helper never reached its npm dependency precondition: {controls}")
    return proc, index, npm_calls


def test_bd_provision_no_output_build_cannot_report_ready(tmp_path):
    """RED: a successful command without dist/index.html is not a built SPA."""
    proc, index, npm_calls = _run_bd_provision(tmp_path, "no-output")
    assert not index.exists(), (
        "the no-output build control unexpectedly emitted the SPA artifact")
    assert proc.returncode != 0, (
        "bd-provision returned READY without building frontend/dist/index.html" +
        _proc_context(proc))
    assert "run build" in npm_calls, (
        f"the advertised recovery helper never invoked the SPA build: {npm_calls}")
    assert "bd-provision: READY" not in proc.stdout


def test_bd_provision_healthy_build_remains_ready(tmp_path):
    """Healthy control: npm builds index.html and the helper reports READY."""
    proc, index, npm_calls = _run_bd_provision(tmp_path, "healthy")
    assert "run build" in npm_calls, npm_calls
    assert index.read_text(encoding="utf-8") == "<!doctype html>\n"
    assert proc.returncode == 0, _proc_context(proc)
    assert proc.stdout.count("bd-provision: READY") == 1


def test_bd_provision_npm_ci_failure_is_fatal_even_if_a_build_could_pass(tmp_path):
    """Unavailable dependency installation cannot be demoted into READY."""
    proc, _index, npm_calls = _run_bd_provision(tmp_path, "ci-fails")
    assert npm_calls[0].startswith("ci ")
    assert proc.returncode != 0, (
        "npm ci exited 7 but bd-provision demoted it to WARN and returned READY" +
        _proc_context(proc))
    assert "bd-provision: READY" not in proc.stdout


def test_bd_provision_build_failure_is_fatal(tmp_path):
    """Negative control: the build command's nonzero bound still fires."""
    proc, index, npm_calls = _run_bd_provision(tmp_path, "build-fails")
    assert "run build" in npm_calls, npm_calls
    assert not index.exists()
    assert proc.returncode != 0, (
        "npm run build exited 9 but bd-provision returned success" +
        _proc_context(proc))
    assert "bd-provision: READY" not in proc.stdout


# --------------------------------------------------------------------------
# @903 -- the cloud provisioner installed 2 of the 5 declared package groups.
# --------------------------------------------------------------------------
# The fragment declares five. cloud-setup installs FOUR: `node` is excluded
# deliberately and the exclusion is itself asserted below, because 4-of-5 reads
# like an oversight and "fixing" it breaks both machines.
_GROUPS = ("core", "gtk", "lint", "media")
_EXCLUDED_GROUPS = ("node",)


def _setup_code() -> str:
    """cloud-setup.sh with whole-line comments removed.

    Load-bearing: this file MENTIONS every group name in prose -- the lint step
    explains why fd-find stays local, the GTK step explains what breaks without
    typelibs. A bare grep for "media" matches the comment that describes ffmpeg
    and reports the group installed when nothing installs it. Measured: 34 lines
    mention a group name; far more than the calls.
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from shell_source import shell_code_only
    return shell_code_only(CLOUD_SETUP)


def test_cloud_setup_requests_every_declared_package_group() -> None:
    """The container is provisioned from ONE list or it drifts from the box.

    scripts/lib/system_deps.sh is the single source of truth and declares five
    groups. install_linux.sh takes `all`; provision_test_host.sh installs all
    five by name. cloud-setup.sh took only gtk and lint -- so the cloud
    container ran without core, node and media, and hand-rolled substitutes
    inline (fd-find, wireguard-tools nftables iproute2 iptables, pypy3 caddy
    postgresql-client patchelf). That is the three-copies drift CLAUDE.md
    section 5 records, with the container as the copy nobody updated.

    MEASURED CONSEQUENCE: ffmpeg is in the `media` group, and the box carried
    6.1.1-3ubuntu5 while this container had NONE -- so bulk_downloader/
    integrity.py's ffprobe shell-out could not run here at all, and its check
    fails open.
    """
    code = _setup_code()
    requested = set(re.findall(r"bd_system_pkgs\s+([a-z]+)", code))
    assert requested, (
        "no bd_system_pkgs call found in comment-stripped source -- the scan "
        "found nothing, which would make every assertion below vacuous")
    missing = [g for g in _GROUPS if g not in requested and "all" not in requested]
    assert not missing, (
        f"cloud-setup.sh never requests {missing} from bd_system_pkgs, so the "
        f"cloud container is provisioned from a different list than the box. "
        f"Requested: {sorted(requested)}")


def test_the_node_group_stays_excluded_and_says_why() -> None:
    """4-of-5 must not read as an oversight, or someone closes the "gap".

    bd_system_pkgs node is `nodejs npm` -- UBUNTU's packages -- and neither
    machine gets node from apt. MEASURED: on the operator's box apt refuses
    outright ("nodejs : Conflicts: npm ... you have held broken packages"), and
    in this container Ubuntu's candidate is nodejs 18.19.1 against the v22.22.2
    actually in use at /opt/node22, which would be installed alongside it with
    PATH deciding which the frontend build gets.

    Node IS required -- npm run build produces frontend/dist and a missing
    bundle is a silent 503 from app.py. It is simply not apt's to provide here.
    So this asserts BOTH halves: the call is absent, and the reason is written
    down where the next reader would otherwise add it.
    """
    code = _setup_code()
    requested = set(re.findall(r"bd_system_pkgs\s+([a-z]+)", code))
    for group in _EXCLUDED_GROUPS:
        assert group not in requested, (
            f"cloud-setup.sh asks apt for the {group!r} group. On the box that "
            f"is an outright apt refusal; here it shadows node v22 with 18.19.1")
    raw = CLOUD_SETUP.read_text(encoding="utf-8")
    assert "DELIBERATELY NOT INSTALLED" in raw and "Conflicts: npm" in raw, (
        "the exclusion is undocumented, so it reads as a gap -- name the "
        "measured consequence beside the code a reader would change")


@pytest.mark.parametrize("group", _GROUPS)
def test_each_group_install_refuses_an_empty_package_list(group: str) -> None:
    """The guard the GTK step's own comment argues for, applied to every group.

    Command substitution DISCARDS the function's non-zero exit, and
    `apt-get install` with zero package arguments exits 0 -- so the obvious
    one-liner installs nothing after a failed lookup and step() records OK.
    That is CLAUDE.md section 0 in three lines of shell, and it must hold for
    every group, not just the one whose author noticed.
    """
    code = _setup_code()
    var = re.search(
        r"([A-Z_]+)=\"\$\(bd_system_pkgs\s+%s\)\"" % group, code)
    assert var, (
        f"the {group} group is not captured into a variable; a bare "
        f"$(bd_system_pkgs {group}) inside the apt call would install nothing "
        f"on a failed lookup and still record OK")
    name = var.group(1)
    assert re.search(r'if\s+\[\s+-n\s+"\$%s"\s+\]' % name, code), (
        f"{name} is captured but never checked non-empty before use")
