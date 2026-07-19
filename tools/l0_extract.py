#!/usr/bin/env python3
"""l0_extract -- the L0 AST pass for the code-intelligence program.

Reads the production tree READ-ONLY and upserts per-file + per-function facts
into KNOWLEDGE_GRAPH.db (the canonical store, CODE_INTELLIGENCE_SCHEMAS.md §14).
Python is parsed deeply with stdlib `ast`; TS/TSX get grep-level facts (no TS
compiler offline -- documented partial).

Nodes:  module | function
Edges:  contains (module->fn) | call (fn->name, may be unresolved) | imports
Per-fn meta captures the rubric-relevant facts: sinks (sql/subprocess/path/
fetch/redaction/template), auth-gate decorators, secret field touches, raises,
float()/get_json shapes (defect-pattern fuel).

Deterministic: sorted inserts, schema-pinned. Offline, stdlib-only.

Usage:  python3 l0_extract.py --root <tree> --db <db>   (defaults: ../work, ./artifacts/KNOWLEDGE_GRAPH.db)
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import sys

SCHEMA = 1

PROD = (("bulk_downloader", (".py",)), ("tools", (".py",)),
        ("frontend/src", (".ts", ".tsx")))

# --- sink / secret / auth signatures (string-level, AST-confirmed where possible)
SECRET_RE = re.compile(
    r"(password|passwd|secret|token|cookie|api[_-]?key|authorization|bearer|"
    r"private[_-]?key|signing|otp|credential|session[_-]?key|access[_-]?token)",
    re.I)
SUBPROCESS_NAMES = {"run", "Popen", "call", "check_output", "check_call",
                    "getoutput", "getstatusoutput"}
FETCH_NAMES = {"get", "post", "put", "patch", "delete", "request", "urlopen",
               "fetch"}
PATH_SINK_NAMES = {"join", "abspath", "realpath", "open", "makedirs", "mkdir"}
REDACT_RE = re.compile(r"(redact|scrub|mask|sanitiz|_is_secret|_mask)", re.I)


def sloc(path):
    n = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.strip():
                    n += 1
    except OSError:
        pass
    return n


def prod_files(root):
    out = []
    for base, exts in PROD:
        b = os.path.join(root, base)
        for dp, _, fns in os.walk(b):
            if "node_modules" in dp or "__pycache__" in dp:
                continue
            for f in fns:
                if f.endswith(exts):
                    out.append(os.path.relpath(os.path.join(dp, f), root))
    return sorted(out)


def _call_name(node):
    """Best-effort dotted name of a Call's func."""
    f = node.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts)) if parts else ""


