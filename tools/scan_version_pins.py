#!/usr/bin/env python3
"""scan_version_pins.py — standalone hardcoded-version-pin scanner."""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple

VER = re.compile(r'(\d+\.\d+\.\d+)')
APP_PIN = re.compile(r'__version__\s*==\s*["\'](3\.66\.\d+)["\']')
SOFT_PIN = re.compile(r'(?:assert|==)[^\n]*["\'](3\.66\.\d+)["\']')


def _iter_py(root: str, subdir: str) -> List[str]:
    base = os.path.join(root, subdir)
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules", ".git", "venv", ".venv")]
        for fn in filenames:
            if fn.endswith(".py"):
                found.append(os.path.join(dirpath, fn))
    return found


def _inside_literal(prefix: str) -> bool:
    """True if the position after `prefix` is inside a string literal — i.e. a
    `__version__ == "X"` here is fixture DATA (e.g. inside `_mktree({...})` or a
    `"path": 'assert __version__ == "X"\\n'` value), not a real assertion pin.
    Escaped quotes are removed first so they don't miscount."""
    p = prefix.replace('\\"', '').replace("\\'", '')
    return (p.count('"') % 2 == 1) or (p.count("'") % 2 == 1)


def scan_test_pins(root: str, expect: str, ignore: List[str] = ()):
    hard, soft = [], []
    for path in _iter_py(root, "tests"):
        rel = os.path.relpath(path, root)
        if any(ig in rel for ig in ignore):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    ma = APP_PIN.search(line)
                    if ma:
                        if _inside_literal(line[:ma.start()]):
                            continue   # fixture literal, not a real pin
                        if ma.group(1) != expect:
                            hard.append((rel, i, ma.group(1), line.strip()))
                        continue
                    ms = SOFT_PIN.search(line)
                    if ms and not _inside_literal(line[:ms.start()]) \
                            and ms.group(1) != expect:
                        soft.append((rel, i, ms.group(1), line.strip()))
        except OSError:
            continue
    return hard, soft


def scan_runtime_strays(root: str, expect: str) -> List[Tuple[str, int, str, str]]:
    out = []
    for path in _iter_py(root, "bulk_downloader"):
        if os.path.basename(path) == "__init__.py":
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    for m in VER.finditer(line):
                        v = m.group(1)
                        if v.startswith("3.66.") and "#" not in line.split(v)[0][-3:]:
                            out.append((os.path.relpath(path, root), i, v, line.strip()))
                            break
        except OSError:
            continue
    return out


def _run(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Hardcoded version-pin scanner")
    ap.add_argument("--root", default=".")
    ap.add_argument("--expect", required=True, help="the version that pins SHOULD equal")
    ap.add_argument("--tests-only", action="store_true")
    ap.add_argument("--ignore", nargs="*", default=[],
                    help="substring(s) of test paths to skip (e.g. fixture-bearing files)")
    args = ap.parse_args(argv)

    failed = False
    hard, soft = scan_test_pins(args.root, args.expect, ignore=args.ignore)
    if hard:
        failed = True
        print("APP VERSION-PIN MISMATCH (expected %s) — FAILS BUILD:" % args.expect)
        for p, ln, v, line in hard:
            print("  %s:%d  __version__ pinned to %s  | %s" % (p, ln, v, line[:90]))
    else:
        print("APP VERSION-PIN SCAN: OK (no __version__ test pin disagrees with %s)" % args.expect)
    if soft:
        print("INFORMATIONAL — other 3.66.x literals (verify these are not app-version pins):")
        for p, ln, v, line in soft:
            print("  %s:%d  %s  | %s" % (p, ln, v, line[:90]))

    if not args.tests_only:
        strays = scan_runtime_strays(args.root, args.expect)
        if strays:
            print("RUNTIME VERSION STRAYS (version should live only in __init__.py):")
            for p, ln, v, line in strays:
                print("  %s:%d  %s  | %s" % (p, ln, v, line[:90]))

    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(_run(sys.argv[1:]))
    except OSError as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(2)
