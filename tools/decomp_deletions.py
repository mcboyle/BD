#!/usr/bin/env python3
"""decomp_deletions.py -- the overlay-can't-delete guard for a .py->package cut.

`unzip -o` (the deploy overlay) ADDS and REPLACES files but NEVER removes one. So a
decomposition cut that turns `bulk_downloader/dev_suite.py` into a `dev_suite/`
package ships the new package yet leaves the OLD `dev_suite.py` on the host, where it
shadows the package at import. This tool diffs the new (built) release against the
currently-deployed baseline, lists every path that exists in the baseline but not the
new build (the files overlay will silently leave behind), and flags the `X.py`->`X/`
package conversions specifically -- emitting the exact `rm` lines for the deploy note.

Stdlib-only; accepts zips or directories on either side; runs on stash with the
system python3. ADVISORY -- the deploy + the on-stash full suite stay authoritative.

Usage:
    decomp_deletions.py --new <built.zip|dir> --old <deployed.zip|dir>
    decomp_deletions.py --new <zip> --old <zip> --prefix bulk_downloader/   # filter
    decomp_deletions.py --new <zip> --old <zip> --json
Exit 0 always (advisory); prints a banner when conversions are detected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile


def _paths_from_zip(p: str) -> set[str]:
    with zipfile.ZipFile(p) as z:
        return {n for n in z.namelist() if not n.endswith("/")}


def _paths_from_dir(root: str) -> set[str]:
    out: set[str] = set()
    for dp, _dirs, fns in os.walk(root):
        # skip the usual noise so a work-tree comparison isn't swamped
        if any(seg in dp for seg in ("__pycache__", "node_modules", "/.git", "/venv", "/.venv")):
            continue
        for fn in fns:
            if fn.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), root)
            out.add(rel.replace(os.sep, "/"))
    return out


def list_paths(src: str) -> set[str]:
    """Repo-relative file paths from a zip or a directory."""
    if os.path.isdir(src):
        return _paths_from_dir(src)
    return _paths_from_zip(src)


def compute(old: set[str], new: set[str]) -> dict:
    """Diff two repo-relative path sets. Returns the deletion report.

    deletions  -- in old, absent from new (overlay will NOT remove these).
    conversions-- the subset where `X.py` was dropped AND an `X/` package now exists
                  in new (the shadowing case that breaks imports).
    rm_lines   -- ready-to-paste `rm <path>` lines for the deploy note.
    """
    deletions = sorted(old - new)
    # every package-dir prefix present in the new tree, e.g. "a/b/c.py" -> {"a/","a/b/"}
    pkg_prefixes: set[str] = set()
    for p in new:
        parts = p.split("/")
        for i in range(1, len(parts)):
            pkg_prefixes.add("/".join(parts[:i]) + "/")
    conversions = []
    for d in deletions:
        if d.endswith(".py"):
            stem = d[:-3] + "/"  # dev_suite.py -> dev_suite/
            if stem in pkg_prefixes:
                conversions.append((d, stem))
    return {
        "deletions": deletions,
        "conversions": conversions,
        "rm_lines": [f"rm {d}" for d in deletions],
        "added": sorted(new - old),
    }


def _filter(paths: set[str], prefix: str | None) -> set[str]:
    return paths if not prefix else {p for p in paths if p.startswith(prefix)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="overlay-can't-delete guard for .py->package cuts")
    ap.add_argument("--new", required=True, help="the built release (zip or dir)")
    ap.add_argument("--old", required=True, help="the currently-deployed baseline (zip or dir)")
    ap.add_argument("--prefix", default=None, help="only consider paths under this prefix")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    old = _filter(list_paths(a.old), a.prefix)
    new = _filter(list_paths(a.new), a.prefix)
    rep = compute(old, new)

    if a.json:
        print(json.dumps(rep, indent=2))
        return 0

    if not rep["deletions"]:
        print("OK  no overlay-orphans: every baseline path is present in the new build.")
        return 0

    print(f"OVERLAY WILL NOT REMOVE {len(rep['deletions'])} path(s) "
          f"(unzip -o cannot delete) -- add these to the deploy note:")
    for line in rep["rm_lines"]:
        print(f"    {line}")
    if rep["conversions"]:
        print()
        print("!! .py -> package CONVERSIONS (the old .py will SHADOW the new package "
              "until removed):")
        for old_py, pkg in rep["conversions"]:
            print(f"    {old_py}  ->  {pkg}   (rm {old_py} after overlay, before restart)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
