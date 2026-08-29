"""Any CI shard that delegates to Vitest must be given node.

THE HAZARD THIS CLOSES, FOUND BEFORE IT FIRED. The five T-series wired gates
stopped being text scans in this cut and now call ``tests/frontend_vitest.py``,
which runs the real Vitest binary. That helper is FAIL-CLOSED on purpose --

    assert VITEST.is_file(), "Vitest unavailable at ...; run `npm ci` in frontend/"

-- because a gate that SKIPS when its tool is missing is a gate that does not
exist, which is the whole disease this sweep has been treating. The five live in
the ``parity-graph`` shard, and ``gate-suites`` installed only Python. Shipping
the delegation without provisioning node would therefore have failed five gates
on EVERY run.

WHY THE FIX IS CONDITIONAL RATHER THAN GLOBAL. Installing node on all eighteen
shards to serve five tests pays ``npm ci`` seventeen times for nothing. So node
is installed only for the shards that need it -- and a condition nobody checks
is exactly how the next person adds a sixth delegating gate to a nineteenth
shard and discovers the problem from a red CI run instead of from a test.

THE TEMPTING WRONG FIX, recorded so it is not re-attempted: make
``frontend_vitest`` skip when the binary is absent. That turns five real gates
into five silent no-ops and CI goes green over them forever. The bridge asserts
for a reason.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest
import yaml

BD_GATE_SCOPE = "repo-wide"

ROOT = pathlib.Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
BRIDGE = ROOT / "tests" / "frontend_vitest.py"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _shards() -> dict[str, list[str]]:
    job = _workflow()["jobs"]["gate-suites"]
    include = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
    return {e["name"]: str(e.get("suites", "")).split() for e in include}


def _delegates_to_vitest(rel: str, root: pathlib.Path | None = None) -> bool:
    """Does this suite RUN the Vitest bridge, or only name it?

    The substring scan this started as could not tell the two apart, and the
    difference is the whole point: a gate that merely NAMES
    ``tests/frontend_vitest.py`` -- in prose, or as a path string inside a data
    table -- never launches node, and provisioning a shard for it pays ``npm
    ci`` for nothing.  This file's own docstring was inside that denominator,
    so the scan counted this gate as a Vitest delegator.  Parse instead, per
    CLAUDE.md A7: delegation means the module reaches the file's CODE, as an
    import or as an identifier.  Text remains the outer net -- a file that
    never mentions the bridge cannot delegate to it -- and unparseable source
    keeps the conservative text answer rather than dropping silently out of the
    denominator.
    """
    path = (root or ROOT) / rel
    if not path.is_file():
        return False
    source = path.read_text(encoding="utf-8", errors="replace")
    if "frontend_vitest" not in source:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == "frontend_vitest" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "frontend_vitest":
                return True
            if any(a.name == "frontend_vitest" for a in node.names):
                return True
        elif isinstance(node, ast.Name) and node.id == "frontend_vitest":
            return True
        elif isinstance(node, ast.Attribute) and node.attr == "frontend_vitest":
            return True
    return False


def _node_provisioned_shards() -> set[str]:
    """Shard names for which gate-suites installs node.

    Read from the STEP CONDITIONS rather than assumed, so the test measures what
    the workflow will actually do.
    """
    job = _workflow()["jobs"]["gate-suites"]
    names: set[str] = set()
    for step in job["steps"]:
        blob = " ".join(str(v) for v in step.values())
        if "setup-node" not in blob and "npm ci" not in blob:
            continue
        cond = step.get("if")
        if cond is None:
            return set(_shards())          # unconditional: every shard has node
        names |= set(re.findall(r"matrix\.name\s*==\s*'([^']+)'", str(cond)))
        names |= set(re.findall(r'matrix\.name\s*==\s*"([^"]+)"', str(cond)))
    return names


def test_the_bridge_still_fails_closed_rather_than_skipping():
    """The premise of this whole file. If the bridge ever starts skipping, node
    provisioning stops mattering and five gates quietly become no-ops."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "assert VITEST.is_file()" in source, (
        "tests/frontend_vitest.py no longer asserts the Vitest binary exists. If "
        "it now SKIPS instead, the five T-series gates silently stop running and "
        "CI goes green over them -- re-derive this gate before changing that")
    assert "pytest.skip" not in source, (
        "the Vitest bridge acquired a skip; a gate that skips when its tool is "
        "missing is a gate that does not exist")


