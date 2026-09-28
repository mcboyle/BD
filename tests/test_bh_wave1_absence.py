"""bd-absence-proof.sh must never certify absence it did not measure.

FINDING-BH-bd-grok-audit-1-001/002/003 (bughunt wave 1, H-ABSENCE):
  001 test-probe dropped the probe's exit code and stderr, so a crashing
      target probe (no stdout) was ABSENCE-VERIFIED.
  002 check-find sent find's stderr to /dev/null and counted lines, so an
      unreadable target directory was ABSENCE-VERIFIED with count 0.
  003 validate-report matched the substring ABSENCE anywhere, so a report
      saying "not an absence result" / "TARGET-FOUND: 9" was ACCEPTED.

The harness script is deployed from bd-persist, not this repo; the candidate is
opt-in via BD_BH_WAVE1_ABSENCE_CANDIDATE (absolute path). Exit contract:
0 absence verified, 1 target found, 3 refused, 4 unknown.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH_WAVE1_ABSENCE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    path = Path(CANDIDATE)
    assert path.is_file(), f"candidate missing: {CANDIDATE}"
    assert os.access(path, os.X_OK), f"candidate not executable: {CANDIDATE}"
    return subprocess.run(
        [str(path), *args], capture_output=True, text=True, timeout=30, check=False
    )


# --- 001 test-probe -------------------------------------------------------


def test_probe_target_crash_is_unknown() -> None:
    res = _run("test-probe", "echo 3", "echo FAIL-stderr >&2; exit 9")
    assert res.returncode == 4, res
    assert "UNKNOWN" in res.stderr
    assert "FAIL-stderr" in res.stderr  # probe stderr is not discarded


def test_probe_target_crash_printing_zero_is_unknown() -> None:
    res = _run("test-probe", "echo 3", "echo 0; exit 2")
    assert res.returncode == 4, res


def test_probe_positive_crash_is_refused() -> None:
    res = _run("test-probe", "echo 5; exit 1", "echo 0")
    assert res.returncode == 3, res
    assert "REFUSED" in res.stderr


def test_probe_target_found() -> None:
    res = _run("test-probe", "echo 3", "echo 4")
    assert res.returncode == 1, res
    assert "TARGET-FOUND: target returned 4." in res.stdout


def test_probe_clean_zero_is_absence() -> None:
    res = _run("test-probe", "echo 384", "echo 0")
    assert res.returncode == 0, res
    assert res.stdout.startswith("ABSENCE-VERIFIED")


def test_probe_positive_zero_is_refused() -> None:
    res = _run("test-probe", "echo 0", "echo 0")
    assert res.returncode == 3, res


# --- 002 check-find -------------------------------------------------------


@pytest.fixture
def trees(tmp_path: Path):  # type: ignore[no-untyped-def]
    pos = tmp_path / "pos"
    tgt = tmp_path / "tgt"
    (pos / "sub").mkdir(parents=True)
    (pos / "sub" / "hit.txt").write_text("x")
    locked = tgt / "locked"
    locked.mkdir(parents=True)
    (locked / "hidden.txt").write_text("x")
    yield pos, tgt, locked
    locked.chmod(0o755)


def test_find_unreadable_target_is_unknown(trees) -> None:  # type: ignore[no-untyped-def]
    pos, tgt, locked = trees
    locked.chmod(0o000)
    if os.access(locked, os.R_OK):
        pytest.skip("running as root: chmod 000 does not block reads")
    res = _run("check-find", str(pos), str(tgt), "-type", "f")
    assert res.returncode == 4, res
    assert "UNKNOWN" in res.stderr
    assert "Permission denied" in res.stderr  # find's own diagnostic surfaces


def test_find_readable_target_is_found(trees) -> None:  # type: ignore[no-untyped-def]
    pos, tgt, _ = trees
    res = _run("check-find", str(pos), str(tgt), "-type", "f")
    assert res.returncode == 1, res
    assert "TARGET-FOUND: probe matched 1 item(s)" in res.stdout


def test_find_empty_target_is_absence(trees, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pos, _, _ = trees
    empty = tmp_path / "empty"
    empty.mkdir()
    res = _run("check-find", str(pos), str(empty), "-type", "f")
    assert res.returncode == 0, res
    assert "Positive control verified (1 found" in res.stdout


def test_find_nomatch_positive_is_refused(trees) -> None:  # type: ignore[no-untyped-def]
    pos, tgt, _ = trees
    res = _run("check-find", str(pos), str(tgt), "-name", "NOMATCH")
    assert res.returncode == 3, res


def test_find_bad_expression_is_unknown(trees) -> None:  # type: ignore[no-untyped-def]
    pos, tgt, _ = trees
    res = _run("check-find", str(pos), str(tgt), "-bogus-primary")
    assert res.returncode == 4, res


# --- 003 validate-report --------------------------------------------------


def _report(tmp_path: Path, body: str) -> str:
    rpt = tmp_path / "report.md"
    rpt.write_text(body)
    return str(rpt)


def test_report_target_found_is_refused(tmp_path: Path) -> None:
    rpt = _report(
        tmp_path,
        "POSITIVE CONTROL: 5 passed\nnot an absence result\nTARGET-FOUND: 9\n",
    )
    res = _run("validate-report", rpt)
    assert res.returncode == 3, res
    assert "REFUSED" in res.stderr


def test_report_absence_substring_is_not_a_claim(tmp_path: Path) -> None:
    rpt = _report(tmp_path, "POSITIVE CONTROL: 5 passed\nnot an absence result\n")
    res = _run("validate-report", rpt)
    assert res.returncode == 4, res
    assert "missing absence claim" in res.stderr


def test_report_zero_inside_word_is_not_a_claim(tmp_path: Path) -> None:
    rpt = _report(tmp_path, "POSITIVE CONTROL: 5 passed\nzeroed the counters\n")
    res = _run("validate-report", rpt)
    assert res.returncode == 4, res


def test_report_nonzero_target_count_is_refused(tmp_path: Path) -> None:
    rpt = _report(
        tmp_path,
        "POSITIVE-CONTROL: passed (384 hits)\nABSENCE: claimed\nFOUND 7 in target\n",
    )
    res = _run("validate-report", rpt)
    assert res.returncode == 3, res


def test_report_valid_absence_is_accepted(tmp_path: Path) -> None:
    rpt = _report(
        tmp_path,
        "POSITIVE-CONTROL: passed (scored 384 positive hits in bd-review-wt)\n"
        "TARGET: empty-pool\nABSENCE: verified (FOUND 0 in target)\n",
    )
    res = _run("validate-report", rpt)
    assert res.returncode == 0, res
    assert res.stdout.startswith("ACCEPTED")


def test_report_without_positive_control_is_unknown(tmp_path: Path) -> None:
    rpt = _report(tmp_path, "FOUND 0 review roots\nCOUNT: 0\n")
    res = _run("validate-report", rpt)
    assert res.returncode == 4, res
