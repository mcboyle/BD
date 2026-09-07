#!/usr/bin/env python3
"""One denominator for toolchain/bin, shared by every auditor of it.

Row 469: bd-selfcheck, bd-tools, bd-tool-lint and bd-tool-smoke each derived
their own population of toolchain/bin with a private glob, each got a different
count, none stated an exclusion and none reconciled to the directory. A tool
absent from an auditor's denominator is unaudited while the auditor prints
CLEAN -- the shape A7 forbids, applied to the instruments that enforce A7.

This module derives the population FROM THE TREE (every regular file in the bin
directory), classifies each name that is not an invocable tool with a REASON
drawn from a closed vocabulary, and refuses to certify a census in which any
file is neither examined nor excluded. An auditor that cannot examine a file
reads UNKNOWN and fails; it never drops the file silently.
"""
from __future__ import annotations

import os

# Closed vocabulary. An exclusion whose reason is not one of these does not
# reconcile -- "silently omitted" cannot be spelled here.
REASONS = {
    "helper-module": "leading-underscore module: imported by tools, never invoked as one",
    "library-module": "importable library beside the tools: no shebang, no entrypoint",
}

# Why an auditor could not MEASURE a tool it did examine. An unmeasured tool
# stays IN the examined set and is named in the output; it is never counted
# clean and never dropped. CLASSIFICATION MUST NOT GATE EXAMINATION: a tool
# whose shebang is wrong would otherwise leave two auditors' view entirely,
# which is the row's own failure shape.
UNMEASURED_REASONS = {
    "not-python-source": "no python shebang: a Python source analyzer cannot read it",
    # Row 745 + CLAUDE.md A7. An unreadable MEMBER is a failed measurement, not a
    # membership decision, and it is not the same event as a denominator that
    # cannot be derived at all. It stays in the population, is examined BY NAME,
    # and its auditor refuses over it -- collapsing it into the whole-population
    # UNKNOWN loses the one fact an operator needs: WHICH file could not be read.
    "unreadable": "permission denied or I/O error: no auditor can read it",
}


class PopulationUnavailable(Exception):
    """The denominator could not be derived -- UNKNOWN, never OK."""


def expected(bindir):
    """Tree-derived denominator: every regular file in bindir, sorted.

    Missing or empty is UNKNOWN, not an empty population: a verdict over
    nothing certifies nothing.
    """
    if not os.path.isdir(bindir):
        raise PopulationUnavailable(
            "bin dir absent: %s (a denominator cannot be derived from a tree "
            "that does not exist)" % bindir)
    names = sorted(f for f in os.listdir(bindir)
                   if os.path.isfile(os.path.join(bindir, f)))
    if not names:
        raise PopulationUnavailable(
            "bin dir empty: %s (a verdict over an empty population is not a "
            "verdict)" % bindir)
    return names


def classify(bindir, name):
    """Reason `name` is not an invocable Python tool, or None if it is one.

    Every auditor asks this same question, so the four cannot disagree about
    WHY a file is out of scope even when they disagree about whether their own
    checks apply to it.
    """
    if name.startswith("_"):
        return "helper-module"
    path = os.path.join(bindir, name)
    try:
        with open(path, "rb") as fh:
            first = fh.readline()
    except OSError:
        # Unreadable is UNMEASURED, never EXCLUDED and never a population-wide
        # abort: the file is in the tree, so it is in the denominator. `readable`
        # below is what records it, by name, with its own declared reason.
        return None
    if not first.startswith(b"#!"):
        return "library-module"
    return None


def readable(bindir, name):
    """Can any auditor read this member at all? Measurability, never membership."""
    try:
        with open(os.path.join(bindir, name), "rb") as fh:
            fh.readline()
    except OSError:
        return False
    return True


def is_python_source(bindir, name):
    """Can a Python source analyzer read this tool?

    This decides MEASURABILITY, never MEMBERSHIP. A tool that answers False is
    still examined by every auditor and is reported by name as unmeasured.
    """
    with open(os.path.join(bindir, name), "rb") as fh:
        first = fh.readline()
    return first.startswith(b"#!") and b"python" in first


