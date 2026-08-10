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

AND THIS GATE IS NOW ITSELF AXIS-6. It was not, and the docstring here said so;
that was true only because the gate could not see tests/ at all -- the same
blindness that let an undeclared `requests` sit in two tracked test files while
the gate reported clean. The declaration gate below now scans every tracked file
it can read as Python and resolves each import against the importing file's OWN
directory (so tests/conftest.py, tests/_env.py and tests/capture_lanes.py do not
read as PyPI distributions), which means adding or renaming a tests/ file moves
what it measures. Band this file on any cut that adds or renames a tracked
Python file.

THIRD REPAIR, v3.66.848. A reviewer found the section 0 shape here a third time,
in three distinct places, and all three are fixed by measurement rather than by
assertion:
  * The denominator was `git ls-files -- '*.py'` while the scope note claimed
    toolchain/ and project-knowledge/. MEASURED: 469 tracked python-shebang
    scripts in those two directories carry no extension at all, so the glob
    reached 2.5% and 14.5% of their Python. `import zeep` added to tracked
    toolchain/bin/bd-guardcheck left this gate GREEN (15 passed). Fixed by
    typing tracked files on their shebang, not by narrowing the claim.
  * _declared_names did not follow requirements-dev.txt's `-r requirements.txt`,
    so 15 core pins read as absent from the dev manifest and a tests-only
    import of any of them would be told to duplicate a pin it already has.
  * _DIST_ALIASES was hand-written and therefore incomplete: `import xdist`
    failed as undeclared while pytest-xdist is pinned. The map is now derived
    from installed metadata with the hand list as an override.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import importlib.util
import importlib.metadata as md
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
# v3.66.848: the DECLARATION gate -- every third-party import a tracked .py
# file makes must be declared, or explicitly recorded as a decision not to.
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
# THE SUBJECT IS THE WHOLE TRACKED TREE, SPLIT IN TWO BY MANIFEST. This gate's
# first version scanned bulk_downloader/ only, so tests/, tools/, toolchain/,
# bin/, scripts/, live_tests/, docs/ and project-knowledge/ sat structurally
# outside its subject -- the CLAUDE.md section 0 shape, reproduced inside the
# gate written to catch that shape, and it is now the THIRD gate on this branch
# to ship with it. It reported clean over `requests`, hard-imported by two
# tracked test files and declared in no manifest (MEASURED: blocking requests
# took those two files to 7 failed / 2 passed, control 9 passed).
#
# The SECOND widening did not finish the job, and the shape survived a repair:
# the denominator stayed `git ls-files -- '*.py'` while the scope note named
# toolchain/ and project-knowledge/, whose Python is 469 EXTENSIONLESS bd-*
# scripts -- 2.5% and 14.5% covered. `git ls-files` typing files on their
# extension is the same instrument/predicate distinction CLAUDE.md section 1
# draws: the instrument (git) fixed the denominator, the predicate ('*.py')
# still excluded the subject. Files are now typed on their shebang, and the
# seven undeclared names that were hiding there are waived by name below.
#
# The scope was not merely widened, because the two halves answer to DIFFERENT
# manifests: a runtime import belongs in requirements.txt, a test-only import
# in requirements-dev.txt. So there are two assertions over disjoint halves
# whose union is every tracked .py, plus a third that holds a tests/-only name
# to the dev manifest specifically. Each NAMES its own scope in the failure
# message rather than leaving it implied by silence.
#
# WHAT NO HALF COVERS, stated because a gate must say what it cannot see: the
# predicate is ast.Import / ast.ImportFrom, so a name reached through
# __import__(), importlib.import_module() or pytest.importorskip() is
# structurally invisible here. diagnostics_bundle.py:130 is the live example.
# That is also why pytest.importorskip is an acceptable repair for a test-only
# import: not because it hides the name from this walk, but because it turns an
# absent dependency into a SKIP -- which says so -- instead of an error.
#
# THIS GATE IS AXIS-6 (CLAUDE.md section 4). It was not, and the change is
# deliberate: resolving an import against the importing file's own directory is
# what keeps tests/conftest.py, tests/_env.py and tests/capture_lanes.py from
# reading as PyPI distributions, and that resolution moves when a tests/ file
# is added or renamed. Band this file on any cut that adds or renames a
# tracked .py.
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent

