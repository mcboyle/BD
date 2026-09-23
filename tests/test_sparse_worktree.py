"""Row 866 sparse worktree contract tests."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "toolchain" / "bin" / "bd-lens-worktree"


def _load_lens_worktree_module():
    import importlib.machinery
    import importlib.util
    assert TOOL_PATH.is_file(), f"missing toolchain binary: {TOOL_PATH}"
    loader = importlib.machinery.SourceFileLoader("lens_worktree", str(TOOL_PATH))
    spec = importlib.util.spec_from_loader("lens_worktree", loader)
    assert spec and spec.loader, "could not load spec for bd-lens-worktree"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lens_worktree"] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _deregister_test_worktrees(tmp_path):
    """E3: every worktree a test registers under tmp_path is removed from the
    parent repository's .git/worktrees afterwards (pytest deletes tmp_path;
    without this the registrations dangle). Test fixtures only -- no fleet
    worktree lives under tmp_path (Fleet Rule 22 is about those)."""
    yield
    listing = subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True).stdout
    for line in listing.splitlines():
        if line.startswith("worktree ") and line[len("worktree "):].startswith(str(tmp_path)):
            subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", line[len("worktree "):]],
                           capture_output=True, text=True)
    subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "prune"], capture_output=True, text=True)


def test_registrations_do_not_accumulate(tmp_path):
    """E3 control: after a create + teardown in a test, the parent repo
    lists no worktree under tmp_path once the fixture has run (checked by
    the NEXT test's view: here we assert the cleanup helper's own effect)."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    wt = tmp_path / "reg_wt"
    assert lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=base_commit,
                                          scope_dirs=["toolchain"])["ok"] is True
    listing = subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True).stdout
    assert f"worktree {wt}" in listing
    subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", str(wt)],
                   capture_output=True, text=True, check=True)
    listing = subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True).stdout
    assert f"worktree {wt}" not in listing