class Census(object):
    """expected = examined + excluded, or the auditor is UNKNOWN."""

    def __init__(self, bindir, expected_names, examined, excluded, unmeasured=None):
        self.bindir = bindir
        self.expected = sorted(expected_names)
        self.examined = sorted(examined)
        self.excluded = dict(excluded)
        self.unmeasured = dict(unmeasured or {})
        exp = set(self.expected)
        exa = set(self.examined)
        exc = set(self.excluded)
        self.unknown = sorted(exp - exa - exc)
        self.stray = sorted(exa - exp) + sorted(exc - exp)
        self.double = sorted(exa & exc)
        self.unreasoned = sorted(n for n, r in self.excluded.items()
                                 if r not in REASONS)
        # An unmeasured tool must be one this auditor EXAMINES, with a declared
        # reason -- otherwise it is a silent drop wearing another name.
        self.unreasoned += sorted(n for n, r in self.unmeasured.items()
                                  if r not in UNMEASURED_REASONS or n not in exa)

    @property
    def ok(self):
        return not (self.unknown or self.stray or self.double or self.unreasoned)

    def line(self):
        return ("POPULATION bin=%s expected=%d examined=%d excluded=%d unknown=%d "
                "unmeasured=%d"
                % (self.bindir, len(self.expected), len(self.examined),
                   len(self.excluded), len(self.unknown), len(self.unmeasured)))

    def members_line(self):
        """The examined SET, member by member.

        Row 469 asks the four auditors to agree on the examined SET, not on a
        NUMBER: four identical counts over four different sets is the same
        blindness with better arithmetic. The gate reads this line.
        """
        return "POPULATION-EXAMINED " + " ".join(self.examined)

    def unmeasured_line(self):
        return "POPULATION-UNMEASURED " + " ".join(
            "%s=%s" % (n, r) for n, r in sorted(self.unmeasured.items()))

    def diagnosis(self):
        out = []
        if self.unknown:
            out.append("UNKNOWN -- in the tree but neither examined nor excluded: "
                       + " ".join(self.unknown))
        if self.stray:
            out.append("UNKNOWN -- claimed but not in the tree: " + " ".join(self.stray))
        if self.double:
            out.append("UNKNOWN -- both examined and excluded: " + " ".join(self.double))
        if self.unreasoned:
            out.append("UNKNOWN -- excluded without a declared reason: "
                       + " ".join(self.unreasoned))
        return "\n".join(out)


def census(bindir, examined, excluded, unmeasured=None):
    """Build a Census over the tree-derived denominator."""
    return Census(bindir, expected(bindir), examined, excluded, unmeasured)


def census_from_examined(bindir, examined, unmeasured=None):
    """The census an auditor owes for the list it ACTUALLY uses.

    An auditor that reports one set and examines another is the original defect
    wearing a POPULATION line, so every auditor builds its census FROM the list
    it goes on to work with. Anything in the tree that the list drops and that
    is not a declared non-tool lands in `unknown` -> report() refuses.
    """
    names = expected(bindir)
    have = set(examined)
    excluded = {}
    for n in names:
        if n in have:
            continue
        why = classify(bindir, n)
        if why is not None:
            excluded[n] = why
    return Census(bindir, names, examined, excluded, unmeasured)


def scoped_census(bindir, python_analyzer=False):
    """THE census. Every auditor gets the SAME examined set.

    The only files outside it are the ones that are not tools at all (a helper
    module, a library module), each with a declared reason. `python_analyzer`
    does NOT change membership: it only records which examined tools this
    auditor's analyzer cannot read, by name, as unmeasured.
    """
    names = expected(bindir)
    examined, excluded, unmeasured = [], {}, {}
    for n in names:
        why = classify(bindir, n)
        if why is None:
            examined.append(n)
            if not readable(bindir, n):
                unmeasured[n] = "unreadable"
            elif python_analyzer and not is_python_source(bindir, n):
                unmeasured[n] = "not-python-source"
        else:
            excluded[n] = why
    return Census(bindir, names, examined, excluded, unmeasured)


def report(census_obj, stream):
    """Emit the census on `stream` (stderr: stdout may be JSON).

    Returns True when the census reconciles; False means the caller must fail
    with UNKNOWN rather than print a verdict over a population it cannot state.
    """
    stream.write(census_obj.line() + "\n")
    stream.write(census_obj.members_line() + "\n")
    if census_obj.unmeasured:
        stream.write(census_obj.unmeasured_line() + "\n")
    if not census_obj.ok:
        stream.write(census_obj.diagnosis() + "\n")
        return False
    return True


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for name, first in (("bd-a", "#!/usr/bin/env python3\n"),
                            ("bd-sh", "#!/bin/bash\n"),
                            ("bd_lib.py", '"""lib."""\n'),
                            ("_bd_helper.py", "#!/usr/bin/env python3\n")):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(first)
        wide = scoped_census(d)
        narrow = scoped_census(d, python_analyzer=True)
        ok = (len(wide.expected) == 4 and wide.ok and narrow.ok
              and wide.examined == narrow.examined == ["bd-a", "bd-sh"]
              and not wide.unmeasured
              and narrow.unmeasured.get("bd-sh") == "not-python-source")
        broken = Census(d, wide.expected, ["bd-a"], {})
        ok = ok and not broken.ok and "bd-sh" in broken.unknown
        print("PASS" if ok else "FAIL", " census reconciles and refuses a silent drop")
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest() if "--selftest" in sys.argv else 0)
