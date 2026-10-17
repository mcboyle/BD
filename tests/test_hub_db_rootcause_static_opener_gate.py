"""hub-db-corruption-rootcause r7 (PM 08:53Z, cx230 HIGH): DENY-BY-DEFAULT static gate over every SQLite opener.

r6 traced each opener's argument back to a history-path source and passed what it could not connect to one. cx230
broke that: backup._restore_db opened its destination with a bare sqlite3.connect on a VARIABLE path, the trace
labelled it NH, and restore_backup(target_dir=<NFS checkout>) wrote the history DB over NFS. r7 drops the
heuristic. This gate reads the source, it imports nothing.

Every opener site under bulk_downloader/ (storage/ included), tools/, scripts/ and the top-level *.py fails unless
it is db.guarded_connect's open (r7: db._open_history_conn) or carries an ALLOWLIST entry. An opener site is, import aliases followed:
  connect     any reference to sqlite3/_sqlite3/aiosqlite/sqlite3.dbapi2 ``connect`` (called, or passed as a value)
  Connection  a call of that module's ``Connection`` class
  dynamic     getattr(<sqlite module>, ...), __import__/import_module of a sqlite module
  engine      a create_engine/create_async_engine call, unless its URL is a str literal whose scheme is not sqlite
  backup      any ``<x>.backup(...)`` call (sqlite3.Connection.backup writes every page of its target)
An ALLOWLIST entry is keyed file:symbol and carries the exact count of each kind in that symbol (a second opener
added to an allowlisted function fails), a proof and a reason naming the target. Proofs "mode=ro", "memory" and
"guarded-handles" are checked mechanically; "seam" and "non-history" are judged by the reason, which says why.

Run as a script to write the inventory the DONE cites:  python -I tests/test_hub_db_rootcause_static_opener_gate.py OUT.md [REPO]
"""

from __future__ import annotations

import ast
import os
import sys

# The subject is the tree, not a module: a raw opener added anywhere under ROOT_DIRS fails here, whatever the diff touched.
BD_GATE_SCOPE = "repo-wide"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROOT_DIRS = ("bulk_downloader", "tools", "scripts")
SKIP_DIRS = {"__pycache__", "venv", ".venv", "node_modules", ".git"}
RAW_MODULES = {"sqlite3", "_sqlite3", "aiosqlite", "sqlite3.dbapi2"}
ENGINE_FUNCS = {"create_engine", "create_async_engine"}
GUARD = "bulk_downloader/db.py:_open_history_conn"  # r7: guarded_connect opens through it