def test_delegation_is_read_from_code_and_not_from_prose(tmp_path):
    """Both directions of the parse refinement, including its fail-closed edge.

    Without the first case the gate refuses shards that never launch node;
    without the rest it could quietly stop seeing a real delegator, which is
    the failure the whole file exists to prevent.
    """
    cases = {
        "names_only.py": ('"""Uses tests/frontend_vitest.py."""\nP = "tests/frontend_vitest.py"\n', False),
        "imports_from.py": ("from tests.frontend_vitest import run_vitest\n", True),
        "imports_module.py": ("from tests import frontend_vitest\n", True),
        "plain_import.py": ("import tests.frontend_vitest\n", True),
        "attribute_use.py": ("import tests\ntests.frontend_vitest.run_vitest()\n", True),
        "unparseable.py": ("from tests.frontend_vitest import (\n", True),
        "silent.py": ("x = 1\n", False),
    }
    for name, (source, expected) in cases.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
        assert _delegates_to_vitest(name, tmp_path) is expected, name
    assert _delegates_to_vitest("absent.py", tmp_path) is False

    # And the live tree still has real delegators, so the refinement did not
    # empty the population it is meant to classify.
    live = [
        rel
        for suites in _shards().values()
        for rel in suites
    ]
    assert sum(_delegates_to_vitest(rel) for rel in live) >= 5


def test_every_shard_that_delegates_to_vitest_gets_node():
    """THE CONTRACT. Derived from the tree, not from a pinned shard name."""
    shards = _shards()
    assert shards, "gate-suites has no matrix include entries to check"

    provisioned = _node_provisioned_shards()
    delegating = {
        name: [s for s in suites if _delegates_to_vitest(s)]
        for name, suites in shards.items()
    }
    delegating = {k: v for k, v in delegating.items() if v}

    assert delegating, (
        "no shard delegates to Vitest, so this gate is measuring nothing. Either "
        "the T-series gates stopped using tests/frontend_vitest.py or the shard "
        "matrix changed shape; re-derive rather than deleting this assertion")

    unprovisioned = {k: v for k, v in delegating.items() if k not in provisioned}
    assert not unprovisioned, (
        "shard(s) run Vitest-delegating gates with no node installed, so those "
        "gates fail on every CI run: %r" % unprovisioned)


def test_node_is_not_installed_for_shards_that_do_not_need_it():
    """OVER-SENSITIVITY CONTROL. The conditional exists to avoid paying `npm ci`
    on every shard; if someone 'fixes' a failure by making it unconditional, the
    saving is gone and nothing else here would notice."""
    shards = _shards()
    provisioned = _node_provisioned_shards()
    if provisioned == set(shards):
        pytest.fail(
            "node is installed for EVERY gate-suites shard. Only the shards that "
            "delegate to Vitest need it; installing it for all %d pays `npm ci` "
            "on shards that never use node." % len(shards))
    needless = {
        name for name in provisioned
        if not any(_delegates_to_vitest(s) for s in shards.get(name, []))
    }
    assert not needless, (
        "node is installed for shard(s) that run no Vitest-delegating gate: %r"
        % sorted(needless))


def test_the_delegating_gates_are_still_in_a_shard_at_all():
    """Moving them OUT of the matrix would satisfy the node contract vacuously
    while removing them from CI entirely -- the loudest possible version of the
    bug this cut exists to avoid."""
    sharded = {s for suites in _shards().values() for s in suites}
    delegating_tracked = [
        str(p.relative_to(ROOT))
        for p in sorted((ROOT / "tests").glob("test_t*.py"))
        if _delegates_to_vitest(str(p.relative_to(ROOT)))
    ]
    assert delegating_tracked, "no T-series gate delegates to Vitest any more"
    missing = [p for p in delegating_tracked if p not in sharded]
    assert not missing, (
        "Vitest-delegating gate(s) are in no CI shard, so nothing runs them: %r"
        % missing)
