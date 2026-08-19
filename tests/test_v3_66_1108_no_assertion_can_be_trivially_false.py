"""No tracked test may carry an assertion that is false for every input.

BACKLOG 26, SLICE 2 -- THE MIRROR OF v3.66.1098. That cut shipped the
always-TRUE slice: an assertion true for every input constrains nothing and
passes on any implementation. This is the other half, and where it occurs it is
strictly worse, because an assertion that can never PASS is a test that can
never be green -- so wherever one is live and the suite is green, something is
SUPPRESSING it. At @1098 that was literally the case: the always-false clause
`"sec" not in [s.lower() for s in ("SEC",)]` was sitting inside an
`assert ... or True`, and removing the suppression is what exposed it.

WHAT THIS FOUND WHEN IT WAS RUN, WHICH IS THE HONEST HEADLINE: NOTHING.
Measured at v3.66.1107 over every tracked test file -- 32127 assert nodes,
37284 boolean-context sub-expressions -- there are ZERO live always-false
assertions and ZERO statically unreachable ones. So this gate closes no defect.
It is a FLOOR against a class that has occurred exactly once, and it is shipped
saying so rather than claiming a clean-up it did not do.

THE DECIDABLE POPULATION IS TINY, AND THAT IS THE PROPERTY TO UNDERSTAND
BEFORE READING A GREEN RESULT. Of those 37284 boolean-context expressions,
exactly TWO fold to a constant at all. The rest reference names, call
functions, or use node types the folder refuses. A first version of this gate
folded only WHOLE assert tests and could decide 1 of 32127 -- 0.003% -- which
is section 0's blind gate: it would have reported clean over a subject it
structurally could not see. Widening to boolean-context sub-expressions is what
made it able to decide the shape that motivated the row at all.

But the small denominator is not itself the defect it looks like. An assertion
built only from literals is INHERENTLY suspicious -- a real test asserts about
a value the code produced. So "expressions this folder can decide" and
"expressions worth suspecting" are close to the same set, and the gate decides
2 of 2 of them. What it cannot see is stated below and in its own failure text,
per CLAUDE.md's rule that an instrument's blind spots belong in output nobody
can skip.

IT DOES NOT `eval`, AND THAT IS A DELIBERATE COST. Compiling corpus source and
running it would be shorter and would cover more expressions. It would also
execute arbitrary code from every tracked test file during every gate run, in
the process that grades the suite. The folder below handles a closed set of
node types over literals and comprehension variables, so the worst input costs
bounded time and can touch nothing. Everything it will not evaluate is
UNFOLDABLE, which produces silence -- section 0's third state, not a pass.

TWO EXEMPTIONS, BOTH LEARNED FROM A LIVE FALSE POSITIVE RATHER THAN GUESSED:

  1. A BARE falsy constant -- `assert False`, `assert 0`, with or without a
     message -- is the sanctioned deliberate-failure idiom, and this suite has
     32 of them (`assert False, "should have rejected encrypted bitwarden"`).
     Two files document IN A COMMENT why they use it over `pytest.fail` (a
     custom runner). Firing on those reports 32 defects where there are none.

  2. A falsy constant in the FINAL position of an `or` is a null-coalescing
     DEFAULT, not a dead clause. The first version of this gate fired on
     `not (r2.get_json().get("gate_warnings") or [])` in
     tests/test_2c_guard_zero_match_interlock.py -- the one live hit it
     produced, and it was wrong. `x or []` supplies a value when x is falsy;
     that the value is also falsy is the point of the idiom. Section 0 counts
     over-sensitivity as a soundness bug, not a safe default, and a gate whose
     only live finding is a false positive is one that gets switched off in
     week one.

SLICE 3 @1193 ADDS ONE MORE DELIBERATELY NARROW HEURISTIC: an assertion
following an unconditional statement-level conventional NoReturn spelling in
the same statement list. The current tree contains 174 recognized spellings;
1 is refused because a surrounding try can catch it and 12 are refused because
the root name is lexically rebound, leaving 161 eligible and ZERO findings.
Parameters, assignments, named expressions, imports, exception/with/loop
targets, global, and nonlocal all trigger the binding refusal. Runtime mutation
of `pytest.skip`/`fail`/`xfail`/`exit`, `sys.exit`, or `os._exit` remains
unproved. This is therefore a syntactic heuristic, NOT proof that a callee has
NoReturn identity or that an assertion is unreachable.

WHAT THIS CANNOT SEE, so backlog 26 stays open above it:
  - an assertion vacuous or false through a VARIABLE bound elsewhere;
  - an assertion unreachable for a reason other than a preceding terminator
    (a fixture that skips, a guard that returns, an exception raised upstream);
  - anything whose truth depends on a call this folder will not make;
  - an assertion true of every possible implementation.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

# Its subject is every tracked test file, so a new test file changes its
# denominator. That makes it axis-6 and repo-wide.
BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Bounds. Every one exists to make the WORST input cheap, not the common input
# correct -- an unbounded folder inside a gate is a hang in the thing that
# grades the suite.
# ---------------------------------------------------------------------------
_MAX_NODES = 400          # larger expressions are simply not folded
_MAX_INT = 10 ** 6        # a bigger literal makes the expression unfoldable
_MAX_LEN = 10_000         # a longer value abandons the fold

_STR_METHODS = frozenset("""
    lower upper casefold strip lstrip rstrip title capitalize swapcase
    replace split rsplit splitlines startswith endswith count find rfind
    index rindex join zfill ljust rjust center isdigit isalpha isalnum
    isspace isupper islower istitle removeprefix removesuffix partition
    rpartition format