# Import name -> distribution name, where PyPI and the module disagree.
#
# DERIVED, NOT HAND-ASSERTED -- and the hand list stays as the override.
# A hand-written map carries only the aliases somebody remembered, and this one
# cried wolf for the ones it did not: MEASURED at v3.66.848, injecting
# `import xdist` into a tracked tests/ file took
# test_third_party_imports_outside_the_app_package_are_declared RED with
# "declared in no requirements*.txt", while pytest-xdist is pinned in
# requirements.txt. Over-sensitivity is a soundness bug (CLAUDE.md section 0),
# so the map is now built from installed metadata and _DIST_ALIAS_OVERRIDES is
# applied last.
#
# WHAT THE DERIVED HALF COVERS: every top-level import name that a distribution
# installed in the RUNNING INTERPRETER provides, read from
# importlib.metadata.packages_distributions(). Measured in this container: 70
# top-level names, of which 9 differ from the distribution providing them --
# xdist -> pytest-xdist, bs4 -> beautifulsoup4, attr -> attrs, _pytest and py
# -> pytest, markdown_it -> markdown-it-py, _cffi_backend -> cffi, plus two
# metadata artefacts (__pycache__, a mypyc shared object).
#
# WHAT IT CANNOT COVER, said out loud because a gate must state what it cannot
# see:
#   * A distribution that is NOT installed here contributes nothing. pillow and
#     yt-dlp are both absent from this container's metadata (measured:
#     packages_distributions() has no PIL and no yt_dlp key), so those two
#     entries stay hand-written. The override list is load-bearing, not a
#     convenience.
#   * A top-level name provided by MORE THAN ONE distribution is left unmapped,
#     because picking one would be a guess. Measured: exactly one such name
#     here, `jaraco`, from jaraco.classes / jaraco.functools / jaraco.context.
#   * The derived half is a property of the ENVIRONMENT the gate runs in, not
#     of the tree, so it can differ between this container and the box. It may
#     therefore only ADD aliases: the override is applied afterwards and wins,
#     and no hand entry is deleted just because the derivation happened to
#     cover it here.
# An unmapped name still falls through to itself, which is correct for every
# other case in this tree.
_DIST_ALIAS_OVERRIDES = {
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
    "requests": "transitive under EXACTLY ONE declared distribution, and only "
                "through the posture-sensitive optional manifest: "
                "requirements-cloak.txt's cloakbrowser[geoip] -> geoip2>=4.0 "
                "-> requests>=2.24.0,<3.0.0 (MEASURED from installed metadata "
                "at v3.66.848). psutil and pytest name requests only under "
                "their 'dev'/'testing' extras, which BD never asks for, so "
                "nothing in requirements.txt pulls it in and install_linux's "
                "cloak step is NON-FATAL by design -- treat it as possibly "
                "absent. Every bulk_downloader importer soft-imports it "
                "(site_weather, webhooks, discovery, tpdb, wayback_cdx, "
                "selector_playground, cli_dashboard, tray_app) and returns a "
                "'requests not installed' result rather than raising",
    "rich": "cli_dashboard TUI decoration only; soft-imported, plain text "
            "otherwise",
    "PIL": "system-tray icon rendering (tray_app.py) -- the tray is a desktop "
           "affordance the headless deploy never starts",
    "pystray": "the tray itself; same reason as PIL",
    "plexapi": "Plex library sync, opt-in per install",
    # psycopg's waiver was REMOVED 2026-08-10: it is declared in
    # requirements-test.txt now (operator decision -- see the comment on the
    # pin), and the anti-rot check below correctly fails a waiver for a
    # declared name.
    "psycopg2": "the legacy binding for the optional Postgres backend "
                "(plugin_kv.py's fallback import); the modern binding is a "
                "declared test dependency, this one stays undeclared",
    "subliminal": "subtitle fetching, opt-in",
    "babelfish": "language-code helper reached only through subliminal",
}

