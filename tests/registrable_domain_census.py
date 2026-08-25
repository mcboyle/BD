"""Find code that computes a registrable domain by joining the last two labels.

WHY THIS IS NOT A SUBSTRING SCAN. The predicate this replaces was

    ("split('.')" in code or 'split(".")' in code) and "[-2:]" in code

which is a TEXTUAL PROXY for a BEHAVIOURAL claim (backlog row 189). It judges
the spelling and not the answer, so every behavioural twin walks past it:
``p[-2] + '.' + p[-1]``, ``h.rsplit('.', 2)``, ``'.'.join(p[len(p)-2:])``,
``n = 2; p[-n:]``, and ``f'{p[-2]}.{p[-1]}'`` all produce ``co.uk`` for BOTH
``attacker.co.uk`` and ``victim.co.uk`` -- two unrelated registrants collapsing
into one origin, which is the cookie and same-site boundary this rule exists to
defend. Measured at v3.66.1232: the substring predicate reported ZERO of those
five as offenders.

This module decides by EVALUATING the candidate expression and comparing its
VALUE to the last-two-labels answer across probes chosen to discriminate. A
spelling nobody anticipated is caught because the arithmetic is the subject,
not the characters.

THE PROBES ARE THE ARGUMENT. Two of them are hosts where last-two IS correct
(``www.example.com`` and ``a.b.example.com`` both give ``example.com``), so an
expression that merely returns *some* domain cannot pass by accident -- it must
actually produce domains. Three are hosts where last-two is WRONG:
``www.bbc.co.uk``, ``a.github.io`` and ``site.com.au``. An expression is an
offender only when it matches the last-two answer on ALL FIVE.
"""
from __future__ import annotations

import ast
import copy
import warnings

#: (host, last-two-labels answer). The first two agree with the correct
#: registrable domain; the last three do not, which is the whole defect.
PROBES = (
    ("www.example.com", "example.com"),
    ("a.b.example.com", "example.com"),
    ("www.bbc.co.uk", "co.uk"),
    ("a.github.io", "github.io"),
    ("site.com.au", "com.au"),
)

#: str methods that keep an expression evaluable from a bound host string.
_SAFE_METHODS = frozenset({
    "split", "rsplit", "join", "lower", "upper", "strip", "lstrip", "rstrip",
    "removeprefix", "removesuffix", "replace", "partition", "rpartition",
    "casefold", "format",
})
_SAFE_BUILTINS = {"len": len, "str": str, "list": list, "tuple": tuple,
                  "sorted": sorted, "reversed": reversed, "min": min, "max": max}

_MAX_ROOTS = 3
_MAX_INLINE_DEPTH = 6


def _strip_docstrings(node):
    """Remove docstrings so prose describing the bug is not read as the bug.

    ``ast.unparse`` drops comments already, but a docstring survives as an
    expression statement. This mattered historically: a migrated function whose
    docstring NAMED the pattern it had removed was reported UNMIGRATED. That is
    the same class of error this whole module exists to leave behind, so it is
    kept even though evaluation is far less prose-sensitive than a scan.
    """
    for inner in ast.walk(node):
        body = getattr(inner, "body", None)
        if (isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef, ast.Module))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            inner.body = body[1:] or [ast.Pass()]
    return node


def _single_assignments(fn):
    """Names assigned EXACTLY ONCE in this function's own body, name -> expr.

    Once, because a name reassigned in a branch does not have one value to
    inline and guessing would invent an expression the source never had.
    """
    counts, exprs = {}, {}
    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name):
                counts[target.id] = counts.get(target.id, 0) + 1
                exprs[target.id] = stmt.value
        elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
            target = getattr(stmt, "target", None)
            if isinstance(target, ast.Name):
                counts[target.id] = counts.get(target.id, 0) + 2
    return {k: v for k, v in exprs.items() if counts.get(k) == 1}


def _inline(node, aliases, depth=0):
    """Substitute single-assignment names by their expressions."""
    if depth > _MAX_INLINE_DEPTH:
        return node
    class _Sub(ast.NodeTransformer):
        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Load) and n.id in aliases:
                return _inline(copy.deepcopy(aliases[n.id]), aliases, depth + 1)
            return n
    return ast.fix_missing_locations(_Sub().visit(copy.deepcopy(node)))


def _is_pure(node):
    """Can this subtree be evaluated from bound strings alone?"""
    if isinstance(node, (ast.Constant, ast.Name, ast.Slice, ast.Tuple, ast.List,
                         ast.JoinedStr, ast.FormattedValue, ast.Index)):
        return True
    if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
                         ast.IfExp, ast.Subscript, ast.Starred)):
        return True
    if isinstance(node, ast.Attribute):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _SAFE_METHODS:
            return True
        if isinstance(func, ast.Name) and func.id in _SAFE_BUILTINS:
            return True
        return False
    return False


class _Abstract(ast.NodeTransformer):
    """Rewrite every MAXIMAL impure subtree to a fresh opaque host-ish name.

    ``urlparse(u).hostname`` is not evaluable here, but it IS a host, so it is
    replaced by a bound name rather than making the whole expression
    unanalysable. That is what lets the census see a call site that gets its
    host from a parser instead of from an argument.
    """

    def __init__(self):
        self.roots = []

    def _fresh(self, node):
        name = "_bd_root_%d" % len(self.roots)
        self.roots.append(name)
        return ast.copy_location(ast.Name(id=name, ctx=ast.Load()), node)

    def generic_visit(self, node):
        if isinstance(node, ast.expr) and not _is_pure(node):
            return self._fresh(node)
        return super().generic_visit(node)


def _candidate_expressions(fn):
    """Every expression this function could RETURN, aliases already inlined."""
    aliases = _single_assignments(fn)
    out = []
    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            out.append(_inline(stmt.value, aliases))
    return out


def _free_names(node):
    return sorted({n.id for n in ast.walk(node)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                   and n.id not in _SAFE_BUILTINS})


def _joins_last_two(expr) -> bool:
    """True when `expr` returns the last-two-labels answer for EVERY probe."""
    abstractor = _Abstract()
    shaped = ast.fix_missing_locations(abstractor.visit(copy.deepcopy(expr)))
    names = _free_names(shaped)
    if not names or len(names) > _MAX_ROOTS:
        return False
    try:
        # SyntaxWarning is raised by some probe expressions at COMPILE time and
        # is noise, not evidence; the verdict below is the value, not the warning.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            code = compile(ast.Expression(body=shaped), "<census>", "eval")
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return False
    for host, expected in PROBES:
        env = {"__builtins__": dict(_SAFE_BUILTINS)}
        env.update({name: host for name in names})
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                value = eval(code, env)  # noqa: S307 - restricted namespace
        except Exception:
            return False
        if value != expected:
            return False
    return True


def function_joins_last_two_labels(fn) -> bool:
    """The per-function verdict, kept as the name 1013's seam already uses."""
    stripped = _strip_docstrings(copy.deepcopy(fn))
    return any(_joins_last_two(expr) for expr in _candidate_expressions(stripped))


def scan(tree):
    """Every function in `tree` whose value IS the last-two-labels answer."""
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and function_joins_last_two_labels(n)]