""".split())
_SEQ_METHODS = frozenset("count index copy".split())
_SET_METHODS = frozenset("""
    union intersection difference symmetric_difference issubset issuperset
    isdisjoint copy
""".split())
_DICT_METHODS = frozenset("keys values items get copy".split())

_BUILTINS = {
    "len": len, "bool": bool, "str": str, "int": int, "float": float,
    "tuple": tuple, "list": list, "set": set, "frozenset": frozenset,
    "dict": dict, "sorted": sorted, "reversed": reversed, "sum": sum,
    "min": min, "max": max, "any": any, "all": all, "abs": abs,
    "round": round, "enumerate": enumerate, "zip": zip, "repr": repr,
    "ord": ord, "chr": chr, "divmod": divmod, "range": range,
}

_CMP = {
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b,
}

_BINOP = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b, ast.Mod: lambda a, b: a % b,
    ast.FloorDiv: lambda a, b: a // b, ast.Div: lambda a, b: a / b,
    ast.BitAnd: lambda a, b: a & b, ast.BitOr: lambda a, b: a | b,
    ast.BitXor: lambda a, b: a ^ b,
}

_TERMINATORS = (ast.Return, ast.Raise, ast.Break, ast.Continue)
_NORETURN_CALLS = frozenset({
    ("pytest", "skip"),
    ("pytest", "fail"),
    ("pytest", "xfail"),
    ("pytest", "exit"),
    ("sys", "exit"),
    ("os", "_exit"),
})
_NORETURN_ROOTS = frozenset(root for root, _ in _NORETURN_CALLS)


class _Unfoldable(Exception):
    """Raised the moment anything is not decidable. A partial fold is a guess,
    and a guessing gate fires on real assertions."""


def _guard(value):
    """Refuse a value large enough to make the NEXT operation expensive.

    The bound is on the result rather than on the source, because that is
    where the growth happens: `"x" * 10**6` has a small syntax tree.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > _MAX_INT:
            raise _Unfoldable("integer beyond the fold bound")
    elif isinstance(value, (str, bytes, tuple, list, set, frozenset, dict)):
        if len(value) > _MAX_LEN:
            raise _Unfoldable("value longer than the fold bound")
    return value


def _truth(value) -> bool:
    try:
        return bool(value)
    except Exception as exc:                      # noqa: BLE001
        raise _Unfoldable("value has no truth") from exc


def _bind(target: ast.expr, value, env: dict) -> dict:
    """Bind a comprehension target: names, and (nested) tuples of names."""
    out = dict(env)
    if isinstance(target, ast.Name):
        out[target.id] = value
        return out
    if isinstance(target, (ast.Tuple, ast.List)):
        try:
            items = list(value)
        except TypeError as exc:
            raise _Unfoldable("comprehension target over a non-iterable") from exc
        if len(items) != len(target.elts):
            raise _Unfoldable("comprehension target arity mismatch")
        for elt, item in zip(target.elts, items):
            out = _bind(elt, item, out)
        return out
    raise _Unfoldable("unsupported comprehension target")


def _comprehend(node, env: dict):
    results = []

    def walk(i: int, scope: dict) -> None:
        if i == len(node.generators):
            if isinstance(node, ast.DictComp):
                results.append((_fold(node.key, scope), _fold(node.value, scope)))
            else:
                results.append(_fold(node.elt, scope))
            if len(results) > _MAX_LEN:
                raise _Unfoldable("comprehension produced too many items")
            return
        gen = node.generators[i]
        if gen.is_async:
            raise _Unfoldable("async comprehension")
        try:
            items = list(_fold(gen.iter, scope))
        except TypeError as exc:
            raise _Unfoldable("comprehension over a non-iterable") from exc
        if len(items) > _MAX_LEN:
            raise _Unfoldable("comprehension iterable too long")
        for item in items:
            inner = _bind(gen.target, item, scope)
            if all(_truth(_fold(c, inner)) for c in gen.ifs):
                walk(i + 1, inner)

    walk(0, env)
    if isinstance(node, ast.DictComp):
        return _guard(dict(results))
    if isinstance(node, ast.SetComp):
        return _guard(set(results))
    return _guard(list(results))