# The same decision, for the half of the tree that is NOT the application
# package. Kept as a second dict rather than merged into the first so that a
# waiver cannot silently widen from "a dev tool imports this" to "the service
# imports this" -- the two questions have different consequences and different
# manifests.
_UNDECLARED_OUTSIDE_BY_DESIGN = {
    "werkzeug": "flask's own hard dependency, so it arrives with the declared "
                "flask pin; the in-process test servers and "
                "project-knowledge/spa_serve.py reach make_server through "
                "flask's install, not on their own",
    "PIL": "project-knowledge/build_montage.py and build_navigator.py are "
           "one-off montage builders that read an off-box capture directory "
           "(/home/claude/capture); documentation scratch, run by no lane",
    "atheris": "tools/fuzz_probe.py's coverage-guided harness, try/except "
               "guarded into HAS_ATHERIS; it needs a clang build and the "
               "whole Z.8 section is explicitly best-effort",
    "hypothesis": "tools/code_intelligence/fuzz_service.py only, inside the "
                  "branch that has already been handed a strategy factory; a "
                  "dev fuzz service, not a path any lane or the service runs",
    "markdown": "tools/cockpit_console.py and tools/framework_dashboard.py "
                "render docs when it is present; both try/except to _md = "
                "None and fall back to raw text",
    "paho": "docs/plugin_examples/notify_events.py is an EXAMPLE plugin the "
            "operator copies and edits. BD never imports it, and the import "
            "is try/except guarded inside the publish helper",
    # psycopg's waiver was REMOVED 2026-08-10: declared in
    # requirements-test.txt now (see the comment on the pin). The four MOD3
    # tests still skip unless MOD3_PG_TEST_DSN is set and a server answers --
    # declaring the driver changed where it is installed, not when it is used.
    "bd_dev_inspect": "an out-of-tree, dev-only inspection package that is on "
                      "no index and in no manifest by design. "
                      "tools/capture_session.py catches ImportError and "
                      "tests/test_v3_66_59_redactor_seam.py skips",
    # The seven below entered the subject at v3.66.848 when the denominator
    # stopped being a *.py glob (see _tracked_py). They are REAL findings, not
    # a retreat: each was undeclared and invisible, and each is now waived with
    # a reason measured at its import site. All seven live in the bd-* operator
    # scripts, which exist in two identical tracked copies (toolchain/bin/<x>
    # and project-knowledge/<x>; md5 verified equal for bd-opv, bd-lsp and
    # bd-proxy at v3.66.848), so each name reports two import sites for one
    # script. None of the seven is installed in this container, and declaring
    # them in requirements-dev.txt would put an SMTP server, a proxy, an OCR
    # binding and a barcode binding on every dev install for tools no lane
    # runs -- three of them need a system package as well as a wheel.
    "aiosmtpd": "bd-opv's mail-delivery lane only, try/except guarded TWICE "
                "(default path, then the prestaged path) before returning "
                "SKIP with the reason at the site. bd-opv is an operator "
                "verification script run by hand; no lane and no service "
                "imports it",
    "freezegun": "bd-opv's admission-window time-warp smoke, inside a "
                 "try/except that sets warp_ok False and carries on. Same "
                 "operator-tool population as aiosmtpd",
    "jedi": "bd-lsp installs jedi ITSELF from a bundled wheelhouse "
            "(/home/claude/lsp_kit/wheels) and every jedi subcommand returns 1 "
            "out of cmd_setup while the import is still MISSING, so the bare "
            "`import jedi` in _jedi_script is reached only after that check "
            "passed. A manifest pin would put an offline code-intelligence "
            "tool on every dev install",
    "mitmproxy": "bd-proxy's optional wire-capture gate, try/except -> SKIP "
                 "carrying the reason written at the site: 'throwaway-venv "
                 "only; not in service venv'",
    "prometheus_client": "bd-opv's metrics-endpoint lane, try/except -> SKIP. "
                         "The endpoint itself is served without it; the "
                         "library is only the lane's PARSER",
    "pytesseract": "bd-opv's OCR lane, try/except -> SKIP. Needs the tesseract "
                   "system binary as well as the wheel, which is why it is not "
                   "a dev-install dependency",
    "pyzbar": "bd-opv's QR pairing lane, try/except -> SKIP. Needs the libzbar "
              "system library as well as the wheel",
}

# These two must be in requirements.txt SPECIFICALLY, not merely somewhere.
# scripts/deploy.sh step [5] resolves tools/check_requirements.py against
# requirements.txt only (DEFAULT_REQUIREMENTS at check_requirements.py:56), so
# a pin that migrates to requirements-optional.txt silently stops being
# installed on the box. lxml and cssselect back the same selector/ARIA surface
# and both fail open, so their absence is invisible at runtime.
_CORE_MANIFEST_REQUIRED = {"lxml", "cssselect"}

# A name imported ONLY from tests/ is a test dependency, and the manifest that
# says so is requirements-dev.txt. requirements.txt may ALSO carry it (pytest
# does, deliberately -- capture runs the real runner on a core-only install),
# but -dev.txt is the one that must be true.
_DEV_MANIFEST = "requirements-dev.txt"

_SCOPE_NOTE = {
    "app": "SCOPE: tracked bulk_downloader/*.py ONLY -- this assertion does "
           "not cover tests/, tools/, toolchain/, bin/, scripts/, live_tests/, "
           "docs/ or project-knowledge/. Those are "
           "test_third_party_imports_outside_the_app_package_are_declared's "
           "subject, held to a different manifest.",
    "outside": "SCOPE: every tracked file OUTSIDE bulk_downloader/ that this "
               "walk can read as Python -- *.py PLUS the extensionless "
               "python-shebang scripts (469 of them at v3.66.848, 234 under "
               "toolchain/ and 235 under project-knowledge/). That reaches "
               "tests/, tools/, toolchain/, bin/, scripts/, live_tests/, "
               "docs/, project-knowledge/ and the repo root. "
               "bulk_downloader/ is test_third_party_imports_are_declared's "
               "subject. NOT REACHED: a tracked file that is Python but "
               "declares it neither by a .py extension nor by a python "
               "shebang on its first line.",
}

# Both halves share this blind spot, and both say so on failure.
_PREDICATE_NOTE = (
    "NOT COVERED BY EITHER HALF: the predicate is ast.Import / ast.ImportFrom, "
    "so __import__(), importlib.import_module() and pytest.importorskip() are "
    "structurally invisible to this walk.")

