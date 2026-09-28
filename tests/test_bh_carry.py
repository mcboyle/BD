"""BH-CARRY: carry-store currency UNKNOWN on an empty store (004) and the unprotected root entries (003).

FINDING-BH-bd-dispatch-A-004 (HIGH): bd-precollect-guard.sh returned UNKNOWN (exit 4) for every cut when the carry
store existed but held no *.patch -- an empty store is a measurable FOUND NONE, not COULD NOT LOOK. The live O1475
hotfix exempts only a store whose README declares an O-number; the candidate exempts any searchable store, provided
the same counting probe counts a planted patch in a fixture store (positive control). Missing/unsearchable stays 4.
FINDING-BH-bd-dispatch-A-003 (HIGH): a home clean-up moved root entries the harness reads by default (bd-carry,
bd-codex-queue, ...). bd-root-protected.sh derives the PROTECTED set from what harness code references by
$BD_ROOT/<name>; `check` refuses those and any dir holding .git; `missing` lists referenced entries that are gone.

Opt in with BD_BH_CARRY_CANDIDATE=<dir holding bd-precollect-guard.sh and bd-root-protected.sh>.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH_CARRY_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _tool(name: str) -> Path:
    path = Path(CANDIDATE) / name
    assert path.is_file(), f"BH-CARRY: candidate supplied but {path} is missing"
    return path


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True,
                          check=True, timeout=30).stdout.strip()


@pytest.fixture
def guard(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    (wt / "a.txt").write_text("base\n")
    _git(wt, "add", "a.txt")
    _git(wt, "commit", "-q", "-m", "base")
    base = _git(wt, "rev-parse", "HEAD")
    (wt / "a.txt").write_text("fixed\n")
    _git(wt, "add", "a.txt")
    (wt / "DONE.md").write_text(f"VERDICT: PATCH\nBASE: {base}\nRED COMMAND: true\n")

    def run(store: Path, **extra: str):
        env = dict(os.environ, BD_CARRY=str(store), BD_GUARD_CANON=str(wt), **extra)
        return subprocess.run(["bash", str(_tool("bd-precollect-guard.sh")), "local", str(wt)], env=env,
                              capture_output=True, text=True, timeout=120, check=False)

    return run


def _carry_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "currency" in line or "carried-patch" in line or "*.patch" in line]


def test_empty_store_is_found_none_with_control(guard, tmp_path):
    store = tmp_path / "carry"
    store.mkdir()
    res = guard(store)
    assert res.returncode == 0, f"BH-CARRY 004: empty searchable store exit {res.returncode}: {_carry_lines(res.stdout)}"
    lines = _carry_lines(res.stdout)
    assert any("FOUND NONE" in x and "control" in x and "counted 1 planted patch" in x for x in lines), lines


def test_planted_patch_is_measured(guard, tmp_path):
    store = tmp_path / "carry"
    store.mkdir()
    (store / "old.patch").write_text("diff --git a/zz/x.py b/zz/x.py\n--- a/zz/x.py\n+++ b/zz/x.py\n")
    res = guard(store)
    assert res.returncode == 0, (res.returncode, _carry_lines(res.stdout))
    assert not any("FOUND NONE" in x or "UNMEASURED" in x for x in _carry_lines(res.stdout))


def test_missing_store_is_could_not_look(guard, tmp_path):
    res = guard(tmp_path / "no-such-carry")
    assert res.returncode == 4, (res.returncode, _carry_lines(res.stdout))
    assert any(x.startswith("UNKNOWN") and "missing or unreadable" in x for x in _carry_lines(res.stdout))


def test_empty_store_without_control_is_could_not_look(guard, tmp_path):
    """Rule 7: when the planted-patch control cannot run (mktemp fails), an empty store is UNKNOWN, not FOUND NONE."""
    store = tmp_path / "carry"
    store.mkdir()
    res = guard(store, TMPDIR=str(tmp_path / "no-such-tmpdir"))
    lines = _carry_lines(res.stdout)
    assert res.returncode == 4, (res.returncode, lines)
    assert any(x.startswith("UNKNOWN") and "control=not run" in x for x in lines), lines
    assert not any("FOUND NONE" in x for x in lines), lines


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the search bit")
def test_unsearchable_store_is_could_not_look(guard, tmp_path):
    store = tmp_path / "carry"
    store.mkdir()
    store.chmod(0o600)
    try:
        res = guard(store)
    finally:
        store.chmod(0o755)
    assert res.returncode == 4, (res.returncode, _carry_lines(res.stdout))
    assert any(x.startswith("UNKNOWN") and "UNMEASURED" in x for x in _carry_lines(res.stdout))


# --- 003: bd-root-protected.sh ---------------------------------------------------------------------------------

@pytest.fixture
def root(tmp_path):
    r = tmp_path / "home"
    harness = r / "bd-persist" / "harness"
    harness.mkdir(parents=True)
    (harness / "reader.sh").write_text(f'#!/bin/bash\nCARRY=${{BD_CARRY:-{r}/bd-carry}}\nQ="{r}/bd-queue/x"\n'
                                       f'# history only: {r}/bd-commented\nT={r}/bd-$ROLE.sh\n')
    (harness / "notes.md").write_text(f"{r}/bd-doc-only\n")
    (r / "bd-carry").mkdir()
    (r / "bd-queue").mkdir()
    (r / "scratch").mkdir()
    (r / "wt" / "sub").mkdir(parents=True)
    (r / "wt" / "sub" / ".git").write_text("gitdir: /elsewhere\n")

    def run(*args: str):
        env = dict(os.environ, BD_ROOT=str(r), BD_HARNESS=str(harness))
        return subprocess.run(["bash", str(_tool("bd-root-protected.sh")), *args], env=env, capture_output=True,
                              text=True, timeout=60, check=False)

    run.root = r
    run.harness = harness
    return run


def test_list_derives_code_references_only(root):
    res = root("list")
    assert res.returncode == 0, res.stderr
    got = set(res.stdout.split())
    r = root.root
    assert got == {f"{r}/bd-carry", f"{r}/bd-queue"}, got


def test_check_refuses_referenced_entry(root):
    res = root("check", f"{root.root}/bd-carry", f"{root.root}/scratch")
    assert res.returncode == 1, res.stdout
    assert f"PROTECTED {root.root}/bd-carry -- harness references" in res.stdout
    assert f"ok {root.root}/scratch" in res.stdout


def test_check_refuses_parent_of_referenced_entry(root):
    (root.root / "wt" / "sub" / ".git").unlink()  # no .git anywhere: only the containment rule can refuse
    res = root("check", str(root.root))
    assert res.returncode == 1, res.stdout
    assert f"PROTECTED {root.root} -- harness references {root.root}/bd-" in res.stdout


def test_check_refuses_worktree_at_any_depth(root):
    deep = root.root / "scratch" / "a" / "b" / "c" / "d" / "wt"
    deep.mkdir(parents=True)
    (deep / ".git").write_text("gitdir: /elsewhere\n")
    res = root("check", f"{root.root}/scratch")
    assert res.returncode == 1 and "holds a .git entry" in res.stdout, res.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the search bit")
@pytest.mark.parametrize("locked", ["lock", "lock/inner"])
def test_check_unsearchable_dir_is_could_not_look(root, locked):
    (root.root / "lock" / "inner" / "wt").mkdir(parents=True)
    (root.root / "lock" / "inner" / "wt" / ".git").write_text("gitdir: /elsewhere\n")
    target = root.root / locked
    target.chmod(0)
    try:
        res = root("check", f"{root.root}/lock")
    finally:
        target.chmod(0o755)
    assert res.returncode == 4, res.stdout
    assert f"UNKNOWN {root.root}/lock -- " in res.stdout and "COULD NOT LOOK" in res.stdout, res.stdout
    control = root("check", f"{root.root}/lock")
    assert control.returncode == 1 and "holds a .git entry" in control.stdout, control.stdout


def test_check_refuses_dir_holding_a_worktree(root):
    res = root("check", f"{root.root}/wt")
    assert res.returncode == 1 and "holds a .git entry" in res.stdout, res.stdout


def test_check_passes_unreferenced_entry(root):
    res = root("check", f"{root.root}/scratch")
    assert res.returncode == 0 and f"ok {root.root}/scratch" in res.stdout, res.stdout


def test_missing_found_none_then_found(root):
    res = root("missing")
    assert res.returncode == 0 and "FOUND NONE missing of 2" in res.stdout, res.stdout
    (root.root / "bd-carry").rename(root.root / "scratch" / "bd-carry")
    res = root("missing")
    assert res.returncode == 1 and "FOUND 1 missing of 2" in res.stdout, res.stdout
    assert f"{root.root}/bd-carry" in res.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
@pytest.mark.parametrize("locked", ["sub", "sub/hidden.sh"])
def test_partly_unreadable_harness_is_could_not_look(root, locked):
    """A partial derivation must not read as a complete one: the entry only the unread part references stays refused."""
    (root.harness / "sub").mkdir()
    (root.harness / "sub" / "hidden.sh").write_text(f"#!/bin/bash\nH={root.root}/bd-onlyhidden\n")
    (root.root / "bd-onlyhidden").mkdir()
    control = root("check", f"{root.root}/bd-onlyhidden")
    assert control.returncode == 1 and "harness references" in control.stdout, control.stdout
    target = root.harness / locked
    target.chmod(0)
    try:
        results = {args[0]: root(*args) for args in (("check", f"{root.root}/bd-onlyhidden"), ("list",), ("missing",))}
    finally:
        target.chmod(0o755)
    for mode, res in results.items():
        assert res.returncode == 4, (mode, res.returncode, res.stdout)
        assert "could not be read; protected set INCOMPLETE" in res.stdout, (mode, res.stdout)
        assert "ok " not in res.stdout, (mode, res.stdout)


def test_empty_derivation_is_could_not_look(root):
    (root.harness / "reader.sh").write_text("#!/bin/bash\necho nothing\n")
    for args in (("missing",), ("check", f"{root.root}/bd-carry")):
        res = root(*args)
        assert res.returncode == 4 and "0 references found" in res.stdout, (args, res.stdout)