def _fold(node: ast.expr, env: dict):
    """Evaluate over literals and comprehension variables, or raise.

    Total in its refusals: anything not named here raises rather than being
    approximated.
    """
    if isinstance(node, ast.Constant):
        return _guard(node.value)

    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise _Unfoldable("free name %r" % node.id)

    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        items = [_fold(e, env) for e in node.elts]
        if isinstance(node, ast.Tuple):
            return _guard(tuple(items))
        if isinstance(node, ast.Set):
            return _guard(set(items))
        return _guard(items)

    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):      # {**other}
            raise _Unfoldable("dict unpacking")
        return _guard({_fold(k, env): _fold(v, env)
                       for k, v in zip(node.keys, node.values)})

    if isinstance(node, ast.UnaryOp):
        val = _fold(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not _truth(val)
        if isinstance(node.op, ast.USub):
            return _guard(-val)
        if isinstance(node.op, ast.UAdd):
            return _guard(+val)
        if isinstance(node.op, ast.Invert):
            return _guard(~val)
        raise _Unfoldable("unary op")

    if isinstance(node, ast.BoolOp):
        # Short-circuit exactly as Python does, so an unfoldable right-hand
        # operand cannot defeat a decidable left-hand one.
        if isinstance(node.op, ast.And):
            last = True
            for v in node.values:
                last = _fold(v, env)
                if not _truth(last):
                    return last
            return last
        last = False
        for v in node.values:
            last = _fold(v, env)
            if _truth(last):
                return last
        return last

    if isinstance(node, ast.Compare):
        left = _fold(node.left, env)
        for op, right_node in zip(node.ops, node.comparators):
            fn = _CMP.get(type(op))
            if fn is None:
                raise _Unfoldable("comparison operator")
            right = _fold(right_node, env)
            try:
                ok = fn(left, right)
            except Exception as exc:               # noqa: BLE001
                raise _Unfoldable("comparison raised") from exc
            if not _truth(ok):
                return False
            left = right
        return True

    if isinstance(node, ast.BinOp):
        fn = _BINOP.get(type(node.op))
        if fn is None:                             # Pow is deliberately absent
            raise _Unfoldable("binary operator")
        left, right = _fold(node.left, env), _fold(node.right, env)
        try:
            return _guard(fn(left, right))
        except Exception as exc:                   # noqa: BLE001
            raise _Unfoldable("binary op raised") from exc

    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                         ast.GeneratorExp)):
        return _comprehend(node, env)

    if isinstance(node, ast.JoinedStr):
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant):
                parts.append(str(piece.value))
            elif isinstance(piece, ast.FormattedValue):
                if piece.format_spec is not None or piece.conversion not in (-1, 115):
                    raise _Unfoldable("f-string conversion")
                parts.append(str(_fold(piece.value, env)))
            else:
                raise _Unfoldable("f-string part")
        return _guard("".join(parts))

    if isinstance(node, ast.Call):
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise _Unfoldable("call with keywords or unpacking")
        args = [_fold(a, env) for a in node.args]
        if isinstance(node.func, ast.Name):
            fn = _BUILTINS.get(node.func.id)
            if fn is None:
                raise _Unfoldable("call to %r" % node.func.id)
            if fn is range:
                if not all(isinstance(a, int) and not isinstance(a, bool)
                           for a in args):
                    raise _Unfoldable("range over non-integers")
                if len(range(*args)) > _MAX_LEN:
                    raise _Unfoldable("range too long")
            try:
                return _guard(fn(*args))
            except Exception as exc:               # noqa: BLE001
                raise _Unfoldable("builtin raised") from exc
        if isinstance(node.func, ast.Attribute):
            recv = _fold(node.func.value, env)
            name = node.func.attr
            allowed = (
                (isinstance(recv, str) and name in _STR_METHODS)
                or (isinstance(recv, (tuple, list)) and name in _SEQ_METHODS)
                or (isinstance(recv, (set, frozenset)) and name in _SET_METHODS)
                or (isinstance(recv, dict) and name in _DICT_METHODS)
            )
            if not allowed:
                raise _Unfoldable("method %r on %s" % (name, type(recv).__name__))
            try:
                return _guard(getattr(recv, name)(*args))
            except Exception as exc:               # noqa: BLE001
                raise _Unfoldable("method raised") from exc
        raise _Unfoldable("call target")

    raise _Unfoldable("node type %s" % type(node).__name__)


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------
def _bare_falsy_constant(node: ast.expr) -> bool:
    """`assert False` / `assert 0` -- the sanctioned deliberate-failure idiom.

    Bare only. `assert 0 == 1` is a Compare and is NOT exempt: it looks like a
    check and folds to false, which is the defect this gate is for.
    """
    return isinstance(node, ast.Constant) and not bool(node.value)


