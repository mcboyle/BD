"""A read-only check rewrote the artifact it was asked to inspect, then certified clean.

THE DEFECT. `bd-regen` (check mode is the DEFAULT -- there is no literal --check
flag; --write is the opt-in) decided "does this generator support --check?" by
RUNNING IT WITH --help and string-matching the output. `tools/build_pin_index.py`
had no argparse: main() tested for "--stdout", then "--check", and otherwise fell
through to `out.write_text(text)`. So the capability probe itself REGENERATED
PIN_INDEX.json.

The consequence is worse than a wasted write. On a tree with real drift, the probe
rewrote the pin to match the current source -- erasing the drift -- and the check
then reported `??` and exited 0 with "all derived docs in sync". A gate that
destroys its own evidence and then certifies the result.

Reproduced before the fix:
    venv/bin/python tools/build_pin_index.py --help
    -> "wrote PIN_INDEX.json: 39 pins ..."   exit 0, artifact rewritten

SECOND DEFECT, same function. `missing` was incremented for every absent generator
and never read. A tree where EVERY generator was missing printed "all derived docs
in sync" and exited 0 -- CLAUDE.md section 0's "unknown is a third state and it
FAILS", inverted into a green.

THIRD, and why bd-coretest is edited in the same cut: its `test_regen` asserted
`rc in (0,1) and ("OK" in out or "DRIFT" in out or "sync" in out.lower())`. The
historical bug satisfies that predicate exactly -- exit 0 and the word "sync". A
verdict-SHAPED answer is not a correct one, so the selftest is replaced with real
fault injection (build a drifted tree, assert the artifact is untouched).

The assertions below are BEHAVIOURAL (run the tools, hash the artifact, read the
exit code) or STRUCTURAL over the AST of both mirror copies -- never a substring
scan for an expected token.
"""
from __future__ import annotations

import ast
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_PIN_INDEX = REPO_ROOT / "tools" / "build_pin_index.py"
BD_REGEN_COPIES = (
    REPO_ROOT / "toolchain" / "bin" / "bd-regen",
)

_PIN_TEST = '__version__ = "1.0.0"\n\ndef test_v():\n    assert __version__ == "1.0.0"\n'
_PIN_TEST_DRIFTED = _PIN_TEST.replace("1.0.0", "2.0.0")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _pin_tree(tmp_path: Path, *, drift: bool) -> Path:
    """A miniature work tree with one generator and one version pin."""
    work = tmp_path / "work"
    (work / "tools").mkdir(parents=True)
    (work / "tests").mkdir(parents=True)
    shutil.copy(BUILD_PIN_INDEX, work / "tools" / "build_pin_index.py")
    (work / "tests" / "test_pin.py").write_text(_PIN_TEST, encoding="utf-8")
    subprocess.run([sys.executable, str(work / "tools" / "build_pin_index.py")],
                   cwd=work, capture_output=True, text=True, check=True)
    if drift:
        (work / "tests" / "test_pin.py").write_text(_PIN_TEST_DRIFTED, encoding="utf-8")
    return work


