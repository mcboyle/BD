"""No tracked test may carry an assertion that is true for every input.

A DECIDABLE SLICE OF BACKLOG 26. That row asks for a vacuous-test detector and
is filed as hard, correctly: "can this assertion fail?" is undecidable in
general. But a useful subset is not hard at all, and it has live hits --
`assert <anything> or True` is true for every input, and an AST walk finds it in
a few lines.

FOUND BY WRITING ONE. At v3.66.1095 this session shipped
`assert "aifc" not in sys.modules or True` into the file whose entire subject is
tests that cannot fail, and it survived review, a mutation battery, a 517-file
band, twelve CI checks and four captures. Nothing could have caught it, because
nothing was looking. A census then found four more across the suite, one of them
also carrying a clause (`"sec" not in [s.lower() for s in ("SEC",)]`) that is
always False -- so that assertion could not fail in one direction and could not
pass in the other.

SLICE 4 adds one variable-mediated shape whose value remains decidable without
executing the suite: a simple immutable literal local, bound in the straight-
line body of a function, then used as a bare assertion or as an operand of the
assertion's root `or` chain. Any intervening lexical binding clears the fact;
any `global` or `nonlocal` declaration anywhere below the function refuses the
name. Assertions nested under control flow are deliberately outside the slice.

WHAT THIS GATE DELIBERATELY DOES NOT CLAIM. It is not the vacuous-test detector
row 26 asks for and does not close it. It cannot see:
  - variable-mediated vacuity outside the exact straight-line literal slice;
  - a test whose assertions are real but unreachable;
  - a test that asserts something true of every possible implementation.
These are decidable syntax floors, and the row stays open above them.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
from pathlib import Path

# Its subject is every tracked test file, so a new test file changes its
# denominator. That makes it axis-6 and repo-wide.
BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent

_VARIABLE_METRIC_KEYS = (
    "literal_bindings_seen",
    "literal_bindings_excluded_declaration",
    "literal_bindings_tracked",
    "variable_asserts_seen",
    "variable_asserts_excluded_already_true",
    "variable_asserts_eligible",
    "variable_asserts_decided_true",
    "variable_asserts_not_decided_true",
)


def _tracked_test_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", "tests/test*.py"],
        cwd=str(_REPO), capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    return [_REPO / p for p in out if p.strip()]


def _always_true(node: ast.expr, env: dict[str, object] | None = None) -> bool:
    """Is this expression true for every input, decidably, from syntax alone?

    Deliberately narrow. A free NAME is not judged; only a local literal proven
    by the caller's environment is. A call is never judged -- guessing there
    is how a gate starts crying wolf, which CLAUDE.md section 0 counts as a
    soundness bug rather than a safe default.
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.Name) and env is not None and node.id in env:
        return bool(env[node.id])
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        # `x or True` is true regardless of x.
        return any(_always_true(v, env) for v in node.values)
    return False


def _literal_binding(statement: ast.stmt) -> tuple[str, object] | None:
    """Return one simple immutable literal binding, or refuse the statement."""
    if (isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Constant)):
        return statement.targets[0].id, statement.value.value
    if (isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Constant)):
        return statement.target.id, statement.value.value
    return None