# file:symbol -> exact opener counts by kind, proof, reason naming the target. Inventory: run this file as a script.
ALLOWLIST = {
    "bulk_downloader/backup.py:_snapshot_db": {"kinds": {"backup": 1}, "proof": "guarded-handles",
        "reason": "live DB -> tempdir snapshot; both handles from db.guarded_connect (r6)"},
    "bulk_downloader/backup.py:_restore_db": {"kinds": {"backup": 1}, "proof": "guarded-handles",
        "reason": "r7 cx230: dest judged by the history guard first; both handles from db.guarded_connect"},
    "bulk_downloader/backup_verify.py:verify_db_dump": {"kinds": {"connect": 1}, "proof": "mode=ro",
        "reason": "integrity read of a backup DB dump, literal mode=ro URI"},
    "bulk_downloader/cas_ledger.py:CASLedger._get_conn": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "CAS ledger <cas root>/ledger.db (only constructor: CASStore, root_dir / ledger.db)"},
    "bulk_downloader/connection_pool.py:SegregatedConnectionPool._open_raw_conn": {"kinds": {"connect": 1}, "proof": "seam",
        "reason": "raw branch runs only without connector=; every in-repo constructor passes connector=_open_history_conn (test_seam_entries_are_reached_only_through_the_guard)"},
    "bulk_downloader/dedup.py:HashRegistry._conn": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "perceptual-hash dedup registry, video_hashes.db or config dedup_db_path"},
    "bulk_downloader/dev_suite/capture_diag.py:dedup_hash_explore": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "dev diag over the dedup registry (dedup_db_path or video_hashes.db)"},
    "bulk_downloader/human_challenge.py:cookies_on_disk": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "reads a tempdir COPY of the browser profile Cookies DB"},
    "bulk_downloader/ledger_reconcile.py:digest_from_path": {"kinds": {"connect": 1}, "proof": "mode=ro",
        "reason": "replica digest, literal mode=ro URI"},
    "bulk_downloader/migrations.py:_restore_db_from_backup": {"kinds": {"backup": 1}, "proof": "seam",
        "reason": "r7: source .bak via db.guarded_connect; target is a db.db_conn() lease (_open_history_conn)"},
    "bulk_downloader/migrations.py:apply_pending": {"kinds": {"connect": 1}, "proof": "memory",
        "reason": "dry-run replica of the schema in :memory:"},
    "bulk_downloader/orm_engine.py:create_async_engine_for": {"kinds": {"engine": 1}, "proof": "seam",
        "reason": "every in-repo caller wraps it in db._guard_engine_connections (test_seam_entries_are_reached_only_through_the_guard)"},
    "bulk_downloader/sqlite_cdc.py:_read_table_snapshot": {"kinds": {"connect": 1}, "proof": "mode=ro",
        "reason": "r7 AMEND 09:0xZ: CDC snapshot read, literal mode=ro URI, path percent-quoted"},
    "bulk_downloader/sqlite_cdc.py:_table_columns": {"kinds": {"connect": 1}, "proof": "mode=ro",
        "reason": "r7 AMEND 09:0xZ: CDC column read, literal mode=ro URI, path percent-quoted"},
    "bulk_downloader/telemetry_db.py:TelemetryDB._get_connection": {"kinds": {"connect": 2}, "proof": "non-history",
        "reason": "telemetry store: TELEMETRY_PG_DSN sqlite file, else :memory:"},
    "bulk_downloader/vector_index.py:sqlite_vec_available": {"kinds": {"connect": 1}, "proof": "memory",
        "reason": "sqlite-vec capability probe on :memory:"},
    "tools/baselines_snapshot.py:main": {"kinds": {"connect": 1}, "proof": "mode=ro",
        "reason": "r6: live DB read-only or not at all, literal mode=ro URI; r7 AMEND F2: path percent-quoted"},
    "tools/code_intelligence/coverage_service.py:_graph_snapshot": {"kinds": {"connect": 2, "backup": 1}, "proof": "non-history",
        "reason": "analysis graph (KNOWLEDGE_GRAPH) opened ?mode=ro -> tempdir snapshot copy"},
    "tools/code_intelligence/coverage_service.py:_sqlite_read_only": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "analysis graph opened as_uri()+?mode=ro&immutable=1"},
    "tools/coverage_map.py:_fn_spans": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
    "tools/graph_build.py:content_hash": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
    "tools/graph_build.py:load": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
    "tools/l0_extract.py:_populate_database": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
    "tools/read_coverage.py:run": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
    "tools/review_merge.py:merge": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
    "tools/risk_score.py:load_facts": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
    "tools/seed_review_state.py:load_files": {"kinds": {"connect": 1}, "proof": "non-history",
        "reason": "target is the review KNOWLEDGE_GRAPH.db (--db default <root>/artifacts/KNOWLEDGE_GRAPH.db), a tools artifact, never the history DB"},
}


def source_files(repo):
    """Every .py under ROOT_DIRS plus the top-level *.py, sorted, as repo-relative paths."""
    out = [f for f in os.listdir(repo) if f.endswith(".py") and os.path.isfile(os.path.join(repo, f))]
    for top in ROOT_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(repo, top)):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            out += [os.path.relpath(os.path.join(dirpath, f), repo) for f in filenames if f.endswith(".py")]
    return sorted(out)


