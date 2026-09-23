"""Row 963: Sparse-Checkout Worktree Footprint Minimizer for Lenses.

Acceptance, re-scoped to the measured numbers by RULING-2332 (option A): the
row's "<50 MB AND <500 ms AND identical gates" cannot all hold (git checkout
alone of the default scope is ~550 ms on the fleet host; gate parity needs the
excluded corpora back). Pinned here, at DEFAULT_SCOPE -- the scope a lens uses:
1. footprint under the existing MAX_WORKTREE_MB / MAX_WORKTREE_SHARE bound;
2. the default sparse worktree hides nothing but the two corpora: precut/mutate
   inputs (tests/mutants) and fixture-reading gates run like a full checkout, and
   a corpus-reading gate has a named, scoped route that does too;
3. creation latency is reported, and bounded only when a caller asks.
"""
from __future__ import annotations

BD_GATE_SCOPE = "repo-wide"

import ast
import importlib.machinery
import importlib.util
import inspect
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "toolchain" / "bin" / "bd-lens-worktree"
RATCHET = REPO_ROOT / "project-knowledge" / "BUDGET_RATCHET.json"

# Budget of one nested gate run (_run_gate), by the v3.66.1222 / row 338 rule: twice the
# cost measured at the subprocess boundary, with a 60 s cold-start floor. A parity item
# runs TWO gates back to back (full checkout, then the sparse worktree), so two budgets
# plus a 30 s item reserve must stay under the governing per-item bound (BUDGET_RATCHET.json
# governing_bound_s, CI --timeout=240): a hung gate then raises TimeoutExpired inside its
# item instead of pytest-timeout killing the worker. Measured 2026-09-23 on hub-mesh01
# (load 32-61 on 48 cores, nice 10), max over 3 runs x {full, sparse}, 18 runs, all rc 0.
_GATE_MEASURED_S = {
    "tests/test_row788_transform_control_is_separate.py": 16.590,
    "tests/test_extraction_core_characterization.py": 4.550,
    "tests/test_v3_66_33_recon_corpus_extraction.py": 16.596,
}
_GATE_BUDGET_FLOOR_S = 60
_GATE_CONTENTION_FACTOR = 2
_GATE_RUNS_PER_ITEM = 2
_ITEM_RESERVE_S = 30


def _load_lens_worktree_module():
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
    """Clean up worktrees registered under tmp_path after each test."""
    yield
    listing = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, check=False,
    ).stdout
    for line in listing.splitlines():
        if line.startswith("worktree ") and line[len("worktree "):].startswith(str(tmp_path)):
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", line[len("worktree "):]],
                capture_output=True, text=True, check=False,
            )
    subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "prune"], capture_output=True, text=True, check=False)


