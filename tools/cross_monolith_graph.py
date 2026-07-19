#!/usr/bin/env python3
"""cross_monolith_graph.py -- regenerate CROSS_MONOLITH_IMPORT_GRAPH.md.

The decomposition program's load-bearing program-level invariant: the monoliths
(dev_suite, runner, deep_detect, app) form a CYCLIC import graph that only works
because the cyclic edges are LAZY (in-function). A split that hoists a lazy
inter-monolith import to module level creates a real circular import that fails at
load. This tool proves the property and regenerates the doc so it can't decay.

For every bulk_downloader/*.py it AST-classifies each import of a SIBLING package
module as **module-level** (cycle-risk) or **lazy** (inside a function -> safe),
builds the edge graph, computes strongly-connected components on (a) all edges and
(b) module-level-only edges, and asserts: **the module-level-only graph is acyclic**
(every cycle is lazy-broken). Emits a markdown report (stdout).

    python3 tools/cross_monolith_graph.py > CROSS_MONOLITH_IMPORT_GRAPH.md
    python3 tools/cross_monolith_graph.py --root /path --check   # exit 1 if a module-level cycle exists

Read-only, stdlib only.
"""
from __future__ import annotations
import ast, sys
from pathlib import Path

MONOLITHS = ("dev_suite", "runner", "deep_detect", "app")


def _root(argv) -> Path:
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    here = Path(__file__).resolve()
    for up in here.parents:
        if (up / "bulk_downloader" / "app.py").exists():
            return up
    return Path.cwd()


def _pkg_modules(pkg: Path) -> set[str]:
    return {p.stem for p in pkg.glob("*.py") if p.stem != "__init__"}


def _imports(tree: ast.AST, pkg_mods: set[str]):
    """Yield (target_module, is_module_level) for each sibling-package import."""
    # map each node to whether it's inside a function (lazy) -- walk with a func-depth flag
    results = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.fn_depth = 0

        def _emit(self, node):
            lazy = self.fn_depth > 0
            targets = set()
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                lvl = node.level  # 1 == "from ."
                if lvl == 1 and mod == "":            # from . import X
                    for a in node.names:
                        if a.name in pkg_mods:
                            targets.add(a.name)
                elif lvl == 1 and mod in pkg_mods:     # from .X import ...
                    targets.add(mod)
                elif mod == "bulk_downloader":         # from bulk_downloader import X
                    for a in node.names:
                        if a.name in pkg_mods:
                            targets.add(a.name)
                elif mod.startswith("bulk_downloader."):  # from bulk_downloader.X import ...
                    sub = mod.split(".", 1)[1].split(".")[0]
                    if sub in pkg_mods:
                        targets.add(sub)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("bulk_downloader."):
                        sub = a.name.split(".", 1)[1].split(".")[0]
                        if sub in pkg_mods:
                            targets.add(sub)
            for t in targets:
                results.append((t, not lazy))

        def visit_FunctionDef(self, n):
            self.fn_depth += 1; self.generic_visit(n); self.fn_depth -= 1
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ImportFrom(self, n): self._emit(n); self.generic_visit(n)
        def visit_Import(self, n): self._emit(n); self.generic_visit(n)

    V().visit(tree)
    return results


def build(root: Path):
    pkg = root / "bulk_downloader"
    pkg_mods = _pkg_modules(pkg)
    # edges[(src,dst)] = {"module": n, "lazy": n}
    edges: dict[tuple, dict] = {}
    for p in pkg.glob("*.py"):
        src = p.stem
        if src == "__init__":
            continue
        try:
            t = ast.parse(p.read_text(encoding="utf-8"), str(p))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for dst, is_modlevel in _imports(t, pkg_mods):
            if dst == src:
                continue
            e = edges.setdefault((src, dst), {"module": 0, "lazy": 0})
            e["module" if is_modlevel else "lazy"] += 1
    return pkg_mods, edges


def _scc(nodes, adj):
    """Tarjan SCC -> list of components (each a set of nodes)."""
    idx = {}; low = {}; onstack = {}; stack = []; comps = []; counter = [0]

    def strong(v):
        idx[v] = low[v] = counter[0]; counter[0] += 1
        stack.append(v); onstack[v] = True
        for w in adj.get(v, ()):
            if w not in idx:
                strong(w); low[v] = min(low[v], low[w])
            elif onstack.get(w):
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = set()
            while True:
                w = stack.pop(); onstack[w] = False; comp.add(w)
                if w == v:
                    break
            comps.append(comp)

    for v in nodes:
        if v not in idx:
            strong(v)
    return comps