class _Module:
    def __init__(self, rel, tree):
        self.rel, self.tree = rel, tree
        self.raw_aliases, self.connect_names, self.engine_names = set(), set(), set(ENGINE_FUNCS)
        self.class_names = set()  # local names bound to a sqlite module's Connection class
        self.funcs = []  # (qualname, node, class name or None); a qualname can repeat (if/else defs, setters)
        self.parent = {}
        parts = rel[:-3].split("/")
        self.package = parts[:-1]
        if parts[-1] == "__init__":
            parts = parts[:-1]
        self.modname = ".".join(parts)
        self.mod_aliases, self.from_imports = {}, {}  # local name -> module; local name -> (module, attr)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.mod_aliases[a.asname] = a.name
                    else:
                        self.mod_aliases[a.name.split(".")[0]] = a.name.split(".")[0]
                        self.mod_aliases[a.name] = a.name
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    up = self.package[:len(self.package) - (node.level - 1)] if node.level > 1 else self.package
                    base = ".".join(up + ([node.module] if node.module else []))
                for a in node.names:
                    self.from_imports[a.asname or a.name] = (base, a.name)
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in RAW_MODULES:
                        self.raw_aliases.add(a.asname or a.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    if node.module in RAW_MODULES and a.name in ("connect", "*"):
                        self.connect_names.add("connect" if a.name == "*" else a.asname or a.name)
                    if node.module in RAW_MODULES and a.name in ("Connection", "*"):
                        self.class_names.add("Connection" if a.name == "*" else a.asname or a.name)
                    if node.module in RAW_MODULES and a.name in ("dbapi2",):
                        self.raw_aliases.add(a.asname or a.name)
                    elif a.name in ENGINE_FUNCS:
                        self.engine_names.add(a.asname or a.name)
        self._index(tree, [], None)
        self.qual_of = {id(fn): q for q, fn, _c in self.funcs}
        self.class_of = {id(fn): c for _q, fn, c in self.funcs}

    def _index(self, node, path, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs.append((".".join(path + [child.name]), child, cls))
                self._index(child, path + [child.name], cls)
            elif isinstance(child, ast.ClassDef):
                self._index(child, path + [child.name], child.name)
            else:
                self._index(child, path, cls)

    def enclosing(self, node):
        """(qualname, FunctionDef) of the innermost function holding ``node``, or (None, None) at module level."""
        cur = self.parent.get(node)
        while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cur = self.parent.get(cur)
        if cur is None:
            return None, None
        return self.qual_of[id(cur)], cur

    def names_module(self, local, modname):
        """Does the local name ``local`` (a dotted chain allowed) bind module ``modname`` in this module?"""
        head, _, rest = local.partition(".")
        if head in self.mod_aliases:
            full = self.mod_aliases[head] + ("." + rest if rest else "")
        elif head in self.from_imports:
            base, attr = self.from_imports[head]
            full = ".".join(x for x in (base, attr, rest) if x)
        else:
            return False
        return _same_module(full, modname)

    def imports_module(self, modname):
        """Does this module import ``modname`` (or a name out of it) at all?"""
        return (any(_same_module(v, modname) for v in self.mod_aliases.values())
                or any(_same_module(b, modname) or _same_module(b + "." + a, modname)
                       for b, a in self.from_imports.values()))


def _same_module(imported, modname):
    """``bulk_downloader.db`` is imported as ``bulk_downloader.db`` or, via a sys.path insert, as ``db``."""
    return bool(imported) and (imported == modname or modname.endswith("." + imported))


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id] + parts[::-1])



def _raw_module(m, node):
    """Does expression ``node`` name a sqlite module (sqlite3, an alias of it, sqlite3.dbapi2, aiosqlite...)?"""
    name = _dotted(node)
    return bool(name) and (name in m.raw_aliases or name in RAW_MODULES
                           or any(m.names_module(name, r) for r in RAW_MODULES))