def _falsy_literal(node: ast.expr) -> bool:
    """A falsy value written as a LITERAL DISPLAY, which is what the
    null-coalescing idiom uses.

    `_bare_falsy_constant` is not enough here and the difference is not
    cosmetic: `[]` parses as an empty ast.List, NOT as an ast.Constant, so a
    Constant-only exemption misses `x or []` -- the exact expression the
    exemption was written for. Measured: with the Constant-only form this gate
    produced exactly one finding across the whole suite and it was a false
    positive.
    """
    if _bare_falsy_constant(node):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not node.elts:
        return True
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    # set() / list() / dict() / tuple() with no arguments
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("set", "list", "dict", "tuple", "frozenset")
            and not node.args and not node.keywords)


def _decide(node: ast.expr):
    """Fold, returning True/False, or None when the folder will not evaluate."""
    if sum(1 for _ in ast.walk(node)) > _MAX_NODES:
        return None
    try:
        return _truth(_fold(node, {}))
    except (_Unfoldable, RecursionError):
        return None


def findings(test: ast.expr) -> list[str]:
    """Every reason this assert's expression can never pass, or contains a
    clause that can never contribute. Empty means no verdict, not a pass.

    THE SOUNDNESS ARGUMENT, because each case is a different claim:

      * the whole test folds falsy               -> can never pass.
      * ANY operand of an `and` folds falsy      -> can never pass. If the
        others are truthy the result is that falsy operand; if one is falsy the
        result is falsy too. Either way the assert fails.
      * a NON-FINAL operand of an `or` folds falsy -> that clause is dead. The
        assert may still pass on a later operand, so this is a weaker finding
        and is reported as such.
      * the FINAL operand of an `or` folding falsy is EXEMPT when it is a bare
        constant -- `x or []` is null-coalescing -- and reported when it is a
        compound expression, which no idiom produces.
    """
    out: list[str] = []

    if not _bare_falsy_constant(test) and _decide(test) is False:
        out.append("the whole expression folds to a constant false value")

    for node in _bool_contexts(test):
        if not isinstance(node, ast.BoolOp):
            continue
        last = len(node.values) - 1
        for i, operand in enumerate(node.values):
            if _decide(operand) is not False:
                continue
            if isinstance(node.op, ast.Or) and i == last and _falsy_literal(operand):
                continue                    # null-coalescing default: `x or []`
            if isinstance(node.op, ast.And):
                out.append("operand %d of an `and` folds false, so the whole "
                           "assertion fails whatever the others are: %s"
                           % (i + 1, ast.unparse(operand)))
            elif i != last:
                out.append("clause %d of an `or` folds false, so it can never "
                           "contribute: %s" % (i + 1, ast.unparse(operand)))
            else:
                out.append("the final clause of an `or` is a compound "
                           "expression folding to false, so it can never "
                           "contribute: %s" % ast.unparse(operand))
    return out


def _bool_contexts(node: ast.expr):
    """Sub-expressions evaluated for TRUTH rather than for value.

    Deliberately does NOT descend through a Compare or a Call: in
    `assert x == (1 == 2)` the inner `1 == 2` is a VALUE being compared
    against, and flagging it would fire on correct code.
    """
    yield node
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            yield from _bool_contexts(v)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield from _bool_contexts(node.operand)


class _RootBindings(ast.NodeVisitor):
    """Find lexical stores without crossing into a nested lexical scope."""

    def __init__(self, *, module: bool):
        self.module = module
        self.bound: set[str] = set()

    def visit_Name(self, node):  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bound.add(node.id)

    def visit_arg(self, node):
        self.bound.add(node.arg)

    def visit_Global(self, node):  # noqa: N802
        self.bound.update(node.names)

    def visit_Nonlocal(self, node):  # noqa: N802
        self.bound.update(node.names)

    def visit_ExceptHandler(self, node):  # noqa: N802
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node):  # noqa: N802
        for alias in node.names:
            root = alias.asname or alias.name.split(".", 1)[0]
            canonical = (self.module and alias.asname is None
                         and alias.name in _NORETURN_ROOTS)
            if not canonical:
                self.bound.add(root)

    def visit_ImportFrom(self, node):  # noqa: N802
        for alias in node.names:
            self.bound.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node):  # noqa: N802
        self.bound.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):  # noqa: N802
        self.bound.add(node.name)


def _scope_bindings(node: ast.AST, *, module: bool) -> set[str]:
    visitor = _RootBindings(module=module)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in (*node.args.posonlyargs, *node.args.args,
                    *node.args.kwonlyargs):
            visitor.visit(arg)
        if node.args.vararg:
            visitor.visit(node.args.vararg)
        if node.args.kwarg:
            visitor.visit(node.args.kwarg)
    for statement in getattr(node, "body", []):
        visitor.visit(statement)
    return visitor.bound & _NORETURN_ROOTS


def _noreturn_root(statement: ast.stmt) -> str | None:
    if not (isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and isinstance(statement.value.func.value, ast.Name)):
        return None
    pair = (statement.value.func.value.id, statement.value.func.attr)
    return pair[0] if pair in _NORETURN_CALLS else None


