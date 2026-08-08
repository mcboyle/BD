"""bd-ready is a cut-readiness preflight that aggregates the on-stash gates.

Before this cut it had never once reported success: an all-green run raised
UnboundLocalError (the header print + `worst=0` sat AFTER a `return` on the same
line, so `worst` was never bound); `--work` was parsed and never forwarded, so
members ran against a nonexistent default tree; and members were invoked as bare
names, which is FileNotFoundError because toolchain/bin is not on PATH.

This file pins the repaired contract BEHAVIOURALLY (drive the real bd-ready over
stub members with controlled exit codes) and STRUCTURALLY (AST over the source).
The verdict is three-state -- a member that could not evaluate (rc 2, an
unexpected rc, or a member that could not be launched) is UNKNOWN, and UNKNOWN
never mints the green verdict (CLAUDE.md section 0). Stub members are /bin/sh
scripts on purpose: bd-ready must invoke members by path via their shebang, not
force a Python interpreter onto them.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(os.environ.get("BD_REPO_ROOT", Path(__file__).resolve().parents[1]))
# Override hook used ONLY by the package's own pre-merge validation harness; in
# the tree it always resolves to the shipped tool.
BD_READY = Path(os.environ.get("BD_READY_UNDER_TEST",
                               REPO_ROOT / "toolchain" / "bin" / "bd-ready"))
BDTOOLS_SEC = REPO_ROOT / "toolchain" / "bin" / "bdtools_sec.py"

MEMBERS = ["bd-guardcheck", "bd-versync", "bd-changelog", "bd-regen", "bd-imports"]


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _write_stub(bin_dir: Path, name: str, rc: int, out: str = "", record: bool = False):
    """A /bin/sh member stub: optionally records its argv, prints `out`, exits rc."""
    rec = f'printf "%s" "$*" > "{bin_dir / (name + ".args")}"\n' if record else ""
    body = f'#!/bin/sh\n{rec}'
    if out:
        # printf so callers can embed escape sequences (e.g. ANSI-wrapped `??`)
        body += f'printf "{out}\\n"\n'
    body += f"exit {rc}\n"
    p = bin_dir / name
    p.write_text(body)
    p.chmod(0o755)


def _make_tree(tmp_path: Path) -> Path:
    work = tmp_path / "tree"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.999"\n')
    return work


def _make_bin(tmp_path: Path) -> Path:
    """A tmp bin dir holding a copy of bd-ready + its bdtools_sec dependency.

    bd-ready resolves its members from its OWN directory, so running a copy here
    lets stub members stand in for the real gates while exercising the real
    resolution + invocation code.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(BD_READY, bin_dir / "bd-ready")
    (bin_dir / "bd-ready").chmod(0o755)
    shutil.copy(BDTOOLS_SEC, bin_dir / "bdtools_sec.py")
    return bin_dir


def _run(bin_dir: Path, work: Path):
    return subprocess.run(
        [sys.executable, str(bin_dir / "bd-ready"), "--work", str(work)],
        capture_output=True, text=True, timeout=60,
    )


def _source() -> str:
    return BD_READY.read_text()


# --------------------------------------------------------------------------- #
# behavioural -- RED on pristine (crash / wrong verdict)                       #
# --------------------------------------------------------------------------- #
def test_all_green_reports_ready_exit0(tmp_path):
    """Every member returns 0 -> READY, exit 0. Pristine raised UnboundLocalError
    on exactly this path (worst was never bound), so it never reached here."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}")
    r = _run(bin_dir, work)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"READY: all {len(MEMBERS)} of {len(MEMBERS)} cut gates green" in r.stdout
    assert "Traceback" not in r.stderr


def test_one_fail_reports_not_ready_exit1(tmp_path):
    """A member rc 1 is a real FAIL -> NOT READY, exit 1."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}")
    _write_stub(bin_dir, "bd-versync", 1, out="DISAGREE 3.66.1 != 3.66.2")
    r = _run(bin_dir, work)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "NOT READY" in r.stdout
    assert "READY: all" not in r.stdout


def test_cannot_evaluate_blocks_green_exit2(tmp_path):
    """CLAUDE.md 0: a member rc 2 (CANNOT-EVALUATE) is UNKNOWN. With no real FAIL
    the verdict must be CANNOT CERTIFY, exit 2 -- never a green READY."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}")
    _write_stub(bin_dir, "bd-guardcheck", 2, out="CANNOT-EVALUATE reason=ABSENT")
    r = _run(bin_dir, work)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "CANNOT CERTIFY" in r.stdout
    assert "READY: all" not in r.stdout


def test_qq_lines_surface_even_on_pass(tmp_path):
    """C1-2: a member can return 0 while emitting `??` sub-checks it could not
    run (bd-regen does this for generators lacking a --check flag). Those lines
    must be surfaced on PASS, not hidden -- pristine only tailed output on FAIL."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}")
    # ANSI-wrapped `??`, exactly as bd-regen prints it, exit 0.
    _write_stub(bin_dir, "bd-regen", 0,
                out="  \\033[33m?? \\033[0mPIN_INDEX \\033[2m(no --check flag)\\033[0m")
    r = _run(bin_dir, work)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "PIN_INDEX" in r.stdout