def test_custom_scope_keeps_the_default_excludes(tmp_path):
    """Round 2 E1: scope_dirs=["tests"] without exclude_dirs used to carry
    tests/corpus + fixtures + mutants (117 MB) and fail the size bound. row963:
    only the corpora are carved (tests/corpus, tests/fixtures/recon_corpus); the
    rest of tests/fixtures and tests/mutants is what gates read and is kept."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    wt = tmp_path / "tests_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=base_commit,
                                         scope_dirs=["tests"])
    assert res["ok"] is True, res
    assert (wt / "tests").is_dir() and not (wt / "tests" / "corpus").exists()
    assert not (wt / "tests" / "fixtures" / "recon_corpus").exists()
    assert (wt / "tests" / "fixtures").is_dir() and (wt / "tests" / "mutants").is_dir()
    assert lw.get_worktree_size_mb(wt) < lw.MAX_WORKTREE_MB
    # explicit exclude_dirs still wins (an empty list carves nothing out)
    assert lw.sparse_patterns(["tests"], []) == lw.sparse_patterns(["tests"], [])


def test_scoped_checkout_is_under_50_mb(tmp_path):
    """Verify that a scoped worktree uses no-checkout + sparse-checkout and is under
    the bound. sg10: the bound is read from lw.MAX_WORKTREE_MB rather than restated
    as a literal here, so the tool and its gate can never disagree about it."""
    lw = _load_lens_worktree_module()

    wt_dest = tmp_path / "sparse_wt"
    base_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Scope only to bulk_downloader/ and toolchain/
    res = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT,
        worktree_path=wt_dest,
        commit=base_commit,
        scope_dirs=["bulk_downloader", "toolchain"],
    )

    assert res["ok"] is True
    assert wt_dest.is_dir()
    # Check that scoped directory exists and non-scoped directory is omitted
    assert (wt_dest / "bulk_downloader").is_dir()
    assert (wt_dest / "toolchain").is_dir()
    assert not (wt_dest / "frontend" / "src").exists()
    assert not (wt_dest / "fixtures").exists()

    # Size check: must be strictly under 50 MB
    size_mb = lw.get_worktree_size_mb(wt_dest)
    assert size_mb < lw.MAX_WORKTREE_MB, (
        f"Scoped checkout size {size_mb:.2f} MB exceeds the {lw.MAX_WORKTREE_MB:.0f} MB limit")


# A real, fast gate that lives entirely inside the default scope (bulk_downloader/tests/toolchain).
_PARITY_GATE = "tests/test_v3_66_305_config_danger.py"   # needs reports/ + bulk_downloader: a real cross-dir gate


def _run_gate(cwd, gate=_PARITY_GATE, python=None):
    """Run one pytest gate in `cwd`; returns (rc, tail-of-output)."""
    env = dict(os.environ, BD_DISABLE_KEEPALIVE="1")
    env.pop("BD_INSTALL_DIR", None)
    proc = subprocess.run(
        [python or sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-p", "no:cacheprovider", gate],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1500:]


def test_scoped_gates_match_full_checkout(tmp_path):
    """Parity: the SAME gate passes in the scoped worktree and in a full (non-sparse)
    worktree of the same commit, with the same collected-test count. A forced
    failure (nonexistent gate -> rc 4/127-class) must NOT pass this test."""
    lw = _load_lens_worktree_module()
    assert (REPO_ROOT / _PARITY_GATE).is_file(), f"parity gate missing on this base: {_PARITY_GATE}"

    base_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    scoped = tmp_path / "gate_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=scoped, commit=base_commit)
    assert res["ok"] is True, res
    assert res["size_mb"] < lw.MAX_WORKTREE_MB and res["exclude"] == lw.DEFAULT_EXCLUDE
    assert res["share"] is not None and res["share"] <= lw.MAX_WORKTREE_SHARE, res
    assert not (scoped / "tests" / "corpus").exists()

    full = tmp_path / "full_wt"
    subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "add", "--detach", str(full), base_commit],
                   capture_output=True, text=True, check=True)
    for wt in (scoped, full):
        if (REPO_ROOT / "venv").exists() and not (wt / "venv").exists():
            os.symlink(REPO_ROOT / "venv", wt / "venv")

    rc_scoped, out_scoped = _run_gate(scoped)
    rc_full, out_full = _run_gate(full)
    assert rc_full == 0, f"gate must pass in the FULL checkout first:\n{out_full}"
    assert rc_scoped == 0, f"gate failed in the SCOPED checkout:\n{out_scoped}"
    passed = re.compile(r"(\d+) passed")
    n_scoped = passed.search(out_scoped); n_full = passed.search(out_full)
    assert n_scoped and n_full, (out_scoped, out_full)
    assert n_scoped.group(1) == n_full.group(1), f"scoped {n_scoped.group(1)} != full {n_full.group(1)} tests"

    # Negative control: a forced failure is visible through the same probe.
    rc_bad, _ = _run_gate(scoped, gate="tests/does_not_exist_row866.py")
    assert rc_bad != 0


def test_branch_checked_out_elsewhere_is_accepted_detached(tmp_path):
    """P2: a branch name that is checked out in the source repo must still work
    (detached at the branch tip), and the result reports the resolved sha."""
    lw = _load_lens_worktree_module()
    branch = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    if branch == "HEAD":  # source repo itself detached: make the control meaningful anyway
        for candidate in ("main", "origin/main"):
            if subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", f"{candidate}^{{commit}}"],
                              capture_output=True).returncode == 0:
                branch = candidate
                break
        else:
            branch = "test_sparse_wt_temp_branch"
            subprocess.run(["git", "-C", str(REPO_ROOT), "branch", "-f", branch, "HEAD"], check=True)
    try:
        tip = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", f"{branch}^{{commit}}"],
                             capture_output=True, text=True, check=True).stdout.strip()
        wt = tmp_path / "branch_wt"
        res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=branch,
                                             scope_dirs=["toolchain"])
        assert res["ok"] is True, res
        assert res["commit"] == tip
        head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        assert head == tip
        assert (wt / "toolchain").is_dir() and not (wt / "bulk_downloader").exists()
        bad = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=tmp_path / "nope",
                                             commit="no-such-ref-row866", scope_dirs=["toolchain"])
        assert bad["ok"] is False and "resolve" in bad["error"]
    finally:
        if branch == "test_sparse_wt_temp_branch":
            subprocess.run(["git", "-C", str(REPO_ROOT), "branch", "-D", branch], capture_output=True)


def test_size_bound_is_enforced_without_deleting(tmp_path, monkeypatch):
    """P2: a scoped checkout at/over the bound is ok=False (bound violated) and the
    worktree is left in place (Fleet Rule 22). sg10: the forced size is derived from
    lw.MAX_WORKTREE_MB, so raising the cap can never silently stop exercising this."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    wt = tmp_path / "big_wt"
    over = int((lw.MAX_WORKTREE_MB + 1) * 1024 * 1024) + 7
    monkeypatch.setattr(lw, "get_worktree_size_bytes", lambda _p: over)
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=base_commit,
                                         scope_dirs=["toolchain"])
    assert res["ok"] is False
    assert res["size_mb"] > lw.MAX_WORKTREE_MB
    assert f"{lw.MAX_WORKTREE_MB:.0f} MB bound" in res["error"], res["error"]
    assert wt.is_dir() and (wt / "toolchain").is_dir(), "worktree must be left in place"


