#!/usr/bin/env python3
"""Report which requirements.txt entries do not resolve in THIS interpreter.

WHY THIS IS NOT `pip check`. `pip check`'s denominator is the set of INSTALLED
distributions, which structurally excludes an uninstalled requirement -- so it
reports clean for exactly the failure it is being asked about (CLAUDE.md
section 0). Measured: a container reported "runtime deps OK" with
beautifulsoup4 and pytest-xdist both absent. `pip install -r` exiting 0 is not
proof either: it can succeed on a subset, or succeed against a different
interpreter than the one that will import them.

WHY A FILE RATHER THAN A HEREDOC. This logic previously lived inline in
scripts/cloud-setup.sh, and scripts/deploy.sh needs the same question answered
on the deploy box. CLAUDE.md section 5 records what three inlined copies of a
package list cost: they drift, and the copy nobody updated is the one the box
runs. One file, two callers.

WHY IT ANSWERS FOR THE INVOKING INTERPRETER. `importlib.metadata` resolves
against the running interpreter's own sys.path, so the answer is about the
python that executes this file. That is the property both callers need -- they
invoke it as the venv python whose site-packages the service will import from.
Three python resolution paths exist on the box (system / prestaged / service
venv) and they carry different package sets, so "is it installed" is only ever
a question about one of them.

CONTRACT (both callers depend on all three codes):

    exit 0    every entry resolves, over a NON-EMPTY set of names; stdout SILENT
    exit 1    one or more do not resolve; their names, space-separated, on stdout
    exit 2    UNEVALUABLE -- the file could not be read, could not be parsed,
              or parsed to ZERO requirement names

Exit 2 is not a softer exit 0. Unknown is a third state and it fails: a caller
that renders "could not evaluate" as "satisfied" has built the gate this file
exists to replace.

WHY ZERO NAMES IS EXIT 2 AND NOT EXIT 0. `unresolved([])` is `[]`, so a
readable file declaring no names would otherwise report "every entry resolves"
over an EMPTY denominator -- true, and useless. That is the same defect one
level up as the `pip check` behaviour above, and the same shape CLAUDE.md
section 2 records for bd-guardcheck, which reported "0 ok, 0 drifted, 7
missing" and exited 0 on a clean tree until v3.66.818: a zero-in-every-bucket
summary is a failure signal, not a pass. The condition is reachable in the
field -- a truncated write, a caller handed a path that exists but is the wrong
file, or a refactor that moves the deps and leaves a stub behind -- and in
every one of those cases the honest answer is that nothing was verified.

Usage: check_requirements.py [PATH]   (default: requirements.txt, relative to cwd)
"""
from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, version

DEFAULT_REQUIREMENTS = "requirements.txt"

# A requirement line ends at the first version specifier, extras bracket,
# environment marker or space. Kept identical to the parse this replaced so the
# two callers cannot disagree about what counts as a name.
_NAME_END = re.compile(r"[<>=!~\[; ]")


def requirement_names(text):
    """Distribution names declared in a requirements.txt body, in file order.

    Comments, blanks and option lines (-e, -r, --index-url, ...) are skipped:
    an option line names no distribution, so treating one as a name would
    manufacture a missing package that was never required.
    """
    names = []
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = _NAME_END.split(line, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


def unresolved(names):
    """The subset of `names` importlib.metadata cannot find a version for."""
    missing = []
    for name in names:
        try:
            version(name)
        except PackageNotFoundError:
            missing.append(name)
    return missing


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    path = argv[0] if argv else DEFAULT_REQUIREMENTS

    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        # UNEVALUABLE. Say which file and why on stderr, and keep stdout empty
        # so a caller that reads stdout for names never sees an error message
        # mistaken for a package.
        print("cannot evaluate %s: %s" % (path, exc), file=sys.stderr)
        return 2

    try:
        names = requirement_names(text)
    except Exception as exc:                                # pragma: no cover
        print("cannot parse %s: %s" % (path, exc), file=sys.stderr)
        return 2

    if not names:
        # UNEVALUABLE, not satisfied. The file was readable and parsed, but it
        # declares nothing, so the question "does every entry resolve" has an
        # empty denominator and its answer says nothing about this
        # interpreter. Name the condition on stderr -- exit 2 alone is also
        # what an unreadable file produces, so the code by itself does not tell
        # a caller which of the two fired. stdout stays empty, per the
        # contract, so a caller reading it for package names sees none.
        print("cannot evaluate %s: parsed to zero requirement names" % path,
              file=sys.stderr)
        return 2

    missing = unresolved(names)
    if not missing:
        return 0
    print(" ".join(missing))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