def _url_literal(call):
    first = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "url"), None)
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _site_kind(m, node):
    """The opener kind of ``node`` (see the module docstring), or None."""
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr == "connect":
        return "connect" if _raw_module(m, node.value) else None
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in m.connect_names:
        return "connect"
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if (isinstance(f, ast.Attribute) and f.attr == "Connection" and _raw_module(m, f.value)) or (
            isinstance(f, ast.Name) and f.id in m.class_names):
        return "Connection"
    if isinstance(f, ast.Name) and f.id == "getattr" and node.args and _raw_module(m, node.args[0]):
        return "dynamic"
    if ((isinstance(f, ast.Name) and f.id in ("__import__", "import_module"))
            or (isinstance(f, ast.Attribute) and f.attr == "import_module")) and node.args and (
            isinstance(node.args[0], ast.Constant) and node.args[0].value in RAW_MODULES):
        return "dynamic"
    if (isinstance(f, ast.Name) and f.id in m.engine_names) or (isinstance(f, ast.Attribute) and f.attr in ENGINE_FUNCS):
        url = _url_literal(node)
        return None if url is not None and not url.lower().startswith("sqlite") else "engine"
    if isinstance(f, ast.Attribute) and f.attr == "backup":
        return "backup"
    return None


def _read_only_literal(call):
    """True when the opener's argument is a literal file: URI whose every mode is ro, opened with uri=True."""
    if not call.args or not any(k.arg == "uri" and isinstance(k.value, ast.Constant) and k.value.value is True
                                for k in call.keywords):
        return False
    arg = call.args[0]
    parts = ([arg.value] if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else
             [v.value for v in arg.values if isinstance(v, ast.Constant)] if isinstance(arg, ast.JoinedStr) else None)
    if not parts:
        return False
    text = "".join(parts)
    modes = [kv.split("=", 1)[1] for kv in text.partition("?")[2].split("&") if kv.startswith("mode=")]
    return text.startswith("file:") and bool(modes) and all(mo == "ro" for mo in modes)


def _is_guarded_call(node):
    f = node.func if isinstance(node, ast.Call) else None
    return (isinstance(f, ast.Name) and f.id == "guarded_connect") or (
        isinstance(f, ast.Attribute) and f.attr == "guarded_connect")


def _guarded_name(fn, name):
    """Is local ``name`` bound in ``fn`` only by ``... = guarded_connect(...)`` (and bound at least once)?"""
    binds = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            binds += [node.value for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name) and n.id == name]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            binds.append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.ExceptHandler, ast.arguments,
                               ast.Global, ast.Nonlocal, ast.Delete)):
            targets = ([node.target] if isinstance(node, (ast.For, ast.AsyncFor)) else
                       [i.optional_vars for i in node.items if i.optional_vars] if isinstance(node, (ast.With, ast.AsyncWith)) else [])
            if any(isinstance(n, ast.Name) and n.id == name for t in targets for n in ast.walk(t)):
                binds.append(None)
            if isinstance(node, ast.ExceptHandler) and node.name == name:
                binds.append(None)
            if isinstance(node, ast.arguments) and name in [a.arg for a in node.posonlyargs + node.args + node.kwonlyargs]:
                binds.append(None)
            if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
                binds.append(None)
    return bool(binds) and all(_is_guarded_call(v) for v in binds)


def _proof_holds(proof, fn, node, kind):
    """Mechanical proofs. None = holds; else why it does not."""
    if proof == "mode=ro":
        ok = kind == "connect" and isinstance(node, ast.Attribute) and isinstance(getattr(node, "_bd_call", None), ast.Call) \
            and _read_only_literal(node._bd_call)
        return None if ok else "proof mode=ro: not a called connect with a literal file:...?mode=ro URI and uri=True"
    if proof == "memory":
        call = getattr(node, "_bd_call", None)
        ok = isinstance(call, ast.Call) and call.args and isinstance(call.args[0], ast.Constant) \
            and call.args[0].value == ":memory:"
        return None if ok else "proof memory: argument is not the literal ':memory:'"
    if proof == "guarded-handles":
        ok = (kind == "backup" and fn is not None and isinstance(node.func.value, ast.Name) and len(node.args) >= 1
              and isinstance(node.args[0], ast.Name) and _guarded_name(fn, node.func.value.id)
              and _guarded_name(fn, node.args[0].id))
        return None if ok else "proof guarded-handles: source and target are not both locals bound only by guarded_connect"
    if proof in ("seam", "non-history"):
        return None
    return "unknown proof %r" % proof


