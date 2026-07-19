#!/usr/bin/env python3
"""decomp_lint.py -- AST lint for the playbook's two transforms + the lazy-cycle rule.

A moved module can introduce three structural mistakes that are SANDBOX-INVISIBLE
(no behavioral test catches them until the on-stash full suite or a load failure):

  MODULE_LEVEL_MONOLITH_IMPORT  a top-level import of another monolith
        (app / runner / dev_suite / deep_detect). The four monoliths form a cyclic
        import graph that only works because the cyclic edges are LAZY (in-function).
        A hoisted edge fails at load. (`cross_monolith_graph.py --check` proves the
        module-level-only graph is acyclic; this catches the offending line locally.)
  STRAY_FILE   a __file__ use -- per the playbook, path math must be centralized in a
        single `_common` helper (depth bugs hide in scattered __file__ sites).
  DEPTH_SUSPECT  a Path(__file__).parents[N] -- moving a module changes its depth, so
        N must be re-checked (.parents[2] vs [1] is the classic slip).

AST-only, stdlib-only; runs on stash. ADVISORY -- a finding is a "look here", not a
hard fail. Module-level monolith imports are the one you must not ignore.

Usage:
    decomp_lint.py <file.py> [<file.py> ...]
    decomp_lint.py --root /home/claude/work bulk_downloader/dev_suite/<mod>.py
Exit 0 if no MODULE_LEVEL_MONOLITH_IMPORT findings; 1 if any (the load-breaking class).
"""
from __future__ import annotations

import argparse
import ast
import os
import sys

MONOLITHS = {"app", "runner", "dev_suite", "deep_detect"}


def _import_targets(node) -> list[str]:
    """Dotted names this import statement brings in (for monolith detection)."""
    names: list[str] = []
    if isinstance(node, ast.Import):
        for a in node.names:
            names.append(a.name)                      # import bulk_downloader.runner
    elif isinstance(node, ast.ImportFrom):
        mod = node.module or ""                        # from .runner import X -> "runner"? (level>0)
        if mod:
            names.append(mod)
        for a in node.names:
            names.append(f"{mod}.{a.name}" if mod else a.name)  # from . import runner -> "runner"
    return names


def _hits_monolith(node) -> str | None:
    for dotted in _import_targets(node):
        for part in dotted.split("."):
            if part in MONOLITHS:
                return part
    return None


def lint_source(src: str, filename: str = "<module>") -> list[tuple]:
    """Return a sorted list of (category, lineno, message) findings."""
    tree = ast.parse(src, filename)
    findings: list[tuple] = []

    # module-level imports ONLY (an in-function/lazy monolith import is correct)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            m = _hits_monolith(node)
            if m:
                findings.append(("MODULE_LEVEL_MONOLITH_IMPORT", node.lineno,
                                 f"top-level import of monolith '{m}' -- make it lazy (move into the function)"))

    # __file__ uses + parents[...] subscripts anywhere in the module
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__file__":
            findings.append(("STRAY_FILE", node.lineno,
                             "__file__ use -- centralize path math in one _common helper"))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "parents":
            findings.append(("DEPTH_SUSPECT", node.lineno,
                             "Path(__file__).parents[...] -- re-verify the index for the new module depth"))

    findings.sort(key=lambda f: (f[1], f[0]))
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="lint a moved module for the decomposition transforms")
    ap.add_argument("files", nargs="+", help="module file(s) to lint")
    ap.add_argument("--root", default="", help="prefix for relative file paths")
    a = ap.parse_args(argv)

    hard = 0
    for f in a.files:
        path = os.path.join(a.root, f) if a.root and not os.path.isabs(f) else f
        try:
            src = open(path, encoding="utf-8").read()
        except OSError as e:
            print(f"{f}: cannot read ({e})")
            continue
        findings = lint_source(src, path)
        if not findings:
            print(f"{f}: clean")
            continue
        print(f"{f}: {len(findings)} finding(s)")
        for cat, ln, msg in findings:
            mark = "FAIL" if cat == "MODULE_LEVEL_MONOLITH_IMPORT" else "warn"
            print(f"  {mark} L{ln} {cat}: {msg}")
            if cat == "MODULE_LEVEL_MONOLITH_IMPORT":
                hard += 1
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
