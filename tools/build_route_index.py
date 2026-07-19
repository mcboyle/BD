#!/usr/bin/env python3
"""build_route_index.py — KB Tier-A / A2.

Generates ROUTE_INDEX.json: every route as a (method, path) entry joined with its
spa_wired / operator_facing flags. The route source of truth is app.url_map (reused via
build_endpoint_catalog._import_app — NOT a re-parse of the generated ENDPOINT_CATALOG.md,
which would be a derived-of-a-derived); the wiring truth is reports/gui_parity_inventory.json.

This is a *join over existing generators*, not a new source of truth: ENDPOINT_CATALOG owns
routes+CSRF, gui_parity owns spa_wired. ROUTE_INDEX makes the join queryable
(what-pins / can-i-retire) and gated.

Per-entry schema:
    {method, path, blueprint, endpoint, file, line, csrf, spa_wired, operator_facing, kind}
  - identity = (method, path)  [matches test_parity_method_aware]
  - kind ∈ {api, page}; blueprint = endpoint prefix or "(app)" for top-level @app.route
  - `line` is a regenerated convenience field, NOT equality-gated (anchor on symbol, not line)

Usage:
    python tools/build_route_index.py            # write ROUTE_INDEX.json
    python tools/build_route_index.py --check     # exit 1 if the stable projection drifted
    python tools/build_route_index.py --stdout     # print, don't write
"""
import os
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import sys
from pathlib import Path

# Make repo root importable when invoked as `python tools/build_route_index.py`
# (sys.path[0] is then tools/, not the repo root). Harmless when imported as a module.
_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORT))

import inspect  # noqa: E402
import json  # noqa: E402

# Reuse the catalog's app import + CSRF rule — single source of truth for routes.
from tools.build_endpoint_catalog import (  # noqa: E402
    _repo_root, _import_app, _csrf_fires_for,
)

SCHEMA_VERSION = 1
# Flask auto-adds these; not user-facing (same skip set as the catalog).
_AUTO_METHODS = frozenset({"HEAD", "OPTIONS"})
# Parity item kinds that correspond to real HTTP routes.
_ROUTE_KINDS = frozenset({"cockpit_api", "gui_api", "cockpit_page", "gui_page"})


def _parity_path() -> Path:
    return _repo_root() / "reports" / "gui_parity_inventory.json"


def _load_parity_join():
    """{(method, path): {spa_wired, operator_facing, kind}} from gui_parity items."""
    p = _parity_path()
    join = {}
    if not p.exists():
        return join
    data = json.loads(p.read_text(encoding="utf-8"))
    for it in data.get("items", []):
        if it.get("kind") not in _ROUTE_KINDS:
            continue
        ce = it.get("command_or_endpoint", "")
        parts = ce.split(" ", 1)
        if len(parts) != 2:
            continue
        methods, path = parts[0], parts[1]
        for m in methods.split("|"):
            join[(m, path)] = {
                "spa_wired": bool(it.get("spa_wired")),
                "operator_facing": it.get("operator_facing"),
                "parity_kind": it.get("kind"),
            }
    return join


# Path components that mark a file as a dependency/builtin rather than our source.
# Needed because on stash the venv is nested INSIDE the repo
# (~/BulkDownloader/venv/.../site-packages), so a file there resolves UNDER the repo
# root and a bare relative_to() check would emit a venv path the sandbox never produces
# (the v3.66.355 on-stash in-sync break). Marker-based detection is venv-layout-independent.
_EXTERNAL_MARKERS = ("site-packages", "dist-packages", "node_modules")
_VENV_TOPDIRS = ("venv", ".venv", "env", ".env")


def _is_external(resolved_file: Path, resolved_root: Path) -> bool:
    """True if the file is third-party/builtin (not our source): it lives under a
    site-packages / dist-packages / node_modules tree, under a venv dir at the repo
    root, or outside the repo root entirely."""
    if any(m in resolved_file.parts for m in _EXTERNAL_MARKERS):
        return True
    try:
        rel = resolved_file.relative_to(resolved_root)
    except ValueError:
        return True
    return bool(rel.parts) and rel.parts[0] in _VENV_TOPDIRS


