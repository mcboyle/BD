#!/usr/bin/env python3
"""runner_api_snapshot -- the method-inventory + import-surface invariant gate for
the runner.py decomposition (sibling to route_map_snapshot for app.py).

AST-only, stdlib-only -- never imports the package, so it runs in any environment
(sandbox or stash) and is immune to missing runtime deps. It models Python's MRO by
reading SiteRunner's bases and following the `from .runner_<x> import <Mixin>`
imports to the local mixin modules.

  python3 tools/runner_api_snapshot.py --write kb/runner_api_snapshot.json
  python3 tools/runner_api_snapshot.py --check kb/runner_api_snapshot.json   # exit 1 on drift

A cut is invariant-clean iff --check passes: the SiteRunner method SET and each
method's KIND (instance/static/class/property) are unchanged, no method resolves
from more than one class (MRO ambiguity), and the module export surface is intact.
The OWNER of a method is allowed to move (mixin extraction is the point); only the
set/kind/uniqueness/exports are frozen.
"""
import ast, json, os, sys

PKG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "bulk_downloader")
RUNNER = os.path.join(PKG_DIR, "runner.py")

# external symbols imported from bulk_downloader.runner elsewhere in the tree.
# (Frozen here so a dropped re-export shim is caught even if no test imports it.)
# Derived from a tree-wide scan of `from .runner import ...` + `runner.<attr>` access
# (app.py, admission.py, perf_lab.py, dev_suite.py, tests). Several of these move to
# runner_util.py during the decomposition and MUST be re-exported from runner.py.
REQUIRED_EXPORTS = {
    "SiteRunner",                       # app.py, dev_suite, tests
    "_ts",                              # app.py
    "set_global_concurrent_cap",       # app.py  (stays in runner.py -- worker-coupled)
    "get_bandwidth_history",           # app.py  (-> runner_util)
    "_check_video_magic_bytes",        # tests   (-> runner_util)
    "disk_free_gb",                    # admission.py (imported INTO runner, re-exported)
    "gate_candidate_url",              # (-> runner_util)
    "resolve_url_attribute",           # (-> runner_util)
    "DEFAULT_MAX_CONCURRENT",          # external const (stays core)
    "DEFAULT_MIN_RESOLUTION",          # external const (-> runner_util, mixin-shared)
    "_BD_TO_APPRISE_EVENT",            # external const dict (-> runner_util)
    "_bw_history",                     # perf_lab reads runner._bw_history (-> runner_util)
}


def _kind(node):
    decos = {d.id for d in node.decorator_list if isinstance(d, ast.Name)}
    if "staticmethod" in decos:
        return "static"
    if "classmethod" in decos:
        return "class"
    if "property" in decos:
        return "property"
    return "instance"


def _class_methods(classnode):
    out = {}
    for b in classnode.body:
        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[b.name] = _kind(b)
    return out


def _parse(path):
    return ast.parse(open(path, encoding="utf-8").read(), filename=path)