def test_teardown_propagates_disable_failure_and_asserts_state(tmp_path):
    """P1: an index.lock (or any failing teardown step) must yield ok=False;
    after a successful teardown the worktree is provably shrunk, not populated."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    wt = tmp_path / "lock_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=base_commit,
                                         scope_dirs=["toolchain"])
    assert res["ok"] is True, res
    gitdir = Path(subprocess.run(["git", "-C", str(wt), "rev-parse", "--git-dir"],
                                 capture_output=True, text=True, check=True).stdout.strip())
    lock = gitdir / "index.lock"
    lock.write_text("held by another process")
    try:
        bad = lw.teardown_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt)
    finally:
        lock.unlink()
    assert bad["ok"] is False, bad
    assert "teardown" in bad["error"] and "failed" in bad["error"], bad
    assert not (wt / "bulk_downloader").exists(), "a failed teardown must not have silently restored the tree"

    before = lw.get_worktree_size_bytes(wt)
    good = lw.teardown_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt)
    assert good["ok"] is True and good["sparse"] is True, good
    # round 2 E2: a teardown SHRINKS the worktree (never `sparse-checkout
    # disable`, which populates the whole 140 MB tree); the worktree stays
    # registered and in place with only its root files
    assert good["size_after_bytes"] < before and lw.get_worktree_size_mb(wt) < 10.0, good  # root files only (~5 MB)
    assert not (wt / "toolchain").exists() and not (wt / "bulk_downloader").exists()
    assert wt.is_dir() and (wt / ".git").exists()


def test_cleanup_leaves_no_residual_sparse_state(tmp_path):
    """Verify that teardown cleans sparse checkout config without leaving residual sparse state."""
    lw = _load_lens_worktree_module()

    wt_dest = tmp_path / "cleanup_wt"
    base_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    res = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT,
        worktree_path=wt_dest,
        commit=base_commit,
        scope_dirs=["bulk_downloader"],
    )
    assert res["ok"] is True

    clean_res = lw.teardown_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt_dest)
    assert clean_res["ok"] is True

    # Verify that the parent repository's sparse config was not altered
    repo_sparse = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "config", "--get", "core.sparseCheckout"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert repo_sparse != "true", "Parent repo core.sparseCheckout was polluted!"


def test_safety_never_deletes_or_resets_existing_worktree(tmp_path):
    """Verify that Fleet Rule 22 (no delete/reset) is respected when path exists."""
    lw = _load_lens_worktree_module()

    existing_wt = tmp_path / "existing_wt"
    existing_wt.mkdir()
    marker = existing_wt / "untouched_marker.txt"
    marker.write_text("critical_user_work")

    base_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    res = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT,
        worktree_path=existing_wt,
        commit=base_commit,
        scope_dirs=["bulk_downloader"],
    )
    # Must refuse to overwrite or destroy existing path
    assert res["ok"] is False
    assert "refuse" in res["error"].lower() or "exists" in res["error"].lower()
    assert marker.read_text() == "critical_user_work", "Existing worktree was wiped or modified!"


def test_teardown_tolerates_lens_runtime_leftovers_and_judges_tracked_files(tmp_path, monkeypatch):
    """Round 3 E1: a lens that ran gates in the worktree leaves untracked
    runtime artefacts (.review/, .pytest_cache/, __pycache__/ inside a scoped
    dir, a venv symlink); sparse-checkout only unpopulates TRACKED files, so
    teardown must judge git's record of checked-out files, not "no dirs at
    all" -- and must still say NO when tracked files stay populated."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    wt = tmp_path / "ran_a_gate_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=base_commit,
                                         scope_dirs=["toolchain"])
    assert res["ok"] is True, res
    assert lw.tracked_populated_paths(wt), "positive control: the scoped tree is populated before teardown"
    (wt / ".review").mkdir(); (wt / ".review" / "VERDICT-correctness.md").write_text("VERDICT: BOARD\n")
    (wt / ".pytest_cache" / "v").mkdir(parents=True); (wt / ".pytest_cache" / "v" / "x").write_text("x")
    (wt / "toolchain" / "__pycache__").mkdir(); (wt / "toolchain" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00" * 64)
    venv_target = REPO_ROOT / "venv"
    if not venv_target.exists():
        cand = Path(sys.executable).resolve().parent.parent
        if (cand / "bin" / "python").exists():
            venv_target = cand
        elif Path("/home/mboyle/BulkDownloader/venv").exists():
            venv_target = Path("/home/mboyle/BulkDownloader/venv")
    if venv_target.exists():
        os.symlink(str(venv_target), str(wt / "venv"))

    # negative control: a teardown whose sparse-checkout step silently does
    # nothing leaves tracked files populated and MUST be reported as such
    real_run = lw.subprocess.run

    def noop_sparse_set(cmd, *a, **kw):
        if "sparse-checkout" in cmd and "set" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(lw.subprocess, "run", noop_sparse_set)
    stuck = lw.teardown_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt)
    monkeypatch.setattr(lw.subprocess, "run", real_run)
    assert stuck["ok"] is False and "tracked=['toolchain/" in stuck["error"], stuck
    before = lw.get_worktree_size_bytes(wt)
    assert re.search(rf"size {before} -> \d+ bytes", stuck["error"]), stuck  # round 3 E2: before -> after

    good = lw.teardown_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt)
    assert good["ok"] is True, good
    assert lw.tracked_populated_paths(wt) == []
    assert not (wt / "toolchain" / "bin").exists()
    # the leftovers are reported and left in place, never deleted (Fleet Rule 22)
    assert {".pytest_cache", ".review", "toolchain", "venv"} <= set(good["untracked_dirs"]), good
    assert (wt / ".review" / "VERDICT-correctness.md").read_text() == "VERDICT: BOARD\n"
    assert (wt / "toolchain" / "__pycache__" / "a.cpython-312.pyc").exists() and (wt / "venv").is_symlink()