def _head():
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _run_gate(root, test_file, timeout_s=60):
    """Run one real gate file with this interpreter in `root`; return (rc, summary).
    A gate still running after `timeout_s` raises subprocess.TimeoutExpired (child killed)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", test_file],
        cwd=str(root), capture_output=True, text=True, timeout=timeout_s, check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if re.search(r"\d+ (passed|failed|error)", ln)]
    summary = re.sub(r" in [0-9.]+s.*$", "", lines[-1].strip("= ")) if lines else proc.stdout[-400:]
    return proc.returncode, summary


def test_row963_default_scope_create_pins_the_measured_numbers(tmp_path):
    """(1)+(3) at DEFAULT_SCOPE: ok under the size/share bound, latency reported,
    and every tracked file outside the two corpora is checked out -- including
    tests/mutants (bd-precut / bd-mutate) and tests/fixtures."""
    lw = _load_lens_worktree_module()
    wt = tmp_path / "default_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=_head())
    assert res["ok"] is True, res
    assert res["size_mb"] < lw.MAX_WORKTREE_MB and res["share"] <= lw.MAX_WORKTREE_SHARE, res
    assert res["latency_ms"] > 0.0
    assert res["exclude"] == lw.DEFAULT_EXCLUDE == ["tests/corpus", "tests/fixtures/recon_corpus"]
    tracked = subprocess.run(["git", "-C", str(wt), "ls-files", "tests/mutants", "tests/fixtures"],
                             capture_output=True, text=True, check=True).stdout.split()
    tracked = [t for t in tracked if not t.startswith("tests/fixtures/recon_corpus/")]
    assert any(t.startswith("tests/mutants/") for t in tracked) and any(
        t.startswith("tests/fixtures/") for t in tracked), "nothing tracked to compare"
    missing = [t for t in tracked if not (wt / t).is_file()]
    assert not missing, (
        f"row963: the default sparse worktree hides {len(missing)}/{len(tracked)} tests/mutants|fixtures "
        f"files that precut/mutate/fixture gates read, e.g. {missing[:3]}")


def test_row963_latency_is_reported_not_enforced_by_default(tmp_path, monkeypatch):
    """(3) A slow create (clock advanced 10 s) is still ok=True unless the caller
    passes a bound; with MAX_CREATION_LATENCY_MS it is refused and left in place."""
    lw = _load_lens_worktree_module()
    real = time.perf_counter
    calls = {"n": 0}

    def slow_clock():
        calls["n"] += 1
        return real() + (10.0 if calls["n"] % 2 == 0 else 0.0)

    monkeypatch.setattr(lw.time, "perf_counter", slow_clock)
    unbounded = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT, worktree_path=tmp_path / "slow_default", commit=_head(),
        scope_dirs=["toolchain"])
    bounded = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT, worktree_path=tmp_path / "slow_bounded", commit=_head(),
        scope_dirs=["toolchain"], max_latency_ms=lw.MAX_CREATION_LATENCY_MS)
    monkeypatch.undo()
    assert unbounded["ok"] is True and unbounded["latency_ms"] >= 10000.0, unbounded
    assert bounded["ok"] is False and "exceeds" in bounded["error"], bounded
    assert (tmp_path / "slow_bounded").is_dir()


def test_row963_latency_enforcement_and_negative_control(tmp_path, monkeypatch):
    """Negative control: artificial latency injection exceeds 500ms and is refused when bound enforced."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    wt_dest = tmp_path / "slow_wt"

    # Enforced bound: if max_latency_ms is passed and exceeded, result is ok=False
    bad = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT,
        worktree_path=wt_dest,
        commit=base_commit,
        scope_dirs=["toolchain"],
        max_latency_ms=0.01,  # 0.01ms limit guarantees threshold violation
    )
    assert bad["ok"] is False, "Worktree creation with exceeded latency bound must return ok=False"
    assert "exceeds" in bad["error"] and "bound" in bad["error"], bad["error"]
    assert bad["latency_ms"] > 0.01
    assert wt_dest.is_dir(), "Violated worktree must be left in place (Fleet Rule 22: never deleted)"


def test_row963_default_worktree_runs_a_mutant_gate_like_a_full_checkout(tmp_path):
    """(2) A real gate that reads tests/mutants gives the same result in the
    default sparse worktree as in the full checkout."""
    lw = _load_lens_worktree_module()
    wt = tmp_path / "gate_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=_head())
    assert res["ok"] is True, res
    gate = "tests/test_row788_transform_control_is_separate.py"
    full = _run_gate(REPO_ROOT, gate)
    sparse = _run_gate(wt, gate)
    assert full[0] == 0, f"control: gate is not green in the full checkout: {full}"
    assert sparse == full, f"row963: {gate} differs in the sparse worktree: sparse={sparse} full={full}"


def test_row963_default_worktree_runs_a_fixture_gate_like_a_full_checkout(tmp_path):
    """(2) A real gate reading tests/fixtures (and tools/) gives the same result
    in the default sparse worktree as in the full checkout."""
    lw = _load_lens_worktree_module()
    wt = tmp_path / "fixture_wt"
    res = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=wt, commit=_head())
    assert res["ok"] is True, res
    gate = "tests/test_extraction_core_characterization.py"
    assert (wt / "tests/fixtures/extraction_core/golden_derivation.json").is_file()
    full = _run_gate(REPO_ROOT, gate)
    assert full[0] == 0, f"control: gate is not green in the full checkout: {full}"
    assert _run_gate(wt, gate) == full


