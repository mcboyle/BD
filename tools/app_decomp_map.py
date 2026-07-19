#!/usr/bin/env python3
"""app_decomp_map.py -- generate APP_DECOMP_MAP.json for the F5.1 decomposition.

THE anti-staleness tool. Membership ("which view moves to which blueprint, and what
private helpers/globals travel with it") is DERIVED from live source every cut --
never hand-maintained. This is the structural antidote to decayed anchors
(the x157/933 class of bug): there is no number to keep up to date by hand.

Usage (from the work tree root, with the bd env):
    python3 tools/app_decomp_map.py                 # writes APP_DECOMP_MAP.json + prints summary
    python3 tools/app_decomp_map.py --json-only     # just write the JSON
    python3 tools/app_decomp_map.py --domain api/notify   # focus one domain

Read-only: parses bulk_downloader/app.py with AST. No app import required (pure
static analysis), so it runs under plain python3 in the sandbox or on stash.

What it emits per view: path, methods, line span, domain bucket, referenced
app-level names (funcs+globals), and a classification (kernel / private / state).
Plus rollups: per-domain what-moves, the two-domain cross-cut helpers, the kernel
set (>=3 domains), the giant sub-clusters, and the shrink/effort model.
"""
from __future__ import annotations
import ast, json, re, sys, collections
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "bulk_downloader" / "app.py"
VERB = {"route", "get", "post", "put", "delete", "patch"}
STATE_SINGLETONS = {"s_cfg", "s_meta", "_app_cfg", "runners"}  # finding 2

# ---- helpers ----------------------------------------------------------------

def _domain(path: str | None) -> str:
    if not path:
        return "(noliteral)"
    segs = [s for s in path.split("/") if s]
    if not segs:
        return "(root)"
    if segs[0] == "api" and len(segs) >= 2:
        return "api/" + re.sub(r"<.*?>", "<>", segs[1])
    return re.sub(r"<.*?>", "<>", segs[0])

def _route_decorators(fn: ast.AST):
    """Yield (verb, path, methods) for each @app.<verb>(...) on a function."""
    for dec in getattr(fn, "decorator_list", []):
        if not isinstance(dec, ast.Call):
            continue
        f = dec.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "app" and f.attr in VERB):
            continue
        path = dec.args[0].value if (dec.args and isinstance(dec.args[0], ast.Constant)) else None
        methods = None
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
        yield f.attr, path, methods

def _is_hook(fn: ast.AST) -> str | None:
    for dec in getattr(fn, "decorator_list", []):
        f = dec.func if isinstance(dec, ast.Call) else dec
        if (isinstance(f, ast.Attribute) and isinstance(getattr(f, "value", None), ast.Name)
                and f.value.id == "app" and f.attr in (
                "before_request", "after_request", "teardown_request",
                "teardown_appcontext", "errorhandler", "context_processor",
                "before_first_request", "template_filter", "template_global")):
            return f.attr
    return None

# ---- build the map ----------------------------------------------------------