def scan(repo=REPO, allowlist=None):
    """Every opener site as a dict: site, line, kind, verdict (GUARD/ALLOWED/RAW/UNREADABLE), why."""
    allowlist = ALLOWLIST if allowlist is None else allowlist
    modules, sites = {}, []
    for rel in source_files(repo):
        try:
            with open(os.path.join(repo, rel), encoding="utf-8", errors="replace") as f:
                modules[rel] = _Module(rel.replace(os.sep, "/"), ast.parse(f.read(), rel))
        except SyntaxError as e:
            sites.append({"site": rel, "line": e.lineno or 0, "kind": "-", "verdict": "UNREADABLE",
                          "why": "could not parse: %s" % e.msg})
    found = []
    for rel, m in modules.items():
        for node in ast.walk(m.tree):
            if isinstance(node, ast.Call):
                node.func._bd_call = node
        for node in ast.walk(m.tree):
            kind = _site_kind(m, node)
            if kind is None:
                continue
            qual, fn = m.enclosing(node)
            found.append(("%s:%s" % (m.rel, qual or "<module>"), node, kind, fn))
    per_symbol = {}
    for site, _n, kind, _f in found:
        per_symbol.setdefault(site, {}).setdefault(kind, 0)
        per_symbol[site][kind] += 1
    for site, node, kind, fn in found:
        entry = allowlist.get(site)
        if site == GUARD and kind == "connect":
            verdict, why = "GUARD", "db._open_history_conn (guarded_connect's open): pre-open + post-open history refusal"
        elif entry is None:
            verdict, why = "RAW", "no ALLOWLIST entry: route it through db.guarded_connect or add file:symbol + proof"
        elif per_symbol[site] != entry["kinds"]:
            verdict, why = "RAW", "ALLOWLIST count drift: entry %r, source %r" % (entry["kinds"], per_symbol[site])
        elif not entry.get("reason", "").strip():
            verdict, why = "RAW", "ALLOWLIST entry has no reason"
        else:
            bad = _proof_holds(entry["proof"], fn, node, kind)
            verdict, why = ("RAW", bad) if bad else ("ALLOWED", "%s: %s" % (entry["proof"], entry["reason"]))
        sites.append({"site": site, "line": node.lineno, "kind": kind, "verdict": verdict, "why": why})
    return sorted(sites, key=lambda s: (s["site"], s["line"], s["kind"]))


FAILING = ("RAW", "UNREADABLE")


def failures(sites):
    return ["%s line %d (%s %s): %s" % (s["site"], s["line"], s["kind"], s["verdict"], s["why"]) for s in sites
            if s["verdict"] in FAILING]


def inventory(sites, repo=REPO):
    counts = {}
    for s in sites:
        counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1
    lines = ["# SQLite opener inventory, deny-by-default (generated by tests/test_hub_db_rootcause_static_opener_gate.py)",
             "Denominator: %d opener sites in %d files (bulk_downloader/ tools/ scripts/ top-level *.py); ALLOWLIST %d entries."
             % (len(sites), len(source_files(repo)), len(ALLOWLIST)),
             "Counts: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())), "",
             "| site | line | kind | verdict | why |", "|---|---|---|---|---|"]
    lines += ["| %s | %d | %s | %s | %s |" % (s["site"], s["line"], s["kind"], s["verdict"], s["why"].replace("|", "/"))
              for s in sites]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------- tests

