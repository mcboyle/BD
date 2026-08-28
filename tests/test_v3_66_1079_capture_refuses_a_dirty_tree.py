"""v3.66.1079 -- a capture run against an edited tree grades the edit, not the tree.

THE INCIDENT, twice. test5's working tree IS the deployed tree, and capture step
[2b] rebuilds a source graph and compares its hash against a pin written at
deploy time. Four uncommitted files therefore turned a healthy capture into
`CAPTURE VERDICT: FAIL (graph exit=1)` at v3.66.1063, with unit 15709/0/0/26 and
live 34/2/0 sitting underneath it -- a red verdict over a green suite. It
happened again at v3.66.1069, and stashing mid-run is no better: collection has
already happened, so removing files leaves a collected-but-inconsistent state and
the suite fails instead.

The gate was RIGHT both times. What was missing is that nothing said so at the
START, when it costs a second, rather than forty minutes later in a verdict line
that reads like a code defect.

WHY A LIBRARY AND NOT A LINE IN capture.sh. Backlog 35 wants the same question
asked before a commit -- tree clean, no orphans, services healthy. Writing it
inline here would guarantee a second copy there, and `scripts/lib/system_deps.sh`
already carries the lesson about what three copies of one fact cost. One
predicate, two callers.

THE OVERRIDE IS DELIBERATELY NOT `BD_`-PREFIXED. CLAUDE.md section 4: any
`BD_`-prefixed name, including a shell local, enters `test_gui_parity`'s env
ledger and reads as a promoted-but-unledgered config key. `CAPTURE_VAULT_PW` is
the existing precedent for a capture-only knob.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Its subject is one script's preflight, not an invariant over the tree.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "scripts" / "lib" / "tree_state.sh"
_CAPTURE = _REPO / "capture.sh"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _repo_with(tmp_path: Path, dirty: bool) -> Path:
    """A real git repo, clean or dirty. The predicate reads git, so a fake
    directory would prove nothing about it."""
    r = tmp_path / ("dirty" if dirty else "clean")
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("one\n", encoding="utf-8")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-qm", "base")
    if dirty:
        (r / "f.txt").write_text("two\n", encoding="utf-8")
    return r


def _ask(repo: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the library's check inside `repo`. Executed, never read."""
    script = (
        'set -e\n'
        f'. "{_LIB}"\n'
        'bd_tree_state_check "$PWD"\n'
    )
    e = {"PATH": "/usr/bin:/bin", "HOME": str(repo)}
    e.update(env or {})
    return subprocess.run(["bash", "-c", script], cwd=str(repo),
                          capture_output=True, text=True, env=e, timeout=60)


# ── the predicate ────────────────────────────────────────────────────────────

def test_the_library_exists_and_is_sourceable():
    assert _LIB.is_file(), (
        f"{_LIB.relative_to(_REPO)} is missing -- the predicate has to live "
        f"somewhere both capture.sh and a pre-commit check can source it from")
    r = subprocess.run(["bash", "-n", str(_LIB)], capture_output=True, text=True)
    assert r.returncode == 0, f"{_LIB.name} is not valid bash: {r.stderr}"


