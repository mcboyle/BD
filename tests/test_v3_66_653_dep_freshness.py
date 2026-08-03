"""v3.66.653 -- S3.2: dependency-freshness advisory scanner (POS-2).

tools/dep_freshness.py reports drifted pins / unpinned / missing packages across
requirements*.txt vs installed versions. Advisory only -- never auto-bumps.

v3.66.848 ADDITION -- the declaration gate (bottom of this file).
dep_freshness's denominator is "names already written in requirements*.txt": it
asks whether a DECLARED pin is satisfied. That structurally excludes the failure
this repo actually hit twice -- an import that was never declared at all. lxml
was imported at three sites and declared nowhere, and `pip check` reported clean
throughout, because pip cannot see a requirement nobody wrote down (CLAUDE.md
section 5). Before v3.66.848 no test in the tree read the real requirements.txt
for these names, so deleting the lxml line left the whole band green.

WHY IT LIVES HERE and not in tests/test_deploy_script.py. Both files were read.
test_deploy_script.py's subject is scripts/deploy.sh, and every requirements.txt
it touches is a SYNTHETIC fixture written into a temp work tree
(test_deploy_script.py:357, :1207) so the script's behaviour can be observed on
a controlled input. Asserting there about the REAL repo manifest would put a
second, unrelated denominator in a file whose harness deliberately never reads
the real one. This file already takes "the repo's requirements manifests" as its
subject (test_build_report_over_real_repo_runs), so the gate joins its existing
denominator instead of importing a new one.

NOT A NEW FILE, on purpose: a new tests/*.py moves nine axis-6 gates plus
PIN_INDEX's test_files_scanned (CLAUDE.md section 4).

AND THIS GATE IS NOT ITSELF AXIS-6. It runs `git ls-files -- '*.py'`, which does
reach tests/, but the only use of a tests/ path is discarded: _first_party_names
takes stems from the repo root and tools/ ONLY (those are the directories whose
modules get imported by bare name via sys.path), and top-level directory names
from everything. Adding or renaming a tests/ file therefore cannot change what
this gate measures. Changing that filter WOULD make it axis-6 -- say so here if
you do.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parent.parent / "tools" / "dep_freshness.py"
_spec = importlib.util.spec_from_file_location("dep_freshness", _p)
df = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(df)


def test_parse_requirement_line():
    assert df.parse_requirement_line("flask>=3.0,<4.0") == {
        "name": "flask", "specifier": ">=3.0,<4.0", "raw": "flask>=3.0,<4.0"}
    assert df.parse_requirement_line("# a comment") is None
    assert df.parse_requirement_line("-r base.txt") is None
    assert df.parse_requirement_line("git+https://x/y.git") is None
    assert df.parse_requirement_line("requests")["specifier"] == ""
    # trailing inline comment stripped from the specifier
    assert df.parse_requirement_line("playwright>=1.45,<2.0  # pin")["specifier"] \
        == ">=1.45,<2.0"


def test_check_freshness_flags_drift_unpinned_missing_ok():
    reqs = {"requirements.txt": (
        "flask>=3.0,<4.0\n"        # ok
        "playwright>=1.45,<2.0\n"  # drift (installed 2.5)
        "requests\n"               # unpinned
        "ghostpkg==1.0\n"          # missing
    )}
    installed = {"flask": "3.1.0", "playwright": "2.5.0", "requests": "2.31.0"}
    rep = df.check_freshness(reqs, installed)
    assert rep["checked"] == 4, rep
    assert rep["ok"] == 1, rep
    drifted = {d["name"] for d in rep["drifted"]}
    assert drifted == {"playwright"}, rep
    assert {u["name"] for u in rep["unpinned"]} == {"requests"}, rep
    assert {m["name"] for m in rep["missing"]} == {"ghostpkg"}, rep


def test_satisfies_comparator_no_packaging_dep():
    # Self-contained comparator (no `packaging`): the exact range pins in the repo.
    assert df._satisfies("3.1.0", ">=3.0,<4.0") is True
    assert df._satisfies("4.0.0", ">=3.0,<4.0") is False
    assert df._satisfies("1.61.0", ">=1.45,<2.0") is True
    assert df._satisfies("2.5.0", ">=1.45,<2.0") is False
    assert df._satisfies("2.0.0", "==2.0") is True
    assert df._satisfies("2.0.1", "==2.0") is False
    assert df._satisfies("1.5", "!=1.5") is False
    assert df._satisfies("1.6", ">1.5") is True


def test_no_packaging_import_in_source():
    # Guard the regression: the deploy venv lacks `packaging`, so the tool must not
    # import it (the failure that took 654 red on stash).
    src = _p.read_text(encoding="utf-8")
    assert "import packaging" not in src and "from packaging" not in src


def test_check_freshness_name_canonicalization():
    # Underscore/case variants of the same distribution match.
    reqs = {"r.txt": "My_Pkg>=1.0\n"}
    installed = {"my-pkg": "1.2"}
    rep = df.check_freshness(reqs, installed)
    assert rep["ok"] == 1 and not rep["drifted"] and not rep["missing"], rep


def test_build_report_over_real_repo_runs():
    # Smoke: the real repo's requirements parse + check without raising.
    rep = df.build_report()
    assert rep["checked"] > 0
    assert set(rep) >= {"checked", "ok", "drifted", "unpinned", "missing", "errors"}


def test_main_check_exit_code_on_drift(capsys=None):
    # --check returns 1 only when drift is present; report path returns 0.
    import types
    rep_drift = {"checked": 1, "ok": 0, "drifted": [
        {"file": "r", "name": "x", "specifier": ">=2", "installed": "1"}],
        "unpinned": [], "missing": [], "errors": []}
    orig = df.build_report
    df.build_report = lambda root=None: rep_drift
    try:
        assert df.main(["--check"]) == 1
        rep_drift["drifted"] = []
        assert df.main(["--check"]) == 0
    finally:
        df.build_report = orig


# ---------------------------------------------------------------------------
# v3.66.848: the DECLARATION gate -- every third-party import the application
# package makes must be declared, or explicitly recorded as a decision not to.
#
# THE INSTRUMENT IS AST, AND THAT IS THE POINT. The census this gate replaces
# was derived by grep and was wrong in BOTH directions at once (CLAUDE.md
# section 1): it missed bulk_downloader/synthetic_tests.py:96, a real fail-open
# lxml importer, and it cited bulk_downloader/diagnostics_bundle.py:130, which
# is a string in a tuple of optional-dependency NAMES passed to __import__ for
# a version report -- not an import node. A grep for a distribution name sees
# the second and not the first; an AST walk over Import/ImportFrom sees the
# first and not the second.
#
# THE SUBJECT IS bulk_downloader/. The predicate fixes the subject, the
# instrument fixes the denominator, and they are different jobs. tools/ and
# tests/ import build-only and test-only distributions (pytest, hypothesis,
# atheris, ...) that have no business in a runtime manifest, so widening the
# subject to the whole tree would force either a much larger exception list or
# a false failure. bulk_downloader/ IS what the service runs.
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent

# Import name -> distribution name, where PyPI and the module disagree. Only
# names actually reachable from bulk_downloader/ need an entry; an unmapped
# name falls through to itself, which is correct for every other case here.
_DIST_ALIASES = {
    "bs4": "beautifulsoup4",
    "PIL": "pillow",
    "yt_dlp": "yt-dlp",
}

# Third-party imports in bulk_downloader/ that are deliberately declared in NO
# requirements manifest. Each is an operator decision with a reason, not a
# waiver of convenience: adding a name here is how you say "this stays
# undeclared", and it must come with why. A NEW third-party import that is
# neither declared nor listed here fails the gate, which is the whole point.
_UNDECLARED_BY_DESIGN = {
    "werkzeug": "flask's own hard dependency; pinning it separately would let "
                "it drift out of flask's supported range",
    "requests": "transitive under several declared distributions; the modules "
                "that touch it (cli_dashboard, plugin paths) soft-import it",
    "rich": "cli_dashboard TUI decoration only; soft-imported, plain text "
            "otherwise",
    "PIL": "system-tray icon rendering (tray_app.py) -- the tray is a desktop "
           "affordance the headless deploy never starts",
    "pystray": "the tray itself; same reason as PIL",
    "plexapi": "Plex library sync, opt-in per install",
    "psycopg": "optional Postgres backend; sqlite is the default and the only "
               "backend the deploy uses",
    "psycopg2": "the legacy binding for the same optional Postgres backend",
    "subliminal": "subtitle fetching, opt-in",
    "babelfish": "language-code helper reached only through subliminal",
}

# These two must be in requirements.txt SPECIFICALLY, not merely somewhere.
# scripts/deploy.sh step [5] resolves tools/check_requirements.py against
# requirements.txt only (DEFAULT_REQUIREMENTS at check_requirements.py:56), so
# a pin that migrates to requirements-optional.txt silently stops being
# installed on the box. lxml and cssselect back the same selector/ARIA surface
# and both fail open, so their absence is invisible at runtime.
_CORE_MANIFEST_REQUIRED = {"lxml", "cssselect"}


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _tracked_py() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=str(_REPO), capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def _first_party_names(tracked: list[str]) -> set[str]:
    """Module names that resolve inside this repo, so they are never a
    third-party distribution however they are imported.

    Two sources, and deliberately only two: every top-level tracked directory
    (bulk_downloader, tools, tests, ...), and the stems of tracked .py files at
    the repo root or under tools/ -- the two locations whose modules get
    imported by bare name once they are on sys.path. tools/ matters: without
    it, `import build_template_from_wacz` (dom_analyzer.py) and
    `import template_drift_report` (template_keystone.py) read as undeclared
    PyPI distributions. tests/ stems are excluded so that adding a test file
    cannot move this gate's denominator.
    """
    names = {p.split("/")[0] for p in tracked if "/" in p}
    names |= {Path(p).stem for p in tracked
              if "/" not in p or p.startswith("tools/")}
    return names


def _declared_names() -> dict[str, list[str]]:
    """canonical distribution name -> the requirements manifests declaring it.

    All four requirements*.txt are read, not just the core one: a name declared
    in requirements-optional.txt IS declared, and calling it undeclared would
    be a false failure. Which manifest matters is a separate question, asked by
    _CORE_MANIFEST_REQUIRED below.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(_REPO.glob("requirements*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = df.parse_requirement_line(line)
            if parsed:
                found.setdefault(_canon(parsed["name"]), []).append(path.name)
    return found


def _third_party_imports() -> tuple[dict[str, set[str]], int, int]:
    """AST walk over tracked bulk_downloader/*.py.

    Returns (name -> importing files, files parsed, import nodes seen). The two
    counts come back so the caller can refuse to report on an empty scan.
    """
    tracked = _tracked_py()
    first = _first_party_names(tracked)
    stdlib = set(sys.stdlib_module_names)
    tops: dict[str, set[str]] = {}
    parsed = 0
    nodes = 0
    errors = []
    for rel in tracked:
        if not rel.startswith("bulk_downloader/"):
            continue
        text = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:          # never skip silently -- a file this
            errors.append("%s: %s" % (rel, exc))   # walk cannot read shrinks
            continue                                # the denominator
        parsed += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # A relative import (level > 0) is first-party by construction.
                if node.level or not node.module:
                    continue
                names = [node.module.split(".")[0]]
            else:
                continue
            nodes += 1
            for top in names:
                if top in stdlib or top in first:
                    continue
                tops.setdefault(top, set()).add(rel)
    assert not errors, "bulk_downloader sources that would not parse: %s" % errors
    return tops, parsed, nodes


def test_third_party_imports_are_declared():
    tops, parsed, nodes = _third_party_imports()
    declared = _declared_names()

    # DENOMINATOR FIRST. Each of these is a way the walk could report "all
    # declared" while having examined nothing -- a broken git invocation, a
    # subject filter that matches no file, an AST predicate that collects no
    # node, a requirements glob that reads no manifest, or a canonicalization
    # that joins nothing. A scan over an empty set is UNKNOWN, and unknown
    # fails (CLAUDE.md section 0).
    assert parsed > 0, "no bulk_downloader/*.py parsed -- nothing was examined"
    assert nodes > 0, "no import nodes found in %d files -- the AST predicate " \
                      "is not matching" % parsed
    assert tops, "zero third-party imports found -- the stdlib/first-party " \
                 "filter is swallowing the whole subject"
    assert declared, "no names parsed out of any requirements*.txt"
    joined = {t for t in tops if _canon(_DIST_ALIASES.get(t, t)) in declared}
    assert joined, "no import name joined to any declared name -- the " \
                   "import-name -> distribution join is broken, so every " \
                   "name would read as undeclared"

    undeclared = sorted(
        t for t in tops
        if _canon(_DIST_ALIASES.get(t, t)) not in declared
        and t not in _UNDECLARED_BY_DESIGN)
    assert not undeclared, (
        "third-party imports in bulk_downloader/ declared in no "
        "requirements*.txt and not recorded in _UNDECLARED_BY_DESIGN: %s\n"
        "Declare the distribution, or add it to _UNDECLARED_BY_DESIGN WITH A "
        "REASON. Import sites: %s" % (
            undeclared, {t: sorted(tops[t]) for t in undeclared}))


def test_undeclared_by_design_names_are_still_imported_and_still_undeclared():
    """The reverse direction, which keeps the exception list from rotting.

    Two failure modes it closes. A name that stops being imported leaves a
    stale waiver behind that would silence a future re-introduction. A name
    that BECOMES declared while sitting on the waiver list means the list is
    now lying -- and, worse, it is the shape that would let a broken
    requirements parser (one that matched every line, say) pass the gate above
    vacuously, because every name would read as declared.
    """
    tops, _parsed, _nodes = _third_party_imports()
    declared = _declared_names()
    assert _UNDECLARED_BY_DESIGN, "the waiver list is empty -- nothing to check"

    stale = sorted(n for n in _UNDECLARED_BY_DESIGN if n not in tops)
    assert not stale, (
        "_UNDECLARED_BY_DESIGN names no longer imported by bulk_downloader/: "
        "%s -- remove them" % stale)

    now_declared = sorted(
        n for n in _UNDECLARED_BY_DESIGN
        if _canon(_DIST_ALIASES.get(n, n)) in declared)
    assert not now_declared, (
        "these are on the waiver list but ARE declared: %s (in %s) -- take "
        "them off the list" % (
            now_declared,
            {n: declared[_canon(_DIST_ALIASES.get(n, n))] for n in now_declared}))

    for name, reason in _UNDECLARED_BY_DESIGN.items():
        assert reason.strip(), "%s is waived with no reason given" % name


def test_lxml_and_cssselect_are_declared_in_the_core_manifest():
    """v3.66.848's actual subject, pinned by name.

    The gate above would still pass if lxml moved to requirements-optional.txt,
    and the box would then stop installing it -- so this asks the narrower
    question directly. Both are imported from bulk_downloader/ and both fail
    open, which is exactly why nothing else notices their absence.
    """
    tops, _parsed, _nodes = _third_party_imports()
    core = _REPO / "requirements.txt"
    body = core.read_text(encoding="utf-8")
    in_core = {_canon(p["name"]) for p in
               (df.parse_requirement_line(ln) for ln in body.splitlines())
               if p}
    assert in_core, "requirements.txt parsed to zero names"
    for name in sorted(_CORE_MANIFEST_REQUIRED):
        assert name in tops, (
            "%s is no longer imported from bulk_downloader/ -- if that is "
            "intended, take it out of _CORE_MANIFEST_REQUIRED; until then "
            "this pin is asserting over a subject that left" % name)
        assert _canon(name) in in_core, (
            "%s is imported at %s but is not declared in requirements.txt, "
            "which is the only manifest scripts/deploy.sh step [5] resolves"
            % (name, sorted(tops[name])))