# A fourth case the three remedies above do not fit, named because a failure
# message that offers only inapplicable repairs is its own defect.
_TYPE_CHECKING_NOTE = (
    "IF THE IMPORT IS UNDER `if TYPE_CHECKING:` none of the remedies above "
    "apply: it never executes, so it needs neither a runtime pin nor a "
    "skip-guard. Use a string annotation, or waive it saying exactly that. "
    "This walk DOES still see such an import; excluding TYPE_CHECKING blocks "
    "was deliberately not done, because that would remove names from the "
    "subject to repair a case the tree does not have (MEASURED at v3.66.848: "
    "zero TYPE_CHECKING-guarded third-party imports in the tracked tree).")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _derive_dist_aliases() -> dict[str, str]:
    """import name -> distribution, from installed metadata; see the notes above.

    Only an UNAMBIGUOUS mapping is taken (exactly one providing distribution),
    and only where the name actually differs from the distribution, so the
    result carries aliases and nothing else. _DIST_ALIAS_OVERRIDES is applied
    last and wins.
    """
    derived: dict[str, str] = {}
    try:
        mapping = md.packages_distributions()
    except Exception:            # pragma: no cover - unreadable site metadata
        mapping = {}
    for top, dists in mapping.items():
        if len(dists) == 1 and _canon(dists[0]) != _canon(top):
            derived[top] = dists[0]
    derived.update(_DIST_ALIAS_OVERRIDES)
    return derived


_DIST_ALIASES = _derive_dist_aliases()


def _tracked_py() -> list[str]:
    """Every tracked file this walk can read as Python -- BY EXTENSION OR SHEBANG.

    `git ls-files -- '*.py'` was the denominator until v3.66.848's third
    repair, and it structurally could not see the population CLAUDE.md section
    8 names as its own: toolchain/bin's bd-* suite. MEASURED on this tree,
    3423 tracked files -- 2108 end in .py, and 469 MORE carry a python shebang
    and no extension at all (234 under toolchain/, 235 under
    project-knowledge/, none anywhere else). The *.py glob therefore reached 6
    of toolchain/'s 240 Python files (2.5%) and 40 of project-knowledge/'s 275
    (14.5%), while the scope note claimed both directories outright. Seven
    undeclared third-party names were living in the gap, and an `import zeep`
    added to tracked toolchain/bin/bd-guardcheck left this gate GREEN (measured
    2026-08-03: 15 passed). That is CLAUDE.md section 0 exactly.

    Fixed by making the denominator contain the subject rather than by
    narrowing the claim: the first line of every tracked non-.py file is read,
    and a python shebang makes it Python. All 469 parse (zero SyntaxError), and
    _build_census already fails loudly on a file it cannot parse instead of
    skipping it, so a shebang that lies is a red gate rather than a quiet drop.

    Still not reached, and _SCOPE_NOTE says so: a tracked Python file that
    declares itself neither way.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(_REPO), capture_output=True, text=True, check=True).stdout
    keep: list[str] = []
    for rel in (f for f in out.split("\0") if f):
        if rel.endswith(".py"):
            keep.append(rel)
            continue
        try:
            with (_REPO / rel).open("rb") as fh:
                first = fh.readline(256).decode("utf-8", "replace")
        except OSError:          # pragma: no cover - unreadable tracked path
            continue
        first = first.split("\n", 1)[0]
        if first.startswith("#!") and "python" in first:
            keep.append(rel)
    return keep


def _resolver(tracked: list[str]):
    """`import <name>` from <rel> -> the tracked repo path that satisfies it.

    DERIVED per importer, not asserted over the tree (CLAUDE.md section 0's fix
    pattern). The previous version took a global BAG of stems from the repo
    root and tools/, which failed twice over. It could not see tests/ helpers
    at all (conftest, _env, capture_lanes), so widening the subject to tests/
    would have read them as PyPI distributions. And a bag is unconditional: one
    tracked tools/<distname>.py would have made <distname> first-party for
    EVERY file in the tree, silently removing a real third-party name from the
    subject -- a gate reporting clean over an undeclared import. Resolution is
    now per-importer, and whatever it removes is reported by _shadowed()
    instead of being dropped in silence.

    The three bases are the ones that actually resolve at import time: the repo
    root and tools/ (both reach sys.path -- tools/ is load-bearing, or
    `import build_template_from_wacz` reads as a distribution), and the
    importing file's own directory, which is how tests/ and toolchain/bin/
    helpers resolve.
    """
    tset = set(tracked)
    topdirs = {p.split("/")[0] for p in tracked if "/" in p}

    def resolve(name: str, rel: str) -> str | None:
        if name in topdirs:
            return name + "/"
        owner = rel.rsplit("/", 1)[0] if "/" in rel else ""
        # @861: toolchain/bin is a real FIRST-PARTY module location -- it holds
        # bdtools_sec, bdtools_cli, bdtools_cache and bdtools_taint, the shared
        # libraries the bd-* suite imports. It was missing from this tuple, so a
        # tool importing bdtools_sec from OUTSIDE toolchain/bin resolved to
        # nothing and was reported as an undeclared THIRD-PARTY package. That is
        # not an exemption case: the module is first-party and in the tree, the
        # resolver's denominator just did not include where it lives. Failed the
        # box capture at v3.66.859 once tools/bd-triage.py started importing it.
        for base in ("", "tools", "toolchain/bin", owner):
            prefix = base + "/" if base else ""
            for cand in (prefix + name + ".py", prefix + name + "/__init__.py"):
                if cand in tset:
                    return cand
        return None

    return resolve


def _read_manifest(path: Path, out: set[str], seen: set[Path]) -> None:
    """Canonical names in <path>, FOLLOWING `-r` / `--requirement` includes.

    df.parse_requirement_line returns None for an include directive -- correct
    for its own job, and the reason the caller has to handle them here.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        inc = re.match(r"^(?:-r|--requirement)[=\s]+(\S+)", line.strip())
        if inc:
            target = (path.parent / inc.group(1)).resolve()
            assert target.is_relative_to(_REPO), (
                "%s includes %r, which resolves OUTSIDE the repo (%s); this "
                "gate must not read a file the tree does not carry"
                % (path.name, inc.group(1), target))
            assert target.exists(), (
                "%s includes %r, which does not exist (%s)"
                % (path.name, inc.group(1), target))
            if target in seen:          # a cycle: already counted, stop here
                continue
            seen.add(target)
            _read_manifest(target, out, seen)
            continue
        parsed = df.parse_requirement_line(line)
        if parsed:
            out.add(_canon(parsed["name"]))