def _assert_lines_without_definitions(node: ast.AST) -> list[int]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                         ast.Lambda)):
        return []
    hits = [node.lineno] if isinstance(node, ast.Assert) else []
    for child in ast.iter_child_nodes(node):
        hits.extend(_assert_lines_without_definitions(child))
    return hits


def unreachable_asserts(tree: ast.AST, counts: dict | None = None) -> list[int]:
    """Return syntactically dead assertions under a conservative heuristic.

    Conventional NoReturn spellings are accepted only when their root is not
    lexically rebound in the relevant scope. Runtime mutation of module
    attributes remains unproved, so this is not proof of unreachability.
    """
    metrics = counts if counts is not None else {}
    for key in ("noreturn_calls_seen", "noreturn_calls_excluded_catchable",
                "noreturn_calls_excluded_rebound"):
        metrics.setdefault(key, 0)
    hits: list[int] = []

    def walk_scope(scope, inherited_bindings: set[str], *, module=False):
        bindings = inherited_bindings | _scope_bindings(scope, module=module)

        def walk_list(stmts, *, catchable=False):
            terminated = False
            for statement in stmts:
                if terminated:
                    hits.extend(_assert_lines_without_definitions(statement))
                    continue
                root = _noreturn_root(statement)
                if root is not None:
                    metrics["noreturn_calls_seen"] += 1
                    if catchable:
                        metrics["noreturn_calls_excluded_catchable"] += 1
                    elif root in bindings:
                        metrics["noreturn_calls_excluded_rebound"] += 1
                    else:
                        terminated = True
                        continue
                if (isinstance(statement, ast.If)
                        and isinstance(statement.test, ast.Constant)
                        and not statement.test.value):
                    hits.extend(_assert_lines_without_definitions(statement))
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                          ast.ClassDef)):
                    walk_scope(statement, bindings)
                    continue
                for field in ("body", "orelse", "finalbody"):
                    child = getattr(statement, field, None)
                    if isinstance(child, list):
                        child_catchable = (catchable or
                                           (isinstance(statement, ast.Try)
                                            and field == "body"
                                            and bool(statement.handlers)))
                        walk_list(child, catchable=child_catchable)
                if isinstance(statement, ast.Try):
                    for handler in statement.handlers:
                        walk_list(handler.body, catchable=catchable)
                if isinstance(statement, _TERMINATORS):
                    terminated = True

        walk_list(scope.body)

    if isinstance(tree, ast.Module):
        walk_scope(tree, set(), module=True)
    return sorted(set(hits))


def _tracked_test_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", "tests/test*.py"],
        cwd=str(_REPO), capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    return [_REPO / p for p in out if p.strip()]


def _scan() -> tuple[list[str], dict]:
    """Returns (offenders, counts). The counts are the denominator, and they
    are reported with the verdict so a green result cannot be read as more
    than it is."""
    offenders: list[str] = []
    counts = {"files": 0, "asserts": 0, "bool_contexts": 0, "decided": 0,
              "noreturn_calls_seen": 0,
              "noreturn_calls_excluded_catchable": 0,
              "noreturn_calls_excluded_rebound": 0}
    for path in _tracked_test_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        counts["files"] += 1
        rel = path.relative_to(_REPO)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            counts["asserts"] += 1
            for sub in _bool_contexts(node.test):
                counts["bool_contexts"] += 1
                if _decide(sub) is not None:
                    counts["decided"] += 1
            for why in findings(node.test):
                offenders.append("%s:%d  %s\n      assert %s"
                                 % (rel, node.lineno, why,
                                    ast.unparse(node.test)[:160]))
        for lineno in unreachable_asserts(tree, counts):
            offenders.append("%s:%d  the assert follows a structural terminator "
                             "or a syntactically recognized conventional "
                             "NoReturn call. Root names lexically rebound in "
                             "the relevant scope are refused; runtime attribute "
                             "mutation is unproved, so this is a syntactic "
                             "heuristic, not proof of unreachability" %
                             (rel, lineno))
    return offenders, counts


# ---------------------------------------------------------------------------
# preconditions
# ---------------------------------------------------------------------------
def test_the_walk_sees_a_substantial_number_of_files():
    """Non-empty denominator, asserted before the verdict. A gate that scans
    nothing reports clean -- truthfully and uselessly."""
    files = _tracked_test_files()
    assert len(files) > 500, (
        f"only {len(files)} tracked test files found; the walk is not seeing "
        "the suite, so a clean result below would mean nothing")