def test_row963_corpus_gate_is_named_and_has_a_scoped_route(tmp_path):
    """(2) The default carves the recon corpus out and SAYS so; the documented
    route (--scope bulk_downloader tests --exclude tests/corpus) stays under the
    bound and runs the corpus-reading gate like the full checkout."""
    lw = _load_lens_worktree_module()
    gate = "tests/test_v3_66_33_recon_corpus_extraction.py"
    default = lw.create_sparse_lens_worktree(repo_path=REPO_ROOT, worktree_path=tmp_path / "d", commit=_head())
    assert default["ok"] is True and "tests/fixtures/recon_corpus" in default["exclude"], default
    assert not (tmp_path / "d" / "tests/fixtures/recon_corpus").exists()

    wt = tmp_path / "corpus_wt"
    res = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT, worktree_path=wt, commit=_head(),
        scope_dirs=["bulk_downloader", "tests"], exclude_dirs=["tests/corpus"])
    assert res["ok"] is True, res
    assert (wt / "tests/fixtures/recon_corpus").is_dir()
    full = _run_gate(REPO_ROOT, gate)
    assert full[0] == 0, f"control: gate is not green in the full checkout: {full}"
    assert _run_gate(wt, gate) == full


def test_row963_sparse_patterns_exact_counts():
    lw = _load_lens_worktree_module()
    pats = lw.sparse_patterns(["toolchain", "bulk_downloader"], lw.DEFAULT_EXCLUDE)
    assert pats == ["/*", "!/*/", "/toolchain/", "/bulk_downloader/", "!/tests/corpus/",
                    "!/tests/fixtures/recon_corpus/"]


def test_row963_teardown_reports_symlink_leftovers_and_keeps_them(tmp_path):
    """N1-A mutant M2: an untracked symlink left by a lens (a venv link, a
    dangling link) is reported by teardown and never deleted. A symlink to a
    directory already reads as is_dir(); only a file link or a dangling link
    can tell the `or p.is_symlink()` clause apart."""
    lw = _load_lens_worktree_module()
    base_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    wt = tmp_path / "symlink_wt"
    res = lw.create_sparse_lens_worktree(
        repo_path=REPO_ROOT, worktree_path=wt, commit=base_commit, scope_dirs=["toolchain"],
    )
    assert res["ok"] is True, res
    target = tmp_path / "outside.txt"
    target.write_text("x\n")
    (wt / "zz_file_link").symlink_to(target)
    (wt / "zz_dangling_link").symlink_to(tmp_path / "does-not-exist")

    down = lw.teardown_sparse_lens_worktree(REPO_ROOT, wt)
    assert down["ok"] is True, down
    for name in ("zz_file_link", "zz_dangling_link"):
        assert name in down["untracked_dirs"], (
            f"row963 M2: teardown did not report symlink leftover {name!r}: {down['untracked_dirs']!r}")
        assert (wt / name).is_symlink(), f"{name} deleted by teardown (Fleet Rule 22)"
    assert wt.is_dir()


