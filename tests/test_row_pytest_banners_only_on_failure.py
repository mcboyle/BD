"""The session diagnostics belong to failures, not routine green output."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_RUN_CONTEXT = Path("/tmp") / "bd-runctx"
_PREFIXES = (
    "socket recorder [stage 1]:",
    "run context:",
    "replay one worker exactly:",
    "assignment: assignment.json -- RECORDED",
)


def _run_inner(mode: str, *, banners: bool = False,
               collect_only: bool = False) -> tuple[str, set[Path]]:
    env = os.environ.copy()
    env["BD_PYTEST_BANNER_INNER"] = mode
    if banners:
        env["BD_RUN_BANNERS"] = "1"
    subject = "def test_nested_banner_subject():\n    assert %s\n" % (
        "True" if mode == "green" else "False, 'intentional nested failure'")
    before = set(_RUN_CONTEXT.glob("*/assignment.json"))
    with tempfile.TemporaryDirectory(prefix=".pytest_banners_", dir=_REPO / "tests") as td:
        test_path = Path(td) / "test_subject.py"
        test_path.write_text(subject, encoding="utf-8")
        argv = [sys.executable, "-m", "pytest", str(test_path), "-vv"]
        if collect_only:
            argv.append("--collect-only")
        result = subprocess.run(
            argv,
            cwd=_REPO,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
        )
    output = result.stdout + result.stderr
    collected = re.search(r"collected (\d+) item(?:s)?", output)
    assert collected and int(collected.group(1)) > 0, (
        "the nested pytest collected no tests, so its banner output is not "
        "evidence about a completed test session:\n%s" % output[-3000:])
    if mode == "red":
        assert result.returncode != 0, output[-3000:]
    else:
        assert result.returncode == 0, output[-3000:]
    return output, set(_RUN_CONTEXT.glob("*/assignment.json")) - before


def _assert_all_prefixes_once(output: str) -> None:
    counts = {prefix: output.count(prefix) for prefix in _PREFIXES}
    assert counts == {prefix: 1 for prefix in _PREFIXES}, (
        "the failure diagnostics must retain every banner exactly once:\n"
        "%r\n%s" % (counts, output[-3000:]))


def test_a_green_nested_session_omits_all_diagnostic_banners():
    output, _assignments = _run_inner("green")
    present = [prefix for prefix in _PREFIXES if prefix in output]
    assert not present, (
        "a green nested session printed failure-only diagnostics: %r\n%s"
        % (present, output[-3000:]))


def test_a_red_nested_session_keeps_every_diagnostic_banner_once():
    output, _assignments = _run_inner("red")
    _assert_all_prefixes_once(output)


def test_an_explicit_banner_request_keeps_diagnostics_on_green():
    output, _assignments = _run_inner("green", banners=True)
    _assert_all_prefixes_once(output)


def test_a_green_nested_session_still_writes_its_assignment_record():
    _output, assignments = _run_inner("green")
    assert assignments, (
        "a green nested session did not retain an assignment.json: %r"
        % sorted(map(str, assignments)))


def test_a_collect_only_nested_session_does_not_require_an_assignment():
    _output, assignments = _run_inner("green", collect_only=True)
    assert not assignments
