#!/usr/bin/env python3
"""dependency_graph.py — internal dependency graph (A3-W2).

Generates DEPENDENCY_GRAPH.json (queryable, bidirectional) and
DEPENDENCY_GRAPH.md (human summary) from a pure-AST walk of
bulk_downloader/ + tools/. No Flask import (function-index-class, ~0.3s).

Four sub-graphs:
  package    — module import adjacency (out: imports / in: imported-by)
  tool       — tool coverage (which tools have internal / tool->package edges)
  blueprint  — each Flask blueprint's module, owned routes, provider modules
  config     — each config store's reader + writer modules

Three idioms that defeat naive AST extraction are handled explicitly
(this is the whole reason the tool exists — the prior reference graph in
dependency_inventory.py undercounts internal edges ~2.6x by missing #2):
  P1  ternary-guarded defs/decorators:
        x = Blueprint(...) if Flask else None
        @bp.route(...) if bp else (lambda f: f)
      -> unwrap ast.IfExp.body before inspecting the Call.
  P2  package-relative submodule import:
        from . import global_config        (ImportFrom.module is None)
      -> each alias name is a submodule edge (naive `if node.module` drops it).
  P3  verb_noun mutation methods + import alias:
        from . import vpn_config as VC ; VC.update_tunnel_config(...)
      -> resolve aliases, AST-match Call <alias>.<verb|verb_*>(...).

Usage:
    python tools/dependency_graph.py            # regen both artifacts at repo root
    python tools/dependency_graph.py --check     # diff regen vs on-disk; exit 1 on drift
    python tools/dependency_graph.py --json       # print JSON to stdout
    python tools/dependency_graph.py --selftest   # P1/P2/P3 + reconciliation assertions
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import sys
from collections import defaultdict, Counter
from pathlib import Path

# Bumped whenever the emitted schema/format changes; the in-sync test pins it.
_GRAPH_VERSION = "1"

_MUT = ("save", "update", "store", "set", "write", "delete", "create",
        "persist", "disable", "enable", "remove", "add", "put")
_CONFIG_STORES = ["global_config", "vpn_config", "widgets_config",
                  "site_editor", "app_settings_center"]
_ROUTE_VERBS = ("route", "get", "post", "put", "delete", "patch")


def _root() -> Path:
    return Path(os.environ.get("BD_ROOT", ".")).resolve()


def _bd_mods(root: Path):
    return {p.stem for p in (root / "bulk_downloader").glob("*.py")}


def _tool_stems(root: Path):
    return {p.stem for p in (root / "tools").glob("*.py")}


def _node(stem: str, bd_mods, tool_stems):
    if stem in bd_mods:
        return f"bulk_downloader/{stem}.py"
    if stem in tool_stems:
        return f"tools/{stem}.py"
    return None


def _parse(p: Path):
    try:
        return ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None


def _unwrap(v):
    """P1: a ternary `Call if cond else other` exposes its Call as .body."""
    return v.body if isinstance(v, ast.IfExp) else v


def _internal_imports(tree, bd_mods, tool_stems):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.module:                                   # from X import ... / from .X import ...
                b = n.module.split(".")
                cand = (b[1] if b[0] in ("bulk_downloader", "tools")
                        and len(b) > 1 else b[0])
                if (x := _node(cand, bd_mods, tool_stems)):
                    out.add(x)
            else:                                          # P2: from . import a, b
                for a in n.names:
                    if (x := _node(a.name.split(".")[0], bd_mods, tool_stems)):
                        out.add(x)
        elif isinstance(n, ast.Import):
            for a in n.names:
                b = a.name.split(".")
                cand = (b[1] if b[0] in ("bulk_downloader", "tools")
                        and len(b) > 1 else b[0])
                if (x := _node(cand, bd_mods, tool_stems)):
                    out.add(x)
    return out


def _aliases(tree):
    """P3 support: local alias -> imported stem."""
    al = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module is None:
            for a in n.names:
                al[a.asname or a.name] = a.name.split(".")[0]
        elif isinstance(n, ast.Import):
            for a in n.names:
                b = a.name.split(".")
                al[a.asname or b[-1]] = b[-1]
    return al


def _py_files(root: Path):
    for r in ("bulk_downloader", "tools"):
        for dp, _, names in os.walk(root / r):
            if "__pycache__" in dp:
                continue
            for nm in sorted(names):
                if nm.endswith(".py"):
                    yield Path(dp) / nm


def build(root: Path | None = None) -> dict:
    root = root or _root()
    bd_mods, tool_stems = _bd_mods(root), _tool_stems(root)

    out_e, in_e = defaultdict(set), defaultdict(set)
    blueprints = {}
    config = {s: {"readers": set(), "writers": set()} for s in _CONFIG_STORES}

    for p in _py_files(root):
        tree = _parse(p)
        if tree is None:
            continue
        rp = str(p.relative_to(root))

        # package/tool edges
        imps = _internal_imports(tree, bd_mods, tool_stems)
        for nid in imps:
            if nid != rp:
                out_e[rp].add(nid)
                in_e[nid].add(rp)

        # blueprint graph (bulk_downloader only)
        if p.parent.name == "bulk_downloader":
            bp_vars = {}
            for n in ast.walk(tree):
                if isinstance(n, ast.Assign):
                    v = _unwrap(n.value)                    # P1
                    if isinstance(v, ast.Call):
                        fn = getattr(v.func, "id", None) or getattr(v.func, "attr", None)
                        if (fn == "Blueprint" and n.targets
                                and isinstance(n.targets[0], ast.Name)):
                            nm = (v.args[0].value if v.args
                                  and isinstance(v.args[0], ast.Constant) else None)
                            bp_vars[n.targets[0].id] = nm
            if bp_vars:
                routes = defaultdict(set)
                for n in ast.walk(tree):
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for dec in n.decorator_list:
                            d = _unwrap(dec)                # P1
                            if (isinstance(d, ast.Call)
                                    and isinstance(d.func, ast.Attribute)):
                                obj = getattr(d.func.value, "id", None)
                                if obj in bp_vars and d.func.attr in _ROUTE_VERBS:
                                    path = (d.args[0].value if d.args
                                            and isinstance(d.args[0], ast.Constant) else "?")
                                    routes[obj].add((d.func.attr.upper(), path))
                prov = sorted(imps - {rp})
                for var, nm in bp_vars.items():
                    key = nm or var
                    rs = sorted(routes.get(var, set()))
                    blueprints[key] = {
                        "module": p.name, "var": var,
                        "routes": [f"{m} {pth}" for m, pth in rs],
                        "route_count": len(rs),
                        "providers": prov,
                    }

        # config graph (P3)
        al = _aliases(tree)
        local_for = {ln: st for ln, st in al.items() if st in _CONFIG_STORES}
        for s in _CONFIG_STORES:
            local_for.setdefault(s, s)
        txt = p.read_text(encoding="utf-8", errors="replace")
        for ln, st in local_for.items():
            if re.search(rf"\b{re.escape(ln)}\b", txt):
                config[st]["readers"].add(rp)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                base = getattr(n.func.value, "id", None)
                if base in local_for and any(
                        n.func.attr == m or n.func.attr.startswith(m + "_")
                        for m in _MUT):
                    config[local_for[base]]["writers"].add(rp)

    tool_files = [f"tools/{p.name}" for p in sorted((root / "tools").glob("*.py"))]
    return {
        "graph_version": _GRAPH_VERSION,
        "package": {
            "out": {k: sorted(v) for k, v in sorted(out_e.items())},
            "in": {k: sorted(v) for k, v in sorted(in_e.items())},
            "edge_count": sum(len(v) for v in out_e.values()),
        },
        "tool": {
            "total": len(tool_files),
            "with_edges": sorted(t for t in tool_files if out_e.get(t)),
            "tool_to_pkg": sorted(
                t for t in tool_files
                if any(e.startswith("bulk_downloader/") for e in out_e.get(t, ()))),
        },
        "blueprint": dict(sorted(blueprints.items())),
        "config": {s: {"readers": sorted(v["readers"]),
                       "writers": sorted(v["writers"])}
                   for s, v in sorted(config.items())},
    }


def render_json(g: dict) -> str:
    return json.dumps(g, indent=2, sort_keys=True) + "\n"


def render_md(g: dict) -> str:
    pkg, tool = g["package"], g["tool"]
    indeg = Counter({k: len(v) for k, v in pkg["in"].items()})
    L = [
        "# DEPENDENCY_GRAPH",
        "",
        "Auto-generated by `tools/dependency_graph.py` (A3). DO NOT EDIT BY",
        "HAND — `tests/test_dependency_graph_in_sync.py` fails the build on",
        "drift. Regenerate with:",
        "",
        "    python tools/dependency_graph.py",
        "",
        "Pure-AST; handles ternary-guarded blueprints/decorators, "
        "`from . import X`, and verb_noun config writers (see module docstring).",
        "",
        f"Graph version: {g['graph_version']}",
        "",
        f"- internal import edges: **{pkg['edge_count']}**",
        f"- tools: {tool['total']} · with internal edge: {len(tool['with_edges'])} "
        f"· with tool→package edge: {len(tool['tool_to_pkg'])}",
        f"- blueprints: {len(g['blueprint'])} · config stores: {len(g['config'])}",
        "",
        "## Most-imported modules (coupling hotspots)",
        "",
    ]
    for nid, n in sorted(indeg.most_common(20), key=lambda x: (-x[1], x[0])):
        L.append(f"- `{nid}`: {n}")
    L += ["", "## Blueprints → providers", ""]
    for name, b in g["blueprint"].items():
        L.append(f"- **{name}** (`{b['module']}`) — routes {b['route_count']}, "
                 f"providers {len(b['providers'])}")
    L += ["", "## Config stores → reader / writer modules", ""]
    for store, c in g["config"].items():
        L.append(f"- **{store}** — readers {len(c['readers'])}, "
                 f"writers {len(c['writers'])}")
    return "\n".join(L) + "\n"


def _json_path(root: Path) -> Path:
    return root / "DEPENDENCY_GRAPH.json"


def _md_path(root: Path) -> Path:
    return root / "DEPENDENCY_GRAPH.md"


def _write_if_changed(content: str, path: Path) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def _diff_text(have: str, want: str, label: str) -> str:
    diff = list(difflib.unified_diff(
        have.splitlines(keepends=True), want.splitlines(keepends=True),
        fromfile=f"{label} (on-disk)", tofile=f"{label} (regenerated)"))
    if len(diff) > 60:
        diff = diff[:60] + [f"... (+{len(diff) - 60} more lines)\n"]
    return "".join(diff)


def selftest(g: dict, root: Path):
    res = []
    ec = g["package"]["edge_count"]
    db = len(g["package"]["in"].get("bulk_downloader/db.py", []))
    res.append(("P2 edges >= 600 (vs ~267 naive)", ec >= 600, f"edges={ec}"))
    res.append(("P2 db.py in-degree >= 50 (vs 12 naive)", db >= 50, f"db_in={db}"))
    nbp = len(g["blueprint"])
    grc = {b: g["blueprint"].get(b, {}).get("route_count", 0)
           for b in ("vpn_api", "widgets_api")}
    res.append(("P1 blueprints detected == 10", nbp == 10, f"n={nbp}"))
    res.append(("P1 guarded blueprints routes>0", all(v > 0 for v in grc.values()),
                str(grc)))
    vw = len(g["config"]["vpn_config"]["writers"])
    res.append(("P3 vpn_config writers > 0", vw > 0, f"writers={vw}"))
    cat = root / "ENDPOINT_CATALOG.md"
    if cat.exists():
        ct = cat.read_text(encoding="utf-8", errors="replace")
        vpn_cat = len(re.findall(r"^(GET|POST|PUT|DELETE|PATCH)\s+/api/vpn/", ct, re.M))
        vpn_g = g["blueprint"].get("vpn_api", {}).get("route_count", 0)
        res.append(("reconcile vpn graph ~ catalog /api/vpn",
                    vpn_g > 0 and vpn_g >= vpn_cat * 0.5,
                    f"graph={vpn_g} catalog={vpn_cat}"))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="diff regenerated artifacts against on-disk; exit 1 on drift")
    ap.add_argument("--json", action="store_true", help="print JSON to stdout")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    root = _root()
    g = build(root)

    if a.selftest:
        ok = True
        for name, passed, detail in selftest(g, root):
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}  ({detail})")
            ok &= passed
        print("RESULT:", "ALL PASS" if ok else "FAILURES")
        return 0 if ok else 1

    if a.json:
        sys.stdout.write(render_json(g))
        return 0

    js, md = render_json(g), render_md(g)
    jp, mp = _json_path(root), _md_path(root)

    if a.check:
        ok = True
        for content, path in ((js, jp), (md, mp)):
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            if existing != content:
                print(f"FAIL: {path.name} drift detected.", file=sys.stderr)
                print(_diff_text(existing, content, path.name), file=sys.stderr)
                ok = False
        if ok:
            print(f"OK: dependency graph in sync (edges={g['package']['edge_count']})")
            return 0
        print("Run `python tools/dependency_graph.py` to fix.", file=sys.stderr)
        return 1

    wrote = [p.name for c, p in ((js, jp), (md, mp)) if _write_if_changed(c, p)]
    print(f"WROTE: {wrote}" if wrote else "OK: artifacts already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
