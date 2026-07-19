#!/usr/bin/env python3
"""bdtools_taint.py -- shared intra-procedural AST taint analysis for Wave 4.

A tractable (function-scoped, flow-insensitive-within-function) taint tracker:
mark SOURCE expressions, propagate taint through simple assignments / f-strings
/ calls that pass a tainted arg, and report when a tainted value reaches a SINK.
Each Wave-4 tool supplies its own SOURCE + SINK matchers and reuses this engine
(no copy-paste). Stdlib-only (ast). Read-only.

This is an advisory heuristic, not sound dataflow: it finds the common
capture/user/template -> sink flows and is explicit about its scope. False
negatives across function boundaries are expected (call it per-hop).

Self-test: python3 bdtools_taint.py --selftest
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import bdtools_sec as sec


class TaintFlow:
    """One tainted-source -> sink flow within a function."""
    __slots__ = ("file", "func", "source_line", "sink_line", "source_desc",
                 "sink_desc", "var")

    def __init__(self, file, func, source_line, sink_line, source_desc, sink_desc, var):
        self.file, self.func = file, func
        self.source_line, self.sink_line = source_line, sink_line
        self.source_desc, self.sink_desc, self.var = source_desc, sink_desc, var

    def as_dict(self):
        return {"file": self.file, "func": self.func,
                "source_line": self.source_line, "sink_line": self.sink_line,
                "source": self.source_desc, "sink": self.sink_desc, "var": self.var}


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


class _FuncTaint(ast.NodeVisitor):
    """Analyze one function body. source_pred(node)->desc|None marks a source
    expression; sink_pred(callnode)->desc|None marks a sink call whose args are
    inspected for tainted names."""

    def __init__(self, file, func, source_pred, sink_pred, tainted_params):
        self.file, self.func = file, func
        self.source_pred, self.sink_pred = source_pred, sink_pred
        self.tainted = {}  # varname -> (line, desc)
        self.flows = []
        for p in tainted_params:
            self.tainted[p] = (func_lineno(func), f"param {p}")

    def _expr_taint(self, node):
        """Return a (line, desc) if this expression is/contains a tainted value."""
        # a source expression itself
        d = self.source_pred(node)
        if d:
            return (getattr(node, "lineno", 0), d)
        # a reference to a tainted name
        nm = _name_of(node)
        if nm and nm in self.tainted:
            return self.tainted[nm]
        # f-strings / concatenations / calls that carry a tainted subexpr
        for child in ast.iter_child_nodes(node):
            t = self._expr_taint(child)
            if t:
                return t
        return None

    def visit_Assign(self, node):
        t = self._expr_taint(node.value)
        if t:
            for tgt in node.targets:
                nm = _name_of(tgt)
                if nm:
                    self.tainted[nm] = (t[0], t[1])
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        t = self._expr_taint(node.value) or self._expr_taint(node.target)
        if t:
            nm = _name_of(node.target)
            if nm:
                self.tainted[nm] = t
        self.generic_visit(node)

    def visit_Call(self, node):
        sink_desc = self.sink_pred(node)
        if sink_desc:
            # any tainted arg -> a flow
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                t = self._expr_taint(arg)
                if t:
                    self.flows.append(TaintFlow(
                        self.file, self.func, t[0], getattr(node, "lineno", 0),
                        t[1], sink_desc, _name_of(arg) or "<expr>"))
                    break
        self.generic_visit(node)


def func_lineno(func):
    return getattr(func, "lineno", 0)


def analyze_tree(work, source_pred, sink_pred, param_pred,
                 strict=True, label="--work/--tree"):
    """Run the taint engine across all functions in the tree.
    - source_pred(node) -> str|None : is this expr a taint source?
    - sink_pred(callnode) -> str|None : is this call a sink?
    - param_pred(argname) -> bool : is this parameter tainted by name?
    Returns list[TaintFlow].
    """
    flows = []
    for _p, rel, txt in sec.iter_py(work, strict=strict, label=label):
        try:
            tree = ast.parse(txt)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                tainted_params = [a.arg for a in node.args.args if param_pred(a.arg)]
                ft = _FuncTaint(rel, node.name, source_pred, sink_pred, tainted_params)
                for stmt in node.body:
                    ft.visit(stmt)
                flows.extend(ft.flows)
    return flows


# ---- reusable predicate builders (Wave-4 tools compose these) --------------
def call_name_matches(node, names):
    """True if a Call node's callee dotted-name ends with one of `names`."""
    if not isinstance(node, ast.Call):
        return False
    nm = _name_of(node.func) or ""
    return any(nm == n or nm.endswith("." + n) for n in names)


def attr_source(node, roots):
    """A source like request.args / request.json / page.content -- returns desc."""
    nm = _name_of(node) or ""
    for r in roots:
        if nm == r or nm.startswith(r + ".") or nm.endswith("." + r):
            return f"source {nm}"
    return None


def _selftest():
    ok = True

    def src(node):
        # request.* is a source
        return attr_source(node, ["request.args", "request.json", "request.form"])

    def sink(node):
        return "network" if call_name_matches(node, ["get", "post", "urlopen"]) else None

    code = '''
import requests
def handler(user_id):
    url = request.args.get("u")
    x = url + "/path"
    requests.get(x)          # tainted flow: request.args -> requests.get
def safe():
    requests.get("https://fixed.example/")   # no taint
'''
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        b = os.path.join(d, "bulk_downloader")
        os.makedirs(b)
        open(os.path.join(b, "m.py"), "w").write(code)
        flows = analyze_tree(d, src, sink, lambda a: a in ("user_id",))
    got = [(f.func, f.var) for f in flows]
    good = any(f.func == "handler" for f in flows) and not any(f.func == "safe" for f in flows)
    print(("PASS" if good else "FAIL") + f"  taint flow detected in handler, not safe ({got})")
    ok &= good
    # determinism
    with tempfile.TemporaryDirectory() as d:
        b = os.path.join(d, "bulk_downloader")
        os.makedirs(b)
        open(os.path.join(b, "m.py"), "w").write(code)
        f2 = analyze_tree(d, src, sink, lambda a: False)
    print(("PASS" if isinstance(f2, list) else "FAIL") + "  engine returns a list")
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
