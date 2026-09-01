"""bd-rebase-all.sh must not be able to discard a committed candidate.

THE INCIDENT THIS ENCODES. On 2026-08-30 this script discarded six committed
candidates. The sequence is exact: `git stash push -u` SUCCEEDS AND STASHES
NOTHING when the work is committed rather than dirty; the following
`git checkout --detach "$MAIN"` moves the worktree off the candidate commit
anyway; `git stash pop` has nothing to restore; and the loop reports success.
The committed work is then reachable only from the reflog.

These tests drive the real script against real git repositories. The load-
bearing one is test_a_committed_candidate_survives, which reproduces the
incident shape directly.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

HOME = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home())))
SCRIPT = HOME / "bd-rebase-all.sh"


def git(r, *a, check=True):
    p = subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)}: {p.stderr}")
    return p.stdout.strip()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A repo, a main that has advanced, and a worker worktree for row 999."""
    repo = tmp_path / "BulkDownloader"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", "base.txt"); git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")

    (repo / "moved.txt").write_text("main moved\n")
    git(repo, "add", "moved.txt"); git(repo, "commit", "-qm", "main advances")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    main = git(repo, "rev-parse", "HEAD")

    wt = tmp_path / "bd-codex-wt" / "row999"
    wt.parent.mkdir(parents=True)
    git(repo, "worktree", "add", "-q", "--detach", str(wt), base)
    return {"repo": repo, "wt": wt, "main": main, "base": base, "tmp": tmp_path}


def run_script(world):
    """Run the real script with its hard-coded paths redirected via a sandbox."""
    art = world["tmp"] / "fleet-run-artifacts" / "2026-08-25"
    (art / "inflight").mkdir(parents=True, exist_ok=True)
    body = SCRIPT.read_text()
    body = body.replace("/home/mboyle/BulkDownloader", str(world["repo"]))
    body = body.replace("/home/mboyle/bd-codex-wt", str(world["tmp"] / "bd-codex-wt"))
    body = body.replace("/home/mboyle/fleet-run-artifacts/2026-08-25", str(art))
    body = body.replace("python3 /home/mboyle/bd-resolve-owned.py", "true")
    sandboxed = world["tmp"] / "bd-rebase-all-sandboxed.sh"
    sandboxed.write_text(body)
    p = subprocess.run(["bash", str(sandboxed), "999"], capture_output=True, text=True)
    log = (art / "inflight" / "bd-rebase.log")
    out = log.read_text() if log.exists() else ""
    for extra in (art / "inflight").glob("*.log"):
        out += extra.read_text()
    return p, out


def test_a_committed_candidate_survives(world):
    """The 2026-08-30 incident, reproduced: work COMMITTED, nothing dirty."""
    wt = world["wt"]
    (wt / "candidate.txt").write_text("the candidate's only copy\n")
    git(wt, "add", "candidate.txt")
    git(wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "the candidate")
    head = git(wt, "rev-parse", "HEAD")
    assert git(wt, "status", "--porcelain") == "", "precondition: nothing dirty, all committed"

    p, out = run_script(world)

    # The work must still be reachable: either still in the worktree, or from a
    # ref that outlives the reflog. Never only in the reflog.
    still_there = (wt / "candidate.txt").exists()
    pinned = subprocess.run(["git", "-C", str(world["repo"]), "for-each-ref",
                             "--format=%(objectname)", "refs/candidate-safety/"],
                            capture_output=True, text=True).stdout.split()
    assert still_there or head in pinned, (
        f"the committed candidate was discarded. head={head[:8]} "
        f"file_present={still_there} pinned={[x[:8] for x in pinned]}\n{out}")
    assert head in pinned, f"the candidate was not pinned before the destructive step\n{out}"


def test_the_pin_is_a_real_ref_not_a_reflog_entry(world):
    wt = world["wt"]
    (wt / "candidate.txt").write_text("x\n")
    git(wt, "add", "candidate.txt")
    git(wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
    head = git(wt, "rev-parse", "HEAD")
    run_script(world)
    refs = subprocess.run(["git", "-C", str(world["repo"]), "for-each-ref",
                           "--format=%(refname) %(objectname)", "refs/candidate-safety/"],
                          capture_output=True, text=True).stdout
    assert head in refs, refs
    assert f"refs/candidate-safety/row999/" in refs, refs


def test_dirty_work_still_rebases_normally(world):
    """The negative control: the ordinary dirty-worktree path must still work."""
    wt = world["wt"]
    (wt / "dirty.txt").write_text("uncommitted work\n")
    assert git(wt, "status", "--porcelain") != "", "precondition: the worktree is dirty"
    p, out = run_script(world)
    assert (wt / "dirty.txt").exists(), f"dirty work was lost\n{out}"
    assert (wt / "dirty.txt").read_text() == "uncommitted work\n"
    assert (wt / "moved.txt").exists(), f"the worktree did not advance to main\n{out}"


def test_an_already_current_worktree_is_left_alone(world):
    wt = world["wt"]
    git(wt, "checkout", "-q", "--detach", world["main"])
    p, out = run_script(world)
    assert "already on main" in out, out
    refs = subprocess.run(["git", "-C", str(world["repo"]), "for-each-ref",
                           "refs/candidate-safety/"], capture_output=True, text=True).stdout
    assert refs.strip() == "", "nothing destructive ran, so nothing needed pinning"