def test_missing_member_is_unknown_not_crash(tmp_path):
    """defect (c): a member that cannot be launched is CANNOT-EVALUATE, not an
    uncaught FileNotFoundError. No traceback; exit 2 (UNKNOWN blocks green)."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}")
    (bin_dir / "bd-imports").unlink()
    r = _run(bin_dir, work)
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "READY: all" not in r.stdout


def test_work_is_forwarded_to_members(tmp_path):
    """defect (b): the parsed --work must be forwarded to each member. A stub
    records its argv; the recorded argv must contain `--work <work>`."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}", record=(m == "bd-guardcheck"))
    _run(bin_dir, work)
    args_file = bin_dir / "bd-guardcheck.args"
    assert args_file.is_file(), "member never ran (bare-name invocation?)"
    recorded = args_file.read_text()
    assert "--work" in recorded and str(work) in recorded, recorded


# --------------------------------------------------------------------------- #
# structural -- RED on pristine (AST over the source)                          #
# --------------------------------------------------------------------------- #
_TERMINATORS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def test_no_unreachable_code_after_a_terminator():
    """defect (a): pristine had `return selftest(); print(...); worst=0` -- two
    dead statements after a return in the same block. Assert no block places any
    statement after a return/raise/break/continue."""
    tree = ast.parse(_source())
    offenders = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block[:-1]):
                if isinstance(stmt, _TERMINATORS):
                    offenders.append((stmt.lineno, type(block[i + 1]).__name__))
    assert not offenders, f"unreachable statement(s) after a terminator: {offenders}"


def test_main_is_guarded_and_not_executed_at_import():
    """Testability: main() must be reachable for import without running. Assert
    (structural) a module-level `if __name__ == '__main__':` guard and no bare
    module-level call to main(); and (behavioural) importing the source under a
    non-__main__ name does not execute main()."""
    tree = ast.parse(_source())
    guarded = False
    bare_main_call = False
    for stmt in tree.body:
        if isinstance(stmt, ast.If):
            t = stmt.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "__name__"
                    and any(isinstance(c, ast.Constant) and c.value == "__main__"
                            for c in t.comparators)):
                guarded = True
                continue
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id == "main":
                bare_main_call = True
    assert guarded, "no `if __name__ == '__main__'` guard"
    assert not bare_main_call, "main() is called at module top level (runs on import)"

    # behavioural: exec under a non-__main__ name must not run main()
    ns = {"__name__": "bd_ready_under_test", "__file__": str(BD_READY)}
    sys_argv = sys.argv
    try:
        sys.argv = ["bd-ready"]
        exec(compile(_source(), str(BD_READY), "exec"), ns)
    finally:
        sys.argv = sys_argv
    assert callable(ns.get("main")), "main() not defined after import"


# --------------------------------------------------------------------------- #
# regression guards -- these PASS on pristine; they prevent the fix from        #
# breaking selftest, and prevent the pk mirror from drifting during the edit.   #
# --------------------------------------------------------------------------- #
def test_regressionguard_selftest_passes_when_coretest_present(tmp_path):
    bin_dir = _make_bin(tmp_path)
    (bin_dir / "bd-coretest").write_text("#!/bin/sh\nexit 0\n")
    r = subprocess.run([sys.executable, str(bin_dir / "bd-ready"), "--selftest"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "SELFTEST PASS" in r.stdout


def test_regressionguard_selftest_fails_closed_without_coretest(tmp_path):
    bin_dir = _make_bin(tmp_path)  # no bd-coretest sibling
    r = subprocess.run([sys.executable, str(bin_dir / "bd-ready"), "--selftest"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "SELFTEST FAIL" in r.stdout


# @943: retired with the project-knowledge mirrors. There is no second copy
# of the executable toolchain left to compare against; tests/
# test_pk_mirrors_stay_retired.py asserts the stronger property that no such
# duplicate exists at all.


# --------------------------------------------------------------------------- #
# added by refutation: launch-failure and timeout must be UNKNOWN, not a crash #
# --------------------------------------------------------------------------- #
def test_unlaunchable_member_is_unknown_not_crash(tmp_path):
    """A member file that exists but cannot be exec'd (not executable) raises
    OSError inside subprocess.run. That is CANNOT-EVALUATE: exit 2, no traceback
    -- the OSError launch-failure branch, distinct from the missing-file path."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}")
    victim = bin_dir / "bd-changelog"
    victim.write_text("#!/bin/sh\nexit 0\n")
    victim.chmod(0o644)  # present but NOT executable -> OSError on exec
    r = _run(bin_dir, work)
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "READY: all" not in r.stdout


def test_hanging_member_times_out_to_unknown_not_crash(tmp_path):
    """A member that hangs past the per-member budget must be treated as
    CANNOT-EVALUATE (exit 2), NOT crash bd-ready with subprocess.TimeoutExpired.
    Drives a sleeping stub with BD_READY_MEMBER_TIMEOUT=1. Pre-fix, run_member
    caught only OSError, so TimeoutExpired escaped -> traceback, exit 1."""
    bin_dir, work = _make_bin(tmp_path), _make_tree(tmp_path)
    for m in MEMBERS:
        _write_stub(bin_dir, m, 0, out=f"OK {m}")
    (bin_dir / "bd-versync").write_text("#!/bin/sh\nsleep 30\n")
    (bin_dir / "bd-versync").chmod(0o755)
    env = dict(os.environ, BD_READY_MEMBER_TIMEOUT="1")
    r = subprocess.run(
        [sys.executable, str(bin_dir / "bd-ready"), "--work", str(work)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert "Traceback" not in r.stderr, r.stderr
    assert "TimeoutExpired" not in r.stderr, r.stderr
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "READY: all" not in r.stdout