def _declared_names() -> dict[str, list[str]]:
    """canonical distribution name -> the requirements manifests declaring it.

    All four requirements*.txt are read, not just the core one: a name declared
    in requirements-optional.txt IS declared, and calling it undeclared would
    be a false failure. Which manifest matters is a separate question, asked by
    _CORE_MANIFEST_REQUIRED and _DEV_MANIFEST below.

    `-r` INCLUDES ARE FOLLOWED, and not following them made this gate cry wolf.
    requirements-dev.txt's first directive is `-r requirements.txt`, so
    installing the dev manifest installs every core pin -- but
    parse_requirement_line returns None for that line, so before v3.66.848's
    third repair the dev manifest was recorded as declaring only the four names
    written under it. MEASURED: 15 names (flask, httpx, lxml, playwright,
    pytest-xdist, cssselect, ...) read as declared in requirements.txt and
    absent from requirements-dev.txt. A tests-only import of any of them would
    then be told by test_tests_only_imports_are_declared_in_the_dev_manifest to
    duplicate a pin the dev install already delivers. Over-sensitivity is a
    soundness bug, not a safe default (CLAUDE.md section 0).

    The manifest recorded against a name is the TOP-LEVEL file being read, so a
    name pulled in through an include counts as declared BY THE INCLUDER --
    which is the question every caller here asks ("would `pip install -r
    <this>` get it?"). A cycle is broken by `seen`, and an include that
    resolves outside the repo or does not exist raises rather than being
    skipped: a manifest this gate cannot read is UNKNOWN, and unknown fails.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(_REPO.glob("requirements*.txt")):
        names: set[str] = set()
        _read_manifest(path, names, {path.resolve()})
        for name in sorted(names):
            found.setdefault(name, []).append(path.name)
    return found


def _in_app(rel: str) -> bool:
    return rel.startswith("bulk_downloader/")


_SCOPES = {"app": _in_app, "outside": lambda rel: not _in_app(rel)}

_CENSUS: dict | None = None


def _build_census() -> dict:
    """One AST walk over every tracked PYTHON file, sliced by scope after.

    "Python file" is _tracked_py's question, not a glob: .py by extension
    plus the extensionless python-shebang scripts. 2577 files at v3.66.848.

    Returns tops (third-party name -> importing files), suppressed (name ->
    {(importer, the repo path that resolved it)}), and per-scope counts of
    files parsed and import nodes seen -- the counts come back so a caller can
    refuse to report on an empty scan.
    """
    tracked = _tracked_py()
    resolve = _resolver(tracked)
    stdlib = set(sys.stdlib_module_names)
    tops: dict[str, set[str]] = {}
    suppressed: dict[str, set[tuple[str, str]]] = {}
    parsed = {k: 0 for k in _SCOPES}
    nodes = {k: 0 for k in _SCOPES}
    errors: list[str] = []
    for rel in tracked:
        # The per-scope COUNTS and the per-scope SLICE must come from the same
        # predicate, or the denominator assertions describe a different set
        # from the one the verdict runs over -- a gate reporting a healthy
        # count for a subject it did not examine. Asking every predicate and
        # requiring exactly one to match makes them the same by construction,
        # and makes "the scopes stopped partitioning the tree" its own failure
        # instead of a silent disagreement.
        matched = [k for k, pred in _SCOPES.items() if pred(rel)]
        assert len(matched) == 1, (
            "the scopes must PARTITION the tracked tree: %r matched %s"
            % (rel, matched))
        scope = matched[0]
        text = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:          # never skip silently -- a file this
            errors.append("%s: %s" % (rel, exc))   # walk cannot read shrinks
            continue                                # the denominator
        parsed[scope] += 1
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
            nodes[scope] += 1
            for top in names:
                if top in stdlib:
                    continue
                where = resolve(top, rel)
                if where:
                    suppressed.setdefault(top, set()).add((rel, where))
                    continue
                tops.setdefault(top, set()).add(rel)
    assert not errors, "tracked sources that would not parse: %s" % errors
    return {"tracked": tracked, "tops": tops, "suppressed": suppressed,
            "parsed": parsed, "nodes": nodes}


def _census() -> dict:
    global _CENSUS
    if _CENSUS is None:
        _CENSUS = _build_census()
    return _CENSUS


def _third_party_imports(scope: str = "app") -> tuple[dict[str, set[str]], int, int]:
    """(name -> importing files, files parsed, import nodes seen) for one scope."""
    assert scope in _SCOPES, "unknown scope %r" % scope
    keep = _SCOPES[scope]
    c = _census()
    sliced = {t: {f for f in files if keep(f)} for t, files in c["tops"].items()}
    return ({t: files for t, files in sliced.items() if files},
            c["parsed"][scope], c["nodes"][scope])


def _installed_from_the_repo_itself(loc: Path | None) -> bool:
    """True when an installed distribution's files live in this checkout.

    "Inside _REPO" is NOT sufficient, and assuming it was cost a run: the
    interpreter is venv/bin/python and `venv/` sits INSIDE the repo, so every
    single site-packages distribution is under _REPO and the whole map came
    back empty -- the shadow check's own denominator assertion caught it and
    said UNKNOWN rather than reporting clean, which is why it was noticed.
    A site-packages / dist-packages component means an installed third party
    wherever the directory happens to live.

    SAY WHAT THIS DOES NOT CATCH, because the sibling docstring used to promise
    more than the code delivers. The predicate is a CONJUNCTION -- no
    site-packages/dist-packages component AND under _REPO -- so which test runs
    first is immaterial (the truth table is identical either way), but the
    conjunction does not recognise a PEP 660 editable install, whose
    locate_file("") returns the site-packages directory holding the
    __editable__ .pth rather than the source tree. Such an install of BD would
    NOT be excluded here.

    It is not repaired, and that is a deliberate choice rather than an
    oversight: the branch cannot be exercised in this environment, so any fix
    would ship untested. MEASURED at v3.66.848 -- packages_distributions() has
    no bulk_downloader key, site-packages holds no __editable__* file, and none
    of the 67 installed distributions is BD. If it ever happens the failure is
    loud, not silent: test_repo_resolvable_names_do_not_shadow_a_real_
    distribution reports bulk_downloader as shadowing itself.
    """
    if loc is None:
        return False
    if "site-packages" in loc.parts or "dist-packages" in loc.parts:
        return False
    try:
        loc.relative_to(_REPO)
    except ValueError:
        return False
    return True


def _installed_top_levels() -> dict[str, list[str]]:
    """top-level import name -> installed distributions that provide it.

    A distribution whose files sit in this checkout OUTSIDE site-packages is
    excluded, or the shadow check below would flag first-party packages as
    shadowing themselves. Read _installed_from_the_repo_itself for the case
    that exclusion does not cover (a PEP 660 editable install) and why it is
    left uncovered -- the promise this docstring used to make was wider than
    the code.
    """
    try:
        mapping = md.packages_distributions()
    except Exception:                # pragma: no cover - unreadable site metadata
        return {}
    out: dict[str, list[str]] = {}
    for top, dists in mapping.items():
        keep = []
        for dist in dists:
            try:
                loc = Path(md.distribution(dist).locate_file("")).resolve()
            except Exception:        # pragma: no cover - metadata without a path
                loc = None
            if _installed_from_the_repo_itself(loc):
                continue
            keep.append(dist)
        if keep:
            out[top] = sorted(keep)
    return out


def _shadowed(suppressed: dict[str, set[tuple[str, str]]],
              declared: dict[str, list[str]],
              installed: dict[str, list[str]]) -> dict[str, dict]:
    """Names the repo-resolution rule removed that are ALSO a real distribution.

    This is the reason _resolver's removals are recorded rather than discarded.
    A tracked tools/<distname>.py or <distname>.py at the repo root makes every
    `import <distname>` in the tree resolve first-party, and the gate would then
    report clean over a genuinely undeclared third-party import -- silently,
    because nothing else in the tree asks the question. Two independent signals
    say a suppressed name is really a distribution: it is written in a
    requirements manifest, or it is a top-level module of something installed
    from outside this repo.
    """
    hits: dict[str, dict] = {}
    for name, sites in sorted(suppressed.items()):
        dists = installed.get(name)
        decl = declared.get(_canon(_DIST_ALIASES.get(name, name)))
        if dists or decl:
            hits[name] = {
                "declared_in": decl,
                "installed_as": dists,
                "shadowed_by": sorted({where for _rel, where in sites}),
                "importers": sorted({rel for rel, _where in sites})[:5],
            }
    return hits


def test_third_party_imports_are_declared():
    tops, parsed, nodes = _third_party_imports("app")
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
        "REASON. Import sites: %s\n%s\n%s\n%s" % (
            undeclared, {t: sorted(tops[t]) for t in undeclared},
            _SCOPE_NOTE["app"], _PREDICATE_NOTE, _TYPE_CHECKING_NOTE))


def test_third_party_imports_outside_the_app_package_are_declared():
    """The other half of the tree, which the first version could not see.

    Not a widening of the assertion above but a second one, because the two
    halves answer to different manifests. An undeclared import here breaks
    something real: capture.sh runs tests/ and tools/ on the box, and a hard
    import of a distribution nobody installed is a red capture, not a degraded
    feature. `requests` was exactly that -- two tracked test files hard-imported
    it, no manifest declared it, and the app-package scan could not see them.
    """
    tops, parsed, nodes = _third_party_imports("outside")
    declared = _declared_names()

    assert parsed > 0, "no tracked Python file outside bulk_downloader/ " \
                       "parsed -- nothing was examined"
    assert nodes > 0, "no import nodes found in %d files -- the AST predicate " \
                      "is not matching" % parsed
    assert tops, "zero third-party imports found outside bulk_downloader/ -- " \
                 "the stdlib/first-party filter is swallowing the whole subject"
    assert declared, "no names parsed out of any requirements*.txt"
    joined = {t for t in tops if _canon(_DIST_ALIASES.get(t, t)) in declared}
    assert joined, "no import name joined to any declared name -- the " \
                   "import-name -> distribution join is broken, so every " \
                   "name would read as undeclared"

    undeclared = sorted(
        t for t in tops
        if _canon(_DIST_ALIASES.get(t, t)) not in declared
        and t not in _UNDECLARED_OUTSIDE_BY_DESIGN)
    assert not undeclared, (
        "third-party imports outside bulk_downloader/ declared in no "
        "requirements*.txt and not recorded in _UNDECLARED_OUTSIDE_BY_DESIGN: "
        "%s\nDeclare it (a test-only dependency belongs in %s, not "
        "requirements.txt), guard the import so an absent dependency SKIPS "
        "rather than errors, or add it to _UNDECLARED_OUTSIDE_BY_DESIGN WITH A "
        "REASON. Import sites: %s\n%s\n%s\n%s" % (
            undeclared, _DEV_MANIFEST,
            {t: sorted(tops[t]) for t in undeclared},
            _SCOPE_NOTE["outside"], _PREDICATE_NOTE, _TYPE_CHECKING_NOTE))


def test_outside_waivers_are_still_imported_and_still_undeclared():
    """_UNDECLARED_OUTSIDE_BY_DESIGN's own anti-rot check.

    Same two failure modes as the app-package waiver list: a name that stops
    being imported leaves a stale waiver that would silence a future
    re-introduction, and a name that BECOMES declared while sitting on the list
    means the list is lying.
    """
    tops, _parsed, _nodes = _third_party_imports("outside")
    declared = _declared_names()
    assert _UNDECLARED_OUTSIDE_BY_DESIGN, "the waiver list is empty"

    stale = sorted(n for n in _UNDECLARED_OUTSIDE_BY_DESIGN if n not in tops)
    assert not stale, (
        "_UNDECLARED_OUTSIDE_BY_DESIGN names no longer imported outside "
        "bulk_downloader/: %s -- remove them" % stale)

    now_declared = sorted(
        n for n in _UNDECLARED_OUTSIDE_BY_DESIGN
        if _canon(_DIST_ALIASES.get(n, n)) in declared)
    assert not now_declared, (
        "these are on the outside waiver list but ARE declared: %s (in %s) -- "
        "take them off the list" % (
            now_declared,
            {n: declared[_canon(_DIST_ALIASES.get(n, n))]
             for n in now_declared}))

    for name, reason in _UNDECLARED_OUTSIDE_BY_DESIGN.items():
        assert reason.strip(), "%s is waived with no reason given" % name


def test_tests_only_imports_are_declared_in_the_dev_manifest():
    """The outside half's manifest expectation, asked separately.

    A distribution imported ONLY from tests/ is a test dependency. Declaring it
    in requirements.txt alone would put it on every production install; the
    manifest that must be true for it is requirements-dev.txt. requirements.txt
    MAY also carry it -- pytest does, deliberately, because capture runs the
    real runner on a core-only box -- so this asks for presence in the dev
    manifest, not absence from the core one.

    SAY WHAT THE VERDICT BELOW IS CURRENTLY RUNNING OVER. MEASURED at
    v3.66.848, tests_only == {"pytest"}, and pytest is declared in BOTH
    manifests -- so wrong_manifest is computed over a single name that cannot
    fail. This assertion is therefore a live check for the NEXT test-only
    third-party import, not evidence about today's tree; the non-empty
    assertion above it is the only part that measures anything now. Said
    plainly rather than left implied, because a body that cannot fail reads
    like a body that passed.

    It was ALSO wrong until v3.66.848's third repair, in the other direction:
    _declared_names did not follow requirements-dev.txt's `-r requirements.txt`
    include, so any of the 15 core-manifest names would have landed in
    wrong_manifest the moment it became tests-only -- told to duplicate a pin
    the dev install already delivers.
    """
    all_tops = _census()["tops"]
    declared = _declared_names()
    assert all_tops and declared, "nothing scanned -- see the sibling " \
                                  "denominator assertions"

    # OVER THE WHOLE TREE, not the outside slice. Judging "only tests/ imports
    # this" against the outside slice alone answers a different question: bs4,
    # httpx, lxml, mutagen, openpyxl, curl_cffi and cloakbrowser have no
    # non-tests importer OUTSIDE bulk_downloader/, so the sliced denominator
    # called all seven test-only and demanded they move to the dev manifest --
    # seven false failures on runtime pins the service actually imports.
    tests_only = {t: files for t, files in all_tops.items()
                  if all(f.startswith("tests/") for f in files)}
    assert tests_only, (
        "no third-party name is imported exclusively from tests/ -- either "
        "the tests/ prefix stopped matching or the scan is not reaching "
        "tests/, and this assertion would then hold over nothing")

    wrong_manifest = sorted(
        t for t in tests_only
        if _canon(_DIST_ALIASES.get(t, t)) in declared
        and _DEV_MANIFEST not in declared[_canon(_DIST_ALIASES.get(t, t))])
    assert not wrong_manifest, (
        "imported only from tests/ and declared, but not in %s: %s (declared "
        "in %s). A test-only dependency belongs in the dev manifest." % (
            _DEV_MANIFEST, wrong_manifest,
            {t: declared[_canon(_DIST_ALIASES.get(t, t))]
             for t in wrong_manifest}))


def test_repo_resolvable_names_do_not_shadow_a_real_distribution():
    """Item 5: the first-party rule must not silently remove a real name.

    _resolver treats an import as first-party when a tracked file would satisfy
    it. That is necessary -- `import build_template_from_wacz` is not a PyPI
    distribution -- but it is also a way for this gate to report clean over an
    undeclared third-party import: add tracked tools/requests.py and every
    `import requests` in the tree leaves the subject. The removals are
    therefore RECORDED and checked against two independent signals of "this is
    really a distribution": declared in a manifest, or installed from outside
    this repo.
    """
    c = _census()
    declared = _declared_names()
    installed = _installed_top_levels()

    assert c["suppressed"], "no import was resolved first-party at all -- the " \
                            "resolver is not matching, so this check would " \
                            "report clean over nothing"
    assert installed, "importlib.metadata.packages_distributions() returned " \
                      "nothing -- one of the two shadow signals is blind, so " \
                      "this check cannot answer its question (UNKNOWN, which " \
                      "fails: CLAUDE.md section 0)"
    assert declared, "no names parsed out of any requirements*.txt -- the " \
                     "other shadow signal is blind"

    hits = _shadowed(c["suppressed"], declared, installed)
    assert not hits, (
        "these import names were treated as first-party because a tracked repo "
        "file resolves them, but they are ALSO a real distribution -- so every "
        "importer of them left this gate's subject silently: %s\n"
        "Rename the repo file, or if the shadowing is intended, say so here "
        "and re-scope _resolver." % hits)


def test_shadow_report_fires_on_a_synthetic_shadow():
    """The report above is only worth having if it fires. Proven on a synthetic
    tracked list, so the assertion does not depend on the tree ever growing a
    real shadow (measured at v3.66.848: it has none).

    lxml is used because it is declared in requirements.txt, so the check fires
    on the manifest signal and does not depend on what happens to be installed.
    """
    tracked = ["bulk_downloader/app.py", "tools/lxml.py", "tests/test_x.py"]
    resolve = _resolver(tracked)
    # The defect, reproduced: a tools/ file named after a distribution makes
    # that distribution first-party for an importer anywhere in the tree.
    assert resolve("lxml", "bulk_downloader/app.py") == "tools/lxml.py"
    assert resolve("lxml", "tests/test_x.py") == "tools/lxml.py"

    suppressed = {"lxml": {("bulk_downloader/app.py", "tools/lxml.py"),
                           ("tests/test_x.py", "tools/lxml.py")}}
    hits = _shadowed(suppressed, _declared_names(), _installed_top_levels())
    assert "lxml" in hits, (
        "a tracked tools/lxml.py removes every lxml import from the subject "
        "and the shadow report did not fire: %s" % hits)
    assert hits["lxml"]["shadowed_by"] == ["tools/lxml.py"], hits
    assert hits["lxml"]["declared_in"], hits

    # And it stays quiet on a name that is neither declared nor installed --
    # otherwise it would fire on all 90-odd genuine first-party resolutions.
    quiet = _shadowed(
        {"template_drift_report": {("bulk_downloader/x.py",
                                    "tools/template_drift_report.py")}},
        _declared_names(), _installed_top_levels())
    assert quiet == {}, (
        "the shadow report fired on a genuine first-party module: %s" % quiet)


def test_undeclared_by_design_names_are_still_imported_and_still_undeclared():
    """The reverse direction, which keeps the exception list from rotting.

    Two failure modes it closes. A name that stops being imported leaves a
    stale waiver behind that would silence a future re-introduction. A name
    that BECOMES declared while sitting on the waiver list means the list is
    now lying -- and, worse, it is the shape that would let a broken
    requirements parser (one that matched every line, say) pass the gate above
    vacuously, because every name would read as declared.
    """
    tops, _parsed, _nodes = _third_party_imports("app")
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
    tops, _parsed, _nodes = _third_party_imports("app")
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