# ---------------------------------------------------------------------------
# positive controls. Without these a zero is indistinguishable from a detector
# that matches nothing -- which the first version of this gate very nearly was.
# ---------------------------------------------------------------------------
def test_the_folder_decides_the_live_shape_that_motivated_this_gate():
    """THE @1098 case, verbatim. It needs a comprehension over a literal tuple
    with a method call on the loop variable, so no structural pattern-match
    reaches it -- which is the whole argument for carrying a folder."""
    src = 'assert "sec" not in [s.lower() for s in ("SEC",)]\n'
    node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assert))
    assert findings(node.test), (
        "the folder cannot decide the expression this gate was written for")


def test_it_finds_an_always_false_clause_buried_in_a_larger_expression():
    """The shape the FIRST version of this gate missed, and the reason it was
    rewritten: at @1098 the always-false clause was a sub-expression, not the
    whole test. A whole-test-only folder decides 1 of 32127 asserts."""
    src = 'assert helper() and "sec" not in [s.lower() for s in ("SEC",)]\n'
    node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assert))
    hits = findings(node.test)
    assert hits, "an always-false operand of an `and` was not reported"
    assert "and" in hits[0], hits


def test_it_descends_into_NESTED_boolean_operators():
    """A MUTATION ESCAPE CLOSED. The test above uses a TOP-LEVEL `and`, and
    `_bool_contexts` yields its root node before recursing -- so a mutant that
    removed the recursion entirely still passed it. Only a boolean operator
    nested inside another exercises the descent, and that is exactly where
    @1098's clause lived: inside an `or`, inside an `assert`.
    """
    for src in (
        # the always-false clause is inside an `and` nested in an `or`
        'assert helper() or (other() and "sec" not in [s.lower() for s in ("SEC",)])',
        # ... and inside a `not`, which is the other descent path
        'assert not (helper() or (other() and 0 == 1))',
    ):
        node = next(n for n in ast.walk(ast.parse(src + "\n"))
                    if isinstance(n, ast.Assert))
        assert findings(node.test), (
            "a nested boolean operator was not walked, so an always-false "
            f"clause inside it is invisible: {src!r}")


def test_the_folder_recognises_impossible_assertions():
    for src in (
        "assert 0 == 1",
        "assert 'a' == 'b'",
        "assert 'x' in ('y', 'z')",
        "assert not True",
        "assert False or False",
        "assert True and False",
        "assert len('abc') == 4",
        "assert 'A'.lower() == 'A'",
        "assert []",
        "assert {}",
        "assert 1 > 2",
        "assert 'sec' in [s.upper() for s in ('sec',)]",
        "assert sorted([3, 1]) == [3, 1]",
        "assert f'{1 + 1}' == '3'",
    ):
        node = next(n for n in ast.walk(ast.parse(src + "\n"))
                    if isinstance(n, ast.Assert))
        assert findings(node.test), (
            f"the folder failed to decide an impossible assertion: {src!r}")


def test_it_finds_an_unreachable_assert():
    tree = ast.parse("def t():\n    return 1\n    assert 0 == 1\n")
    assert unreachable_asserts(tree) == [3], unreachable_asserts(tree)


def test_a_direct_conventional_NoReturn_call_makes_the_same_block_tail_unreachable():
    """RED @1193: exact unconditional spellings terminate only their list."""
    src = """\
def test_pytest():
    pytest.skip('stop')
    assert reached_pytest
def test_sys():
    sys.exit(2)
    assert reached_sys
def test_os():
    os._exit(2)
    assert reached_os
"""
    tree = ast.parse(src)
    call_lines = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call)]
    assert call_lines == [2, 5, 8], (
        f"positive fixture did not build exactly three calls: {call_lines}")
    assert unreachable_asserts(tree) == [3, 6, 9], unreachable_asserts(tree)


def test_lexically_rebound_roots_are_refused_for_their_distinctive_reason():
    """Parameters, local stores, and module stores destroy root identity."""
    controls = {
        "parameter": "def test_it(pytest):\n    pytest.skip('returns')\n    assert reached\n",
        "local": "def test_it():\n    sys = fake_sys\n    sys.exit(2)\n    assert reached\n",
        "module": "pytest = fake_pytest\ndef test_it():\n    pytest.fail('returns')\n    assert reached\n",
    }
    for reason, src in controls.items():
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
        assert len(calls) == 1 and len(asserts) == 1, (
            f"{reason} control did not build one call and one assertion")
        assert unreachable_asserts(tree) == [], (
            f"{reason} rebinding must refuse the conventional NoReturn spelling")


def test_every_required_lexical_binding_form_refuses_the_root():
    controls = (
        "def t(pytest):\n    pytest.skip('x')\n    assert reached\n",
        "def t():\n    (sys := fake).exit(1)\n    sys.exit(1)\n    assert reached\n",
        "def t():\n    import fake as os\n    os._exit(1)\n    assert reached\n",
        "def t():\n    try:\n        work()\n    except Error as pytest:\n        pass\n    pytest.fail('x')\n    assert reached\n",
        "def t():\n    with cm() as sys:\n        pass\n    sys.exit(1)\n    assert reached\n",
        "def t():\n    for os in values:\n        pass\n    os._exit(1)\n    assert reached\n",
        "pytest = fake\ndef t():\n    pytest.xfail('x')\n    assert reached\n",
        "def outer():\n    pytest = fake\n    def t():\n        nonlocal pytest\n        pytest.exit('x')\n        assert reached\n",
        "pytest = fake\ndef t():\n    global pytest\n    pytest.skip('x')\n    assert reached\n",
    )
    for src in controls:
        tree = ast.parse(src)
        assert len([n for n in ast.walk(tree) if isinstance(n, ast.Assert)]) == 1
        assert unreachable_asserts(tree) == [], src


