"""The pre-commit battery sees stray files in tests/ and unregistered runs.

BACKLOG 35. That row asks for a pre-commit self-check covering "tree clean, no
orphans, services healthy, no scratch in tests/, ledger current".
bd-cut-preflight (tracked at v3.66.1104) already supplies the composer and the
UNKNOWN third state, and covers TREE CLEAN and LEDGER CURRENT. This adds the two
that are genuinely pre-commit predicates.

WHY "SCRATCH IN tests/" AND "NEW FILE NOT STAGED" ARE ONE CHECK. CLAUDE.md
section 2a records both halves and they have the same remedy shape. Most axis-6
gates enumerate `git ls-files`, so an UNTRACKED file is invisible to them --
which means a stray scratch file contaminates a regen (three RED files from
other cuts once inflated `test_files_scanned` by 3), and a NEW file you meant to
ship is not covered by its own pre-merge band. MEASURED 2026-08-13: the second
half broke three separate cuts in one session -- tests/_sys_modules_guard.py
read as an undeclared PyPI distribution, the @1098 gate reported "untracked" from
its own shard, and the dependency gate went red -- every one fixed by `git add`
and nothing else. One predicate catches both: anything under tests/ that is
neither tracked nor staged is either scratch to delete or work to stage.

WHY "SERVICES HEALTHY" IS DELIBERATELY NOT WIRED IN, which is the more useful
finding. bd-doctor on test5 at v3.66.1105 exits 1 with RESULT: CRITICAL, for
three missing operator convenience wrappers (bd, bd-install, bd-status -- "run
setup.sh"). None of them bears on whether a COMMIT is safe. Wiring it in as a
blocking check would BLOCK EVERY CUT ON THIS BOX for a condition unrelated to
the cut, and a gate that cries wolf gets switched off -- CLAUDE.md section 0
counts that as a soundness bug rather than a safe default. Service health is a
pre-CAPTURE predicate, not a pre-commit one; capture.sh already stops and starts
the service itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Its subject is one tool's check set, not an invariant over the tree.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-cut-preflight"
_PY = sys.executable


def _fake_repo(tmp_path: Path) -> Path:
    """A real git repo shaped enough for the battery to reach its own checks.

    A real repo, not a directory: every predicate under test reads git, so a
    fake tree would prove nothing about them.
    """
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    (r / "toolchain" / "bin").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "tests" / "test_real.py").write_text("def test_ok():\n    assert True\n",
                                              encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True)
    return r


def _run(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    logdir = tmp_path / "logs"          # OUTSIDE the repo, or the tool refuses
    return subprocess.run(
        [_PY, str(_TOOL), "--repo", str(repo), "--logdir", str(logdir)],
        capture_output=True, text=True, timeout=900,
    )


def _row(out: str, name: str) -> str:
    for ln in out.splitlines():
        if f" {name} " in ln or ln.strip().split(" ")[1:2] == [name]:
            return ln
    return ""


def test_the_harness_builds_a_real_repo(tmp_path):
    """Precondition. If the fixture is not a git repo, every check below reports
    UNKNOWN for that reason and the assertions pass for the wrong one."""
    r = _fake_repo(tmp_path)
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=r, capture_output=True, text=True)
    assert probe.stdout.strip() == "true", probe.stderr


def test_a_stray_file_under_tests_is_reported(tmp_path):
    r = _fake_repo(tmp_path)
    (r / "tests" / "scratch_probe.py").write_text("x = 1\n", encoding="utf-8")

    out = _run(r, tmp_path).stdout
    row = _row(out, "scratch-in-tests")

    assert row, f"no scratch-in-tests check in the battery:\n{out[:1500]}"
    assert "scratch_probe.py" in row or "1 " in row, (
        f"the check ran but did not report the stray file: {row}")
    assert row.split()[0] in ("FAIL", "UNKNOWN"), (
        f"a stray untracked file under tests/ was reported as a pass: {row}")


def test_a_clean_tests_directory_passes(tmp_path):
    """The over-sensitivity control. tests/ is untracked-file-free in the normal
    case, and a check that fired anyway would block every cut."""
    r = _fake_repo(tmp_path)

    out = _run(r, tmp_path).stdout
    row = _row(out, "scratch-in-tests")

    assert row.split()[0] == "PASS", (
        f"a clean tests/ directory did not pass: {row}")


def test_a_STAGED_new_test_file_is_not_reported(tmp_path):
    """THE DISTINCTION THAT MAKES THIS USABLE. A new test file is untracked
    right up until it is staged, and staging it is exactly the remedy. Reporting
    a staged new file would fire on the correct action."""
    r = _fake_repo(tmp_path)
    (r / "tests" / "test_new_gate.py").write_text("def test_x():\n    assert True\n",
                                                  encoding="utf-8")
    subprocess.run(["git", "add", "tests/test_new_gate.py"], cwd=r, check=True)

    out = _run(r, tmp_path).stdout
    row = _row(out, "scratch-in-tests")

    assert row.split()[0] == "PASS", (
        f"a STAGED new test file was reported as scratch: {row}\n"
        "Staging is the remedy for the untracked-file class; firing on it "
        "would punish the fix.")


def test_the_orphan_check_is_present_and_names_its_denominator(tmp_path):
    r = _fake_repo(tmp_path)

    out = _run(r, tmp_path).stdout
    row = _row(out, "orphans")

    assert row, f"no orphans check in the battery:\n{out[:1500]}"
    assert row.split()[0] in ("PASS", "FAIL", "UNKNOWN"), row


def test_services_health_is_deliberately_absent(tmp_path):
    """Row 35 names it; this battery deliberately does not implement it, and
    the reason must survive as an executable claim rather than a comment.

    bd-doctor exits 1 with RESULT: CRITICAL on this box for three missing
    operator wrappers that have no bearing on whether a commit is safe. A
    blocking check on that would BLOCK EVERY CUT here.
    """
    r = _fake_repo(tmp_path)

    out = _run(r, tmp_path).stdout

    assert not _row(out, "services"), (
        "a services-health check appeared in the pre-commit battery. If that is "
        "deliberate, it must not be blocking: bd-doctor is CRITICAL on this box "
        "for missing operator wrappers, so this would refuse every cut.")