def build() -> dict:
    src = APP.read_text(encoding="utf-8")
    tree = ast.parse(src, str(APP))

    top_funcs, top_assigns = {}, set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_funcs[n.name] = (n.lineno, n.end_lineno)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    top_assigns.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            top_assigns.add(n.target.id)
    app_names = (set(top_funcs) | top_assigns) - {"app"}

    fnode = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def refs(name: str) -> set[str]:
        out = set()
        for k in ast.walk(fnode[name]):
            if isinstance(k, ast.Name) and isinstance(k.ctx, ast.Load) and k.id in app_names:
                out.add(k.id)
        return out

    views, hooks, multi = {}, collections.defaultdict(list), []
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        rds = list(_route_decorators(n))
        if rds:
            # one view func may carry several rules; record the union
            paths = [p for _, p, _ in rds]
            verbs = [v for v, _, _ in rds]
            methods = sorted({m for _, _, ms in rds if ms for m in ms})
            views[n.name] = dict(paths=paths, verbs=verbs, methods=methods,
                                 lineno=n.lineno, end=n.end_lineno,
                                 domain=_domain(paths[0]))
            if len(rds) > 1:
                multi.append(n.name)
        hk = _is_hook(n)
        if hk:
            hooks[hk].append(n.name)

    view_refs = {v: sorted(refs(v)) for v in views}

    # name -> set(domains) that reference it
    name_doms = collections.defaultdict(set)
    for v, rs in view_refs.items():
        d = views[v]["domain"]
        for nm in rs:
            name_doms[nm].add(d)

    kernel = {nm: sorted(ds) for nm, ds in name_doms.items()
              if len(ds) >= 3 and nm in top_funcs}
    two_domain = {nm: sorted(ds) for nm, ds in name_doms.items()
                  if len(ds) == 2 and nm in top_funcs}

    # per-domain rollup
    dv = collections.defaultdict(list)
    for v in views:
        dv[views[v]["domain"]].append(v)
    per_domain = {}
    for d, vs in sorted(dv.items()):
        vs = sorted(vs, key=lambda v: views[v]["lineno"])
        private = sorted({nm for v in vs for nm in view_refs[v]
                          if name_doms[nm] == {d} and nm in app_names})
        krefs = sorted({nm for v in vs for nm in view_refs[v] if nm in kernel})
        state = sorted({nm for v in vs for nm in view_refs[v] if nm in STATE_SINGLETONS})
        spans = [views[v]["end"] - views[v]["lineno"] + 1 for v in vs]
        per_domain[d] = dict(
            n_views=len(vs), views=vs,
            line_lo=min(views[v]["lineno"] for v in vs),
            line_hi=max(views[v]["end"] for v in vs),
            contiguous=(max(views[v]["end"] for v in vs) - min(views[v]["lineno"] for v in vs) + 1)
                       <= sum(spans) * 3,   # heuristic flag
            private_travelers=private, kernel_imports=krefs,
            state_singletons=state, total_lines=sum(spans),
        )

    # giant sub-clusters (3rd path segment)
    def giant(prefix):
        c = collections.Counter()
        for v in views:
            for p in views[v]["paths"]:
                s = [x for x in (p or "").split("/") if x]
                if len(s) >= 2 and s[0] == "api" and s[1] == prefix:
                    third = re.sub(r"<.*?>", "<>", s[2]) if len(s) > 2 else "(bare)"
                    c[third] += 1
        return dict(c.most_common())

    # resource clusters: paths served by >1 handler func (cohesion constraint --
    # a path must not be split across owning cuts). Flag if its handlers span >1 domain.
    path_funcs = collections.defaultdict(list)
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for verb, p, ms in _route_decorators(n):
            if p:
                path_funcs[p].append(n.name)
    resource_clusters = {}
    for p, fs in path_funcs.items():
        fs = sorted(set(fs))
        if len(fs) > 1:
            doms = sorted({views[f]["domain"] for f in fs if f in views})
            resource_clusters[p] = {"handlers": fs, "domains": doms,
                                    "split_risk": len(doms) > 1}

    # helper transitive closure: for each helper, the helpers it (transitively) calls.
    helpers = set(top_funcs) - set(views)
    direct = {h: {k.func.id for k in ast.walk(fnode[h])
                  if isinstance(k, ast.Call) and isinstance(k.func, ast.Name)
                  and k.func.id in helpers and k.func.id != h} for h in helpers}
    def _closure(h, seen=None):
        seen = seen or set()
        for c in direct.get(h, ()):
            if c not in seen:
                seen.add(c); _closure(c, seen)
        return seen
    helper_closure = {h: sorted(_closure(h)) for h in helpers if direct.get(h)}

    # shrink model
    view_lines = sum(views[v]["end"] - views[v]["lineno"] + 1 for v in views)
    helper_lines = sum(e - s + 1 for nm, (s, e) in top_funcs.items() if nm not in views)
    total = len(src.splitlines())

    return {
        "source": str(APP), "app_py_lines": total,
        "counts": {"top_funcs": len(top_funcs), "views": len(views),
                   "helpers": len(top_funcs) - len(views), "globals": len(top_assigns)},
        "hooks": {k: v for k, v in hooks.items()},
        "multi_decorator_views": multi,
        "state_singletons": sorted(STATE_SINGLETONS),
        "kernel_shared_ge3_domains": kernel,
        "two_domain_helpers": two_domain,
        "per_domain": per_domain,
        "giants": {"api/dev": giant("dev"), "api/sites": giant("sites")},
        "resource_clusters": resource_clusters,
        "resource_clusters_split_risk": [p for p, c in resource_clusters.items() if c["split_risk"]],
        "helper_transitive_closure": helper_closure,
        "shrink_model": {"view_lines": view_lines, "helper_lines": helper_lines,
                         "movable_pct": round(100 * (view_lines + helper_lines) / total, 1)},
    }

# ---- cli --------------------------------------------------------------------

def main(argv):
    m = build()
    out = APP.parent.parent / "APP_DECOMP_MAP.json"
    out.write_text(json.dumps(m, indent=1, sort_keys=False), encoding="utf-8")
    if "--json-only" in argv:
        print(f"wrote {out}")
        return
    c = m["counts"]
    print(f"app.py: {m['app_py_lines']} lines | {c['views']} views + {c['helpers']} helpers "
          f"+ {c['globals']} globals | movable {m['shrink_model']['movable_pct']}%")
    print(f"hooks (stay app-level): {sum(len(v) for v in m['hooks'].values())} "
          f"({', '.join(f'{k}:{len(v)}' for k,v in m['hooks'].items())})")
    print(f"kernel-shared helpers (>=3 domains): "
          f"{', '.join(m['kernel_shared_ge3_domains'])}")
    print(f"two-domain (cross-cut) helpers: {', '.join(m['two_domain_helpers'])}")
    print(f"resource clusters (same-path multi-handler): {len(m['resource_clusters'])} "
          f"| split-across-domains RISK: {m['resource_clusters_split_risk'] or 'none'}")
    foc = next((a for a in argv if a.startswith("api/")), None)
    if foc and foc in m["per_domain"]:
        import pprint; pprint.pprint({foc: m["per_domain"][foc]})
    else:
        print(f"\n{len(m['per_domain'])} domains. Giants: "
              f"api/dev={sum(m['giants']['api/dev'].values())}, "
              f"api/sites={sum(m['giants']['api/sites'].values())}.")
    print(f"wrote {out}")

if __name__ == "__main__":
    main(sys.argv[1:])
