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


def requirement_lines(text):
    """Whole requirement lines, in file order, under the same skipping rules.

    `requirement_names` keeps only the stem, which is all the pre-@896 check
    needed and is the one thing a specifier comparison cannot work from. Both
    exist because tests/test_deploy_script.py imports the name-level parse
    directly; they share their skipping rules so the two cannot disagree about
    what counts as a requirement.
    """
    out = []
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        out.append(line)
    return out


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


class Unevaluable(Exception):
    """The question could not be asked. Never rendered as 'satisfied'."""


def unsatisfied(lines):
    """Requirement lines this interpreter does not SATISFY, in file order.

    @896 -- NAME RESOLUTION IS NOT VERSION SATISFACTION, and `unresolved` above
    only ever answered the first question: it called `version(name)` and threw
    the result away, so the specifier was never compared. Measured before the
    fix: a manifest containing `flask==0.0.1` against an installed flask 3.1.3
    exited 0 with silent stdout -- "every entry resolves", over a version that
    satisfies nothing. Every one of the 19 requirements this repo declares
    carries a specifier, so the blind spot covered all of them, and this tool is
    the sole instrument in all three recovery paths (deploy.sh, cloud-setup.sh).
    A reverted image restoring correct NAMES at wrong VERSIONS passed every one.

    Comparison is PEP 440 and is NOT hand-rolled: `1.10 > 1.9`, `2.0rc1 < 2.0`
    and `!=1.4.*` are not string operations, and a comparator that got them
    subtly wrong would fail correct manifests on the box -- the over-sensitive
    direction, which for a gate on every deploy is the worse one. So
    `packaging` absent raises Unevaluable rather than falling back to the
    name-only answer, which cannot be labelled as partial once it is on stdout.
    """
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError as exc:
        raise Unevaluable(
            "packaging is not importable (%s), so version specifiers cannot be "
            "compared; a name-only answer would be indistinguishable from a "
            "satisfied one" % exc)

    bad = []
    for line in lines:
        try:
            req = Requirement(line)
        except InvalidRequirement as exc:
            raise Unevaluable("cannot parse requirement %r: %s" % (line, exc))
        try:
            have = version(req.name)
        except PackageNotFoundError:
            bad.append(req.name)
            continue
        # why: prereleases satisfy a specifier here even when it does not say
        # so. A venv legitimately holding 2.0rc1 for `>=1.9` is satisfied, and
        # reporting it unsatisfied would send the caller into a reinstall loop
        # that cannot converge.
        if req.specifier and not req.specifier.contains(have, prereleases=True):
            bad.append(req.name)
    return bad


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

    try:
        missing = unsatisfied(requirement_lines(text))
    except Unevaluable as exc:
        # UNEVALUABLE, not satisfied. stdout stays empty so a caller reading it
        # for package names sees none; both callers already treat exit 2 as
        # "treat as NOT satisfied", so this needs no wiring on their side.
        print("cannot evaluate %s: %s" % (path, exc), file=sys.stderr)
        return 2

    if not missing:
        return 0
    print(" ".join(missing))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
