"""BDMUTATE-ETXTBSY -- a held subject writer, and an OS error, are never a catch.

cx8 (o1876-5 r2) measured bd-mutate scoring a direct-exec shell mutant CAUGHT
and its transform control CONTROL-CAUGHT while every band run had died on
OSError 26 (ETXTBSY): the runner held a writable descriptor on the subject
across the band, so the kernel refused to exec it and the tests raised before
any assertion. These fixtures pin the three cx8 acceptance conditions.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_greet.py"
_DEFAULT = f"{_BAND}::test_default"
_SHOUT = f"{_BAND}::test_shout"
_SIGNATURE = "BEHAVIOR-ASSERTION-REACHED"

_SCRIPT = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    '  --shout) echo "HELLO" ;;\n'
    '  *) echo "hello" ;;\n'
    "esac\n"
)

# Direct exec, never `sh greet`: the kernel must load the subject itself.
_TESTS = (
    "import os, pathlib, shutil, subprocess\n"
    "SCRIPT = pathlib.Path(__file__).resolve().parent.parent / 'bin' / 'greet'\n"
    "def _run(*args):\n"
    "    return subprocess.run([str(SCRIPT), *args], capture_output=True,\n"
    "                          text=True, timeout=30,\n"
    "                          env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})\n"
    "class _Unavailable(OSError):\n"
    "    pass\n"
    "def test_default():\n"
    "    if 'INJECT-SAMEFILE' in SCRIPT.read_text():\n"
    "        shutil.copyfile(SCRIPT, SCRIPT)\n"
    "    if 'INJECT-OSERROR-SUBCLASS' in SCRIPT.read_text():\n"
    "        raise _Unavailable('subject unavailable')\n"
    "    if 'INJECT-ETXTBSY' in SCRIPT.read_text():\n"
    "        writer = os.open(SCRIPT, os.O_WRONLY)\n"
    "        try:\n"
    "            _run()\n"
    "        finally:\n"
    "            os.close(writer)\n"
    "    out = _run().stdout\n"
    "    prefix = ''\n"
    "    if 'ERRNO-TEXT-PLAIN' in SCRIPT.read_text():\n"
    "        prefix = '[Errno 2] '\n"
    "    if 'ERRNO-TEXT-OSWORD' in SCRIPT.read_text():\n"
    "        prefix = '[Errno 2] OSError in '\n"
    f"    assert out == 'hello\\n', prefix + '{_SIGNATURE} default ' + repr(out)\n"
    "def test_shout():\n"
    "    out = _run('--shout').stdout\n"
    f"    assert out == 'HELLO\\n', '{_SIGNATURE} shout ' + repr(out)\n"
)


def _tree(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "bin").mkdir(parents=True)
    (work / "tests").mkdir()
    script = work / "bin" / "greet"
    script.write_text(_SCRIPT, encoding="utf-8")
    script.chmod(0o755)
    (work / _BAND).write_text(_TESTS, encoding="utf-8")
    return work


def _invoke(work: Path, document: dict, *extra: str
            ) -> subprocess.CompletedProcess[str]:
    spec = work.parent / "spec.json"
    spec.write_text(json.dumps(document), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_TOOL), "--spec", str(spec), "--work", str(work),
         *extra],
        cwd=_REPO, capture_output=True, text=True, timeout=120,
        env={**os.environ, "LC_ALL": "C"},
    )


def _run(work: Path, document: dict
         ) -> tuple[subprocess.CompletedProcess[str], dict]:
    run = _invoke(work, document, "--json")
    start = run.stdout.find("{")
    assert start >= 0, run.stdout + run.stderr
    return run, json.loads(run.stdout[start:])


def _legacy(mutant: dict, **extra) -> dict:
    return {
        "schema": "bd-mutate-spec/1",
        "_comment": "synthetic direct-exec shell subject",
        "subject": "bin/greet",
        "band": [_BAND],
        "mutants": [mutant],
        **extra,
    }


def test_direct_exec_shell_mutant_is_caught_by_its_behavioural_assertion(tmp_path):
    work = _tree(tmp_path)
    document = {
        "schema": "bd-mutation-spec/2",
        "_comment": "the catcher must fail on its own assertion text",
        "subject": "bin/greet",
        "band": [_DEFAULT, _SHOUT],
        "mutants": [{
            "label": "break the default greeting",
            "file": "bin/greet",
            "old": '  *) echo "hello" ;;',
            "new": '  *) echo "bye" ;;',
            "direction": "regression",
            "catcher": _DEFAULT,
            "expected_failure": {"outcome": "failed", "signature": _SIGNATURE},
            "preserves": [_SHOUT],
        }],
    }
    run, payload = _run(work, document)
    row = payload["rows"][0]
    assert row["verdict"] == "CAUGHT", (
        "BD-MUTATE-ETXTBSY-T1: the exec'd mutant never reached its assertion "
        "(a writable subject descriptor held across the band refuses exec): %r"
        % row)
    assert row["actual_failure"] == {
        "nodeid": _DEFAULT, "outcome": "failed", "signature": _SIGNATURE,
    }, row
    assert run.returncode == 0, run.stdout + run.stderr
    assert (work / "bin" / "greet").read_text(encoding="utf-8") == _SCRIPT


def test_unrelated_arg_transform_escapes(tmp_path):
    work = _tree(tmp_path)
    run, payload = _run(work, _legacy({
        "label": "transform an argument the catcher never passes",
        "file": "bin/greet",
        "old": '  --shout) echo "HELLO" ;;',
        "new": '  --shout) echo "QUIET" ;;',
        "direction": "regression",
        "catcher": _DEFAULT,
    }))
    row = payload["rows"][0]
    assert row["verdict"] == "ESCAPED", (
        "BD-MUTATE-ETXTBSY-T2: a transform the catcher cannot see scored %s"
        % row["verdict"], row)
    assert run.returncode == 1, run.stdout + run.stderr


def test_injected_etxtbsy_scores_error_not_caught(tmp_path):
    work = _tree(tmp_path)
    run, payload = _run(work, _legacy({
        "label": "behaviour break whose exec the kernel refuses",
        "file": "bin/greet",
        "old": '  *) echo "hello" ;;',
        "new": '  *) echo "bye" ;; # INJECT-ETXTBSY',
        "direction": "regression",
        "catcher": _DEFAULT,
    }))
    row = payload["rows"][0]
    assert row["verdict"] == "ERROR", (
        "BD-MUTATE-ETXTBSY-T3: an exec refused with ETXTBSY scored %s"
        % row["verdict"], row)
    assert "BD-MUTATE-OS-ERROR" in row["why"] and "[Errno 26]" in row["why"], row
    assert run.returncode == 2, run.stdout + run.stderr


def test_injected_etxtbsy_in_a_declared_control_is_error_not_control_caught(tmp_path):
    work = _tree(tmp_path)
    # The human report, not --json: its counts line is what a lens reads.
    run = _invoke(work, _legacy({
        "label": "control whose exec the kernel refuses",
        "file": "bin/greet",
        "old": '  *) echo "hello" ;;',
        "new": '  *) echo "hello" ;; # INJECT-ETXTBSY',
        "direction": "regression",
        "catcher": _DEFAULT,
    }, control_spec=True))
    assert "0 control-caught (FAILURE)" in run.stdout and "1 error" in run.stdout, (
        "BD-MUTATE-ETXTBSY-T4: a control whose exec failed was not counted "
        "ERROR:\n" + run.stdout + run.stderr)
    assert "BD-MUTATE-OS-ERROR" in run.stdout, run.stdout
    assert run.returncode == 2, run.stdout + run.stderr


# FIX-R-174 (cx9 F1): an OSError need not carry "[Errno N]". These are graded
# by exception TYPE; shutil.SameFileError and an OSError subclass the test
# defines itself both have errno None.
@pytest.mark.parametrize("marker, kind", [
    ("INJECT-SAMEFILE", "SameFileError"),
    ("INJECT-OSERROR-SUBCLASS", "_Unavailable"),
])
def test_errnoless_os_error_scores_error_not_caught(tmp_path, marker, kind):
    work = _tree(tmp_path)
    run, payload = _run(work, _legacy({
        "label": "behaviour break whose test dies on an errno-less OSError",
        "file": "bin/greet",
        "old": '  *) echo "hello" ;;',
        "new": '  *) echo "bye" ;; # ' + marker,
        "direction": "regression",
        "catcher": _DEFAULT,
    }))
    row = payload["rows"][0]
    assert row["verdict"] == "ERROR", (
        "BD-MUTATE-ETXTBSY-T5: an errno-less %s before any assertion scored %s"
        % (kind, row["verdict"]), row)
    assert "BD-MUTATE-OS-ERROR" in row["why"] and kind in row["why"], row
    assert "[Errno" not in row["why"], row
    assert run.returncode == 2, run.stdout + run.stderr
    assert (work / "bin" / "greet").read_text(encoding="utf-8") == _SCRIPT


def test_errnoless_os_error_in_a_declared_control_is_error(tmp_path):
    work = _tree(tmp_path)
    run = _invoke(work, _legacy({
        "label": "control whose test dies on shutil.SameFileError",
        "file": "bin/greet",
        "old": '  *) echo "hello" ;;',
        "new": '  *) echo "hello" ;; # INJECT-SAMEFILE',
        "direction": "regression",
        "catcher": _DEFAULT,
    }, control_spec=True))
    assert "0 control-caught (FAILURE)" in run.stdout and "1 error" in run.stdout, (
        "BD-MUTATE-ETXTBSY-T6: a control that died on an errno-less OSError "
        "was not counted ERROR:\n" + run.stdout + run.stderr)
    assert "BD-MUTATE-OS-ERROR" in run.stdout and "SameFileError" in run.stdout, (
        run.stdout)
    assert run.returncode == 2, run.stdout + run.stderr


def test_exact_v2_errnoless_os_error_matching_signature_is_error(tmp_path):
    work = _tree(tmp_path)
    document = {
        "schema": "bd-mutation-spec/2",
        "_comment": "a signature naming the OSError is still no assertion",
        "subject": "bin/greet",
        "band": [_DEFAULT, _SHOUT],
        "mutants": [{
            "label": "break the default greeting behind a SameFileError",
            "file": "bin/greet",
            "old": '  *) echo "hello" ;;',
            "new": '  *) echo "bye" ;; # INJECT-SAMEFILE',
            "direction": "regression",
            "catcher": _DEFAULT,
            "expected_failure": {"outcome": "failed", "signature": "SameFileError"},
            "preserves": [_SHOUT],
        }],
    }
    run, payload = _run(work, document)
    row = payload["rows"][0]
    assert row["verdict"] == "ERROR", (
        "BD-MUTATE-ETXTBSY-T7: exact-v2 scored an errno-less OSError %s"
        % row["verdict"], row)
    assert "BD-MUTATE-OS-ERROR" in row["why"], row
    assert run.returncode == 2, run.stdout + run.stderr


# FIX-R-179 (cx9 r2 F1): the TYPE decides, never errno-looking text. A real
# behavioural AssertionError whose message begins "[Errno 2]" is a kill.
@pytest.mark.parametrize("marker", ["ERRNO-TEXT-PLAIN", "ERRNO-TEXT-OSWORD"])
def test_assertion_with_errno_text_is_caught_not_error(tmp_path, marker):
    work = _tree(tmp_path)
    run, payload = _run(work, _legacy({
        "label": "behaviour break whose assertion message begins [Errno 2]",
        "file": "bin/greet",
        "old": '  *) echo "hello" ;;',
        "new": '  *) echo "bye" ;; # ' + marker,
        "direction": "regression",
        "catcher": _DEFAULT,
    }))
    row = payload["rows"][0]
    assert row["verdict"] == "CAUGHT", (
        "BD-MUTATE-ETXTBSY-T8: an AssertionError reading [Errno 2] scored %s"
        % row["verdict"], row)
    assert "BD-MUTATE-OS-ERROR" not in row["why"], row
    assert run.returncode == 0, run.stdout + run.stderr
    assert (work / "bin" / "greet").read_text(encoding="utf-8") == _SCRIPT


def test_exact_v2_assertion_with_errno_text_is_caught(tmp_path):
    work = _tree(tmp_path)
    signature = "[Errno 2] " + _SIGNATURE
    document = {
        "schema": "bd-mutation-spec/2",
        "_comment": "errno-looking assertion text is still an assertion",
        "subject": "bin/greet",
        "band": [_DEFAULT, _SHOUT],
        "mutants": [{
            "label": "break the default greeting; assertion reads [Errno 2]",
            "file": "bin/greet",
            "old": '  *) echo "hello" ;;',
            "new": '  *) echo "bye" ;; # ERRNO-TEXT-PLAIN',
            "direction": "regression",
            "catcher": _DEFAULT,
            "expected_failure": {"outcome": "failed", "signature": signature},
            "preserves": [_SHOUT],
        }],
    }
    run, payload = _run(work, document)
    row = payload["rows"][0]
    assert row["verdict"] == "CAUGHT", (
        "BD-MUTATE-ETXTBSY-T9: exact-v2 scored an [Errno 2] AssertionError %s"
        % row["verdict"], row)
    assert row["actual_failure"] == {
        "nodeid": _DEFAULT, "outcome": "failed", "signature": signature,
    }, row
    assert run.returncode == 0, run.stdout + run.stderr


def test_errno_text_assertion_in_a_declared_control_is_control_caught(tmp_path):
    work = _tree(tmp_path)
    run = _invoke(work, _legacy({
        "label": "control broken by its own [Errno 2]-worded assertion",
        "file": "bin/greet",
        "old": '  *) echo "hello" ;;',
        "new": '  *) echo "bye" ;; # ERRNO-TEXT-PLAIN',
        "direction": "regression",
        "catcher": _DEFAULT,
    }, control_spec=True))
    assert "1 control-caught (FAILURE)" in run.stdout and "0 error" in run.stdout, (
        "BD-MUTATE-ETXTBSY-T10: a control caught by an [Errno 2]-worded "
        "assertion was not counted CONTROL-CAUGHT:\n" + run.stdout + run.stderr)
    assert "BD-MUTATE-OS-ERROR" not in run.stdout, run.stdout
    assert run.returncode == 1, run.stdout + run.stderr