def _constant_budgets(source):
    """Every constant wall-clock budget in `source`, keyed as the v3.66.1222 census keys
    it: (callee, value) for a timeout/budget_s/timeout_s keyword, and
    ("DEFAULT:<function>", value) for such a parameter default."""
    names = {"timeout", "budget_s", "timeout_s"}
    sites = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            sites += [(ast.unparse(node.func), kw.value.value) for kw in node.keywords
                      if kw.arg in names and isinstance(kw.value, ast.Constant)
                      and isinstance(kw.value.value, (int, float))]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            pos = a.posonlyargs + a.args
            pairs = list(zip(pos[len(pos) - len(a.defaults):], a.defaults))
            pairs += [(x, d) for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
            sites += [("DEFAULT:" + node.name, d.value) for x, d in pairs
                      if x.arg in names and isinstance(d, ast.Constant)
                      and isinstance(d.value, (int, float))]
    return sites


def test_row963_gate_budget_is_measured_and_two_runs_fit_the_item_bound():
    """T66 drop (budget gate, v3.66.1222): a budget at or above the item bound never fires,
    so its TimeoutExpired path is dead and a hung gate kills the worker instead. This file's
    one constant budget is the nested gate run: it is the measured derivation, and an item's
    two back-to-back runs plus the item reserve fit under the governing bound."""
    bound = json.loads(RATCHET.read_text(encoding="utf-8"))["governing_bound_s"]
    source = Path(__file__).read_text(encoding="utf-8")
    sites = _constant_budgets(source)
    assert len(sites) == 1, f"row963: expected exactly one constant budget (the gate run): {sites}"
    site, budget = sites[0]
    worst = _GATE_RUNS_PER_ITEM * budget + _ITEM_RESERVE_S
    assert worst <= bound, (
        f"row963: {site}={budget}s cannot fire inside the {bound}s item bound: "
        f"{_GATE_RUNS_PER_ITEM} back-to-back gate runs + {_ITEM_RESERVE_S}s reserve = {worst}s")
    measured = max(_GATE_MEASURED_S.values())
    derived = max(_GATE_BUDGET_FLOOR_S, math.ceil(_GATE_CONTENTION_FACTOR * measured))
    assert (site, budget) == ("DEFAULT:_run_gate", derived), (
        f"row963: {site}={budget}s is not max({_GATE_BUDGET_FLOOR_S}, "
        f"ceil({_GATE_CONTENTION_FACTOR} x {measured})) = {derived}s on _run_gate")
    tree = ast.parse(source)
    runs = {fn.name: sum(isinstance(n, ast.Call) and ast.unparse(n.func) == "_run_gate"
                         and not any(kw.arg == "timeout_s" for kw in n.keywords)
                         for n in ast.walk(fn))
            for fn in tree.body if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_")}
    assert max(runs.values()) == _GATE_RUNS_PER_ITEM and sum(runs.values()) == 6, (
        f"row963: the budget arithmetic assumes at most {_GATE_RUNS_PER_ITEM} gate runs per "
        f"item, 6 in all; the items now run {runs}")
    gates = {n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "gate" for t in n.targets)
             and isinstance(n.value, ast.Constant)}
    assert gates == set(_GATE_MEASURED_S), (
        f"row963: the gates the items run {sorted(gates)} are not the measured ones "
        f"{sorted(_GATE_MEASURED_S)}; re-measure before re-deriving the budget")


def test_row963_a_hung_gate_is_killed_by_its_budget_inside_the_item(tmp_path):
    """The budget's error path is live: the real helper, on a gate that hangs, raises
    TimeoutExpired for each of an item's two runs, and both end inside the item bound
    less its reserve. Budget and bound scaled 1/100 alike, so this costs ~1 s, not 2 min."""
    bound = json.loads(RATCHET.read_text(encoding="utf-8"))["governing_bound_s"]
    scale = 0.01
    budget = inspect.signature(_run_gate).parameters["timeout_s"].default * scale
    (tmp_path / "test_hung_gate.py").write_text(
        "import time\n\n\ndef test_hung_gate():\n    time.sleep(60)\n", encoding="utf-8")
    fired = 0
    started = time.monotonic()
    for _ in range(_GATE_RUNS_PER_ITEM):
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            _run_gate(tmp_path, "test_hung_gate.py", timeout_s=budget)
        assert caught.value.timeout == budget
        fired += 1
    elapsed = time.monotonic() - started
    assert fired == _GATE_RUNS_PER_ITEM == 2
    assert elapsed < (bound - _ITEM_RESERVE_S) * scale, (
        f"row963: {fired} hung gate runs took {elapsed:.2f}s at 1/100 scale; the budget "
        f"does not fire before the {bound}s item bound less its {_ITEM_RESERVE_S}s reserve")