def report(root: Path) -> str:
    pkg_mods, edges = build(root)
    mono = set(MONOLITHS)
    out = []
    out.append("# CROSS-MONOLITH IMPORT GRAPH (generated)\n")
    out.append(f"Generated by `tools/cross_monolith_graph.py` from `{root}`. Do not hand-edit "
               "-- regenerate. Classifies every inter-module import as **module-level** "
               "(cycle-risk) or **lazy** (in-function -> safe).\n")

    # --- the monolith subgraph ---
    out.append("## The four-monolith subgraph\n")
    out.append("| src | dst | module-level | lazy | edge is |")
    out.append("|---|---|---:|---:|---|")
    mono_edges = {k: v for k, v in edges.items() if k[0] in mono and k[1] in mono}
    for (s, d), c in sorted(mono_edges.items(), key=lambda kv: (-(kv[1]["module"] + kv[1]["lazy"]))):
        kind = "**MODULE-LEVEL (hazard if cyclic)**" if c["module"] else "lazy (safe)"
        out.append(f"| {s} | {d} | {c['module']} | {c['lazy']} | {kind} |")
    out.append("")

    # --- cycles, all edges vs module-level-only ---
    adj_all = {}
    adj_mod = {}
    for (s, d), c in edges.items():
        adj_all.setdefault(s, set()).add(d)
        if c["module"] > 0:
            adj_mod.setdefault(s, set()).add(d)

    sccs_all = [c for c in _scc(pkg_mods, adj_all) if len(c) > 1]
    sccs_mod = [c for c in _scc(pkg_mods, adj_mod) if len(c) > 1]
    mono_cycles_all = [c for c in sccs_all if c & mono]
    mono_cycles_mod = [c for c in sccs_mod if c & mono]

    out.append("## Cycles\n")
    out.append(f"- Strongly-connected components (ALL edges) touching a monolith: "
               f"{[sorted(c & mono) or sorted(c) for c in mono_cycles_all] or 'none'}")
    out.append(f"- SCCs in the **module-level-only** graph touching a monolith: "
               f"{[sorted(c) for c in mono_cycles_mod] or 'NONE'}")
    out.append("")
    safe = not mono_cycles_mod
    if safe:
        out.append("**INVARIANT HOLDS:** every monolith import cycle is broken by lazy "
                   "(in-function) imports -- the module-level-only graph among the monoliths "
                   "is **acyclic**. No inter-monolith cycle would fail at load today.\n")
    else:
        out.append("**INVARIANT VIOLATED:** a cycle exists using only MODULE-LEVEL imports: "
                   f"{[sorted(c) for c in mono_cycles_mod]}. This is (or will be) a load-time "
                   "circular import. Make the offending edge lazy.\n")

    # --- the rule ---
    out.append("## The decomposition rule this enforces\n")
    out.append("Every plan's \"keep inter-module imports lazy / absolutize but stay "
               "in-function\" instruction is load-bearing **because of these cycles**. When a "
               "split moves a function carrying a `from . import <sibling>` import:\n")
    out.append("1. transform it to absolute (`from bulk_downloader import <sibling>`), and\n"
               "2. **keep it inside the function** (lazy). Never hoist it to module scope.\n")
    out.append("A hoist that creates a module-level edge closing one of the cycles above fails "
               "at import time. Re-run `--check` from the extracted zip after any split that "
               "touches a monolith; it exits 1 on a module-level cycle.\n")

    # --- module-level inter-monolith edges to watch ---
    ml = {k: v for k, v in mono_edges.items() if v["module"] > 0}
    out.append("## Module-level inter-monolith edges (the watch-list)\n")
    if ml:
        for (s, d), c in sorted(ml.items()):
            out.append(f"- `{s}` imports `{d}` at module level ({c['module']}x) -- safe only "
                       f"while `{d}` does not module-level-import back into `{s}`'s cycle.")
    else:
        out.append("- **none** -- all inter-monolith imports are lazy. The cleanest possible "
                   "state; preserve it.")
    out.append("")
    return "\n".join(out)


def main(argv):
    root = _root(argv)
    doc = report(root)
    if "--check" in argv:
        # exit non-zero if the invariant is violated
        sys.stderr.write(doc)
        sys.exit(1 if "INVARIANT VIOLATED" in doc else 0)
    sys.stdout.write(doc)


if __name__ == "__main__":
    main(sys.argv[1:])
