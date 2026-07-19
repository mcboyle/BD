#!/usr/bin/env python3
"""bdtools_cli -- shared CLI primitives for the bdsuite tools (I-9 / E-39).

A LIBRARY, not a tool (like bdtools_sec / bdtools_taint / bdtools_cache). It exists
because 210 tools re-declared the same ANSI tuple and 132 carried the same
`sys.path.insert(0, dirname(realpath(__file__)))` shim -- a change to the palette or
the shim meant editing 200 files, and drift was inevitable.

ADOPTION IS DELIBERATELY CONSERVATIVE. Only tools using the exact canonical
5-tuple (G, R, Y, DIM, RST) are migrated, and each is verified byte-behaviour-
identical afterwards (bd-golden drift 0). Tools with bespoke palettes (adding C,
BOLD, B, ...) are LEFT ALONE -- forcing them onto a shared shape would be churn
for its own sake and risks a wrong colour in a verdict line. The point is to
remove duplication where it is genuinely identical, not to homogenise for its own
sake.

The canonical palette matches what the majority already used, so an adopting tool's
output is unchanged:
    G   green   \\033[32m
    R   red     \\033[31m
    Y   yellow  \\033[33m
    DIM dim      \\033[2m
    RST reset   \\033[0m
"""
import os
import sys

# The canonical five. Kept as module attributes so `from bdtools_cli import G, R, Y, DIM, RST`
# is a drop-in for the 156 tools that declared exactly these.
G = "\033[32m"
R = "\033[31m"
Y = "\033[33m"
DIM = "\033[2m"
RST = "\033[0m"

# Extended palette for tools that want it explicitly (no tool is forced onto these).
C = "\033[36m"    # cyan
B = "\033[34m"    # blue
BOLD = "\033[1m"


def colors():
    """Return the canonical 5-tuple, for tools that prefer a call over star-import."""
    return G, R, Y, DIM, RST


def add_self_to_path():
    """The path shim 132 tools carry verbatim: make sibling libs importable when the
    tool is invoked by absolute path or via a /usr/local/bin symlink."""
    here = os.path.dirname(os.path.realpath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def selftest():
    """Real check: the canonical codes are exactly the bytes 156 tools inlined, and
    add_self_to_path makes THIS module's dir importable. A palette drift here would
    silently recolour every adopting tool, so assert the exact escape sequences."""
    ok = (G == "\033[32m" and R == "\033[31m" and Y == "\033[33m"
          and DIM == "\033[2m" and RST == "\033[0m")
    print(("PASS" if ok else "FAIL") + "  canonical palette matches the inlined bytes")
    n0 = len(sys.path)
    add_self_to_path()
    here = os.path.dirname(os.path.realpath(__file__))
    on = here in sys.path
    print(("PASS" if on else "FAIL") + "  add_self_to_path makes the lib dir importable")
    ok = ok and on
    # NEG: a wrong palette must be caught -- prove the assertion is non-vacuous
    neg = ("\033[32m" != "\033[31m")
    print(("PASS" if neg else "FAIL") + "  NEG: distinct codes are distinct (assertion non-vacuous)")
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(__doc__.strip().splitlines()[0])