def _src_loc(view_fn, root: Path):
    """(file-relative-to-root, def-line) for a view function; (None, None) on failure.

    Files that are dependencies/builtins (Flask's /static handler, any third-party view)
    get their stable dotted module name (<flask.app>) instead of a path — a path there is
    environment-dependent (it varies with venv layout: work tree vs extracted zip vs the
    nested venv on stash), which breaks the in-sync gate. See _is_external."""
    if view_fn is None:
        return None, None
    fn = inspect.unwrap(view_fn)
    try:
        f = inspect.getsourcefile(fn) or inspect.getfile(fn)
        line = fn.__code__.co_firstlineno
    except (TypeError, OSError, AttributeError):
        return None, None
    fp = Path(f).resolve()
    root_r = Path(root).resolve()
    if _is_external(fp, root_r):
        mod = getattr(fn, "__module__", None)
        return (f"<{mod}>" if mod else "<external>"), None
    try:
        return str(fp.relative_to(root_r)), line
    except ValueError:
        mod = getattr(fn, "__module__", None)
        return (f"<{mod}>" if mod else "<external>"), None


def _kind_for(path: str, parity_kind: str | None) -> str:
    """api vs page. Prefer the parity classification; fall back to the path."""
    if parity_kind in ("cockpit_api", "gui_api"):
        return "api"
    if parity_kind in ("cockpit_page", "gui_page"):
        return "page"
    if "/api/" in path or path.endswith("/api"):
        return "api"
    return "page"


def _blueprint_for(endpoint: str) -> str:
    return endpoint.rsplit(".", 1)[0] if "." in endpoint else "(app)"


def build_index() -> dict:
    """Walk url_map, join parity, return the ROUTE_INDEX dict (sorted, deterministic)."""
    root = _repo_root()
    app = _import_app()
    join = _load_parity_join()

    routes = []
    for rule in app.url_map.iter_rules():
        path = rule.rule
        endpoint = rule.endpoint
        view_fn = app.view_functions.get(endpoint)
        file_rel, line = _src_loc(view_fn, root)
        blueprint = _blueprint_for(endpoint)
        methods = sorted(m for m in (rule.methods or set()) if m not in _AUTO_METHODS)
        if not methods:
            methods = ["?"]
        for m in methods:
            j = join.get((m, path), {})
            routes.append({
                "method": m,
                "path": path,
                "blueprint": blueprint,
                "endpoint": endpoint,
                "function": getattr(view_fn, "__name__", None),
                "file": file_rel,
                "line": line,
                "csrf": _csrf_fires_for(m, path),
                "spa_wired": j.get("spa_wired", False),
                "operator_facing": j.get("operator_facing"),
                "kind": _kind_for(path, j.get("parity_kind")),
                "in_parity": (m, path) in join,
            })

    routes.sort(key=lambda r: (r["path"], r["method"]))

    by_bp = {}
    for r in routes:
        by_bp[r["blueprint"]] = by_bp.get(r["blueprint"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": "app.url_map × reports/gui_parity_inventory.json",
        "route_source": "live url_map",
        "note": ("DO NOT EDIT BY HAND — tests/test_route_index_in_sync.py fails the build. "
                 "Regenerate: python tools/build_route_index.py. `line` is informational "
                 "(not equality-gated)."),
        "counts": {
            "total": len(routes),
            "spa_wired": sum(1 for r in routes if r["spa_wired"]),
            "api": sum(1 for r in routes if r["kind"] == "api"),
            "page": sum(1 for r in routes if r["kind"] == "page"),
            "csrf": sum(1 for r in routes if r["csrf"]),
            "not_in_parity": sum(1 for r in routes if not r["in_parity"]),
            "by_blueprint": dict(sorted(by_bp.items())),
        },
        "routes": routes,
    }


def _serialize(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = _repo_root() / "ROUTE_INDEX.json"
    d = build_index()
    text = _serialize(d)

    if "--stdout" in argv:
        sys.stdout.write(text)
        return 0

    if "--check" in argv:
        if not out.exists():
            sys.stderr.write("ROUTE_INDEX.json missing — run without --check to generate.\n")
            return 1
        committed = json.loads(out.read_text(encoding="utf-8"))
        stable = ("method", "path", "blueprint", "endpoint", "file", "csrf",
                  "spa_wired", "operator_facing", "kind")
        proj = lambda rs: [{k: r.get(k) for k in stable} for r in rs]
        if proj(d["routes"]) != proj(committed["routes"]):
            sys.stderr.write("ROUTE_INDEX.json STALE (stable projection drifted) — regenerate.\n")
            return 1
        # Informational: line drift does not fail the gate.
        if any(a.get("line") != b.get("line")
               for a, b in zip(d["routes"], committed["routes"])):
            sys.stderr.write("note: ROUTE_INDEX.json line numbers drifted (informational; "
                             "regenerate to refresh).\n")
        sys.stdout.write(f"ROUTE_INDEX.json IN-SYNC ({d['counts']['total']} routes).\n")
        return 0

    out.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {out.name}: {d['counts']['total']} routes, "
                     f"{d['counts']['spa_wired']} spa_wired, "
                     f"{len(d['counts']['by_blueprint'])} blueprints.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
