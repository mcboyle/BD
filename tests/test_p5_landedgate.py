"""P5 LANDED-CHECK GATE (ORDER-O1487, TRIAGE-LANDED-CUTS-REQUEUED-bd-sentinel-A-20260928T1310Z).

Landed cuts kept re-entering the lens/fix queues: a cut squash-lands as one commit, a later commit edits the same
files, and every blob comparison then reads NOT-LANDED. The candidate adds bd-landed-gate (patch-id --stable of the
cut == an origin/main commit) and puts it in front of bd-landed-check, bd-verdict-route.sh, bd-offer-sweep.sh,
bd-review-worklist-a and bd-claim-object.sh take.

Hermetic: a throwaway repo with a refs/remotes/origin/main, a "spec" squash commit, then a later edit of the same
file; the cut is a git worktree at the base with the patch staged. Opt in with
BD_P5_LANDEDGATE_CANDIDATE=<dir holding the candidate scripts>.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_P5_LANDEDGATE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _script(name: str) -> Path:
    path = Path(CANDIDATE) / name
    assert path.is_file(), f"candidate script missing: {path}"
    return path


def _git(cwd: Path, *args: str) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
    )
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return r.stdout.strip()


def _fleet(tmp_path: Path) -> dict[str, Path | str]:
    """repo with base -> spec (lands cut A) -> later edit of the same file; cut A and unlanded cut B as worktrees."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("".join(f"line {i}\n" for i in range(40)))
    (repo / "other.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    landed_text = (repo / "app.py").read_text().replace("line 5\n", "line 5 FIXED\n")
    (repo / "app.py").write_text(landed_text)
    _git(repo, "commit", "-q", "-am", "spec: cut-a")
    spec = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text(landed_text.replace("line 30\n", "line 30 later\n"))
    _git(repo, "commit", "-q", "-am", "later edit of the same file")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    def cut(name: str, old: str, new: str) -> Path:
        wt = tmp_path / name
        _git(repo, "worktree", "add", "-q", "--detach", str(wt), base)
        f = wt / "app.py"
        f.write_text(f.read_text().replace(old, new))
        _git(wt, "add", "app.py")
        (wt / "DONE.md").write_text(
            f"VERDICT: PATCH\nSEAT: fixture-owner\nBASE: {base}\n"
        )
        return wt

    return {
        "repo": repo,
        "spec": spec,
        "landed": cut("cut-a", "line 5\n", "line 5 FIXED\n"),
        "open": cut("cut-b", "line 7\n", "line 7 NOT ON MAIN\n"),
    }


def _run(script: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=dict(os.environ, **env),
        timeout=120,
        check=False,
    )


def test_gate_finds_a_squash_landed_cut_after_main_moved(tmp_path: Path) -> None:
    f = _fleet(tmp_path)
    blob_cut = _git(Path(f["landed"]), "rev-parse", ":app.py")
    blob_main = _git(Path(f["repo"]), "rev-parse", "origin/main:app.py")
    assert blob_cut != blob_main, "fixture must defeat blob equality"
    r = _run(_script("bd-landed-gate"), str(f["landed"]), BD_REPO=str(f["repo"]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("LANDED ") and str(f["spec"])[:12] in r.stdout


def test_gate_negative_control_unlanded_cut(tmp_path: Path) -> None:
    f = _fleet(tmp_path)
    r = _run(_script("bd-landed-gate"), str(f["open"]), BD_REPO=str(f["repo"]))
    assert r.returncode == 3, r.stdout + r.stderr
    assert r.stdout.startswith("NOT-LANDED ")


def test_gate_prefers_collected_patch_diff(tmp_path: Path) -> None:
    f = _fleet(tmp_path)
    collected = tmp_path / "collected"
    collected.mkdir()
    (collected / "patch.diff").write_text(
        _git(Path(f["landed"]), "diff", "--cached") + "\n"
    )
    r = _run(_script("bd-landed-gate"), str(collected), BD_REPO=str(f["repo"]))
    assert r.returncode == 0 and "(patch.diff)" in r.stdout, r.stdout + r.stderr


def test_gate_without_an_object_is_unknown(tmp_path: Path) -> None:
    f = _fleet(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(_script("bd-landed-gate"), str(empty), BD_REPO=str(f["repo"]))
    assert r.returncode == 4 and r.stdout.startswith("UNKNOWN "), r.stdout + r.stderr


def test_landed_check_says_landed_for_the_squash_landed_cut(tmp_path: Path) -> None:
    f = _fleet(tmp_path)
    r = _run(_script("bd-landed-check"), str(f["landed"]), BD_REPO=str(f["repo"]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "patch-id" in r.stdout
    r = _run(_script("bd-landed-check"), str(f["open"]), BD_REPO=str(f["repo"]))
    assert r.returncode == 3, r.stdout + r.stderr


def _route(tmp_path: Path, f: dict[str, Path | str], which: str) -> str:
    persist = tmp_path / f"persist-{which}"
    (persist / "logs").mkdir(parents=True)
    (persist / "review.log").write_text("")
    rw = tmp_path / f"rw-{which}"
    rw.mkdir()
    row = rw / f"row{which}-fixture"
    row.symlink_to(f[which])
    review = Path(f[which]) / ".review"
    review.mkdir(exist_ok=True)
    verdict = review / "VERDICT-correctness-fixture.md"
    verdict.write_text("VERDICT: REFUTE\nR1 fixture\n")
    past = time.time() - 600
    os.utime(Path(f[which]) / "DONE.md", (past, past))
    log = persist / "logs" / "route.log"
    say = tmp_path / "say.sh"
    say.write_text('#!/bin/bash\necho "$@" >> "$(dirname "$0")/said.txt"\n')
    say.chmod(0o755)
    _run(
        _script("bd-verdict-route.sh"),
        BD_PERSIST_ROOT=str(persist),
        BD_REVIEW_WT=str(rw),
        BD_VERDICT_ROUTE_LOG=str(log),
        BD_VERDICT_ROUTE_SAY=str(say),
        BD_REPO=str(f["repo"]),
        BD_LANDED_GATE=str(_script("bd-landed-gate")),
        BD_PM_SEAT="fixture-pm",
    )
    return log.read_text() if log.exists() else ""


def test_verdict_route_does_not_route_a_refute_on_a_landed_cut(tmp_path: Path) -> None:
    f = _fleet(tmp_path)
    log = _route(tmp_path, f, "landed")
    assert "VERDICT-LANDED landed-fixture" in log, log
    assert "VERDICT-ROUTED" not in log and "FIX IN PLACE (O928)" not in log, log


def test_verdict_route_sends_a_refute_on_shipped_code_to_pm(tmp_path: Path) -> None:
    """REFUTE A2-A R3: a REFUTE on a landed cut is a live defect; it goes to the PM, never nowhere."""
    f = _fleet(tmp_path)
    log = _route(tmp_path, f, "landed")
    said = tmp_path / "said.txt"
    sent = said.read_text() if said.exists() else ""
    assert "LANDED-DEFECT -> PM fixture-pm" in log, log
    assert "LANDED-DEFECT (P5) VERDICT: REFUTE" in log + sent, log + sent
    assert "fixture-pm" in log + sent and "FIX IN PLACE" not in sent, log + sent


def _landing_then(tmp_path: Path, after: str) -> tuple[Path, Path]:
    """main: base -> spec (b() -> c(), d(): the hunk grows, so a revert must swap its ranges) -> <after>; the cut is staged at base (DONE.md BASE)."""
    repo = tmp_path / "up"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "m.py").write_text("def f(x):\n    if x:\n        a()\n    b()\n")
    _git(repo, "add", "m.py")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "m.py").write_text("def f(x):\n    if x:\n        a()\n    c()\n    d()\n")
    _git(repo, "commit", "-q", "-am", "spec: landed")
    if after == "revert":
        _git(repo, "revert", "--no-edit", "HEAD")
    (repo / "other.py").write_text("x = 1\n")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-q", "-m", "later")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    wt = tmp_path / "cut"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt), base)
    (wt / "DONE.md").write_text(f"VERDICT: PATCH\nBASE: {base}\n")
    return repo, wt


def _gate_with(repo: Path, wt: Path, text: str) -> subprocess.CompletedProcess[str]:
    (wt / "m.py").write_text(text)
    _git(wt, "add", "m.py")
    return _run(_script("bd-landed-gate"), str(wt), BD_REPO=str(repo))


def test_gate_is_not_whitespace_blind(tmp_path: Path) -> None:
    """REFUTE A2-A R1: c() moved into the if-block is a different program; patch-id alone says LANDED."""
    repo, wt = _landing_then(tmp_path, "none")
    r = _gate_with(
        repo, wt, "def f(x):\n    if x:\n        a()\n        c()\n    d()\n"
    )
    assert r.returncode == 3 and "the lines differ" in r.stdout, r.stdout + r.stderr
    r = _gate_with(repo, wt, "def f(x):\n    if x:\n        a()\n    c()\n    d()\n")
    assert r.returncode == 0 and r.stdout.startswith("LANDED "), r.stdout + r.stderr


def test_gate_is_not_revert_blind(tmp_path: Path) -> None:
    """REFUTE A2-A R2: a landing that main later reverted is NOT landed; the cut needs its fix."""
    repo, wt = _landing_then(tmp_path, "revert")
    r = _gate_with(repo, wt, "def f(x):\n    if x:\n        a()\n    c()\n    d()\n")
    assert r.returncode == 3 and "REVERTED by origin/main" in r.stdout, (
        r.stdout + r.stderr
    )


def test_not_landed_message_separates_base_and_main(tmp_path: Path) -> None:
    """REFUTE A2-A R4: the range prints as <base>..<main>, not run together."""
    f = _fleet(tmp_path)
    r = _run(_script("bd-landed-gate"), str(f["open"]), BD_REPO=str(f["repo"]))
    main = _git(Path(f["repo"]), "rev-parse", "origin/main")
    assert f"..{main} " in r.stdout, r.stdout


def test_verdict_route_negative_control_still_routes_unlanded(tmp_path: Path) -> None:
    f = _fleet(tmp_path)
    log = _route(tmp_path, f, "open")
    assert "VERDICT-LANDED" not in log, log
    assert "rowopen-fixture" in log or (tmp_path / "said.txt").exists(), log


def test_wiring_every_queue_writer_calls_the_gate() -> None:
    for name in (
        "bd-offer-sweep.sh",
        "bd-claim-object.sh",
        "bd-review-worklist-a",
        "bd-verdict-route.sh",
    ):
        text = _script(name).read_text()
        assert "bd-landed-gate" in text or "bd-landed-check" in text, name
    assert "SKIP-LANDED-BY-PATCH-ID" in _script("bd-offer-sweep.sh").read_text()
    worklist = _script("bd-review-worklist-a").read_text()
    assert 'if [ "$ship" = 0 ] && [ "$eq" -gt 0 ]' not in worklist, (
        "worklist still skips the check when eq=0"
    )


def _landed_change(tmp_path: Path, change: str, revert: bool) -> tuple[Path, Path]:
    """main: base -> spec (<change>) [-> its git revert] -> later; the cut makes the same <change> staged at base.

    change "rename": git mv m.py n.py plus one appended line; "mode": m.py becomes executable (no content line)."""
    repo = tmp_path / "up"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "m.py").write_text("".join(f"v{i} = {i}\n" for i in range(30)))
    _git(repo, "add", "m.py")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    wt = tmp_path / "cut"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt), base)
    (wt / "DONE.md").write_text(f"VERDICT: PATCH\nBASE: {base}\n")
    for tree in (repo, wt):
        if change == "rename":
            _git(tree, "mv", "m.py", "n.py")
            with (tree / "n.py").open("a") as fh:
                fh.write("v30 = 30\n")
            _git(tree, "add", "n.py")
        else:
            (tree / "m.py").chmod(0o755)
            _git(tree, "add", "m.py")
    _git(repo, "commit", "-q", "-m", f"spec: {change}")
    if revert:
        _git(repo, "revert", "--no-edit", "HEAD")
    (repo / "other.py").write_text("x = 1\n")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-q", "-m", "later")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, wt


@pytest.mark.parametrize("change", ["rename", "mode"])
def test_gate_follows_renames_and_modes_through_landing_and_revert(
    tmp_path: Path, change: str
) -> None:
    """REFUTE A3-A R1/R2: a renamed cut's revert was never matched (false LANDED); a patch with no +/- lines read
    'the lines differ' even when it had landed. Landed -> LANDED; landed then reverted -> NOT-LANDED REVERTED."""
    repo, wt = _landed_change(tmp_path / "kept", change, revert=False)
    r = _run(_script("bd-landed-gate"), str(wt), BD_REPO=str(repo))
    assert r.returncode == 0 and r.stdout.startswith("LANDED "), r.stdout + r.stderr
    repo, wt = _landed_change(tmp_path / "reverted", change, revert=True)
    r = _run(_script("bd-landed-gate"), str(wt), BD_REPO=str(repo))
    assert r.returncode == 3 and "REVERTED by origin/main" in r.stdout, (
        r.stdout + r.stderr
    )