def test_transform_control_sources_library_without_running_tree_check():
    """Mutation transform control: loading the shell library is not evidence
    about what its predicate reports for a failed measurement."""
    r = subprocess.run(
        ["bash", "-c", f'. "{_LIB}"\ndeclare -F bd_tree_state_check >/dev/null\n'],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, f"the tree-state library did not load: {r.stderr}"


def test_a_clean_tree_passes(tmp_path):
    """The over-sensitivity control, and it comes first deliberately. A check
    that refused every run would be switched off inside a day, which section 0
    counts as a soundness bug of equal weight to a false clean."""
    repo = _repo_with(tmp_path, dirty=False)
    r = _ask(repo)
    assert r.returncode == 0, (
        f"a CLEAN tree was refused -- this fires on every run and gets "
        f"disabled:\nstdout={r.stdout}\nstderr={r.stderr}")


def test_a_git_status_failure_is_unknown_not_clean(tmp_path):
    """A repository probe succeeded, then the cleanliness measurement failed.

    The shim records every executed Git operation so an unrelated early
    refusal cannot manufacture this test's nonzero result.
    """
    repo = tmp_path / "reported-repository"
    repo.mkdir()
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    calls = tmp_path / "git-calls"
    git = shim_dir / "git"
    git.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$GIT_CALL_LOG\"\n"
        "case \" $* \" in\n"
        "  *\" rev-parse --is-inside-work-tree \"*) exit 0 ;;\n"
        "  *\" status --porcelain --untracked-files=all \"*) exit 77 ;;\n"
        "  *) printf 'unexpected git operation: %s\\n' \"$*\" >&2; exit 98 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    git.chmod(0o755)

    env = {
        "PATH": f"{shim_dir}:/usr/bin:/bin",
        "HOME": str(repo),
        "GIT_CALL_LOG": str(calls),
    }
    r = subprocess.run(
        ["bash", "-c", f'. "{_LIB}"\nbd_tree_state_check "$PWD"\n'],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert calls.is_file(), "the fake Git boundary was never executed"
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert len(observed) == 2, f"expected exactly two Git probes, got {observed!r}"
    assert sum(" rev-parse --is-inside-work-tree" in call
               for call in observed) == 1, observed
    assert sum(" status --porcelain --untracked-files=all" in call
               for call in observed) == 1, observed
    assert r.stdout == "", (
        f"the failed status command was required to have empty stdout: {r.stdout!r}")
    out = r.stderr.lower()
    assert r.returncode == 2 and "unknown" in out and "status" in out and "77" in out, (
        "git status exited 77 with empty stdout, but the predicate did not "
        f"report that measurement as UNKNOWN: rc={r.returncode} stderr={r.stderr!r}")


def test_a_dirty_tree_is_refused_and_says_why(tmp_path):
    """The assertion the file exists for."""
    repo = _repo_with(tmp_path, dirty=True)
    r = _ask(repo)
    assert r.returncode != 0, (
        f"an edited tree was accepted; the graph pin will drift and the "
        f"verdict will blame the code:\nstdout={r.stdout}")
    out = r.stdout + r.stderr
    assert "f.txt" in out, (
        f"the refusal does not name what is dirty, so the operator cannot act "
        f"on it: {out!r}")
    assert "graph" in out.lower(), (
        f"the refusal must say WHY a dirty tree matters here -- the graph pin "
        f"is compared against source, and without that sentence this reads as "
        f"pedantry: {out!r}")


def test_an_untracked_file_counts_as_dirty(tmp_path):
    """`git status --porcelain` reports untracked files, and they change the
    source graph exactly as a modification does."""
    repo = _repo_with(tmp_path, dirty=False)
    (repo / "stray.py").write_text("x = 1\n", encoding="utf-8")
    assert _ask(repo).returncode != 0, "an untracked file was not counted"


def test_the_override_works_and_is_not_bd_prefixed(tmp_path):
    """An operator who knows what they are doing must be able to proceed.

    A gate with no escape hatch gets removed rather than overridden, and the
    name is unprefixed on purpose: a `BD_` name enters test_gui_parity's env
    ledger and reads as an unledgered config key.
    """
    repo = _repo_with(tmp_path, dirty=True)
    r = _ask(repo, {"CAPTURE_ALLOW_DIRTY": "1"})
    assert r.returncode == 0, (
        f"the documented override did not work: {r.stdout}{r.stderr}")

    lib = _LIB.read_text(encoding="utf-8")
    assert "CAPTURE_ALLOW_DIRTY" in lib
    assert "BD_ALLOW_DIRTY" not in lib, (
        "a BD_-prefixed override would join test_gui_parity's env ledger and "
        "read as a promoted-but-unledgered config key (CLAUDE.md section 4)")


def test_a_non_repo_is_unknown_not_clean(tmp_path):
    """UNKNOWN IS A THIRD STATE. Outside a git repo the question cannot be
    answered, and answering 'clean' would be the check reporting OK over a
    subject it cannot see."""
    plain = tmp_path / "notarepo"
    plain.mkdir()
    r = _ask(plain)
    out = (r.stdout + r.stderr).lower()
    assert r.returncode != 0 and "unknown" in out, (
        f"a non-repository reported as clean rather than unknown: "
        f"rc={r.returncode} {out!r}")


# ── the caller ───────────────────────────────────────────────────────────────

def test_capture_sh_actually_calls_it(tmp_path):
    """A library nothing sources is a library that never runs.

    Asserted over comment-stripped shell so the sentence explaining the call
    cannot stand in for the call -- the trap CLAUDE.md section 0 names, and the
    one that let a mutant through in this session's bd-fleet cut.
    """
    import sys
    sys.path.insert(0, str(_REPO / "tests"))
    from shell_source import shell_code_only

    code = shell_code_only(_CAPTURE)
    assert "tree_state.sh" in code, (
        "capture.sh does not source the tree-state library in CODE (a comment "
        "mentioning it does not count)")
    assert "bd_tree_state_check" in code, (
        "capture.sh sources the library but never calls the check")


def test_capture_refuses_dirty_but_continues_on_unknown():
    """The POLICY split, which is the caller's decision rather than the
    library's.

    The library reports three states. capture.sh refuses only state 1 (dirty),
    because that is the measured hazard: uncommitted edits drift the graph pin.
    State 2 (not a repository) cannot produce that -- there is nothing to be
    uncommitted against -- and refusing it broke thirteen existing tests that
    build a synthetic capture directory, which is the honest signal that a
    non-repo run is a legitimate shape rather than an error.
    """
    import sys
    sys.path.insert(0, str(_REPO / "tests"))
    from shell_source import shell_code_only

    code = shell_code_only(_CAPTURE)
    assert "_tree_rc" in code, "capture.sh no longer inspects the check's status"
    assert "-eq 1" in code, (
        "capture.sh does not distinguish DIRTY from UNKNOWN; treating every "
        "non-zero alike is the collapse CLAUDE.md section 10 names -- assert "
        "the reason, not the code")
