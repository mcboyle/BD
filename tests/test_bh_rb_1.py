"""BH-RB-1: three harness cron fixes (bughunt findings BH-bd-kimi-audit-2-010/003/008).

010 ghost-worktree-sweeper.sh: symlinked review entries are swept (link only, target never moves) unless the target
    is live, protected or holds .git.
003 bd-council-claim-keeper.sh: default seat matches the live tmux session name (AGY-council); a configured name
    that differs only in case resolves to the single matching session; never a prefix match.
008 bd-auto-away.sh: an empty or non-numeric OPERATOR-LAST-TYPED marker is COULD-NOT-LOOK (rc 2, no flip).

Harness candidate test (bd-harness-cut shape): opt in with
BD_BH_RB_1_CANDIDATE=/home/mboyle/bd-persist/harness-work/FIX/bh-rb-1-bd-worker-A3-A (dir holding the three scripts).
Every run is hermetic: paths, the tmux server (TMUX_TMPDIR), the claim helper and bd-say are fixtures.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH_RB_1_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

OLD = time.time() - 3 * 3600
SCRIPTS = ("ghost-worktree-sweeper.sh", "bd-council-claim-keeper.sh", "bd-auto-away.sh")


def _script(name: str) -> str:
    return str(Path(CANDIDATE) / name)


def test_candidate_scripts_are_executable_with_shebang() -> None:
    for name in SCRIPTS:
        path = Path(_script(name))
        assert path.is_file() and os.access(path, os.X_OK), f"candidate missing: {path}"
        assert path.read_text().splitlines()[0] == "#!/bin/bash", path


# --- 010 ghost-worktree-sweeper: symlinked population -------------------------------------------------------------


def _age(path: Path) -> Path:
    """Backdate path (and, for a real dir, everything under it) by 3 h without following links."""
    if path.is_dir() and not path.is_symlink():
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files + dirs:
                os.utime(Path(root) / name, (OLD, OLD), follow_symlinks=False)
    os.utime(path, (OLD, OLD), follow_symlinks=False)
    return path


def _git(env: dict[str, str], *args: str) -> None:
    subprocess.run(["git", *args], env=env, check=True, capture_output=True, timeout=30)


@pytest.fixture
def sweep_env(tmp_path: Path) -> dict[str, str]:
    for name in ("review", "strays", "scratch", "targets"):
        (tmp_path / name).mkdir()
    (tmp_path / "claims.tsv").write_text("")
    env = {
        **os.environ,
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GHOST_SWEEP_REVIEW_WT": str(tmp_path / "review"),
        "GHOST_SWEEP_STRAYS": str(tmp_path / "strays"),
        "GHOST_SWEEP_SCRATCH": str(tmp_path / "scratch"),
        "GHOST_SWEEP_MAIN_REPO": str(tmp_path / "main"),
        "GHOST_SWEEP_CLAIMS": str(tmp_path / "claims.tsv"),
        "GHOST_SWEEP_LOG": str(tmp_path / "sweep.log"),
        "GHOST_SWEEP_LOCK": str(tmp_path / "sweep.lock"),
    }
    _git(env, "init", "-q", str(tmp_path / "main"))
    return env


def _sweep(env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run(
        [_script(SCRIPTS[0])],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    log = Path(env["GHOST_SWEEP_LOG"])
    return proc.returncode, (log.read_text() if log.exists() else "")


def _link(env: dict[str, str], name: str, target: Path) -> Path:
    link = Path(env["GHOST_SWEEP_REVIEW_WT"]) / name
    link.symlink_to(target)
    return _age(link)


def test_old_dangling_symlink_is_swept_as_a_link(sweep_env: dict[str, str]) -> None:
    link = _link(
        sweep_env, "ghost", Path(sweep_env["GHOST_SWEEP_REVIEW_WT"]) / "gone-wt"
    )
    strays = Path(sweep_env["GHOST_SWEEP_STRAYS"])

    rc, log = _sweep(sweep_env)

    assert rc == 0, log
    assert not link.is_symlink() and (strays / "ghost").is_symlink()
    assert f"MOVED {link} -> {strays}/ghost" in log
    assert "SWEEP moved=1 skipped=0 failed=0 dry_run=0" in log


def _other_repo(env: dict[str, str], base: Path) -> Path:
    other = base / "other-main"
    _git(env, "init", "-q", str(other))
    _git(env, "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "i")  # fmt: skip
    return other


# Every kind of live target seen in lens rounds R1/G2 (and the plain case): a link with a live target never moves.
@pytest.mark.parametrize(
    "kind",
    ["plain-idle-dir", "nested-git", "claimed", "main-registered", "other-repo-subdir",
     "separate-git-dir-alias"],
)  # fmt: skip
def test_link_with_live_target_is_never_moved(
    sweep_env: dict[str, str], kind: str
) -> None:
    base = Path(sweep_env["GHOST_SWEEP_STRAYS"]).parent / "targets"
    target = base / "wt"
    if kind == "main-registered":
        main = sweep_env["GHOST_SWEEP_MAIN_REPO"]
        _git(sweep_env, "-C", main, "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "-q", "--allow-empty", "-m", "i")  # fmt: skip
        _git(sweep_env, "-C", main, "worktree", "add", "-q", "--detach", str(target))
    elif kind == "other-repo-subdir":
        other = _other_repo(sweep_env, base)
        _git(
            sweep_env,
            "-C",
            str(other),
            "worktree",
            "add",
            "-q",
            "--detach",
            str(base / "o-wt"),
        )
        target = base / "o-wt" / "subdir"
        target.mkdir()
    elif kind == "separate-git-dir-alias":
        # codex-r127c G2: the link IS the gitdir path a worktree's .git file names
        _git(
            sweep_env,
            "init",
            "-q",
            f"--separate-git-dir={base / 'storage'}",
            str(base / "sep-wt"),
        )
        target = base / "storage"
    else:
        target.mkdir(parents=True)
    if kind == "nested-git":
        (target / "sub").mkdir()
        (target / "sub" / ".git").write_text("gitdir: /nowhere\n")
    if kind == "claimed":
        Path(sweep_env["GHOST_SWEEP_CLAIMS"]).write_text(
            f"CLAIM\t{target}\tc\ts\tt\tx\n"
        )
    _age(base)
    link = _link(sweep_env, "lens-link", target)
    if kind == "separate-git-dir-alias":
        (base / "sep-wt" / ".git").write_text(f"gitdir: {link}\n")
        _age(base)
        # fixture is real: git reaches its metadata THROUGH the link the sweeper must not move
        probe = subprocess.run(["git", "-C", str(base / "sep-wt"), "rev-parse", "--git-dir"],
                               env=sweep_env, capture_output=True, text=True, timeout=30,
                               check=False)  # fmt: skip
        assert probe.returncode == 0, probe
        assert Path(probe.stdout.strip()).resolve() == target.resolve()

    rc, log = _sweep(sweep_env)

    assert rc == 0, log
    assert link.is_symlink() and link.resolve() == target.resolve()
    assert f"SKIP {link}: live link to {target.resolve()}" in log
    assert "SWEEP moved=0 skipped=1 failed=0 dry_run=0" in log


@pytest.mark.parametrize(
    "kind", ["bare-repo", "separate-git-dir-storage", "worktree-admin"]
)
def test_dir_holding_git_metadata_under_another_name_is_skipped(
    sweep_env: dict[str, str], kind: str
) -> None:
    review = Path(sweep_env["GHOST_SWEEP_REVIEW_WT"])
    held = review / "holder"
    held.mkdir()
    if kind == "bare-repo":
        meta = held / "repo.git"
        _git(sweep_env, "init", "-q", "--bare", str(meta))
    elif kind == "separate-git-dir-storage":
        meta = held / "storage"
        other = Path(sweep_env["GHOST_SWEEP_STRAYS"]).parent / "targets" / "sep-wt"
        _git(sweep_env, "init", "-q", f"--separate-git-dir={meta}", str(other))
    else:
        meta = held / "admin"
        meta.mkdir()
        (meta / "HEAD").write_text("0" * 40 + "\n")
        (meta / "commondir").write_text("../..\n")
    _age(held)

    rc, log = _sweep(sweep_env)

    assert rc == 0, log
    assert (meta / "HEAD").is_file()
    assert f"SKIP {held}: holds git metadata: {meta}" in log


def test_head_file_without_git_layout_does_not_protect(
    sweep_env: dict[str, str],
) -> None:
    held = Path(sweep_env["GHOST_SWEEP_REVIEW_WT"]) / "notes"
    held.mkdir()
    (held / "HEAD").write_text("just a file named HEAD\n")
    _age(held)

    rc, log = _sweep(sweep_env)

    assert rc == 0, log
    assert f"MOVED {held} ->" in log


def test_dangling_link_collision_in_strays_is_refused(
    sweep_env: dict[str, str],
) -> None:
    (Path(sweep_env["GHOST_SWEEP_STRAYS"]) / "ghost").symlink_to("/nowhere/else")
    link = _link(sweep_env, "ghost", Path("/nowhere/at/all"))

    rc, log = _sweep(sweep_env)

    assert rc == 1, log
    assert link.is_symlink() and os.readlink(link) == "/nowhere/at/all"
    assert "(target exists)" in log


def test_scratch_symlinks_are_not_swept(sweep_env: dict[str, str]) -> None:
    lens = Path(sweep_env["GHOST_SWEEP_SCRATCH"]) / "as1"
    lens.mkdir()
    log_link = lens / "as1-claim.log"
    log_link.symlink_to("/nowhere/claim.log")
    _age(log_link)

    rc, log = _sweep(sweep_env)

    assert rc == 0, log
    assert log_link.is_symlink()
    assert "SWEEP moved=0 skipped=0 failed=0 dry_run=0" in log


# --- 003 council-claim-keeper: tmux session name case -------------------------------------------------------------


@pytest.fixture
def keeper_env(tmp_path: Path) -> Iterator[dict[str, str]]:
    sock = tempfile.mkdtemp(
        prefix="rb1", dir="/tmp"
    )  # short: tmux socket paths are length-limited
    helper = tmp_path / "role-claim"
    helper.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> {tmp_path}/helper.calls\n'
        f'[ "$1" = who ] && cat {tmp_path}/holders 2>/dev/null\n'
        "exit 0\n"
    )
    helper.chmod(0o755)
    (tmp_path / "presence").write_text("PRESENT\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TMUX", "TMUX_PANE", "BD_COUNCIL_SEAT")
    }
    env |= {
        "LC_ALL": "C",
        "TMUX_TMPDIR": sock,
        "BD_ROLE_CLAIM_HELPER": str(helper),
        "BD_OPERATOR_PRESENCE": str(tmp_path / "presence"),
    }
    yield env
    subprocess.run(
        ["tmux", "kill-server"], env=env, capture_output=True, timeout=30, check=False
    )
    shutil.rmtree(sock, ignore_errors=True)


def _sessions(env: dict[str, str], *names: str) -> None:
    for name in names:
        subprocess.run(["tmux", "new-session", "-d", "-s", name, "sleep 600"], env=env,
                       check=True, capture_output=True, timeout=30)  # fmt: skip


def _keep(env: dict[str, str]) -> tuple[int, str, list[str]]:
    proc = subprocess.run(
        [_script(SCRIPTS[1])],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    calls = Path(env["BD_OPERATOR_PRESENCE"]).parent / "helper.calls"
    lines = calls.read_text().splitlines() if calls.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, lines


def test_default_seat_claims_the_live_lowercase_council_session(
    keeper_env: dict[str, str],
) -> None:
    _sessions(keeper_env, "AGY-council")

    rc, out, calls = _keep(keeper_env)

    assert rc == 0, out
    assert "CLAIMED advisor AGY-council pid=" in out
    assert [c.rsplit(" ", 1)[0] for c in calls if c.startswith("claim")] == [
        "claim advisor AGY-council"
    ]


def test_configured_name_differing_in_case_resolves(keeper_env: dict[str, str]) -> None:
    _sessions(keeper_env, "AGY-council")

    rc, out, calls = _keep({**keeper_env, "BD_COUNCIL_SEAT": "AGY-Council"})

    assert rc == 0, out
    assert "RESOLVED AGY-Council -> AGY-council (tmux name differs in case)" in out
    assert any(c.startswith("claim advisor AGY-council ") for c in calls), calls


def test_exact_name_wins_over_case_variant(keeper_env: dict[str, str]) -> None:
    _sessions(keeper_env, "AGY-council", "AGY-Council")

    rc, out, calls = _keep({**keeper_env, "BD_COUNCIL_SEAT": "AGY-Council"})

    assert rc == 0, out
    assert "RESOLVED" not in out
    assert any(c.startswith("claim advisor AGY-Council ") for c in calls), calls


def test_two_case_variants_without_exact_is_unknown(keeper_env: dict[str, str]) -> None:
    _sessions(keeper_env, "AGY-COUNCIL", "agy-council")

    rc, out, calls = _keep(keeper_env)

    assert rc == 2, out
    assert "UNKNOWN AGY-council: 2 sessions match ignoring case" in out
    assert not [c for c in calls if c.startswith("claim")]


def test_prefix_named_session_is_not_the_council(keeper_env: dict[str, str]) -> None:
    _sessions(keeper_env, "AGY-council-old")

    rc, out, calls = _keep(keeper_env)

    assert rc == 0, out
    assert "SKIP AGY-council: no tmux session" in out
    assert not calls


def test_live_claim_is_left_alone(keeper_env: dict[str, str]) -> None:
    _sessions(keeper_env, "AGY-council")
    (Path(keeper_env["BD_OPERATOR_PRESENCE"]).parent / "holders").write_text(
        "AGY-council\n"
    )

    rc, out, calls = _keep(keeper_env)

    assert rc == 0, out
    assert calls == ["who advisor"] and "CLAIMED" not in out


# --- 008 auto-away: marker must be an epoch -----------------------------------------------------------------------


@pytest.fixture
def away_env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "presence").write_text("PRESENT\n# history\n")
    (tmp_path / "pm-seat").write_text("bd-pm-test\n")
    say = tmp_path / "say"
    say.write_text(f'#!/bin/bash\necho "$*" >> {tmp_path}/say.calls\n')
    say.chmod(0o755)
    return {
        **os.environ,
        "BD_OPERATOR_PRESENCE": str(tmp_path / "presence"),
        "BD_OPERATOR_LAST_TYPED": str(tmp_path / "marker"),
        "BD_PM_SEAT_FILE": str(tmp_path / "pm-seat"),
        "BD_AUTO_AWAY_SAY": str(say),
    }


def _away(env: dict[str, str], marker: str) -> tuple[int, str, str, str]:
    Path(env["BD_OPERATOR_LAST_TYPED"]).write_text(marker)
    proc = subprocess.run(
        [_script(SCRIPTS[2])],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    says = Path(env["BD_AUTO_AWAY_SAY"]).parent / "say.calls"
    return (
        proc.returncode,
        proc.stdout + proc.stderr,
        Path(env["BD_OPERATOR_PRESENCE"]).read_text(),
        says.read_text() if says.exists() else "",
    )


@pytest.mark.parametrize(
    "marker", ["garbage\n", "", "\n", "1790000000\n1790000001\n", "-5\n", "12ab\n"]
)
def test_non_epoch_marker_is_could_not_look(
    away_env: dict[str, str], marker: str
) -> None:
    rc, out, presence, says = _away(away_env, marker)

    assert rc == 2, out
    assert (
        f"COULD-NOT-LOOK: {away_env['BD_OPERATOR_LAST_TYPED']} is not an epoch" in out
    )
    assert presence == "PRESENT\n# history\n" and says == ""


def test_old_epoch_flips_away_and_tells_pm(away_env: dict[str, str]) -> None:
    rc, out, presence, says = _away(away_env, f"{int(time.time()) - 3600}\n")

    assert rc == 0, out
    assert "AUTO-AWAY after 60m" in out
    assert presence.startswith("AWAY\n# history\n")
    assert says.startswith("bd-pm-test AUTO-AWAY 60m")


def test_recent_epoch_changes_nothing(away_env: dict[str, str]) -> None:
    rc, out, presence, says = _away(away_env, f"{int(time.time()) - 60}\n")

    assert rc == 0, out
    assert out == "" and presence == "PRESENT\n# history\n" and says == ""