class PyVisitor(ast.NodeVisitor):
    def __init__(self, relpath, src):
        self.rel = relpath
        self.src = src
        self.scope = []           # qualname stack
        self.fns = []             # list of fn fact dicts
        self.module_calls = []    # edges
        self.imports = []

    def _qual(self, name):
        return ".".join(self.scope + [name]) if self.scope else name

    def visit_Import(self, node):
        for a in node.names:
            self.imports.append(a.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.append(node.module.split(".")[0])
        self.generic_visit(node)

    def _handle_fn(self, node):
        qual = self._qual(node.name)
        facts = {
            "qualname": qual,
            "lineno": node.lineno,
            "end_lineno": getattr(node, "end_lineno", node.lineno),
            "args": [a.arg for a in node.args.args]
                    + ([node.args.vararg.arg] if node.args.vararg else [])
                    + [a.arg for a in node.args.kwonlyargs]
                    + ([node.args.kwwarg.arg] if getattr(node.args, "kwwarg", None) else []),
            "has_kwargs": node.args.kwarg is not None,
            "decorators": [self._deco(d) for d in node.decorator_list],
            "calls": [], "raises": [], "sinks": [], "secrets": [],
            "flags": [],
        }
        # body scan
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                nm = _call_name(sub)
                if nm:
                    facts["calls"].append(nm)
                last = nm.split(".")[-1]
                if last in SUBPROCESS_NAMES:
                    shell = any(isinstance(k, ast.keyword) and k.arg == "shell"
                                and _is_true(k.value) for k in sub.keywords)
                    facts["sinks"].append({"kind": "subprocess", "at": sub.lineno,
                                           "shell": shell})
                elif last in FETCH_NAMES and ("request" in nm.lower()
                                              or "urlopen" in nm or "fetch" in nm
                                              or "session" in nm.lower()):
                    facts["sinks"].append({"kind": "fetch", "at": sub.lineno})
                elif last in PATH_SINK_NAMES and ("path" in nm.lower()
                                                  or "os." in nm or last == "open"):
                    facts["sinks"].append({"kind": "path", "at": sub.lineno})
                elif REDACT_RE.search(nm):
                    facts["sinks"].append({"kind": "redaction", "at": sub.lineno})
                # get_json(silent=True) shape -> DP-01 fuel
                if last == "get_json":
                    sil = any(isinstance(k, ast.keyword) and k.arg == "silent"
                              and _is_true(k.value) for k in sub.keywords)
                    if sil:
                        facts["flags"].append({"f": "get_json_silent", "at": sub.lineno})
                if last == "float":
                    facts["flags"].append({"f": "float_coerce", "at": sub.lineno})
                if last in ("execute", "executescript") or "cursor" in nm.lower():
                    facts["sinks"].append({"kind": "sql", "at": sub.lineno})
            if isinstance(sub, ast.Raise):
                exc = ""
                if sub.exc is not None:
                    if isinstance(sub.exc, ast.Call):
                        exc = _call_name(sub.exc)
                    elif isinstance(sub.exc, ast.Name):
                        exc = sub.exc.id
                facts["raises"].append(exc)
            # secret-keyed subscript/string
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if SECRET_RE.search(sub.value):
                    facts["secrets"].append(sub.value[:40])
            # f-string SQL (DP-10 fuel): JoinedStr containing FROM/SELECT + a name
            if isinstance(sub, ast.JoinedStr):
                txt = "".join(p.value for p in sub.values
                              if isinstance(p, ast.Constant) and isinstance(p.value, str))
                if re.search(r"\b(FROM|INTO|UPDATE|TABLE)\b", txt, re.I) and any(
                        isinstance(p, ast.FormattedValue) for p in sub.values):
                    facts["sinks"].append({"kind": "sql_fstring", "at": sub.lineno})
        facts["secrets"] = sorted(set(facts["secrets"]))
        self.fns.append(facts)
        # descend for nested defs with scope
        self.scope.append(node.name)
        for c in node.body:
            self.visit(c)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self._handle_fn(node)

    def visit_AsyncFunctionDef(self, node):
        self._handle_fn(node)

    def _deco(self, d):
        if isinstance(d, ast.Call):
            return _call_name(d)
        if isinstance(d, ast.Attribute) or isinstance(d, ast.Name):
            return _call_name(ast.Call(func=d, args=[], keywords=[])) or (
                d.id if isinstance(d, ast.Name) else d.attr)
        return ""


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def ts_facts(relpath, src):
    """Grep-level facts for TS/TSX (no offline TS compiler)."""
    exports = re.findall(r"export\s+(?:async\s+)?function\s+(\w+)", src)
    exports += re.findall(r"export\s+const\s+(\w+)\s*=", src)
    fetches = len(re.findall(r"\bfetch\(", src))
    secrets = sorted(set(m.group(0) for m in SECRET_RE.finditer(src)))
    return {"exports": sorted(set(exports)), "fetch_calls": fetches,
            "secrets": secrets[:10]}


def build_db(root, db_path):
    files = prod_files(root)
    con = sqlite3.connect(db_path)
    con.executescript("""
      DROP TABLE IF EXISTS nodes; DROP TABLE IF EXISTS edges; DROP TABLE IF EXISTS meta;
      CREATE TABLE nodes(id TEXT PRIMARY KEY, kind TEXT, path TEXT, qualname TEXT,
                         span TEXT, sha256 TEXT, lines INTEGER, meta_json TEXT);
      CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, meta_json TEXT);
      CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
      CREATE INDEX ix_nodes_path ON nodes(path);
      CREATE INDEX ix_edges_kind ON edges(kind);
    """)
    n_mod = n_fn = n_edge = parse_err = 0
    for rel in files:
        ap = os.path.join(root, rel)
        try:
            raw = open(ap, "rb").read()
        except OSError:
            continue
        sha = hashlib.sha256(raw).hexdigest()
        lines = sloc(ap)
        src = raw.decode("utf-8", "replace")
        if rel.endswith(".py"):
            try:
                tree = ast.parse(src, filename=rel)
            except SyntaxError as e:
                parse_err += 1
                con.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                            (rel, "module", rel, rel, "", sha, lines,
                             json.dumps({"parse_error": str(e)})))
                n_mod += 1
                continue
            v = PyVisitor(rel, src)
            v.visit(tree)
            con.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                        (rel, "module", rel, rel, "", sha, lines,
                         json.dumps({"imports": sorted(set(v.imports))},
                                    sort_keys=True)))
            n_mod += 1
            seen_fids = set()
            for f in v.fns:
                fid = f"{rel}::{f['qualname']}"
                if fid in seen_fids:           # dup qualname (conditional/overload)
                    fid = f"{fid}#{f['lineno']}"
                seen_fids.add(fid)
                con.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                            (fid, "function", rel, f["qualname"],
                             f"{f['lineno']}-{f['end_lineno']}", "",
                             f["end_lineno"] - f["lineno"] + 1,
                             json.dumps({k: f[k] for k in
                                         ("args", "has_kwargs", "decorators",
                                          "raises", "sinks", "secrets", "flags")},
                                        sort_keys=True)))
                n_fn += 1
                con.execute("INSERT INTO edges VALUES(?,?,?,?)",
                            (rel, fid, "contains", "{}"))
                n_edge += 1
                for c in sorted(set(f["calls"])):
                    con.execute("INSERT INTO edges VALUES(?,?,?,?)",
                                (fid, c, "call", "{}"))
                    n_edge += 1
            for imp in sorted(set(v.imports)):
                con.execute("INSERT INTO edges VALUES(?,?,?,?)",
                            (rel, imp, "imports", "{}"))
                n_edge += 1
        else:  # TS/TSX
            tf = ts_facts(rel, src)
            con.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                        (rel, "module", rel, rel, "", sha, lines,
                         json.dumps(tf, sort_keys=True)))
            n_mod += 1
    con.execute("INSERT OR REPLACE INTO meta VALUES('schema',?)", (str(SCHEMA),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('files',?)", (str(len(files)),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('modules',?)", (str(n_mod),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('functions',?)", (str(n_fn),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('edges',?)", (str(n_edge),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('parse_errors',?)", (str(parse_err),))
    con.commit()
    con.close()
    return {"files": len(files), "modules": n_mod, "functions": n_fn,
            "edges": n_edge, "parse_errors": parse_err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/claude/work")
    ap.add_argument("--db", default="/home/claude/review/artifacts/KNOWLEDGE_GRAPH.db")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.db), exist_ok=True)
    if os.path.exists(a.db):
        os.remove(a.db)
    stats = build_db(a.root, a.db)
    print("l0_extract:", json.dumps(stats))


if __name__ == "__main__":
    main()
