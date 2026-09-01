"""Any CI shard whose gates need node must be given node.

THE TWO WAYS A SHARD NEEDS NODE. T-series wired gates call
``tests/frontend_vitest.py``, which runs the real Vitest binary. Other gates
read the installed repository ``frontend/node_modules`` directly without
delegating to Vitest. The bridge is FAIL-CLOSED on purpose --

    assert VITEST.is_file(), "Vitest unavailable at ...; run `npm ci` in frontend/"

-- because a gate that SKIPS when its tool is missing is a gate that does not
exist, which is the whole disease this sweep has been treating. Installed-tree
readers fail closed for the same reason. Provisioning must therefore cover the
union of both derived populations.

WHY THE FIX IS CONDITIONAL RATHER THAN GLOBAL. Installing node on every shard
pays ``npm ci`` repeatedly for nothing. So node is installed only for shards
that need it -- and this gate derives both sides of that condition rather than
trusting shard names or prose.

THE TEMPTING WRONG FIX, recorded so it is not re-attempted: make
``frontend_vitest`` skip when the binary is absent. That turns real gates
into silent no-ops and CI goes green over them forever. The bridge asserts
for a reason.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

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


def _static_path_value(
        node: ast.AST,
        source_path: pathlib.Path,
        names: dict[str, pathlib.Path],
) -> pathlib.Path | None:
    """Evaluate only the small, side-effect-free Path grammar tests use."""
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return source_path
        return names.get(node.id)
    if isinstance(node, ast.Call):
        is_path_constructor = (
            isinstance(node.func, ast.Name) and node.func.id == "Path"
        ) or (
            isinstance(node.func, ast.Attribute) and node.func.attr == "Path"
        )
        if is_path_constructor:
            if len(node.args) != 1 or node.keywords:
                return None
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return pathlib.Path(arg.value)
            value = _static_path_value(arg, source_path, names)
            return pathlib.Path(value) if value is not None else None
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == "resolve"
                and not node.args and not node.keywords):
            value = _static_path_value(node.func.value, source_path, names)
            return value.resolve() if value is not None else None
        return None
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        value = _static_path_value(node.value, source_path, names)
        return value.parent if value is not None else None
    if (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)):
        value = _static_path_value(node.value.value, source_path, names)
        if value is None:
            return None
        try:
            return value.parents[node.slice.value]
        except IndexError:
            return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _static_path_value(node.left, source_path, names)
        if (left is None or not isinstance(node.right, ast.Constant)
                or not isinstance(node.right.value, str)):
            return None
        return left / node.right.value
    return None


def _parameter_path_parts(node: ast.AST, parameter: str) -> tuple[str, ...] | None:
    """Return the lexical suffix a Path `/` expression adds to a parameter."""
    if isinstance(node, ast.Name) and node.id == parameter:
        return ()
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
        return None
    prefix = _parameter_path_parts(node.left, parameter)
    if (prefix is None or not isinstance(node.right, ast.Constant)
            or not isinstance(node.right.value, str)):
        return None
    part = pathlib.PurePath(node.right.value)
    if part.is_absolute():
        return None
    return prefix + part.parts


def _reads_repository_node_modules(
        rel: str, root: pathlib.Path | None = None
) -> bool:
    """Does a suite reach this repository's installed frontend dependencies?

    Comments, string literals, fixture-owned paths, and exclusion lists are not
    dependencies. Module Path aliases are evaluated lexically, and a helper
    parameter counts only when a real call binds it to the repository frontend.
    Syntax errors remain loud rather than disappearing from the denominator.
    """
    repo_root = (root or ROOT).absolute()
    source_path = repo_root / rel
    assert source_path.is_file(), (
        f"cannot classify node requirement for missing suite: {source_path}")
    tree = ast.parse(
        source_path.read_text(encoding="utf-8", errors="replace"),
        filename=str(source_path),
    )
    target = repo_root / "frontend" / "node_modules"

    names: dict[str, pathlib.Path] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value_node = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value_node = statement.value
        else:
            continue
        value = _static_path_value(value_node, source_path, names)
        if value is None:
            continue
        for assigned in targets:
            if isinstance(assigned, ast.Name):
                names[assigned.id] = value

    def reaches_target(value: pathlib.Path) -> bool:
        return value == target or target in value.parents

    if any(
        value is not None and reaches_target(value)
        for node in ast.walk(tree)
        for value in [_static_path_value(node, source_path, names)]
    ):
        return True

    function_paths: dict[str, list[tuple[int, tuple[str, ...]]]] = {}
    for function in (node for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        parameters = [arg.arg for arg in function.args.posonlyargs + function.args.args]
        requirements: list[tuple[int, tuple[str, ...]]] = []
        for index, parameter in enumerate(parameters):
            for child in ast.walk(function):
                parts = _parameter_path_parts(child, parameter)
                if parts is not None and "node_modules" in parts:
                    requirements.append((index, parts))
        if requirements:
            function_paths[function.name] = requirements

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if not isinstance(call.func, ast.Name) or call.func.id not in function_paths:
            continue
        for index, parts in function_paths[call.func.id]:
            if index >= len(call.args):
                continue
            base = _static_path_value(call.args[index], source_path, names)
            if base is not None and reaches_target(base.joinpath(*parts)):
                return True
    return False


def _needs_node(rel: str) -> bool:
    return _delegates_to_vitest(rel) or _reads_repository_node_modules(rel)


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
    """If the bridge starts skipping, its delegators quietly become no-ops."""
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


_LIVE_NODE_MODULES_READERS = (
    "tests/test_secret_display_never.py",
    "tests/test_frontend_dependency_security_floor.py",
    "tests/test_spa_root_routing_contract.py",
)
_NODE_MODULES_TEXT_NEGATIVE_CONTROLS = (
    "tests/test_failed_measurements_have_distinct_states.py",
    "tests/test_v3_66_1169_openapi_has_one_producer.py",
    "tests/test_v3_66_1157_build_output_is_from_this_attempt.py",
    "tests/test_v3_66_947_the_kb_manifest_can_be_regenerated.py",
    "tests/test_playwright_engines_single_source.py",
    "tests/test_deploy_manifest_stays_retired.py",
    "tests/test_desandbox_tool_verifiers.py",
)


def _matrix_with_isolated_suites(
        isolated: tuple[str, ...], shard_name: str
) -> dict[str, list[str]]:
    """Move exact live suites into one synthetic shard without dropping any."""
    shards = _shards()
    before = [suite for suites in shards.values() for suite in suites]
    assert before, "precondition: the live CI suite denominator is empty"
    assert len(before) == len(set(before)), (
        "precondition: the live matrix already contains duplicate suites")
    assert all(before.count(suite) == 1 for suite in isolated), (
        "precondition: an isolated reader is absent or duplicated in the live matrix")

    moved = {
        name: [suite for suite in suites if suite not in isolated]
        for name, suites in shards.items()
    }
    moved[shard_name] = list(isolated)
    after = [suite for suites in moved.values() for suite in suites]
    assert sorted(after) == sorted(before), (
        "the reshuffle changed the matrix union instead of only its partition")
    assert moved[shard_name] == list(isolated)
    return moved


def test_an_unprovisioned_reader_only_shard_is_rejected(monkeypatch):
    """RED: all live node_modules readers need node even without delegation."""
    readers = _LIVE_NODE_MODULES_READERS
    assert len(readers) == 3, "precondition: the reader fixture must be nonzero"
    assert all((ROOT / rel).is_file() for rel in readers)
    assert sum(_delegates_to_vitest(rel) for rel in readers) == 0, (
        "precondition: the reader-only fixture unexpectedly delegates to Vitest")

    shards = _matrix_with_isolated_suites(readers, "reader-only")
    provisioned = _node_provisioned_shards()
    delegator_count = sum(
        _delegates_to_vitest(suite)
        for suites in shards.values()
        for suite in suites
    )
    assert delegator_count > 0, "precondition: the reshuffled matrix has no delegators"
    assert "reader-only" not in provisioned
    assert len(shards["reader-only"]) == 3, (
        "precondition: the injected unprovisioned condition fired zero times")

    monkeypatch.setattr(sys.modules[__name__], "_shards", lambda: shards)
    monkeypatch.setattr(
        sys.modules[__name__], "_node_provisioned_shards", lambda: provisioned)
    with pytest.raises(AssertionError, match="reader-only"):
        test_every_shard_that_needs_node_gets_node()


def test_a_provisioned_reader_only_shard_is_not_needless(monkeypatch):
    """RED: provisioning the reader-only shard must not be rejected as waste."""
    readers = _LIVE_NODE_MODULES_READERS
    assert len(readers) == 3 and all((ROOT / rel).is_file() for rel in readers)
    assert sum(_delegates_to_vitest(rel) for rel in readers) == 0
    shards = _matrix_with_isolated_suites(readers, "reader-only")
    provisioned = _node_provisioned_shards() | {"reader-only"}
    assert "reader-only" in provisioned
    assert len(shards["reader-only"]) > 0, (
        "precondition: the injected provisioned condition fired zero times")

    monkeypatch.setattr(sys.modules[__name__], "_shards", lambda: shards)
    monkeypatch.setattr(
        sys.modules[__name__], "_node_provisioned_shards", lambda: provisioned)
    test_node_is_not_installed_for_shards_that_do_not_need_it()


def test_a_fixture_only_node_modules_mention_remains_needless(monkeypatch):
    """Negative control: unrelated node_modules text must not demand node."""
    fixture_only = ("tests/test_v3_66_1157_build_output_is_from_this_attempt.py",)
    assert all((ROOT / rel).is_file() for rel in fixture_only)
    assert sum(_delegates_to_vitest(rel) for rel in fixture_only) == 0
    shards = _matrix_with_isolated_suites(fixture_only, "fixture-only")
    provisioned = _node_provisioned_shards() | {"fixture-only"}
    assert len(shards["fixture-only"]) == 1, (
        "precondition: the negative-control condition fired zero times")

    monkeypatch.setattr(sys.modules[__name__], "_shards", lambda: shards)
    monkeypatch.setattr(
        sys.modules[__name__], "_node_provisioned_shards", lambda: provisioned)
    with pytest.raises(AssertionError, match="fixture-only"):
        test_node_is_not_installed_for_shards_that_do_not_need_it()


def test_node_modules_readers_are_resolved_from_paths_not_bare_text(tmp_path):
    """The reader predicate sees live dependencies and rejects seven decoys."""
    live = [suite for suites in _shards().values() for suite in suites]
    readers = {suite for suite in live if _reads_repository_node_modules(suite)}
    assert readers, "the live node_modules reader denominator is empty"
    assert set(_LIVE_NODE_MODULES_READERS) <= readers
    assert not (set(_NODE_MODULES_TEXT_NEGATIVE_CONTROLS) & readers), (
        "fixture or exclusion-list node_modules text entered the reader denominator")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "direct.py").write_text(
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "FRONTEND = ROOT / 'frontend'\n"
        "assert (FRONTEND / 'node_modules').is_dir()\n",
        encoding="utf-8",
    )
    (tests / "helper.py").write_text(
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "def use(source):\n"
        "    assert (source / 'node_modules').is_dir()\n"
        "use(ROOT / 'frontend')\n",
        encoding="utf-8",
    )
    (tests / "fixture.py").write_text(
        "def test_fixture(tmp_path):\n"
        "    (tmp_path / 'frontend' / 'node_modules').mkdir(parents=True)\n",
        encoding="utf-8",
    )
    (tests / "prose.py").write_text(
        '"""frontend/node_modules is an exclusion, not a dependency."""\n',
        encoding="utf-8",
    )
    outcomes = {
        name: _reads_repository_node_modules(f"tests/{name}.py", tmp_path)
        for name in ("direct", "helper", "fixture", "prose")
    }
    assert outcomes == {
        "direct": True,
        "helper": True,
        "fixture": False,
        "prose": False,
    }
    with pytest.raises(AssertionError, match="missing suite"):
        _reads_repository_node_modules("tests/absent.py", tmp_path)


def test_every_shard_that_needs_node_gets_node():
    """THE CONTRACT. Derived from delegators plus live dependency readers."""
    shards = _shards()
    assert shards, "gate-suites has no matrix include entries to check"

    provisioned = _node_provisioned_shards()
    requiring = {
        name: [s for s in suites if _needs_node(s)]
        for name, suites in shards.items()
    }
    requiring = {k: v for k, v in requiring.items() if v}

    assert requiring, (
        "no shard delegates to Vitest or reads frontend/node_modules, so this "
        "gate is measuring nothing; re-derive the node requirement population")

    unprovisioned = {k: v for k, v in requiring.items() if k not in provisioned}
    assert not unprovisioned, (
        "shard(s) run gates that need node with no node installed, so those gates "
        "fail on every CI run: %r" % unprovisioned)


def test_node_is_not_installed_for_shards_that_do_not_need_it():
    """OVER-SENSITIVITY CONTROL. The conditional exists to avoid paying `npm ci`
    on every shard; if someone 'fixes' a failure by making it unconditional, the
    saving is gone and nothing else here would notice."""
    shards = _shards()
    provisioned = _node_provisioned_shards()
    if provisioned == set(shards):
        pytest.fail(
            "node is installed for EVERY gate-suites shard. Only the shards that "
            "need node should have it; installing it for all %d pays `npm ci` "
            "on shards that never use node." % len(shards))
    needless = {
        name for name in provisioned
        if not any(_needs_node(s) for s in shards.get(name, []))
    }
    assert not needless, (
        "node is installed for shard(s) that run no gate needing node: %r"
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