def _statement_bindings(statement: ast.stmt) -> set[str]:
    """Conservatively collect lexical bindings made anywhere in a statement.

    Over-collection only discards a fact. The special string-valued AST fields
    matter: imports, exception targets, definitions, and match captures do not
    all appear as ``Name(Store)`` nodes.
    """
    names = {
        node.id for node in ast.walk(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    for node in ast.walk(statement):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            if node.asname:
                names.add(node.asname)
            names.add(node.name.split(".", 1)[0])
            names.add(node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def _declared_dynamic_names(scope: ast.AST) -> set[str]:
    """Names whose local literal identity is not safe to retain.

    Nested ``nonlocal`` may mutate an enclosing local through a call between
    the binding and assertion. Collecting nested ``global`` as well is a
    conservative refusal and is part of this slice's explicit boundary.
    """
    return {
        name
        for node in ast.walk(scope)
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }


def _variable_names_in_true_slice(node: ast.expr) -> set[str]:
    """Names occupying positions the existing Constant/Or grammar judges."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return set().union(*(_variable_names_in_true_slice(value)
                             for value in node.values))
    return set()


def _variable_vacuous_asserts(
        tree: ast.AST, counts: dict[str, int] | None = None,
) -> list[tuple[int, str]]:
    """Find the exact straight-line local-literal variable slice.

    Only direct statements in a function body participate. Nested control-flow
    bodies, aliases, module bindings, inferred values, and merged branch state
    remain UNKNOWN and therefore cannot manufacture a finding.
    """
    metrics = counts if counts is not None else {}
    for key in _VARIABLE_METRIC_KEYS:
        metrics.setdefault(key, 0)
    hits: list[tuple[int, str]] = []

    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        refused = _declared_dynamic_names(scope)
        env: dict[str, object] = {}
        for statement in scope.body:
            if isinstance(statement, ast.Assert):
                # A named expression inside the assert runs before a later
                # load, so no value assigned by the earlier statement survives
                # as evidence for that name.
                assert_env = {
                    name: value for name, value in env.items()
                    if name not in _statement_bindings(statement)
                }
                referenced = (_variable_names_in_true_slice(statement.test)
                              & assert_env.keys())
                if referenced:
                    metrics["variable_asserts_seen"] += 1
                    if _always_true(statement.test):
                        metrics["variable_asserts_excluded_already_true"] += 1
                    else:
                        metrics["variable_asserts_eligible"] += 1
                        if _always_true(statement.test, assert_env):
                            metrics["variable_asserts_decided_true"] += 1
                            hits.append((statement.lineno,
                                         ast.unparse(statement.test)))
                        else:
                            metrics["variable_asserts_not_decided_true"] += 1

            binding = _literal_binding(statement)
            for name in _statement_bindings(statement):
                env.pop(name, None)
            if binding is not None:
                name, value = binding
                metrics["literal_bindings_seen"] += 1
                if name in refused:
                    metrics["literal_bindings_excluded_declaration"] += 1
                else:
                    metrics["literal_bindings_tracked"] += 1
                    env[name] = value

    return hits


def _vacuous_asserts_in_tree(
        tree: ast.AST, counts: dict[str, int] | None = None,
) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and _always_true(node.test):
            hits.append((node.lineno, ast.unparse(node.test)))
    hits.extend(_variable_vacuous_asserts(tree, counts))
    return sorted(set(hits))


def _vacuous_asserts(
        path: Path, counts: dict[str, int] | None = None,
) -> list[tuple[int, str]]:
    # SyntaxError is UNKNOWN, not a clean scan: let it fail the gate.
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _vacuous_asserts_in_tree(tree, counts)


def _scan() -> tuple[list[str], dict[str, int]]:
    counts = {"files": 0, "asserts": 0,
              **{key: 0 for key in _VARIABLE_METRIC_KEYS}}
    offenders: list[str] = []
    for path in _tracked_test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        counts["files"] += 1
        counts["asserts"] += sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Assert))
        for lineno, text in _vacuous_asserts_in_tree(tree, counts):
            rel = path.relative_to(_REPO)
            offenders.append(f"{rel}:{lineno}  assert {text}")
    return offenders, counts


def test_the_walk_sees_a_substantial_number_of_files():
    """Non-empty denominator, asserted before the verdict.

    A gate that scans nothing reports clean -- truthfully and uselessly -- and
    that is the failure CLAUDE.md section 0 is entirely about. This gate's
    subject is `git ls-files`, so a broken path or a cwd surprise would produce
    exactly that silence.
    """
    files = _tracked_test_files()
    assert len(files) > 500, (
        f"only {len(files)} tracked test files found; the walk is not seeing "
        "the suite, so a clean result below would mean nothing")


def test_the_detector_recognises_the_shape_it_hunts():
    """Positive control. Without this, a zero from the real scan is
    indistinguishable from a detector that matches nothing."""
    tree = ast.parse("assert x or True\n")
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assert))
    assert _always_true(node.test), "the detector cannot see `x or True`"

    tree = ast.parse("assert True\n")
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assert))
    assert _always_true(node.test), "the detector cannot see a bare `assert True`"


def test_a_straight_line_local_literal_can_make_an_assertion_vacuous(tmp_path):
    """RED for backlog row 26 slice 4: the value is true through a NAME."""
    source = ("def test_it(condition):\n"
              "    always = True\n"
              "    assert condition or always\n")
    tree = ast.parse(source)
    assignments = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)]
    assertions = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert)]
    assert len(assignments) == 1 and len(assertions) == 1, (
        "positive fixture must contain exactly one assignment and one assert")
    assert isinstance(assignments[0].value, ast.Constant)
    assert assignments[0].value.value is True
    assert isinstance(assertions[0].test, ast.BoolOp)
    assert isinstance(assertions[0].test.op, ast.Or)
    assert isinstance(assertions[0].test.values[-1], ast.Name)
    assert assertions[0].test.values[-1].id == "always"
    assert not _always_true(assertions[0].test), (
        "the syntax-only slice unexpectedly decided the variable fixture")

    path = tmp_path / "test_variable_vacuity.py"
    path.write_text(source, encoding="utf-8")
    assert _vacuous_asserts(path) == [(3, "condition or always")]


def test_the_variable_slice_reports_an_exact_metrics_dictionary():
    source = ("def test_it(result):\n"
              "    fallback = False\n"
              "    assert result or fallback\n"
              "    always = True\n"
              "    assert result or always\n"
              "    direct = True\n"
              "    assert direct or True\n"
              "    global shared\n"
              "    shared = True\n"
              "    assert result or shared\n"
              "    always = dynamic()\n"
              "    assert result or always\n")
    tree = ast.parse(source)
    assignments = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)]
    assertions = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert)]
    assert len(assignments) == 5 and len(assertions) == 5, (
        "metrics fixture must build five assignments and five assertions")

    counts: dict[str, int] = {}
    assert _variable_vacuous_asserts(tree, counts) == [
        (5, "result or always")
    ]
    assert counts == {
        "literal_bindings_seen": 4,
        "literal_bindings_excluded_declaration": 1,
        "literal_bindings_tracked": 3,
        "variable_asserts_seen": 3,
        "variable_asserts_excluded_already_true": 1,
        "variable_asserts_eligible": 2,
        "variable_asserts_decided_true": 1,
        "variable_asserts_not_decided_true": 1,
    }, counts


def test_a_bare_truthy_local_is_inside_the_decidable_slice():
    source = ("def test_it():\n"
              "    always = True\n"
              "    assert always\n")
    tree = ast.parse(source)
    assignments = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)]
    assertions = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert)]
    assert len(assignments) == 1 and len(assertions) == 1
    assert isinstance(assertions[0].test, ast.Name)
    assert _variable_vacuous_asserts(tree) == [(3, "always")]


def test_a_falsy_local_clause_remains_a_real_assertion():
    """Eligible-floor and over-sensitivity control in the tracked census."""
    source = ("def test_it(result):\n"
              "    fallback = False\n"
              "    assert result or fallback\n")
    tree = ast.parse(source)
    assignments = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)]
    assertions = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert)]
    assert len(assignments) == 1 and len(assertions) == 1
    assert assignments[0].value.value is False
    hits = _variable_vacuous_asserts(tree)
    fallback = False
    assert not hits or fallback


def test_rebinding_and_dynamic_scope_refuse_stale_literal_facts():
    controls = {
        "ordinary rebind": ("def test_it(result):\n"
                            "    flag = True\n"
                            "    flag = dynamic()\n"
                            "    assert result or flag\n"),
        "named expression": ("def test_it(result):\n"
                             "    flag = True\n"
                             "    assert (flag := dynamic()) or result\n"),
        "global": ("def test_it(result):\n"
                   "    global flag\n"
                   "    flag = True\n"
                   "    assert result or flag\n"),
        "nested nonlocal": ("def test_it(result):\n"
                            "    flag = True\n"
                            "    def change():\n"
                            "        nonlocal flag\n"
                            "        flag = False\n"
                            "    change()\n"
                            "    assert result or flag\n"),
    }
    for reason, source in controls.items():
        tree = ast.parse(source)
        assertions = [node for node in ast.walk(tree)
                      if isinstance(node, ast.Assert)]
        assert len(assertions) == 1, f"{reason} control built {len(assertions)} asserts"
        assert _variable_vacuous_asserts(tree) == [], (
            f"{reason} left a stale literal fact live")


def test_nested_control_flow_remains_outside_the_decidable_slice():
    source = ("def test_it(result, condition):\n"
              "    flag = True\n"
              "    if condition:\n"
              "        assert result or flag\n")
    tree = ast.parse(source)
    assertions = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert)]
    assert len(assertions) == 1
    counts: dict[str, int] = {}
    assert _variable_vacuous_asserts(tree, counts) == []
    assert counts["literal_bindings_tracked"] == 1, counts
    assert counts["variable_asserts_eligible"] == 0, counts


def test_variable_slice_transform_control_imports_without_judging_behaviour():
    """Mutation transform control: collection imports; this asserts nothing."""
    importlib.import_module(__name__)


def test_the_detector_does_not_fire_on_real_assertions():
    """The over-sensitivity control. Every one of these must stay silent, or
    the gate is unusable and gets switched off."""
    for src in (
        "assert x",
        "assert x or y",
        "assert x, 'message'",
        "assert x or y, 'message'",
        "assert f() or g()",
        "assert x is not None",
        "assert 'a' in b",
        # A FALSE constant in an `or` does not make the whole thing true.
        "assert x or False",
        # An `and` with True still depends on x.
        "assert x and True",
    ):
        tree = ast.parse(src + "\n")
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assert))
        assert not _always_true(node.test), (
            f"the detector fired on a REAL assertion: {src!r}")


def test_no_tracked_test_carries_an_assertion_that_cannot_fail():
    offenders, counts = _scan()

    assert not offenders, (
        f"{len(offenders)} assertion(s) are true for every input, so they "
        f"constrain nothing and pass on any implementation:\n  "
        f"{'\n  '.join(offenders)}\n\n"
        "Remove the assertion, or make it real. If the property is not "
        "reliably true -- which is usually why the `or True` was added -- "
        "deleting it is the honest fix: a comment can say what was intended, "
        "while an assertion that cannot fail claims coverage it does not have."
        f"\n\nDenominator for this run: {counts['files']} files, "
        f"{counts['asserts']} assertions; variable slice: "
        f"{counts['literal_bindings_seen']} literal bindings seen, "
        f"{counts['literal_bindings_excluded_declaration']} declaration "
        f"exclusions, {counts['literal_bindings_tracked']} tracked, "
        f"{counts['variable_asserts_seen']} candidate assertions, "
        f"{counts['variable_asserts_excluded_already_true']} already decided "
        f"by the syntax-only slice, {counts['variable_asserts_eligible']} "
        f"eligible, {counts['variable_asserts_decided_true']} true and "
        f"{counts['variable_asserts_not_decided_true']} not decided true. "
        "Assertions below "
        "nested control flow and every non-literal or merged value remain "
        "UNKNOWN, not clean."
    )


def test_the_variable_slice_denominator_is_nonzero_and_reconciled():
    _, counts = _scan()
    assert set(counts) == {"files", "asserts", *_VARIABLE_METRIC_KEYS}, counts
    assert counts["files"] == len(_tracked_test_files()) > 500, counts
    assert counts["asserts"] > 10_000, counts
    assert counts["literal_bindings_seen"] == (
        counts["literal_bindings_excluded_declaration"]
        + counts["literal_bindings_tracked"]
    ), counts
    assert counts["variable_asserts_seen"] == (
        counts["variable_asserts_excluded_already_true"]
        + counts["variable_asserts_eligible"]
    ), counts
    assert counts["variable_asserts_eligible"] == (
        counts["variable_asserts_decided_true"]
        + counts["variable_asserts_not_decided_true"]
    ), counts
    assert counts["variable_asserts_eligible"] > 0, (
        "the straight-line local-literal slice has zero eligible assertions; "
        "a clean verdict would say nothing about its advertised population")