def test_conditional_calls_callbacks_and_catchable_calls_keep_their_boundaries():
    src = """\
def t(flag):
    if flag:
        pytest.skip('conditional')
    assert outer_reachable
    try:
        pytest.fail('catchable')
        assert caught_tail_reachable
    except BaseException:
        pass
    callback(lambda: pytest.exit('callback'))
    assert callback_tail_reachable
"""
    counts = {}
    assert unreachable_asserts(ast.parse(src), counts) == []
    assert counts == {
        "noreturn_calls_seen": 2,
        "noreturn_calls_excluded_catchable": 1,
        "noreturn_calls_excluded_rebound": 0,
    }


def test_dead_tails_are_walked_without_executing_nested_definitions():
    src = """\
def t():
    pytest.skip('stop')
    if condition:
        assert nested_if_dead
    def callback():
        assert definition_not_executed
    class Deferred:
        assert class_not_executed
"""
    assert unreachable_asserts(ast.parse(src)) == [4]


def test_the_conventional_NoReturn_registry_is_frozen():
    assert _NORETURN_CALLS == frozenset({
        ("pytest", "skip"), ("pytest", "fail"),
        ("pytest", "xfail"), ("pytest", "exit"),
        ("sys", "exit"), ("os", "_exit"),
    })


def test_the_conventional_NoReturn_denominator_is_nonzero_and_reconciled():
    _, counts = _scan()
    seen = counts["noreturn_calls_seen"]
    excluded = (counts["noreturn_calls_excluded_catchable"]
                + counts["noreturn_calls_excluded_rebound"])
    assert seen > 100, f"only {seen} conventional NoReturn calls were seen"
    assert excluded <= seen
    assert seen - excluded >= 0


def test_if_false_remains_a_structural_terminator():
    tree = ast.parse("def t():\n    if False:\n        assert x == 1\n")
    assert unreachable_asserts(tree) == [3], unreachable_asserts(tree)


# ---------------------------------------------------------------------------
# over-sensitivity controls. Section 0 counts a gate that cries wolf as a
# soundness bug, and the second of these is a REAL false positive this gate
# produced before it was fixed.
# ---------------------------------------------------------------------------
def test_the_bare_failure_idiom_is_exempt():
    """32 live sites use `assert False, "..."` as a reachable failure marker."""
    for src in ("assert False", "assert False, 'should have rejected'",
                "assert 0", "assert 0, 'unreachable'"):
        node = next(n for n in ast.walk(ast.parse(src + "\n"))
                    if isinstance(n, ast.Assert))
        assert not findings(node.test), (
            f"the gate fired on the sanctioned failure idiom: {src!r}")


def test_null_coalescing_defaults_are_exempt():
    """THE LIVE FALSE POSITIVE. Before this exemption the gate's ONLY finding
    across the whole suite was tests/test_2c_guard_zero_match_interlock.py's
    `not (r2.get_json().get("gate_warnings") or [])` -- and it was wrong.
    `x or []` supplies a value when x is falsy; that the value is itself falsy
    is the point of the idiom, not a defect."""
    for src in (
        "assert not (r.get_json().get('gate_warnings') or [])",
        "assert (thing() or []) == []",
        "assert not (x or {})",
        "assert not (x or '')",
        "assert not (x or 0)",
    ):
        node = next(n for n in ast.walk(ast.parse(src + "\n"))
                    if isinstance(n, ast.Assert))
        assert not findings(node.test), (
            f"the gate fired on a null-coalescing default: {src!r}")


def test_a_falsy_constant_used_as_a_VALUE_is_not_a_clause():
    """`x == (1 == 2)` compares against False deliberately. The folder must not
    descend into a Compare looking for boolean context."""
    for src in ("assert x == (1 == 2)", "assert x is (0 == 1)",
                "assert f(1 > 2) == 'no'"):
        node = next(n for n in ast.walk(ast.parse(src + "\n"))
                    if isinstance(n, ast.Assert))
        assert not findings(node.test), (
            f"the gate read a compared VALUE as a boolean clause: {src!r}")


