"""EFF-E1: bd-harness-gc.py archives (never deletes) stale backup/variant copies from the top of bd-persist/harness.

PROPOSAL-bd-agy-cartograph-1-001/002/005 (one mechanism). The harness is deployed from bd-persist, not this repo:
BD_EFF_E1_CANDIDATE is the absolute path of the candidate script. Every run is hermetic: a fixture harness root,
archive dir, home dir and crontab file via the script's BD_HARNESS_GC_* seams.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_EFF_E1_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

OLD = time.time() - 30 * 86400
# ctime cannot be set, so a fixture file is always "just arrived". The script's "now" moves 30 days ahead instead
# (BD_HARNESS_GC_NOW): an OLD fixture is then old by mtime AND ctime; a young one gets an mtime 1 h before that now.
NOW = time.time() + 30 * 86400


def _file(p: Path, body: str = "x\n", old: bool = True) -> Path:
    p.write_text(body)
    t = OLD if old else NOW - 3600
    os.utime(p, (t, t))
    return p


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    d = {k: tmp_path / k for k in ("root", "archive", "home")}
    for k in ("root", "home"):
        d[k].mkdir()
    d["crontab"] = tmp_path / "crontab.txt"
    d["crontab"].write_text("* * * * * bash /x/bd-live.sh\n")
    return d


def _gc(
    env: dict[str, Path], *args: str, now: float = NOW
) -> subprocess.CompletedProcess[str]:
    script = Path(CANDIDATE)
    assert script.is_file(), f"candidate missing: {CANDIDATE}"
    e = {
        **os.environ,
        "BD_HARNESS_GC_ROOT": str(env["root"]),
        "BD_HARNESS_GC_ARCHIVE": str(env["archive"]),
        "BD_HARNESS_GC_HOME": str(env["home"]),
        "BD_HARNESS_GC_CRONTAB": str(env["crontab"]),
        "BD_HARNESS_GC_NOW": str(now),
    }
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=e,
        timeout=60,
        check=False,
    )


def _lines(res: subprocess.CompletedProcess[str]) -> set[str]:
    return set(res.stdout.splitlines()[:-1])


def test_dry_run_plans_and_moves_nothing(env: dict[str, Path]) -> None:
    _file(env["root"] / "bd-a.sh.pre-20260901T000000Z")
    res = _gc(env)
    assert res.returncode == 0, res
    assert _lines(res) == {"ARCHIVE pre bd-a.sh.pre-20260901T000000Z"}
    assert res.stdout.splitlines()[-1].startswith(
        "SUMMARY DRY-RUN matched=1 ARCHIVE=1 "
    )
    assert (env["root"] / "bd-a.sh.pre-20260901T000000Z").exists()
    assert not env["archive"].exists()


def test_apply_archives_with_sha_manifest(env: dict[str, Path]) -> None:
    names = [
        "bd-a.sh.pre-20260901T000000Z",
        ".bak-bd-b-20260904T202908Z",
        "bd-c.sh.bak",
        "bd-d.py.ORIGINAL-pre-x",
        "bd-e.sh.post-HB-1",
        "bd-f.sh.known-good-1",
        "bd-g-OLD-decl.sh",
    ]
    shas = {n: hashlib.sha256(f"body {n}\n".encode()).hexdigest() for n in names}
    for n in names:
        _file(env["root"] / n, f"body {n}\n")
    res = _gc(env, "--apply")
    assert res.returncode == 0, res
    assert all(not (env["root"] / n).exists() for n in names)
    (dest,) = [p for p in env["archive"].iterdir() if p.is_dir()]
    rows = (dest / "MANIFEST.tsv").read_text().splitlines()
    assert rows[0] == "sha256\tbytes\tmtime_utc\tclass\toriginal"
    got = {r.split("\t")[4].rsplit("/", 1)[1]: r.split("\t")[0] for r in rows[1:]}
    assert got == shas
    for n in names:
        moved = dest / n
        assert hashlib.sha256(moved.read_bytes()).hexdigest() == shas[n]
        assert abs(moved.stat().st_mtime - OLD) < 2  # mtime preserved


def test_keeps_young_referenced_symlinked_and_nonclass(env: dict[str, Path]) -> None:
    root = env["root"]
    _file(root / "bd-young.sh.pre-20260928T000000Z", old=False)
    _file(root / "bd-live.sh.pre-cron")
    env["crontab"].write_text("* * * * * bash /x/bd-live.sh.pre-cron\n")
    _file(root / "bd-tool.sh.ORIGINAL-kept", old=True)
    _file(root / "bd-caller.sh", "cmp bd-tool.sh.ORIGINAL-kept bd-tool.sh\n")
    _file(root / "bd-sym.sh.pre-1")
    (env["home"] / "bd-sym.sh").symlink_to(root / "bd-sym.sh.pre-1")
    _file(
        root / "FLEET_RUN_STATE.json.PRE-COLDSTATE-20260901"
    )  # not a class (case, no -OLD-)
    _file(root / "bd-thresHOLD.sh")
    (root / "tests").mkdir()
    _file(root / "tests" / "t.sh.pre-1")  # subdirectories are never walked
    (root / "bd-link.sh.pre-1").symlink_to(
        root / "bd-caller.sh"
    )  # a symlink is not archived
    res = _gc(env, "--apply")
    assert res.returncode == 0, res
    assert _lines(res) == {
        "KEEP-young pre bd-young.sh.pre-20260928T000000Z",
        "KEEP-referenced pre bd-live.sh.pre-cron",
        "KEEP-referenced original bd-tool.sh.ORIGINAL-kept",
        "KEEP-referenced pre bd-sym.sh.pre-1",
    }
    for n in (
        "bd-young.sh.pre-20260928T000000Z",
        "bd-live.sh.pre-cron",
        "bd-tool.sh.ORIGINAL-kept",
        "bd-sym.sh.pre-1",
        "FLEET_RUN_STATE.json.PRE-COLDSTATE-20260901",
        "bd-thresHOLD.sh",
        "tests/t.sh.pre-1",
    ):
        assert (root / n).exists(), n
    assert (root / "bd-link.sh.pre-1").is_symlink()
    assert not env["archive"].exists() or not any(
        p.is_dir() for p in env["archive"].iterdir()
    )


def test_cp_p_backup_made_now_is_young(env: dict[str, Path]) -> None:
    """REFUTE B2-B: `cp -p` (tripwire T17) copies the ORIGINAL's mtime, so a backup taken minutes ago of a
    30-day-old script is old by mtime alone. Its ctime is its arrival: it is a live rollback copy and is kept."""
    root = env["root"]
    orig = _file(root / "bd-x.sh")
    for n in ("bd-x.sh.pre-O1460-20260928T0830Z", "bd-x.sh.bak"):
        shutil.copy2(orig, root / n)
        # the copy carries the old mtime
        assert abs((root / n).stat().st_mtime - OLD) < 2
    res = _gc(env, "--apply", now=time.time())
    assert res.returncode == 0, res
    assert _lines(res) == {
        "KEEP-young pre bd-x.sh.pre-O1460-20260928T0830Z",
        "KEEP-young bak bd-x.sh.bak",
    }, "GC_CP_P_BACKUP_ARCHIVED"
    assert (root / "bd-x.sh.pre-O1460-20260928T0830Z").exists()
    assert (root / "bd-x.sh.bak").exists()
    assert not env["archive"].exists()


def test_git_tracked_backup_is_kept(env: dict[str, Path]) -> None:
    root = env["root"]
    _file(root / "bd-t.sh.pre-1")
    _file(root / "bd-u.sh.pre-1")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "bd-t.sh.pre-1"], check=True)
    res = _gc(env, "--apply")
    assert res.returncode == 0, res
    assert _lines(res) == {
        "KEEP-tracked pre bd-t.sh.pre-1",
        "ARCHIVE pre bd-u.sh.pre-1",
    }
    assert (root / "bd-t.sh.pre-1").exists() and not (root / "bd-u.sh.pre-1").exists()


def test_git_failure_refuses_instead_of_archiving_tracked(env: dict[str, Path]) -> None:
    """REFUTE-1 (correctness-A2-A): a broken repo is UNKNOWN, not "nothing tracked"."""
    root = env["root"]
    _file(root / "tool.sh.bak")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "tool.sh.bak"], check=True)
    (root / ".git" / "index").write_bytes(b"corrupt")
    assert (
        subprocess.run(
            ["git", "-C", str(root), "ls-files"], capture_output=True, check=False
        ).returncode
        != 0
    )
    res = _gc(env, "--apply")
    assert res.returncode != 0, res
    assert "REFUSED: git ls-files" in res.stderr
    assert (root / "tool.sh.bak").exists()
    assert not env["archive"].exists()


def _damage(git: Path, how: str) -> None:
    if how == "head-missing":
        (git / "HEAD").unlink()
    elif how == "objects-missing":
        shutil.rmtree(git / "objects")
    elif how == "unreadable":
        git.chmod(0o000)
    elif how == "emptied":
        for p in git.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()


@pytest.mark.parametrize(
    "how", ["head-missing", "objects-missing", "unreadable", "emptied"]
)
def test_damaged_repo_refuses(env: dict[str, Path], how: str) -> None:
    """REFUTE-1 R2 (correctness-A2-A): git says "not a git repository" for a damaged repo too."""
    root = env["root"]
    _file(root / "tool.sh.bak")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "tool.sh.bak"], check=True)
    _damage(root / ".git", how)
    try:
        if how == "unreadable" and os.access(root / ".git", os.R_OK):
            pytest.skip("running as root: chmod 000 does not block reads")
        res = _gc(env, "--apply")
    finally:
        (root / ".git").chmod(0o755)
    assert res.returncode != 0, res
    assert (
        "REFUSED: " in res.stderr
        and "/.git exists but git cannot read it" in res.stderr
    )
    assert (root / "tool.sh.bak").exists()
    assert not env["archive"].exists()


def test_missing_crontab_source_refuses(env: dict[str, Path]) -> None:
    _file(env["root"] / "bd-a.sh.pre-1")
    env["crontab"].unlink()
    res = _gc(env, "--apply")
    assert res.returncode != 0, res
    assert "cannot prove no cron caller" in res.stderr
    assert (env["root"] / "bd-a.sh.pre-1").exists()


def test_unknown_argument_refused(env: dict[str, Path]) -> None:
    _file(env["root"] / "bd-a.sh.pre-1")
    res = _gc(env, "--force")
    assert res.returncode != 0
    assert "usage" in res.stderr
    assert (env["root"] / "bd-a.sh.pre-1").exists()