def test_every_sqlite_opener_is_guarded_connect_or_allowlisted():
    bad = failures(scan())
    assert not bad, ("SQLite opener outside db.guarded_connect with no valid ALLOWLIST entry (route it through "
                     "db.guarded_connect, or add file:symbol + kinds + proof + reason):\n  " + "\n  ".join(bad))


def test_allowlist_has_no_stale_entry_and_guard_exists():
    sites = scan()
    seen = {s["site"] for s in sites}
    assert not set(ALLOWLIST) - seen, "stale ALLOWLIST entries: %s" % sorted(set(ALLOWLIST) - seen)
    assert any(s["site"] == GUARD and s["verdict"] == "GUARD" for s in sites)
    for key, entry in ALLOWLIST.items():
        assert entry["proof"] in ("mode=ro", "memory", "guarded-handles", "seam", "non-history"), key
        assert len(entry["reason"]) > 20, key


def _calls_named(name):
    """(module, call) for every in-scope call whose callee is ``name`` (bare or as an attribute)."""
    out = []
    for rel in source_files(REPO):
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            tree = ast.parse(f.read(), rel)
        parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            f = getattr(node, "func", None)
            if isinstance(node, ast.Call) and ((isinstance(f, ast.Name) and f.id == name)
                                               or (isinstance(f, ast.Attribute) and f.attr == name)):
                out.append((rel, node, parent.get(node)))
    return out


def test_seam_entries_are_reached_only_through_the_guard():
    # The two "seam" entries whose safety lives at their callers, checked mechanically.
    pools = _calls_named("SegregatedConnectionPool")
    assert pools, "positive control: the db.get_segregated_pool constructor call must be found"
    for rel, call, _p in pools:
        kw = {k.arg: k.value for k in call.keywords}
        assert "connector" in kw and not (isinstance(kw["connector"], ast.Constant) and kw["connector"].value is None), \
            "%s:%d builds SegregatedConnectionPool without connector=" % (rel, call.lineno)
    engines = _calls_named("create_async_engine_for")
    assert engines, "positive control: db.async_engine's call must be found"
    for rel, call, parent in engines:
        assert isinstance(parent, ast.Call) and getattr(parent.func, "id", getattr(parent.func, "attr", None)) \
            == "_guard_engine_connections", "%s:%d create_async_engine_for outside _guard_engine_connections" % (
                rel, call.lineno)


def _plant(tmp_path, body, allowlist=None):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "planted.py").write_text(body)
    return {(s["site"], s["kind"]): s for s in scan(str(tmp_path), allowlist or {})}


def test_gate_positive_control_raw_connect_on_a_variable_path(tmp_path):
    # The cx230 shape: a bare connect on a path that is a plain variable, no history name anywhere. r6 passed it (NH).
    got = _plant(tmp_path, "import sqlite3 as sq\n"
                           "def _restore(dest):\n    d = sq.connect(str(dest), timeout=30)\n    return d\n")
    assert got[("tools/planted.py:_restore", "connect")]["verdict"] == "RAW"
    assert failures(list(got.values()))


def test_gate_catches_every_opener_shape(tmp_path):
    got = _plant(tmp_path, "import sqlite3, importlib\nfrom sqlite3 import connect as c2, Connection\n"
                           "import aiosqlite\nfrom sqlalchemy import create_engine\n"
                           "OPEN = sqlite3.connect\n"
                           "def a(p):\n    return c2(p)\n"
                           "def b(p):\n    return Connection(p)\n"
                           "def c(p):\n    return getattr(sqlite3, 'connect')(p)\n"
                           "def d(p):\n    return importlib.import_module('sqlite3')\n"
                           "def e(url):\n    return create_engine(url)\n"
                           "def f(s, t):\n    s.backup(t)\n"
                           "async def g(p):\n    return await aiosqlite.connect(p)\n"
                           "def h(p):\n    return create_engine('sqlite:///' + p)\n")
    want = {"<module>": "connect", "a": "connect", "b": "Connection", "c": "dynamic", "d": "dynamic", "e": "engine",
            "f": "backup", "g": "connect", "h": "engine"}
    for sym, kind in want.items():
        assert got[("tools/planted.py:" + sym, kind)]["verdict"] == "RAW", (sym, kind)