def _run_regen(work: Path):
    r = subprocess.run([sys.executable, str(BD_REGEN_COPIES[0]), "--work", str(work)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── the generator must not write on an argument it does not recognise ───────

def test_build_pin_index_rejects_unknown_arg_without_writing(tmp_path):
    """RED. Pristine main() has no argv guard, so --help falls through to
    out.write_text() -- exit 0 and PIN_INDEX.json created. That fall-through is
    the mechanism the whole defect rests on."""
    work = tmp_path / "work"
    (work / "tools").mkdir(parents=True)
    (work / "tests").mkdir(parents=True)
    shutil.copy(BUILD_PIN_INDEX, work / "tools" / "build_pin_index.py")
    (work / "tests" / "test_pin.py").write_text(_PIN_TEST, encoding="utf-8")
    pin = work / "PIN_INDEX.json"
    assert not pin.exists()
    r = subprocess.run(
        [sys.executable, str(work / "tools" / "build_pin_index.py"), "--help"],
        cwd=work, capture_output=True, text=True)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert not pin.exists(), "an unrecognized argument wrote PIN_INDEX.json"


def test_build_pin_index_legit_paths_unchanged(tmp_path):
    """REGRESSION GUARD -- green on pristine too. Labelled, NOT counted as RED.

    The no-arg write, --check and --stdout contracts are what bd-cut, bd-reindex
    and bd-regen-order depend on; the argv guard must not narrow them.
    """
    work = tmp_path / "work"
    (work / "tools").mkdir(parents=True)
    (work / "tests").mkdir(parents=True)
    tool = work / "tools" / "build_pin_index.py"
    shutil.copy(BUILD_PIN_INDEX, tool)
    (work / "tests" / "test_pin.py").write_text(_PIN_TEST, encoding="utf-8")
    pin = work / "PIN_INDEX.json"
    r = subprocess.run([sys.executable, str(tool)], cwd=work,
                       capture_output=True, text=True)
    assert r.returncode == 0 and pin.exists()
    sha = _sha(pin)
    r = subprocess.run([sys.executable, str(tool), "--check"], cwd=work,
                       capture_output=True, text=True)
    assert r.returncode == 0 and _sha(pin) == sha
    r = subprocess.run([sys.executable, str(tool), "--stdout"], cwd=work,
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.lstrip().startswith("{") and _sha(pin) == sha


# ── the check must be read-only, and must fail closed ───────────────────────

def test_regen_check_does_not_rewrite_artifact(tmp_path):
    """RED, and the heart of the cut. Pristine: the --help probe rewrites the pin
    (sha moves), the drift is erased, and the run reports ?? then exits 0. All
    three assertions fail."""
    work = _pin_tree(tmp_path, drift=True)
    pin = work / "PIN_INDEX.json"
    before = _sha(pin)
    rc, out = _run_regen(work)
    assert _sha(pin) == before, (
        "check mode rewrote PIN_INDEX.json -- the drift it was asked to report "
        "was destroyed by the act of looking for it")
    assert rc != 0, ("a drifted tree got a clean verdict", out)
    assert "DRIFT" in out, out


def test_regen_fails_closed_on_missing_generators(tmp_path):
    """RED. Pristine reads only `drift`, never `missing`, so a tree with NO
    generators at all prints "all derived docs in sync" and exits 0."""
    empty = tmp_path / "empty"
    empty.mkdir()
    rc, out = _run_regen(empty)
    assert rc != 0, ("a tree with no generators was certified in sync", out)


def test_regen_reports_ok_for_in_sync_generator_never_drift(tmp_path):
    """RED (and the cry-wolf guard).

    Pristine routes PIN_INDEX through the --help probe path and reports `??`,
    never `OK`. On the fixed tree it must report OK -- and must NOT report DRIFT
    for a generator that is genuinely in sync, which is what makes this the
    anti-over-sensitivity assertion as well.
    """
    work = _pin_tree(tmp_path, drift=False)
    rc, out = _run_regen(work)
    assert "DRIFT" not in out, out
    assert "PIN_INDEX" in out and "OK" in out, out


# ── structural teeth, over BOTH mirror copies ───────────────────────────────

@pytest.mark.parametrize("copy", BD_REGEN_COPIES, ids=lambda p: p.parent.name)
def test_regen_hands_no_bare_python3_to_subprocess(copy):
    """RED. Pristine builds argv as ["python3", path, *args]; the container's
    python3 is 3.11 without the project deps (CLAUDE.md section 5), so every
    generator ran under the wrong interpreter.

    Exact-value AST match, not a substring scan: the word python3 legitimately
    appears in prose, and a check that fires on a comment is a check that gets
    switched off.
    """
    tree = ast.parse(copy.read_text(encoding="utf-8"))
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == "python3"]
    assert hits == [], f"{copy}: bare 'python3' argv Constant at {hits}"


@pytest.mark.parametrize("copy", BD_REGEN_COPIES, ids=lambda p: p.parent.name)
def test_regen_does_not_probe_capability_by_running_help(copy):
    """RED. The probe is the defect: asking a generator whether it supports a
    flag by EXECUTING it is only safe if every generator is well-behaved on
    unknown argv, and one was not. Capability must be declared, not discovered."""
    tree = ast.parse(copy.read_text(encoding="utf-8"))
    help_consts = [n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and n.value == "--help"]
    assert help_consts == [], f"{copy}: '--help' probe Constant at {help_consts}"
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "supports" not in funcs, f"{copy}: the --help capability probe returned"
