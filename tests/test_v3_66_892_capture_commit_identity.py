"""capture.sh step [1] must record the commit the bundle was captured from.

THE GAP. capture.sh executed no git command anywhere, so 01_sysinfo.log carried
date/uname/os-release/python/pip/disk/memory/ports and no commit identity of any
kind. A bundle could not say which tree it graded. The register filed it four
times; two uploads once arrived together and were separable only by reading
02_SUMMARY.txt and 09_http_smoke.log. The banner is not a workaround -- it goes
to stdout, and capture.sh tars only $OUT, so the operator's redirect lands
outside the archive.

THE TRAP, and it is why direction D below exists. `git rev-parse HEAD` walks UP
the directory tree. capture.sh:285 does `cd "$BD_HOME"`, so if BD_HOME is a
SUBDIRECTORY of some other checkout the stage emits a valid-looking sha about a
DIFFERENT tree -- a confident wrong answer, which is strictly worse than the
honest silence it replaces (CLAUDE.md section 0). The emitter must compare
`--show-toplevel` against the directory it actually ran in and declare MISMATCH.

WHY IT IS NOT WIRED TO --stage-exit. A non-git tree is a legitimate way to run
capture.sh, so gating the release verdict on it would fail the gate for a reason
no code change can fix. `source` is reported instead, mirroring
app_health.build_identity's three-state git | recorded | unknown -- a fallback is
indistinguishable from a live read unless it says so.

Assertions here read COMMENT-STRIPPED source (tests/shell_source.py) so that the
comment explaining a behaviour cannot satisfy the test for it, and extract shell
on STRUCTURE -- a closing brace at column 0, a banner sentinel -- never on a
fixed width, which would both break on the next edit and be counted by
tests/test_source_windows_do_not_shift.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shell_source import shell_code_only  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"

_FN_NAME = "emit_commit_identity"
_SHA40 = re.compile(r"\b[0-9a-f]{40}\b")


def _extract_function(name: str) -> str:
    """Cut the function on STRUCTURE: its def line to the next `}` at column 0.

    A fixed-width slice would break the moment anything above it grows, which is
    the failure CLAUDE.md section 2a records; a closing brace in the first column
    is the shell's own end-of-function marker.
    """
    lines = CAPTURE_SH.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith(f"{name}()")]
    assert len(starts) == 1, f"expected exactly one {name}() definition, got {starts}"
    start = starts[0]
    ends = [i for i in range(start + 1, len(lines)) if lines[i] == "}"]
    assert ends, f"{name}() has no closing brace at column 0"
    return "\n".join(lines[start:ends[0] + 1])


def _run_emitter(cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the real shipped function with cwd as its working directory."""
    script = _extract_function(_FN_NAME) + f"\n{_FN_NAME}\n"
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _source_line(out: str) -> str:
    """The value of the `source` field, which says HOW the answer was obtained.

    Asserted on directly rather than by substring: `"git" in out` is satisfied by
    any prose mentioning git, so it would grade a MISMATCH as a good live read.
    """
    lines = [ln for ln in out.splitlines() if ln.startswith("source")]
    assert len(lines) == 1, f"expected exactly one source line, got {lines}"
    return lines[0].split(":", 1)[1].strip()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_repo(root: Path) -> str:
    """A real one-commit repo. Returns its sha."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "probe@example.test")
    _git(root, "config", "user.name", "probe")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-qm", "seed")
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root), capture_output=True, text=True, timeout=30,
    )
    return out.stdout.strip()


# --------------------------------------------------------------------------
# Direction A/C -- a real work tree reports a real, resolvable identity.
# --------------------------------------------------------------------------
def test_emitter_reports_sha_branch_and_toplevel_in_a_work_tree(tmp_path) -> None:
    repo = tmp_path / "repo"
    sha = _make_repo(repo)

    result = _run_emitter(repo)
    out = result.stdout

    assert sha in out, out
    assert _SHA40.search(out), f"no 40-hex sha emitted: {out!r}"
    assert str(repo.resolve()) in out, f"toplevel not reported: {out!r}"
    assert "UNKNOWN" not in out and "MISMATCH" not in out, out
    assert _source_line(out) == "git", (
        f"a clean live read must be labelled source: git -- got {out!r}"
    )


# --------------------------------------------------------------------------
# Direction B -- outside a work tree, say so. Unknown is a third state.
# --------------------------------------------------------------------------
def test_emitter_says_unknown_outside_a_work_tree(tmp_path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    result = _run_emitter(plain)
    out = result.stdout

    assert "UNKNOWN" in out, out
    assert not _SHA40.search(out), f"invented a sha outside a work tree: {out!r}"
    assert "fatal" not in out.lower(), f"raw git error leaked into the log: {out!r}"


# --------------------------------------------------------------------------
# Direction D -- THE TRAP. Below a repo root, git answers about the PARENT.
# A confident wrong sha is worse than the silence it replaces.
# --------------------------------------------------------------------------
def test_emitter_declares_mismatch_when_run_below_the_repo_root(tmp_path) -> None:
    repo = tmp_path / "outer"
    sha = _make_repo(repo)
    nested = repo / "nested" / "BulkDownloader"
    nested.mkdir(parents=True)

    bare = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(nested), capture_output=True, text=True, timeout=30,
    )
    assert bare.returncode == 0 and bare.stdout.strip() == sha, (
        "precondition: a bare rev-parse must walk UP and answer about the parent, "
        "otherwise this test is not exercising the trap"
    )

    result = _run_emitter(nested)
    out = result.stdout

    assert "MISMATCH" in out, f"the walk-up trap was not detected: {out!r}"
    assert _source_line(out).startswith("unknown"), (
        f"a mismatched read must not be labelled a good git read: {out!r}"
    )
    assert str(nested.resolve()) in out, (
        f"the mismatch must name the directory capture actually ran in: {out!r}"
    )


# --------------------------------------------------------------------------
# Wiring -- the emitter must actually run inside the redirected sysinfo block.
# A function that exists and is never called is CLAUDE.md section 0's
# declared-but-never-written defect.
# --------------------------------------------------------------------------
def _sysinfo_group(code: str) -> str:
    """The `{ ... } > "$OUT/01_sysinfo.log"` group, cut on brace structure.

    NOT via shell_source.blocks_containing: that resolves `for`/`while`...`done`
    constructs and falls back to the single line for anything else, so against a
    brace group it returns only the closing redirect -- a denominator that
    structurally excludes the calls it is being asked about, which is exactly the
    shape it exists to prevent elsewhere. Measured: it returned one line here.
    """
    lines = code.splitlines()
    closers = [i for i, ln in enumerate(lines)
               if ln.startswith("}") and '"$OUT/01_sysinfo.log"' in ln]
    assert len(closers) == 1, f"expected one sysinfo redirect, got {closers}"
    openers = [i for i in range(closers[0], -1, -1) if lines[i].strip() == "{"]
    assert openers, "sysinfo redirect has no opening brace"
    body = "\n".join(lines[openers[0]:closers[0] + 1])
    assert "--- date ---" in body, f"cut the wrong region:\n{body}"
    return body


def test_sysinfo_block_calls_the_emitter() -> None:
    code = shell_code_only(CAPTURE_SH)

    assert f"{_FN_NAME}()" in code, "emitter is not defined in executable source"

    body = _sysinfo_group(code)
    calls = [
        ln for ln in body.splitlines()
        if _FN_NAME in ln and f"{_FN_NAME}()" not in ln
    ]
    assert calls, (
        "the sysinfo block never calls the emitter -- a declared-and-never-written "
        f"fix. Block was:\n{body}"
    )


# --------------------------------------------------------------------------
# Release-gate safety -- a non-git tree is a legitimate way to run capture.sh,
# so this must never be able to fail the whole capture.
# --------------------------------------------------------------------------
def test_commit_identity_is_not_wired_to_stage_exit() -> None:
    code = shell_code_only(CAPTURE_SH)
    gated = [ln for ln in code.splitlines() if "--stage-exit" in ln]
    assert gated, "no --stage-exit lines found; the denominator is empty"
    offenders = [ln for ln in gated if _FN_NAME in ln or "commit_identity" in ln]
    assert not offenders, (
        "commit identity must not gate the release verdict -- a non-git tree "
        f"would fail for a reason no code change can fix: {offenders}"
    )


# --------------------------------------------------------------------------
# End-to-end on the REAL step [1] block: the identity reaches the LOG FILE,
# which is the only artifact that ends up inside the tarball.
# --------------------------------------------------------------------------
def _run_step_one(tmp_path: Path, home: Path) -> str:
    lines = CAPTURE_SH.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if "[1/9]" in ln and ln.startswith("#")]
    ends = [i for i, ln in enumerate(lines) if "[2/9]" in ln and ln.startswith("#")]
    assert len(starts) == 1 and len(ends) == 1, (starts, ends)
    body = "\n".join(lines[starts[0]:ends[0]])
    assert "01_sysinfo.log" in body, "extracted the wrong region"

    out_dir = tmp_path / "capture-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    fn = _extract_function(_FN_NAME)
    preamble = (
        "set -u\n"
        f'BD_HOME="{home}"\n'
        f'OUT="{out_dir}"\n'
        'cd "$BD_HOME"\n'
        f"{fn}\n"
    )
    script = tmp_path / "step1.sh"
    script.write_text(preamble + body + "\n", encoding="utf-8")
    subprocess.run(
        ["bash", str(script)], cwd=str(home),
        capture_output=True, text=True, timeout=60,
    )
    return (out_dir / "01_sysinfo.log").read_text(encoding="utf-8")


def _fake_home(root: Path) -> Path:
    (root / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (root / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "capture-probe"\n', encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# capture probe\n", encoding="utf-8")
    for name in ("python", "pip"):
        stub = root / "venv" / "bin" / name
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    return root


def test_step_one_writes_commit_identity_into_the_log(tmp_path) -> None:
    home = _fake_home(tmp_path / "BulkDownloader")
    sha = _make_repo(home)

    log = _run_step_one(tmp_path, home)

    assert sha in log, (
        "01_sysinfo.log carries no commit identity -- the bundle still cannot "
        f"say which tree it graded. Log was:\n{log}"
    )
    assert _SHA40.search(log), log


def test_step_one_log_says_unknown_rather_than_nothing(tmp_path) -> None:
    home = _fake_home(tmp_path / "BulkDownloader")

    log = _run_step_one(tmp_path, home)

    assert "UNKNOWN" in log, (
        "outside a work tree the log must SAY it cannot identify the source; "
        f"silence is indistinguishable from a check that never ran. Log:\n{log}"
    )
    assert not _SHA40.search(log), f"invented a sha: {log!r}"