def derive():
    tree = _parse(RUNNER)
    module_funcs = set()
    module_classes = set()
    bound_at_module = set()    # every name importable from bulk_downloader.runner
    siterunner = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            module_funcs.add(node.name)
            bound_at_module.add(node.name)
        elif isinstance(node, ast.ClassDef):
            module_classes.add(node.name)
            bound_at_module.add(node.name)
            if node.name == "SiteRunner":
                siterunner = node
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound_at_module.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                bound_at_module.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bound_at_module.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound_at_module.add(node.target.id)
    if siterunner is None:
        sys.exit("FATAL: class SiteRunner not found in runner.py")

    # MRO order = [SiteRunner, *bases left-to-right] (single-inheritance mixins, depth 1)
    base_names = [b.id for b in siterunner.bases if isinstance(b, ast.Name)]
    classes_in_mro = [("SiteRunner", _class_methods(siterunner))]

    # locate each base mixin via its import: from .runner_<x> import <MixinName>
    import_src = {}            # MixinName -> module stem (runner_x)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module \
                and "runner_" in (node.module or ""):
            stem = node.module.split(".")[-1]
            for a in node.names:
                import_src[a.asname or a.name] = stem
    for bn in base_names:
        stem = import_src.get(bn)
        methods = {}
        if stem:
            mpath = os.path.join(PKG_DIR, stem + ".py")
            if os.path.exists(mpath):
                mtree = _parse(mpath)
                for n in mtree.body:
                    if isinstance(n, ast.ClassDef) and n.name == bn:
                        methods = _class_methods(n)
        classes_in_mro.append((bn, methods))

    # resolve: first class in MRO that defines the name wins; track all definers
    definers = {}              # name -> [classnames]
    kinds = {}                 # name -> kind (from the winning/most-derived class)
    for cls, meths in classes_in_mro:
        for name, kind in meths.items():
            definers.setdefault(name, []).append(cls)
            if name not in kinds:
                kinds[name] = kind   # MRO-leftmost wins
    duplicates = {n: cs for n, cs in definers.items() if len(cs) > 1}
    resolved = {n: {"owner": cs[0], "kind": kinds[n]} for n, cs in definers.items()}

    # export surface = the required public names that are actually importable from
    # runner (module funcs/classes or imported at module level). Tracking only the
    # required set keeps the snapshot stable (no churn from stdlib imports).
    exports = sorted((REQUIRED_EXPORTS | {"SiteRunner"}) & (bound_at_module | {"SiteRunner"}))
    return {
        "schema": 1,
        "bases": base_names,
        "method_count": len(resolved),
        "methods": dict(sorted(resolved.items())),
        "duplicates": duplicates,
        "module_funcs": sorted(module_funcs),
        "exports": exports,
        "missing_required_exports": sorted(REQUIRED_EXPORTS - bound_at_module),
    }


def _diff(base, cur):
    fails = []
    bm, cm = base["methods"], cur["methods"]
    added = sorted(set(cm) - set(bm))
    removed = sorted(set(bm) - set(cm))
    if added:
        fails.append(f"METHODS ADDED (set changed): {added}")
    if removed:
        fails.append(f"METHODS REMOVED (set changed): {removed}")
    kind_changed = sorted(n for n in set(bm) & set(cm)
                          if bm[n]["kind"] != cm[n]["kind"])
    if kind_changed:
        fails.append("METHOD KIND CHANGED: "
                     + ", ".join(f"{n} {bm[n]['kind']}->{cm[n]['kind']}"
                                 for n in kind_changed))
    if cur["duplicates"]:
        fails.append("MRO DUPLICATION (name defined in >1 class): "
                     + json.dumps(cur["duplicates"]))
    miss_export = sorted(set(base["exports"]) - set(cur["exports"]))
    if miss_export:
        fails.append(f"EXPORTS DROPPED (missing re-export shim?): {miss_export}")
    miss_required = cur.get("missing_required_exports", [])
    if miss_required:
        fails.append(f"REQUIRED EXPORTS MISSING (no re-export shim): {miss_required}")
    # informational: owners that moved (the intended effect)
    moved = [(n, bm[n]["owner"], cm[n]["owner"]) for n in set(bm) & set(cm)
             if bm[n]["owner"] != cm[n]["owner"]]
    return fails, moved


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("--write", "--check"):
        sys.exit("usage: runner_api_snapshot.py --write|--check <json>")
    mode, path = sys.argv[1], sys.argv[2]
    cur = derive()
    if mode == "--write":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        json.dump(cur, open(path, "w"), indent=1)
        print(f"WROTE {path}: {cur['method_count']} methods, "
              f"{len(cur['exports'])} exports, bases={cur['bases'] or '[]'}, "
              f"duplicates={len(cur['duplicates'])}")
        return
    base = json.load(open(path))
    fails, moved = _diff(base, cur)
    print(f"baseline {base['method_count']} methods / {len(base['exports'])} exports "
          f"vs current {cur['method_count']} / {len(cur['exports'])}; "
          f"bases now {cur['bases'] or '[]'}")
    if moved:
        print(f"  owners moved (intended): {len(moved)} method(s) "
              f"-> {sorted({m[2] for m in moved})}")
    if fails:
        print("RESULT: FAIL")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("RESULT: PASS -- method set + kinds + exports intact, no MRO duplication.")


if __name__ == "__main__":
    main()
