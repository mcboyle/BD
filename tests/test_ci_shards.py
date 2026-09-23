"""tools/ci_shards.py is the one owner of gate-suites membership (O1264 d, O1265 g).

ci.yml carries shard names only; this gate holds the two sides together:
the matrix names ARE the resolver's names, the run step invokes the resolver,
the resolved partition covers the declared census exactly once, and the
resolver fails CLOSED on the shapes that would otherwise run the wrong tests
(a pinned file outside the census, a glob shard with no member, a declared
gate no glob reaches, a pytest call with no paths).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools import ci_shards

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
TOOL = ROOT / "tools" / "ci_shards.py"


def _gate_suites() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["gate-suites"]


def test_matrix_names_are_exactly_the_resolver_names():
    include = _gate_suites()["strategy"]["matrix"]["include"]
    assert all(set(entry) == {"name"} for entry in include), (
        "a matrix entry carries more than a name; membership lives in tools/ci_shards.py")
    assert [entry["name"] for entry in include] == ci_shards.names()


def test_the_run_step_hands_pytest_the_resolved_shard_and_refuses_an_empty_one():
    runs = [str(step.get("run", "")) for step in _gate_suites()["steps"]
            if "pytest" in str(step.get("run", ""))]
    assert len(runs) == 1, f"expected one pytest step in gate-suites, found {len(runs)}"
    run = runs[0]
    assert 'python tools/ci_shards.py files "${{ matrix.name }}"' in run
    assert 'test -n "$suites"' in run, "an empty resolution must not reach pytest"
    assert "$suites" in run.split("pytest", 1)[1]
    assert "${{ matrix.suites }}" not in run


def test_the_partition_covers_the_declared_census_exactly_once():
    table = ci_shards.shards(ROOT)
    population = ci_shards.population(ROOT)
    executed = [f for files in table.values() for f in files]
    assert len(executed) == len(set(executed)) == len(population)
    assert set(executed) == population
    assert all(files for files in table.values()), "an empty shard would run the whole tree"
    assert all((ROOT / f).is_file() for f in executed)


def test_capability_shards_are_the_ones_the_workflow_provisions():
    steps = _gate_suites()["steps"]
    node = [str(s.get("if", "")) for s in steps
            if "setup-node" in str(s.get("uses", "")) or "npm ci" in str(s.get("run", ""))]
    chromium = [str(s.get("if", "")) for s in steps
                if "playwright install" in str(s.get("run", ""))]
    assert node and all("matrix.name == 'parity-graph'" in c for c in node)
    assert chromium and all("matrix.name == 'download-chain'" in c for c in chromium)
    pinned = dict(ci_shards.PINNED)
    assert "parity-graph" in pinned and "download-chain" in pinned


def _cli(*args: str, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), "--repo", str(repo), *args],
                          capture_output=True, text=True, check=False, timeout=120)


def test_cli_files_prints_the_shard_and_check_passes_on_the_live_tree():
    check = _cli("check", repo=ROOT)
    assert check.returncode == 0, check.stderr
    first = ci_shards.names()[0]
    files = _cli("files", first, repo=ROOT)
    assert files.returncode == 0 and files.stdout.split() == ci_shards.shards(ROOT)[first]
    assert _cli("files", "no-such-shard", repo=ROOT).returncode == 3


def _synthetic_repo(tmp_path: Path, files: dict[str, str], legacy: set[str],
                    family: set[str] = frozenset()) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    for rel, text in files.items():
        (repo / rel).write_text(text, encoding="utf-8")
    (repo / ci_shards.DECLARATION).write_text(
        f"_NON_DERIVABLE_DECLARED = {legacy or 'set()'}\n"
        f"_DB_PRUNE_SAFETY_FAMILY = {set(family) or 'set()'}\n", encoding="utf-8")
    return repo


def test_population_is_marker_or_legacy_minus_process_tests_plus_family(tmp_path):
    repo = _synthetic_repo(tmp_path, {
        "tests/test_marked.py": 'BD_GATE_SCOPE = "repo-wide"\n',
        "tests/test_module_scoped.py": 'BD_GATE_SCOPE = "module"\n',
        "tests/test_prose_only.py": '"""BD_GATE_SCOPE = "repo-wide" in a docstring"""\n',
        "tests/test_legacy.py": "",
        "tests/test_process.py": 'BD_GATE_SCOPE = "repo-wide"\n',
        "tests/test_family.py": "",
    }, legacy={"tests/test_legacy.py"}, family={"tests/test_family.py"})
    (repo / "tests" / "PROCESS_TESTS.txt").write_text("# nightly\ntests/test_process.py\n")
    assert ci_shards.population(repo) == {
        "tests/test_marked.py", "tests/test_legacy.py", "tests/test_family.py"}


def test_resolver_fails_closed(tmp_path, monkeypatch):
    repo = _synthetic_repo(tmp_path, {
        "tests/test_row1_a.py": 'BD_GATE_SCOPE = "repo-wide"\n',
        "tests/test_zeta.py": 'BD_GATE_SCOPE = "repo-wide"\n',
    }, legacy=set())
    monkeypatch.setattr(ci_shards, "PINNED", (("pinned", ("tests/test_zeta.py",)),))
    monkeypatch.setattr(ci_shards, "GLOBS", (("rows", ("tests/test_row*.py",)),))
    assert ci_shards.shards(repo) == {"pinned": ["tests/test_zeta.py"],
                                      "rows": ["tests/test_row1_a.py"]}
    # a pinned file outside the census
    monkeypatch.setattr(ci_shards, "PINNED", (("pinned", ("tests/test_missing.py",)),))
    with pytest.raises(ci_shards.ShardError, match="outside the declared gate"):
        ci_shards.shards(repo)
    # a declared gate no glob reaches
    monkeypatch.setattr(ci_shards, "PINNED", ())
    with pytest.raises(ci_shards.ShardError, match="matched by no shard"):
        ci_shards.shards(repo)
    # a glob shard with no member
    monkeypatch.setattr(ci_shards, "GLOBS", (("rows", ("tests/test_row*.py",)),
                                             ("none", ("tests/test_q*.py",)),
                                             ("rest", ("tests/test_*.py",))))
    with pytest.raises(ci_shards.ShardError, match="matches nothing"):
        ci_shards.shards(repo)
    # the same file pinned twice
    monkeypatch.setattr(ci_shards, "PINNED", (("a", ("tests/test_zeta.py",)),
                                              ("b", ("tests/test_zeta.py",))))
    monkeypatch.setattr(ci_shards, "GLOBS", (("rest", ("tests/test_*.py",)),))
    with pytest.raises(ci_shards.ShardError, match="already pinned"):
        ci_shards.shards(repo)
