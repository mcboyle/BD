#!/usr/bin/env python3
"""graph_build -- resolve call edges + materialize the §3-§12 projections.

Reads KNOWLEDGE_GRAPH.db (populated by l0_extract), resolves intra-repo call
edges (callee name -> defining function node), adds `call_resolved` edges, and
emits the deterministic JSON projections that the audit sessions consume:
  CALL_GRAPH.json · MODULE_CATALOG.json · SECURITY_SURFACE.json ·
  ERROR_CATALOG.json · TAINT_MAP.json · DEAD_CODE.json

Mechanical fields only -- the L2 (purpose/data_flow) are left null for the audit
sessions to fill (SCHEMAS §3 gate checks presence, not value). stdlib + offline.

Usage:  python3 graph_build.py [--db DB] [--outdir DIR]
"""
import argparse
import json
import os
import sqlite3
from collections import defaultdict

SCHEMA = 1
AUTH_DECOS = ("require", "login_required", "auth", "csrf", "admin", "bearer")
WRITE_ROUTE_DECOS = ("post", "put", "patch", "delete")


def _dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def content_hash(db):
    """Deterministic CONTENT digest of the graph (P2).

    The raw KNOWLEDGE_GRAPH.db file hash is fragile -- SQLite re-lays pages on
    VACUUM / re-save, so `sha256sum the .db` drifts even when the graph is
    identical (the pilot saw f02b vs pinned 79f2). This hashes the *logical*
    content instead: every node and edge row, canonicalized (sorted, stable
    field order), so a content-preserving re-save yields the SAME hash and a
    real node/edge change yields a different one. This is what KNOWLEDGE_GRAPH.
    db.sha256 pins; check_hash() recomputes and compares it.
    """
    import hashlib
    cx = sqlite3.connect(db)
    try:
        nodes = sorted(
            cx.execute("SELECT id,kind,path,qualname,span,sha256,lines,meta_json "
                       "FROM nodes").fetchall())
        edges = sorted(
            cx.execute("SELECT src,dst,kind FROM edges").fetchall())
    finally:
        cx.close()
    h = hashlib.sha256()
    h.update(b"nodes\x00")
    for row in nodes:
        h.update(("\x1f".join("" if c is None else str(c) for c in row)).encode())
        h.update(b"\x1e")
    h.update(b"edges\x00")
    for row in edges:
        h.update(("\x1f".join("" if c is None else str(c) for c in row)).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def check_hash(db, pin_path):
    """Recompute-and-compare (P2). Read the pinned content hash from pin_path,
    recompute content_hash(db), and return 0 iff they match (else 1, printing the
    mismatch). A missing pin is a hard fail -- the pin is the point."""
    if not os.path.exists(pin_path):
        print(f"graph check-hash: FAIL -- pin {pin_path} absent")
        return 1
    want = open(pin_path).read().strip()
    got = content_hash(db)
    if got == want:
        print(f"graph check-hash: OK -- content hash matches pin ({got[:16]}...)")
        return 0
    print(f"graph check-hash: FAIL -- content hash {got[:16]}... != pin "
          f"{want[:16]}... (graph content changed; re-pin with --write-hash if intended)")
    return 1


def write_hash(db, pin_path):
    """Write the current content hash to pin_path (deliberate re-pin)."""
    h = content_hash(db)
    with open(pin_path, "w") as f:
        f.write(h + "\n")
    print(f"graph write-hash: wrote {h[:16]}... -> {pin_path}")
    return 0


def load(db):
    c = sqlite3.connect(db)
    mods = {}
    fns = {}
    for fid, path, qual, span, lines, mj in c.execute(
            "SELECT id,path,qualname,span,lines,meta_json FROM nodes WHERE kind='function'"):
        fns[fid] = {"path": path, "qual": qual, "span": span, "lines": lines,
                    "meta": json.loads(mj)}
    for mid, path, mj in c.execute(
            "SELECT id,path,meta_json FROM nodes WHERE kind='module'"):
        mods[mid] = {"path": path, "meta": json.loads(mj)}
    calls = [(s, d) for s, d, in c.execute(
        "SELECT src,dst FROM edges WHERE kind='call'")]
    contains = defaultdict(list)
    for s, d in c.execute("SELECT src,dst FROM edges WHERE kind='contains'"):
        contains[s].append(d)
    c.close()
    return mods, fns, calls, contains


def resolve_calls(fns, calls):
    """Map a bare/dotted callee name to candidate function nodes by last segment."""
    by_last = defaultdict(list)
    for fid, f in fns.items():
        last = f["qual"].split(".")[-1]
        by_last[last].append(fid)
    edges = []
    unresolved = []
    seen = set()
    for src, name in calls:
        last = name.split(".")[-1]
        cands = by_last.get(last, [])
        if len(cands) == 1:
            key = (src, cands[0])
            if key not in seen:
                seen.add(key)
                edges.append({"from": src, "to": cands[0], "kind": "call"})
        elif len(cands) == 0:
            unresolved.append({"from": src, "name": name, "reason": "missing"})
        else:
            # ambiguous: record but don't fabricate a single edge
            unresolved.append({"from": src, "name": name, "reason": "ambiguous"})
    return edges, unresolved


def build(db, outdir):
    os.makedirs(outdir, exist_ok=True)
    mods, fns, calls, contains = load(db)
    resolved, unresolved = resolve_calls(fns, calls)

    # --- CALL_GRAPH.json ---
    _dump({"schema": SCHEMA,
           "nodes": sorted(fns.keys()),
           "edges": sorted((e["from"], e["to"]) for e in resolved) and
                    [{"from": a, "to": b, "kind": "call"} for a, b in
                     sorted({(e["from"], e["to"]) for e in resolved})],
           "unresolved_count": len(unresolved)},
          os.path.join(outdir, "CALL_GRAPH.json"))

    # incoming-edge count per fn (for dead-code + risk)
    indeg = defaultdict(int)
    for e in resolved:
        indeg[e["to"]] += 1

    # --- SECURITY_SURFACE.json ---
    auth_gates, secret_sites, sql_sites, subp_sites, path_sinks = [], [], [], [], []
    for fid, f in fns.items():
        meta = f["meta"]
        decos = [d.lower() for d in meta.get("decorators", [])]
        if any(k in d for d in decos for k in AUTH_DECOS):
            auth_gates.append({"name": f["qual"], "at": f"{f['path']}:{f['span']}",
                               "decorators": meta.get("decorators", [])})
        for s in meta.get("secrets", []):
            secret_sites.append({"field": s, "op": "read",
                                 "at": f"{f['path']}:{f['span']}", "masked": None})
        for sink in meta.get("sinks", []):
            at = f"{f['path']}:{sink['at']}"
            if sink["kind"] in ("sql", "sql_fstring"):
                sql_sites.append({"at": at, "fstring": sink["kind"] == "sql_fstring",
                                  "parametrized": None})
            elif sink["kind"] == "subprocess":
                subp_sites.append({"at": at, "shell": sink.get("shell", False)})
            elif sink["kind"] == "path":
                path_sinks.append({"at": at, "allowlisted": None})
    _dump({"schema": SCHEMA,
           "auth_gates": sorted(auth_gates, key=lambda x: x["at"]),
           "secret_sites": sorted(secret_sites, key=lambda x: x["at"]),
           "sql_sites": sorted(sql_sites, key=lambda x: x["at"]),
           "subprocess_sites": sorted(subp_sites, key=lambda x: x["at"]),
           "path_sinks": sorted(path_sinks, key=lambda x: x["at"]),
           "totals": {"auth_gates": len(auth_gates), "secret_sites": len(secret_sites),
                      "sql_sites": len(sql_sites), "sql_fstring": sum(
                          1 for s in sql_sites if s["fstring"]),
                      "subprocess_sites": len(subp_sites),
                      "shell_true": sum(1 for s in subp_sites if s["shell"]),
                      "path_sinks": len(path_sinks)}},
          os.path.join(outdir, "SECURITY_SURFACE.json"))

    # --- ERROR_CATALOG.json (raise shapes; status mapping is L2/audit) ---
    handlers = []
    for fid, f in fns.items():
        raises = [r for r in f["meta"].get("raises", []) if r]
        if raises:
            handlers.append({"at": f"{f['path']}:{f['span']}", "fn": f["qual"],
                             "raises": sorted(set(raises)), "maps_to": None,
                             "expected": None, "ok": None})
    _dump({"schema": SCHEMA, "handlers": sorted(handlers, key=lambda x: x["at"])},
          os.path.join(outdir, "ERROR_CATALOG.json"))

    # --- TAINT_MAP.json (sink inventory; full source->sink paths are audit L2) ---
    sinks = []
    for fid, f in fns.items():
        for s in f["meta"].get("sinks", []):
            sinks.append({"id": f"{f['path']}:{s['at']}", "kind": s["kind"],
                          "at": f"{f['path']}:{s['at']}", "in_fn": f["qual"]})
    _dump({"schema": SCHEMA, "sources": [], "sinks": sorted(sinks, key=lambda x: x["id"]),
           "paths": [], "note": "sink inventory; source->sink path tracing is audit-L2 (per-batch)"},
          os.path.join(outdir, "TAINT_MAP.json"))

    # --- DEAD_CODE.json (uncalled intra-repo fns; vulture confirms separately) ---
    uncalled = []
    for fid, f in fns.items():
        last = f["qual"].split(".")[-1]
        # skip obvious framework/dunder/entrypoints
        if last.startswith("__") or last in ("main",) or "." in f["qual"]:
            continue  # methods (dotted) often dispatched dynamically
        if indeg.get(fid, 0) == 0:
            uncalled.append({"fn": fid, "confidence": 0.5,
                             "note": "no resolved intra-repo caller (may be route/CLI/dynamic)"})
    _dump({"schema": SCHEMA,
           "uncalled": sorted(uncalled, key=lambda x: x["fn"])[:2000],
           "uncalled_total": len(uncalled),
           "unreachable_routes": []},
          os.path.join(outdir, "DEAD_CODE.json"))

    # --- MODULE_CATALOG.json (mechanical; L2 fields null for audit) ---
    catalog = {}
    for mid, m in mods.items():
        mfns = contains.get(mid, [])
        sinks_k = sorted({s["kind"] for fid in mfns if fid in fns
                          for s in fns[fid]["meta"].get("sinks", [])})
        secrets = sorted({s for fid in mfns if fid in fns
                          for s in fns[fid]["meta"].get("secrets", [])})
        public = [{"name": fns[fid]["qual"],
                   "signature": "(" + ", ".join(fns[fid]["meta"].get("args", [])) + ")",
                   "raises": sorted({r for r in fns[fid]["meta"].get("raises", []) if r})}
                  for fid in sorted(mfns) if fid in fns
                  and "." not in fns[fid]["qual"]
                  and not fns[fid]["qual"].startswith("_")]
        catalog[m["path"]] = {
            "purpose": None, "data_flow": None,           # L2 (audit fills)
            "public_api": public, "invariants": [],
            "depends_on": sorted(m["meta"].get("imports", [])),
            "sinks": sinks_k, "secrets": secrets[:20],
            "function_count": len(mfns)}
    _dump({"schema": SCHEMA, "modules": catalog},
          os.path.join(outdir, "MODULE_CATALOG.json"))

    return {"resolved_call_edges": len(resolved), "unresolved": len(unresolved),
            "auth_gates": len(auth_gates), "sql_fstring_sites": sum(
                1 for s in sql_sites if s["fstring"]),
            "subprocess_sites": len(subp_sites), "uncalled_fns": len(uncalled),
            "modules": len(catalog)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/home/claude/review/artifacts/KNOWLEDGE_GRAPH.db")
    ap.add_argument("--outdir", default="/home/claude/review/artifacts")
    ap.add_argument("--hash-pin",
                    default="/home/claude/review/artifacts/KNOWLEDGE_GRAPH.db.sha256",
                    help="content-hash pin file for --check-hash/--write-hash")
    ap.add_argument("--check-hash", action="store_true",
                    help="recompute content hash and compare to --hash-pin (P2 gate); rc!=0 on drift")
    ap.add_argument("--write-hash", action="store_true",
                    help="write the current content hash to --hash-pin (deliberate re-pin)")
    a = ap.parse_args()
    if a.check_hash:
        raise SystemExit(check_hash(a.db, a.hash_pin))
    if a.write_hash:
        raise SystemExit(write_hash(a.db, a.hash_pin))
    stats = build(a.db, a.outdir)
    print("graph_build:", json.dumps(stats))


if __name__ == "__main__":
    main()
