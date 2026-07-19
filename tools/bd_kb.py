#!/usr/bin/env python3
"""bd_kb.py — KB Tier-A / A4: the query layer over the generated indexes.

Turns the by-hand audits that misled us this session into one-line queries, reading
ROUTE_INDEX.json (routes×wiring), PIN_INDEX.json (drift-prone pins), and
DEPENDENCY_GRAPH.json (who-imports-what). Stdlib-only; reads the generated JSON (never
imports the app), so it runs identically on stash and in the sandbox.

Subcommands:
    routes [--blueprint B] [--path P] [--unwired] [--kind api|page]
        Query routes. `routes --path /api/library` answers "who owns /api/library?"
        (the Phase-4 plan got this wrong by grep; this reads url_map-derived truth).
    what-pins TERM
        Pins whose value / gating-test / file mentions TERM, plus the coverage map's
        handled-elsewhere note (guard SHAs, route counts) so the answer is complete.
    can-i-retire TARGET
        Verdict (SAFE | BLOCKED) + evidence for retiring a blueprint / module / path:
        routes it owns, files that import it, pins that mention it. BLOCKED iff any exist.

All subcommands accept --json for machine output (the golden-question suite asserts on it).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    p = ROOT / name
    if not p.exists():
        raise SystemExit(f"{name} missing — regenerate it (tools/build_*.py).")
    return json.loads(p.read_text(encoding="utf-8"))


def _route_index():
    return _load("ROUTE_INDEX.json")


def _pin_index():
    return _load("PIN_INDEX.json")


def _dep_graph():
    return _load("DEPENDENCY_GRAPH.json")


# ---- routes -------------------------------------------------------------------

def query_routes(blueprint=None, path=None, unwired=False, kind=None):
    rows = _route_index()["routes"]
    out = []
    for r in rows:
        if blueprint is not None and r["blueprint"] != blueprint:
            continue
        if path is not None and not r["path"].startswith(path):
            continue
        if kind is not None and r["kind"] != kind:
            continue
        if unwired and r.get("spa_wired"):
            continue
        out.append(r)
    return {
        "query": {"blueprint": blueprint, "path": path, "unwired": unwired, "kind": kind},
        "count": len(out),
        "blueprints": sorted({r["blueprint"] for r in out}),
        "routes": out,
    }


# ---- what-pins ----------------------------------------------------------------

def what_pins(term):
    idx = _pin_index()
    t = term.lower()
    hits = []
    for p in idx["pins"]:
        hay = f"{p['file']} {p['gates_what']} {json.dumps(p['value'])}".lower()
        if t in hay:
            hits.append(p)
    return {
        "term": term,
        "count": len(hits),
        "pins": hits,
        "handled_elsewhere": idx.get("coverage", {}).get("handled_elsewhere", {}),
    }


# ---- can-i-retire -------------------------------------------------------------

def _bp_module(target, dep):
    """If `target` is a known blueprint name, return its module path (bulk_downloader/X.py)."""
    bp = dep.get("blueprint", {}).get(target)
    if bp and bp.get("module"):
        return f"bulk_downloader/{bp['module']}"
    return None


def _candidate_module_paths(target, dep):
    """Module paths in the dep graph that plausibly ARE `target`."""
    pkg_in = dep.get("package", {}).get("in", {})
    cands = set()
    bpm = _bp_module(target, dep)
    if bpm:
        cands.add(bpm)
    # direct path / stem matches
    for mod in pkg_in:
        stem = Path(mod).stem
        if mod == target or stem == target or stem == Path(target).stem:
            cands.add(mod)
    return sorted(cands)


def can_i_retire(target):
    ri = _route_index()["routes"]
    pi = _pin_index()["pins"]
    dep = _dep_graph()
    pkg_in = dep.get("package", {}).get("in", {})

    # routes owned: blueprint name match, or file is one of the candidate modules
    mod_paths = _candidate_module_paths(target, dep)
    routes = [r for r in ri
              if r["blueprint"] == target
              or (r.get("file") in mod_paths)]

    # importers: union of package.in for each candidate module
    importers = sorted({imp for m in mod_paths for imp in pkg_in.get(m, [])})

    # pins mentioning target (value, gating test, or file)
    t = target.lower()
    pins = [p for p in pi
            if t in f"{p['file']} {p['gates_what']} {json.dumps(p['value'])}".lower()]

    blocked = bool(routes or importers or pins)
    return {
        "target": target,
        "verdict": "BLOCKED" if blocked else "SAFE",
        "resolved_modules": mod_paths,
        "routes_owned": routes,
        "imported_by": importers,
        "pins_to_update": pins,
        "summary": {
            "routes": len(routes), "importers": len(importers), "pins": len(pins),
        },
    }


# ---- CLI ----------------------------------------------------------------------

def _print_routes(res):
    print(f"{res['count']} route(s); blueprint(s): {', '.join(res['blueprints']) or '—'}")
    for r in res["routes"]:
        wired = "wired" if r.get("spa_wired") else "UNWIRED"
        loc = f"{r.get('file')}:{r.get('line')}" if r.get("file") else "?"
        print(f"  {r['method']:7} {r['path']:55} [{r['blueprint']}] {wired}  {loc}")


def _print_what_pins(res):
    print(f"what-pins '{res['term']}': {res['count']} pin(s)")
    for p in res["pins"]:
        print(f"  [{p['form']}] {p['file']}:{p['line']}  {json.dumps(p['value'])}")
        print(f"      gates: {p['gates_what']}")
    he = res.get("handled_elsewhere") or {}
    if he:
        print("  (handled elsewhere — not in PIN_INDEX:)")
        for k, v in he.items():
            print(f"    - {k}: {v}")


def _print_retire(res):
    print(f"can-i-retire '{res['target']}' -> {res['verdict']}")
    print(f"  resolved modules: {', '.join(res['resolved_modules']) or '—'}")
    s = res["summary"]
    print(f"  evidence: {s['routes']} route(s) owned, {s['importers']} importer(s), "
          f"{s['pins']} pin(s) to update")
    for r in res["routes_owned"][:40]:
        print(f"    route: {r['method']} {r['path']} [{r['blueprint']}]")
    for imp in res["imported_by"][:40]:
        print(f"    imported-by: {imp}")
    for p in res["pins_to_update"][:40]:
        print(f"    pin: {p['file']}:{p['line']} {json.dumps(p['value'])}")
    if res["verdict"] == "SAFE":
        print("  -> no routes, importers, or pins reference it. Safe to retire "
              "(still: rm on stash + clear caches; overlay can't delete).")
    else:
        print("  -> resolve the evidence above before retiring.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bd-kb", description="Query the KB indexes.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("routes")
    pr.add_argument("--blueprint")
    pr.add_argument("--path")
    pr.add_argument("--kind", choices=["api", "page"])
    pr.add_argument("--unwired", action="store_true")

    pw = sub.add_parser("what-pins")
    pw.add_argument("term")

    pc = sub.add_parser("can-i-retire")
    pc.add_argument("target")

    args = ap.parse_args(argv)

    if args.cmd == "routes":
        res = query_routes(args.blueprint, args.path, args.unwired, args.kind)
        printer = _print_routes
    elif args.cmd == "what-pins":
        res = what_pins(args.term)
        printer = _print_what_pins
    else:
        res = can_i_retire(args.target)
        printer = _print_retire

    if args.json:
        json.dump(res, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        printer(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