def test_gate_allowlist_is_exact_and_proofs_are_checked(tmp_path):
    body = ("import sqlite3\nfrom bulk_downloader.db import guarded_connect\n"
            "def ro(p):\n    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)\n"
            "def rw_uri(p):\n    return sqlite3.connect(f'file:{p}?mode=ro&mode=rwc', uri=True)\n"
            "def mem():\n    return sqlite3.connect(':memory:')\n"
            "def two(p):\n    sqlite3.connect(':memory:')\n    return sqlite3.connect(p)\n"
            "def snap(a, b):\n    s = guarded_connect(a)\n    d = guarded_connect(b)\n    s.backup(d)\n"
            "def snap_raw(a, b):\n    s = guarded_connect(a)\n    d = sqlite3.connect(b)\n    s.backup(d)\n"
            "def pg():\n    from sqlalchemy import create_engine\n    return create_engine('postgresql://h/db')\n")
    allow = {
        "tools/planted.py:ro": {"kinds": {"connect": 1}, "proof": "mode=ro", "reason": "x"},
        "tools/planted.py:rw_uri": {"kinds": {"connect": 1}, "proof": "mode=ro", "reason": "x"},
        "tools/planted.py:mem": {"kinds": {"connect": 1}, "proof": "memory", "reason": "x"},
        "tools/planted.py:two": {"kinds": {"connect": 1}, "proof": "memory", "reason": "x"},
        "tools/planted.py:snap": {"kinds": {"backup": 1}, "proof": "guarded-handles", "reason": "x"},
        "tools/planted.py:snap_raw": {"kinds": {"backup": 1, "connect": 1}, "proof": "guarded-handles", "reason": "x"},
    }
    got = _plant(tmp_path, body, allow)
    v = {k[0].split(":")[1] + "/" + k[1]: s["verdict"] for k, s in got.items()}
    # Negative controls: a mechanically proven entry passes; a literal non-sqlite engine URL is not an opener.
    assert v["ro/connect"] == "ALLOWED" and v["mem/connect"] == "ALLOWED" and v["snap/backup"] == "ALLOWED"
    assert "pg/engine" not in v
    # A proof that does not hold, and a second opener in an allowlisted symbol, fail.
    assert v["rw_uri/connect"] == "RAW"
    assert v["two/connect"] == "RAW"
    assert v["snap_raw/backup"] == "RAW" and v["snap_raw/connect"] == "RAW"


def test_gate_guarded_connect_itself_passes_only_in_db(tmp_path):
    got = _plant(tmp_path, "import sqlite3\ndef guarded_connect(p):\n    return sqlite3.connect(p)\n")
    assert got[("tools/planted.py:guarded_connect", "connect")]["verdict"] == "RAW"


def test_unparseable_source_is_could_not_look_not_pass(tmp_path):
    got = _plant(tmp_path, "def broken(:\n")
    assert got[("tools/planted.py", "-")]["verdict"] == "UNREADABLE"


if __name__ == "__main__":
    # argv: [OUT.md [REPO]] -- REPO lets the same gate judge another tree (the RED run reads an export of the base).
    repo = sys.argv[2] if len(sys.argv) > 2 else REPO
    found = scan(repo)
    text = inventory(found, repo)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as out:
            out.write(text)
    else:
        sys.stdout.write(text)
    bad = failures(found)
    sys.stdout.write("GATE: %s fail=%d sites=%d allowlist=%d\n" % ("RED" if bad else "GREEN", len(bad), len(found),
                                                                     len(ALLOWLIST)))
    sys.exit(1 if bad else 0)