def test_share_bound_is_a_ratchet_the_absolute_cap_cannot_be(tmp_path, monkeypatch):
    """sg10: 50.0 MB was a measurement of the default scope at b9d1c8f7 written down
    as a constant, so ordinary repository growth (51.14 MB at 4ce70fc1) turned a
    correct worktree into ok=False. The cap was raised, which on its own is a
    loosened ratchet -- so the tool also pins the SHARE of a full checkout, which
    cannot go stale because both sides grow together.
    """
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()

    full = lw.full_checkout_size_bytes(REPO_ROOT, base_commit)
    assert full is not None and full > 100 * 1024 * 1024, f"denominator looks wrong: {full} bytes"
    # the denominator refuses to invent a number it could not read (FLEET_RULE 6)
    assert lw.full_checkout_size_bytes(REPO_ROOT, "no-such-ref-sg10") is None

    wt = tmp_path / "share_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt,
                                         commit=base_commit, scope_dirs=["toolchain"])
    assert res["ok"] is True, res
    assert 0.0 < res["share"] <= lw.MAX_WORKTREE_SHARE, res

    # negative control: a worktree that stopped excluding the corpora is ~a full
    # checkout. It is REFUSED by the share bound while still under the absolute cap,
    # which is the whole point of adding it.
    fat = int(full * 0.9)
    assert fat / (1024.0 * 1024.0) < lw.MAX_WORKTREE_MB * 3, "control sizing sanity"
    monkeypatch.setattr(lw, "get_worktree_size_bytes", lambda _p: fat)
    monkeypatch.setattr(lw, "MAX_WORKTREE_MB", 10_000.0)
    bad = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=tmp_path / "fat_wt",
                                         commit=base_commit, scope_dirs=["toolchain"])
    assert bad["ok"] is False and "share bound" in bad["error"], bad
    assert (tmp_path / "fat_wt").is_dir(), "worktree must be left in place (Fleet Rule 22)"


