#!/usr/bin/env python3
"""deep_detect_surface.py -- generate deep_detect's REAL external surface.

The deep_detect decomposition's hardest constraint is law #2: preserve EVERY name
external code imports -- including the ~22 private functions other modules reach into.
This tool computes that set precisely, from **AST** (so `# Mirrors deep_detect._X`
doc/comment references are excluded -- the trap a grep falls into).

It walks every consumer .py under bulk_downloader/, tools/, tests/ and collects:
  - names imported via `from [bulk_downloader.]deep_detect import X`
  - attribute accesses `<alias>.<name>` where <alias> is bound to deep_detect
    (e.g. `from . import deep_detect as _dd; _dd.foo()`)
plus deep_detect's own public top-level names. The union is the frozen surface the
`deep_detect/__init__.py` shim `__all__` must export and the surface-lock must freeze.

    python3 tools/deep_detect_surface.py                 # human summary
    python3 tools/deep_detect_surface.py --emit-lock      # print the surface-lock frozen sets
    python3 tools/deep_detect_surface.py --root /path     # point at a tree (default: package of this file)

Read-only, stdlib only. Runs under plain python3 anywhere.
"""
from __future__ import annotations
import ast, sys
from pathlib import Path

TARGET = "deep_detect"


def _root(argv) -> Path:
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    # default: the bulk_downloader package that contains this tool's sibling
    here = Path(__file__).resolve()
    for up in here.parents:
        if (up / "bulk_downloader" / f"{TARGET}.py").exists():
            return up
    return Path.cwd()


def own_public(root: Path) -> set[str]:
    """deep_detect's own public top-level names (defs + module constants)."""
    src = (root / "bulk_downloader" / f"{TARGET}.py").read_text(encoding="utf-8")
    t = ast.parse(src)
    names = set()
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_"):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name) and not tgt.id.startswith("_") and tgt.id.isupper() or (
                        isinstance(tgt, ast.Name) and not tgt.id.startswith("_")):
                    names.add(tgt.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and not n.target.id.startswith("_"):
            names.add(n.target.id)
    return names


def external_uses(root: Path) -> dict[str, set[str]]:
    """{consumer_relpath: {names of deep_detect it imports/accesses}} -- AST, comment-safe."""
    uses: dict[str, set[str]] = {}
    scan_dirs = [root / "bulk_downloader", root / "tools", root / "tests"]
    self_file = (root / "bulk_downloader" / f"{TARGET}.py").resolve()
    for d in scan_dirs:
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            if p.resolve() == self_file:
                continue
            try:
                t = ast.parse(p.read_text(encoding="utf-8"), str(p))
            except (SyntaxError, UnicodeDecodeError):
                continue
            aliases: set[str] = set()      # local names bound to the deep_detect module
            direct: set[str] = set()       # names imported directly from deep_detect
            for n in ast.walk(t):
                if isinstance(n, ast.ImportFrom) and (n.module or "").endswith(TARGET):
                    # from [bulk_downloader.]deep_detect import a, b  OR  ... import deep_detect (rare)
                    for a in n.names:
                        direct.add(a.name)
                elif isinstance(n, ast.ImportFrom) and (n.module or "") in (".", "bulk_downloader", ""):
                    # from . import deep_detect [as _dd]  /  from bulk_downloader import deep_detect [as _dd]
                    for a in n.names:
                        if a.name == TARGET:
                            aliases.add(a.asname or a.name)
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name.endswith(f".{TARGET}") or a.name == TARGET:
                            aliases.add(a.asname or a.name.split(".")[-1])
            attr_names: set[str] = set()
            if aliases:
                for n in ast.walk(t):
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
                        attr_names.add(n.attr)
            found = direct | attr_names
            if found:
                uses[str(p.relative_to(root))] = found
    return uses


def compute(root: Path):
    pub = own_public(root)
    uses = external_uses(root)
    ext = set().union(*uses.values()) if uses else set()
    ext_public = {n for n in ext if not n.startswith("_")}
    ext_private = {n for n in ext if n.startswith("_")}
    # frozen surface = own public  ∪  everything external code pulls (incl privates)
    frozen = pub | ext
    return dict(own_public=pub, external=ext, ext_public=ext_public,
                ext_private=ext_private, frozen=frozen, uses=uses)


def main(argv):
    root = _root(argv)
    r = compute(root)
    if "--emit-lock" in argv:
        print("FROZEN_PUBLIC = {")
        for n in sorted(x for x in r["frozen"] if not x.startswith("_")):
            print(f"    {n!r},")
        print("}")
        print("\nEXTERNAL_REQUIRED_PRIVATE = {  # privates external modules import -- shim MUST export")
        for n in sorted(r["ext_private"]):
            print(f"    {n!r},")
        print("}")
        return
    print(f"deep_detect surface @ {root}")
    print(f"  own public names: {len(r['own_public'])}")
    print(f"  external-imported names: {len(r['external'])} "
          f"({len(r['ext_public'])} public + {len(r['ext_private'])} PRIVATE)")
    print(f"  >>> FROZEN SURFACE (shim __all__ / surface-lock): {len(r['frozen'])} names")
    print(f"\n  external PRIVATE fns/consts the shim MUST re-export ({len(r['ext_private'])}):")
    for n in sorted(r["ext_private"]):
        consumers = [c for c, names in r["uses"].items() if n in names]
        print(f"    {n:34s} <- {', '.join(Path(c).name for c in consumers[:3])}"
              + (" ..." if len(consumers) > 3 else ""))
    print(f"\n  (run with --emit-lock to print the frozen sets for the surface-lock test)")


if __name__ == "__main__":
    main(sys.argv[1:])
