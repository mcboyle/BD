"""A provisioning report must be datable, and an undatable one must not pass.

`.claude-env-report.md` opens by telling its reader to trust it before reading
any test result. It is gitignored, survives `git clean -fd`, and is written once
per provisioning run -- so it long outlives the tree it describes. One was found
on a live container seven days old, asserting v3.66.811 against a v3.66.818
tree, while its rows were being read as current.

`bd-env-report-check` answers FRESH / STALE / UNKNOWN. The third state is the
point: a report whose provenance cannot be established is indistinguishable from
one written against a different tree, so it must not exit 0.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "toolchain" / "bin" / "bd-env-report-check"
PYTHON = REPO_ROOT / "venv" / "bin" / "python"
BOOTSTRAP_DOC = REPO_ROOT / "project-knowledge" / "NEXT_SESSION_BOOTSTRAP.md"


def _run(tree: Path, report: Path | None = None):
    cmd = [str(PYTHON), str(CHECKER), "--tree", str(tree)]
    if report is not None:
        cmd += ["--report", str(report)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _tree(tmp: Path, version: str = "9.9.9") -> Path:
    root = tmp / "tree"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8")
    return root


def _report(root: Path, body: str) -> Path:
    p = root / ".claude-env-report.md"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_checker_exists_and_runs():
    assert CHECKER.is_file(), f"{CHECKER} missing"
    proc = subprocess.run([str(PYTHON), str(CHECKER), "--help"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_matching_version_is_fresh(tmp_path):
    root = _tree(tmp_path, "9.9.9")
    _report(root, """
        # Environment provisioning report
        ```
        generated_at=2026-07-27T00:00:00Z
        generated_against_version=9.9.9
        generated_against_commit=deadbeef
        ```
    """)
    proc = _run(root)
    assert proc.returncode == 0, f"expected FRESH:\n{proc.stdout}{proc.stderr}"
    assert "FRESH" in proc.stdout


def test_different_version_is_stale_and_non_zero(tmp_path):
    """The real case: a report seven days old naming a superseded version."""
    root = _tree(tmp_path, "3.66.818")
    _report(root, """
        ```
        generated_at=2026-07-20T22:20:19Z
        generated_against_version=3.66.811
        generated_against_commit=abc123
        ```
    """)
    proc = _run(root)
    assert proc.returncode == 1, f"expected STALE(1):\n{proc.stdout}{proc.stderr}"
    assert "STALE" in proc.stdout
    assert "3.66.811" in proc.stdout and "3.66.818" in proc.stdout


def test_missing_report_is_unknown_not_ok(tmp_path):
    root = _tree(tmp_path)
    proc = _run(root)
    assert proc.returncode == 2, (
        f"a missing report must be UNKNOWN(2), never 0:\n{proc.stdout}{proc.stderr}"
    )
    assert "UNKNOWN" in proc.stdout


def test_report_without_provenance_is_unknown_not_fresh(tmp_path):
    """A pre-v3.66.818 report, or one written by a forked script.

    This is the case that matters most: the report LOOKS complete, has rows,
    has a verdict -- and cannot be dated. Passing it would be the S0 failure
    this checker exists to prevent.
    """
    root = _tree(tmp_path)
    _report(root, """
        # Environment provisioning report

        `scripts/cloud-setup.sh` -- 2026-07-20T22:20:19Z

        | Step | Result | Detail |
        | --- | --- | --- |
        | apt update | OK |  |

        ## VERDICT: READY
    """)
    proc = _run(root)
    assert proc.returncode == 2, (
        f"a report with no provenance must be UNKNOWN(2):\n{proc.stdout}{proc.stderr}"
    )
    assert "UNKNOWN" in proc.stdout


def test_explicit_unknown_provenance_does_not_pass(tmp_path):
    root = _tree(tmp_path)
    _report(root, """
        ```
        generated_at=2026-07-27T00:00:00Z
        generated_against_version=UNKNOWN
        generated_against_commit=UNKNOWN
        ```
    """)
    proc = _run(root)
    assert proc.returncode == 2, (
        f"UNKNOWN provenance must not pass:\n{proc.stdout}{proc.stderr}"
    )


def test_bootstrap_doc_gates_its_grep_behind_the_freshness_check():
    """The documented consumer must not read rows it cannot date.

    NEXT_SESSION_BOOTSTRAP.md greps the report for VERDICT|FAIL|WARN. Today that
    prints a seven-day-old verdict with no indication it is stale.
    """
    text = BOOTSTRAP_DOC.read_text(encoding="utf-8")
    assert "bd-env-report-check" in text, (
        "NEXT_SESSION_BOOTSTRAP.md still reads .claude-env-report.md without "
        "checking whether it describes this tree"
    )