def test_the_folder_stays_silent_on_real_assertions():
    for src in (
        "assert x",
        "assert x == 1",
        "assert 'a' in b",
        "assert f() == 2",
        "assert x.lower() == 'a'",
        "assert len(x) > 0",
        "assert [i for i in x] == []",
        "assert isinstance(x, str)",
        "assert x is not None",
        "assert 1 == 1",
        "assert 'a' in ('a', 'b')",
        "assert not False",
        "assert sorted([3, 1]) == [1, 3]",
        "assert x and y",
        "assert x or y",
        # decidable and TRUE -- @1098's slice, not this one
        "assert True",
        "assert x or True",
    ):
        node = next(n for n in ast.walk(ast.parse(src + "\n"))
                    if isinstance(n, ast.Assert))
        assert not findings(node.test), (
            f"the gate fired on an assertion it cannot decide as false: {src!r}")


def test_an_unfoldable_expression_is_silence_not_a_verdict():
    """UNKNOWN is a third state. Anything the folder will not evaluate must
    produce no finding rather than a guess in either direction."""
    for src in ("assert open('/etc/passwd').read() == ''",
                "assert __import__('os').getpid() == -1",
                "assert 2 ** 999999 == 0"):
        node = next(n for n in ast.walk(ast.parse(src + "\n"))
                    if isinstance(n, ast.Assert))
        assert not findings(node.test), (
            f"the folder claimed a verdict on something it must not "
            f"evaluate: {src!r}")


def test_the_folder_refuses_an_oversized_INTEGER():
    node = next(n for n in ast.walk(ast.parse("assert 'x' * 10000000 == ''\n"))
                if isinstance(n, ast.Assert))
    assert not findings(node.test), (
        "the folder evaluated an expression past its integer bound")


def test_the_folder_refuses_an_oversized_RESULT():
    """A MUTATION ESCAPE CLOSED, and the two bounds are genuinely different.

    The test above is caught by the INTEGER bound -- 10000000 exceeds _MAX_INT,
    so the fold is refused at the literal and the multiplication never happens.
    That meant a mutant deleting the RESULT-length bound sailed through it. A
    multiplier under the integer bound whose product is over the length bound
    is the only input that separates them, and the length bound is the one that
    matters: it is what stops a 1MB string being built inside the process that
    grades the suite.
    """
    node = next(n for n in ast.walk(ast.parse("assert 'x' * 999999 == ''\n"))
                if isinstance(n, ast.Assert))
    assert not findings(node.test), (
        "the folder built a value past its result-length bound; only the "
        "integer bound is holding, and it does not cover this shape")


# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------
def test_no_tracked_test_carries_an_assertion_that_cannot_pass():
    offenders, counts = _scan()

    assert not offenders, (
        "%d assertion(s) can never pass, or carry a clause that can never "
        "contribute:\n  %s\n\n"
        "Either the assertion is wrong and the intended one is different, or "
        "it is right and the code under it is broken. A GREEN SUITE carrying "
        "one of these means something is SUPPRESSING it -- at v3.66.1098 the "
        "live instance was hiding inside an `assert ... or True`. Fix the "
        "assertion or fix the code; do not delete it without deciding which.\n"
        "\nDenominator for this run: %d files, %d asserts, %d boolean-context "
        "expressions, of which %d could be decided at all; conventional "
        "NoReturn spellings: %d seen, %d catchable exclusions, %d lexical-"
        "rebind exclusions, %d eligible. Runtime attribute mutation is "
        "unproved: this is a syntactic heuristic, not proof of callee identity "
        "or unreachability."
        % (len(offenders), "\n  ".join(offenders), counts["files"],
           counts["asserts"], counts["bool_contexts"], counts["decided"],
           counts["noreturn_calls_seen"],
           counts["noreturn_calls_excluded_catchable"],
           counts["noreturn_calls_excluded_rebound"],
           counts["noreturn_calls_seen"]
           - counts["noreturn_calls_excluded_catchable"]
           - counts["noreturn_calls_excluded_rebound"])
    )


def test_the_gate_states_the_denominator_it_could_actually_decide():
    """THE BLIND SPOT, ASSERTED RATHER THAN DESCRIBED.

    CLAUDE.md: an instrument's blind spots belong in output nobody can skip.
    This gate's real limit is that constant folding decides a vanishing
    fraction of real assertions -- measured at v3.66.1107, 2 of 37284
    boolean-context expressions. That is not a defect (an assertion built only
    from literals is inherently suspicious, so the decidable set and the
    suspicious set nearly coincide) but it MUST NOT be mistaken for coverage.

    What this test actually guards is the failure that would be silent: the
    folder degrading until it decides NOTHING, leaving a permanently green
    gate. The positive controls above would catch a total collapse; this
    catches the corpus-level version.
    """
    _, counts = _scan()
    assert counts["asserts"] > 10_000, (
        f"only {counts['asserts']} asserts scanned -- the walk is not "
        "reaching the suite")
    assert counts["bool_contexts"] > counts["asserts"], (
        "boolean-context expansion produced no extra expressions, so the "
        "sub-expression widening that makes this gate able to see @1098's "
        "shape is not running")