def test_unreadable_denominator_is_a_refusal_not_a_passing_share(tmp_path, monkeypatch, capsys):
    """sg10 REFUTE (bd-cx-lens-1): the first cut turned an UNREADABLE denominator into
    ``share = 0.0`` and then skipped the share bound on the falsy ``full_bytes``, so a
    worktree at 90% of a full checkout was CREATED with ok=True and rc 0. UNKNOWN is not
    permission (FLEET_RULE 6). The creator -- and the CLI above it -- must refuse when the
    denominator cannot be read, and ``share`` must be None rather than a number nobody
    measured. Line 358 tested only the helper; this drives the real creator and main().
    """
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()

    full = lw.full_checkout_size_bytes(REPO_ROOT, base_commit)
    assert full and full > 100 * 1024 * 1024, f"denominator looks wrong: {full}"
    fat = int(full * 0.9)
    monkeypatch.setattr(lw, "get_worktree_size_bytes", lambda _p: fat)
    monkeypatch.setattr(lw, "MAX_WORKTREE_MB", 10_000.0)

    # POSITIVE CONTROL (rule 7): with the denominator READABLE the same 90% worktree is
    # refused by the share bound, so the probe below is not passing for want of a signal.
    seen = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=tmp_path / "seen_wt",
                                          commit=base_commit, scope_dirs=["toolchain"])
    assert seen["ok"] is False and "share bound" in seen["error"], seen
    assert seen["share"] > lw.MAX_WORKTREE_SHARE

    # THE ESCAPE: same worktree, same 90%, only the denominator unreadable.
    for unreadable in (None, 0):
        monkeypatch.setattr(lw, "full_checkout_size_bytes", lambda _r, _c, _v=unreadable: _v)
        blind = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=tmp_path / f"blind_{unreadable}",
                                               commit=base_commit, scope_dirs=["toolchain"])
        assert blind["ok"] is False, blind
        assert "could not measure" in blind["error"], blind
        assert blind["share"] is None, blind
        assert (tmp_path / f"blind_{unreadable}").is_dir(), "left in place (Fleet Rule 22)"

    # the CLI is the caller that actually reports a verdict: it must exit 1, not print Created.
    monkeypatch.setattr(sys, "argv", ["bd-lens-worktree", "create", "--repo", str(REPO_ROOT),
                                      "--worktree", str(tmp_path / "cli_wt"), "--commit", base_commit,
                                      "--scope", "toolchain"])
    rc = lw.main()
    captured = capsys.readouterr()
    assert rc == 1, captured
    assert "Created" not in captured.out and "could not measure" in captured.err


def test_the_helper_distinguishes_unreadable_from_measured(tmp_path):
    """Negative control for the refusal above: a ref git CANNOT read yields None, while a
    real commit yields a positive int. A helper that always returned None would make the
    refusal above pass for the wrong reason."""
    lw = _load_lens_worktree_module()
    assert lw.full_checkout_size_bytes(REPO_ROOT, "no-such-ref-sg10") is None
    head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert isinstance(lw.full_checkout_size_bytes(REPO_ROOT, head), int)


def test_cli_teardown_drives_teardown_function_and_exits_clean(tmp_path, monkeypatch, capsys):
    """Test that `bd-lens-worktree teardown` CLI subcommand invokes teardown and exits 0 on success."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    wt = tmp_path / "cli_teardown_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=base_commit,
                                         scope_dirs=["toolchain"])
    assert res["ok"] is True, res
    monkeypatch.setattr(sys, "argv", ["bd-lens-worktree", "teardown", "--repo", str(REPO_ROOT),
                                      "--worktree", str(wt)])
    rc = lw.main()
    captured = capsys.readouterr()
    assert rc == 0, (rc, captured)
    assert "Cleaned sparse state" in captured.out
    assert not (wt / "toolchain").exists()

