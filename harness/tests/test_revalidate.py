"""bd-revalidate.sh: the parked-candidate revalidation tool.

The behaviours under test are the ones that make it safe to trust: it refuses
rather than guesses, an unmeasurable candidate is UNKNOWN and not a pass, and
it never writes outside its worktree.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path("/home/mboyle/bd-persist/harness/bd-revalidate.sh")
FORTY = "0" * 40


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """A real git repo with a main commit and one candidate off it."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    (r / "tests").mkdir()
    (r / "tests" / "test_base.py").write_text("def test_base():\n    assert True\n")
    git(r, "add", "tests/test_base.py"); git(r, "commit", "-qm", "base")
    main = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "-b", "cand")
    (r / "tests" / "test_cand.py").write_text("def test_cand():\n    assert True\n")
    git(r, "add", "tests/test_cand.py"); git(r, "commit", "-qm", "cand")
    cand = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "main")
    # A venv whose python is a stub pytest runner.
    vb = r / "venv" / "bin"; vb.mkdir(parents=True)
    (vb / "python").write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import os, sys
        rc = int(os.environ.get("FAKE_PYTEST_RC", "0"))
        print("1 passed" if rc == 0 else "1 failed")
        sys.exit(rc)
        """))
    (vb / "python").chmod(0o755)
    return {"repo": r, "main": main, "cand": cand, "out": tmp_path / "out"}


def run(repo, *args, **env):
    e = dict(os.environ, BD_REVAL_REPO=str(repo["repo"]), BD_REVAL_OUT=str(repo["out"]))
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                          text=True, env=e)


def test_a_clean_candidate_revalidates_green(repo):
    r = run(repo, repo["main"], "row1", repo["cand"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REVALIDATED-GREEN" in r.stdout, r.stdout


def test_a_red_candidate_is_not_laundered_into_a_pass(repo):
    r = run(repo, repo["main"], "row1", repo["cand"], FAKE_PYTEST_RC=1)
    assert r.returncode == 1, r.stdout
    assert "RED-ON-NEW-MAIN" in r.stdout, r.stdout


def test_a_candidate_with_no_test_files_is_unknown_not_green(repo):
    """The precondition that makes a green meaningful, asserted directly.

    A diff carrying no test file cannot be measured by this tool. Reporting
    that as a pass is the exact fail-open shape CLAUDE.md A7 forbids.
    """
    r = repo["repo"]
    git(r, "checkout", "-q", "-b", "notests", repo["main"])
    (r / "README.md").write_text("no tests here\n")
    git(r, "add", "README.md"); git(r, "commit", "-qm", "docs only")
    sha = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "main")
    res = run(repo, repo["main"], "row1", sha)
    assert res.returncode == 1, res.stdout
    assert "UNKNOWN-no-tests-in-diff" in res.stdout, res.stdout


def test_a_conflicting_candidate_reports_needs_manual_rebase(repo):
    r = repo["repo"]
    # Move main so the candidate's file collides.
    git(r, "checkout", "-q", "main")
    (r / "tests" / "test_cand.py").write_text("def test_cand():\n    assert 0\n")
    git(r, "add", "tests/test_cand.py"); git(r, "commit", "-qm", "main takes the same path")
    newmain = git(r, "rev-parse", "HEAD")
    res = run(repo, newmain, "row1", repo["cand"])
    assert res.returncode == 1, res.stdout
    assert "NEEDS-MANUAL-REBASE" in res.stdout, res.stdout


@pytest.mark.parametrize("bad", ["nope", "0" * 39, "Z" * 40])
def test_a_bad_main_sha_refuses_before_touching_anything(repo, bad):
    res = run(repo, bad, "row1", repo["cand"])
    assert res.returncode == 2, res.stdout + res.stderr
    assert "UNKNOWN" in (res.stdout + res.stderr)
    assert not repo["out"].exists(), "refusal must not create the log directory"


def test_an_absent_candidate_sha_is_unknown(repo):
    res = run(repo, repo["main"], "row1", FORTY)
    assert res.returncode == 1, res.stdout
    assert "UNKNOWN-sha-absent" in res.stdout, res.stdout


def test_no_pairs_is_unknown_not_success(repo):
    res = run(repo, repo["main"])
    assert res.returncode == 2, res.stdout + res.stderr


def test_several_candidates_report_individually(repo):
    r = repo["repo"]
    git(r, "checkout", "-q", "-b", "cand2", repo["main"])
    (r / "tests" / "test_two.py").write_text("def test_two():\n    assert True\n")
    # NEVER `git add -A` HERE. The stub venv is untracked and lives beside the
    # tests; -A commits it onto this branch, and the `git checkout main` below
    # then deletes it from the working tree, so the first candidate's worktree
    # symlinks a venv that no longer exists and reports a spurious RED.
    git(r, "add", "tests/test_two.py"); git(r, "commit", "-qm", "cand2")
    two = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "main")
    res = run(repo, repo["main"], "rowA", repo["cand"], "rowB", two)
    assert res.returncode == 0, res.stdout
    assert res.stdout.count("REVALIDATED-GREEN") == 2, res.stdout
    assert "rowA" in res.stdout and "rowB" in res.stdout
