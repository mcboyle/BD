"""Row 634 -- no reader walks the LIVE ``runners`` registry bare.

Row 449 fixed the eight sites it cited and left twenty it did not.  Each of
those twenty iterated ``app_state.runners`` with a bare ``.items()`` /
``.values()`` outside ``_watch_registry_lock``, and the registry is inserted
into on the site-create request thread and popped on delete.  CPython raises
``RuntimeError: dictionary changed size during iteration`` on any size change
mid-walk, so a create or delete landing while an operator route is walking the
fleet gives a 500 with an HTML body and no JSON -- AFTER the loop body has
already acted on a prefix of the fleet.  Row 449's own runtime probe recorded
exactly that at app_dashboard.py:32: a ``stop()`` that completed 1 of 3
runners, i.e. a half-paused fleet reported as paused.

THE CENSUS IS THE GATE, because the defect is a population and not a line.
A behavioural test can only prove the route it drives; twelve modules can each
reacquire the defect the next time someone adds a loop.  So the subject here is
the whole ``bulk_downloader/`` tree, the denominator is derived mechanically
(``git ls-files`` for the file population, the AST for the iterations), and
every count is asserted nonzero before any verdict is read.  A census whose
population is empty must read UNKNOWN, never OK.

THREE CLASSES, NOT TWO.  ``BARE`` is the defect: an unwrapped walk of the live
dict, which is the only form that can raise.  ``SNAPSHOTTED`` goes through
``runners_snapshot()`` / ``runners_generation()``, which copy under the registry
lock -- the fix.  ``OTHER`` is a copy (``list(runners.items())``) or a
delegation (``_iter_site_jars(runners, sid)``): neither can raise mid-walk, but
a copy still rests on that being a GIL implementation detail rather than on the
lock, and a delegation is judged where it iterates.  ``OTHER`` is therefore not the row's defect and is not rewritten
here; it is held to a shrink-only floor so that the way to add a NEW registry
walk is the helper, and a bare walk cannot be laundered into a passing gate by
wrapping it in ``list(...)``.

AND A FLOOR IS NOT A DENOMINATOR.  ``bare == []`` plus ``snapshotted >= 20``
reads OK while a recognizer regression drops five of the twenty-five sites out
of EVERY class -- a site the census cannot see is not a bare site either.  The
twelve modules this row converted therefore carry an EXACT per-module count
(``_ROW634_CONVERTED_SITES``, summing to 20), with a negative control that
makes one real site invisible and proves every floor still clears while the
exact count names the module.
"""
from __future__ import annotations

import ast
import subprocess
import threading
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_PKG_DIR = "bulk_downloader"

# The live registry's name at every one of the recorded sites.  Modules reach it
# as a module-level import, as an `_app_runners()` local, or as a parameter that
# may be the live dict or a caller-scoped copy -- `runners_generation()` decides
# by identity, which is why one helper covers all three shapes.
_REGISTRY_NAME = "runners"
_ITER_METHODS = frozenset({"items", "values", "keys"})
# The public helpers and the lazy per-module shims that delegate to them.
_SAFE_CALLS = frozenset({
    "runners_snapshot", "runners_generation",
    "_runners_snapshot", "_runners_generation",
})
# NO MODULE IS EXEMPT, app_state.py included.  Its helpers return
# `list(runners.items())` from an EXPRESSION rather than driving a loop, so they
# never enter the iteration population at all -- an exemption for them would be
# an unreachable branch pretending to be a safeguard.

# Shrink-only floor for the OTHER class, measured on this tree.  Lowering it is
# always allowed (convert a copy to the helper); raising it means a new walk was
# added that agrees with neither the lock nor the helper.
_OTHER_FLOOR = 14

# THE EXACT SITE COUNT THIS ROW CONVERTED, per module.  A FLOOR IS NOT A
# DENOMINATOR: `snapshotted >= 20` stays green while a recognizer regression
# silently drops five of the twenty-five sites out of EVERY class, because a
# site the census cannot see is not a bare site either -- the gate's own missing
# denominator, in the gate that exists to refuse missing denominators.  These
# twelve modules are the row's fixed population, so their counts are pinned
# EXACTLY and their sum is asserted nonzero.
#
# Only these twelve.  A tree-wide `== 25` would refuse the idiom itself: the
# five sites row 449 converted (app_quick_add, app_route_preview,
# app_widgets_api x2, metrics_prom) and every future adopter would have to
# re-pin a number here to add a correctly-written walk.  Growth outside this
# dict is what the population floors below are for; inside it, a change of any
# count is a change to this row's subject and must be read.
_ROW634_CONVERTED_SITES = {
    "bulk_downloader/app.py": 4,
    "bulk_downloader/app_dashboard.py": 3,
    "bulk_downloader/app_dev_maint.py": 1,
    "bulk_downloader/app_events_all.py": 1,
    "bulk_downloader/app_extension.py": 1,
    "bulk_downloader/app_health.py": 2,
    "bulk_downloader/app_jobs.py": 1,
    "bulk_downloader/app_queue.py": 3,
    "bulk_downloader/app_sites_id_core.py": 1,
    "bulk_downloader/app_status.py": 1,
    "bulk_downloader/crash_recovery.py": 1,
    "bulk_downloader/dev_suite/_common.py": 1,
}


# ── the census ────────────────────────────────────────────────────────


def _iter_expressions(tree: ast.AST):
    """Every expression a `for` or a comprehension iterates over."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            yield node.iter
        elif isinstance(node, (ast.ListComp, ast.SetComp,
                               ast.DictComp, ast.GeneratorExp)):
            for gen in node.generators:
                yield gen.iter


def _mentions_registry(node: ast.AST) -> bool:
    """The registry reached by its bare name OR through the module that owns it.

    `app_state.runners.items()` is an Attribute root, not a Name root.  A
    recognizer that saw only the Name would not merely misclassify that walk --
    it would leave it out of the POPULATION, which is the one direction in
    which "a new site cannot be added silently" fails silently.
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == _REGISTRY_NAME:
            return True
        if isinstance(n, ast.Attribute) and n.attr == _REGISTRY_NAME:
            return True
    return False


def _is_safe_call(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fn = n.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else None)
            if name in _SAFE_CALLS:
                return True
    return False


def _bare_root(node: ast.AST):
    """The Name a bare walk is rooted at, or None if this is not a bare walk.

    Bare means the live dict is handed to the loop with nothing between: either
    the dict itself, or one no-argument view call on it.  `list(...)`,
    `sorted(...)`, a helper call, or anything taking arguments is NOT bare --
    it is a copy or a delegation, and neither can raise mid-walk.  Unwrapping
    therefore steps through `Attribute` and the first arm of a `BoolOp`
    (`runners or {}`) and stops dead at any `Call`.
    """
    cur = node
    if isinstance(cur, ast.Call):
        if cur.args or cur.keywords or not isinstance(cur.func, ast.Attribute):
            return None
        if cur.func.attr not in _ITER_METHODS:
            return None
        cur = cur.func.value
    while isinstance(cur, ast.BoolOp) and cur.values:
        cur = cur.values[0]
    if isinstance(cur, ast.Name) and cur.id == _REGISTRY_NAME:
        return cur
    if isinstance(cur, ast.Attribute) and cur.attr == _REGISTRY_NAME:
        return cur                      # app_state.runners.items()
    return None


def census_source(rel: str, source: str):
    """Classify every registry iteration in one module.

    Returns (bare, snapshotted, other) lists of "path:line: unparsed" rows.
    """
    tree = ast.parse(source, filename=rel)
    bare, snapshotted, other = [], [], []
    for expr in _iter_expressions(tree):
        if not _mentions_registry(expr):
            continue
        row = "%s:%d: %s" % (rel, expr.lineno, ast.unparse(expr))
        if _is_safe_call(expr):
            snapshotted.append(row)
        elif _bare_root(expr) is not None:
            bare.append(row)
        else:
            other.append(row)
    return bare, snapshotted, other


def counts_by_file(rows) -> dict:
    """{module path: number of rows} for census rows of one class.

    Rows are "path:line: expression"; a repo-relative path never contains a
    colon, so the first one ends the path.
    """
    out: dict = {}
    for row in rows:
        rel = row.split(":", 1)[0]
        out[rel] = out.get(rel, 0) + 1
    return out


def exact_count_mismatches(snapshotted_rows) -> list:
    """Every named module whose converted-site count is not what this row left.

    Reports a MISSING module as found 0 rather than skipping it, because the
    regression this exists to catch removes sites from the census entirely.
    """
    found = counts_by_file(snapshotted_rows)
    return ["%s: expected %d, found %d" % (rel, expected, found.get(rel, 0))
            for rel, expected in sorted(_ROW634_CONVERTED_SITES.items())
            if found.get(rel, 0) != expected]


def _tracked_package_sources() -> dict:
    """{repo-relative path: source} for every tracked .py under the package.

    The file population comes from git, NOT from a filesystem walk of the thing
    under test: an untracked scratch copy must not enter the denominator and a
    tracked file must not be able to leave it by being unreadable.
    """
    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "--", "%s/*.py" % _PKG_DIR],
        capture_output=True, text=True, check=True).stdout.split()
    sources = {}
    for rel in out:
        path = _REPO / rel
        if not path.is_file():
            pytest.fail("tracked but absent, so the census population is "
                        "UNKNOWN rather than complete: %s" % rel)
        sources[rel] = path.read_text(encoding="utf-8")
    return sources


@pytest.fixture(scope="module")
def package_census():
    sources = _tracked_package_sources()
    assert len(sources) >= 100, (
        "precondition: git ls-files returned only %d python files under %s/, "
        "which is not the package -- the census would judge an empty or "
        "truncated population and report OK" % (len(sources), _PKG_DIR))
    bare, snapshotted, other = [], [], []
    for rel, src in sorted(sources.items()):
        b, s, o = census_source(rel, src)
        bare.extend(b)
        snapshotted.extend(s)
        other.extend(o)
    return {"files": len(sources), "bare": bare,
            "snapshotted": snapshotted, "other": other}


# ── the verdict, and the denominators it stands on ────────────────────


def test_the_census_population_is_nonzero_before_any_verdict(package_census):
    """UNKNOWN, not OK, if the census cannot see the subject."""
    assert package_census["files"] >= 100, package_census["files"]
    total = (len(package_census["bare"]) + len(package_census["snapshotted"])
             + len(package_census["other"]))
    assert total >= 30, (
        "the census found only %d registry iterations across %d files; a "
        "recognizer that sees nothing reports a clean tree for the wrong "
        "reason" % (total, package_census["files"]))
    assert len(package_census["snapshotted"]) >= 20, (
        "only %d snapshotted iterations found, so the recognizer cannot see "
        "the FIX either and its zero-bare verdict is not evidence: %r"
        % (len(package_census["snapshotted"]),
           package_census["snapshotted"][:5]))


def test_no_reader_iterates_the_live_registry_bare(package_census):
    """The row's acceptance: every site takes the lock or walks a snapshot."""
    assert package_census["bare"] == [], (
        "%d bare live-registry iteration(s) remain; each raises RuntimeError "
        "'dictionary changed size during iteration' when a site create or "
        "delete lands mid-walk, after the loop has already acted on part of "
        "the fleet. Use runners_snapshot() / runners_generation(runners):\n%s"
        % (len(package_census["bare"]), "\n".join(package_census["bare"])))


def test_the_twelve_converted_modules_carry_their_exact_site_counts(
        package_census):
    """The exact denominator the floors above cannot supply.

    Each module this row converted is named, its count is pinned, and the sum
    is asserted nonzero -- so a recognizer that stops seeing a module, or a
    module that quietly loses a converted walk, is named rather than absorbed.
    """
    assert sum(_ROW634_CONVERTED_SITES.values()) == 20, (
        "the pinned population is not this row's 20 converted sites: %r"
        % (sum(_ROW634_CONVERTED_SITES.values()),))
    assert len(_ROW634_CONVERTED_SITES) == 12, len(_ROW634_CONVERTED_SITES)
    mismatches = exact_count_mismatches(package_census["snapshotted"])
    assert mismatches == [], (
        "the converted-site count changed in %d module(s). A count that FELL "
        "means either a site regressed or the census stopped seeing it -- read "
        "the module before touching this pin:\n%s"
        % (len(mismatches), "\n".join(mismatches)))


def test_a_single_site_the_census_stops_seeing_fails_the_exact_count():
    """The negative control the floors would have passed.

    One real converted walk is rewritten so the recognizer cannot see it at all
    -- it leaves the snapshotted class WITHOUT entering bare or other, which is
    exactly the recognizer-regression shape.  Every floor still clears; only
    the exact per-module count refuses, and it names the module.
    """
    sources = _tracked_package_sources()
    rel = "bulk_downloader/app_health.py"
    real = "        for _sid, r in _runners_generation(runners):"
    assert sources[rel].count(real) == 2, (
        "precondition: %s no longer has the two converted sites this control "
        "drops one of (found %d)" % (rel, sources[rel].count(real)))
    # `_alias` is not the registry name, so the walk vanishes from the census
    # entirely rather than being reclassified.
    sources[rel] = sources[rel].replace(
        real, "        for _sid, r in _runners_generation(_alias):", 1)

    bare, snapshotted, other = [], [], []
    for path, src in sorted(sources.items()):
        b, s, o = census_source(path, src)
        bare.extend(b)
        snapshotted.extend(s)
        other.extend(o)

    assert bare == [], (
        "precondition: the control introduced a BARE site, so it is not the "
        "invisible-site regression this test is about: %r" % (bare[:3],))
    assert len(snapshotted) >= 20 and len(other) <= _OTHER_FLOOR, (
        "precondition: this control must clear the very floors it defeats "
        "(snapshotted=%d, other=%d)" % (len(snapshotted), len(other)))

    mismatches = exact_count_mismatches(snapshotted)
    assert mismatches == ["%s: expected 2, found 1" % rel], (
        "a converted site the census can no longer see was NOT refused by the "
        "exact per-module count: %r" % (mismatches,))


def test_the_weaker_unlocked_copy_class_is_shrink_only(package_census):
    """A copy or a delegation cannot raise, but a copy agrees with neither the
    lock nor the helper -- so the class may shrink and never grow."""
    other = package_census["other"]
    assert len(other) <= _OTHER_FLOOR, (
        "unlocked registry copies grew from %d to %d. A new registry walk must "
        "use runners_generation(runners); wrapping one in list(...) is not the "
        "idiom this row settled on:\n%s"
        % (_OTHER_FLOOR, len(other), "\n".join(other)))


# ── the recognizer has teeth, and does not fire on the wrong things ───


_CONTROL_BARE = '''
def route(runners):
    for sid, runner in runners.items():
        runner.stop()
'''

_CONTROL_FIXED = '''
def route(runners):
    for sid, runner in _runners_generation(runners):
        runner.stop()
'''

_CONTROL_PROSE = '''
# for sid, runner in runners.items():   <- the defect, in a comment
DOC = "for sid, runner in runners.items()"
def route(runners):
    """for sid, runner in runners.items() is what this used to do."""
    for sid, runner in _runners_generation(runners):
        runner.stop()
'''

_CONTROL_COPY = '''
def route(runners):
    for sid, runner in list(runners.items()):
        runner.stop()
'''

_CONTROL_QUALIFIED = '''
from bulk_downloader import app_state
def route():
    for sid, runner in app_state.runners.items():
        runner.stop()
'''


def test_the_census_still_finds_a_deliberately_reintroduced_bare_walk():
    """Negative control.  Without this, a recognizer that matches nothing at
    all would satisfy the verdict above."""
    bare, snap, other = census_source("control.py", _CONTROL_BARE)
    assert len(bare) == 1, (bare, snap, other)
    assert bare[0].startswith("control.py:3:"), bare
    assert (snap, other) == ([], []), (snap, other)


def test_the_census_clears_the_same_site_once_it_uses_the_helper():
    bare, snap, other = census_source("control.py", _CONTROL_FIXED)
    assert bare == [], bare
    assert len(snap) == 1, snap
    assert other == [], other


def test_the_census_finds_the_registry_reached_through_its_module():
    """The same defect written `app_state.runners.items()` is an Attribute root.
    A Name-only recognizer would drop it from the population entirely, which is
    how a new site gets added without the gate ever seeing it."""
    bare, snap, other = census_source("control.py", _CONTROL_QUALIFIED)
    assert len(bare) == 1, (bare, snap, other)
    assert bare[0].startswith("control.py:4:"), bare
    assert (snap, other) == ([], []), (snap, other)


def test_prose_and_string_literals_are_not_inside_the_denominator():
    """A7: a gate that scans source text has its own comments and examples in
    its population.  This one parses structure, and proves it."""
    bare, snap, other = census_source("control.py", _CONTROL_PROSE)
    assert bare == [], (
        "the recognizer counted a comment, a docstring or a string literal as "
        "a defect: %r" % (bare,))
    assert len(snap) == 1, snap


def test_a_copy_is_classified_as_other_and_not_as_the_defect():
    bare, snap, other = census_source("control.py", _CONTROL_COPY)
    assert bare == [], bare
    assert len(other) == 1, other
    assert snap == [], snap


# ── the runtime seam the row was filed from ───────────────────────────


@pytest.fixture()
def live_registry():
    """The LIVE app_state.runners, restored exactly afterwards.  Restoration
    must never be what makes a test green, so every verdict is asserted before
    this teardown runs."""
    from bulk_downloader import app_state
    original = dict(app_state.runners)
    try:
        yield app_state.runners
    finally:
        app_state.runners.clear()
        app_state.runners.update(original)


class _MutatingRunner:
    """A runner whose first ``get_events()`` inserts a new site into the live
    registry -- the create-lands-mid-walk race, made deterministic."""

    def __init__(self, registry, new_sid):
        self._registry = registry
        self._new_sid = new_sid
        self._event_seq = 0
        self._lock = threading.Lock()
        self.jobs = {}
        self.fired = 0

    def get_events(self, after_seq=0, limit=200, kind_filter=None):
        if self.fired == 0:
            self.fired += 1
            self._registry[self._new_sid] = _QuietRunner()
        return []


class _QuietRunner:
    def __init__(self):
        self._event_seq = 0
        self._lock = threading.Lock()
        self.jobs = {}

    def get_events(self, after_seq=0, limit=200, kind_filter=None):
        return []


def _seed(registry, mutator_sid="row634-mutator", extra=3):
    registry.clear()
    registry[mutator_sid] = _MutatingRunner(registry, "row634-created-mid-walk")
    for i in range(extra):
        registry["row634-quiet-%d" % i] = _QuietRunner()
    return registry[mutator_sid]


def test_the_fixture_really_mutates_the_registry_during_a_bare_walk(
        live_registry):
    """Teeth for the seam test below.  If a bare walk of this exact fixture
    does NOT raise, then the 200 below proves nothing about the fix."""
    mutator = _seed(live_registry)
    before = len(live_registry)
    with pytest.raises(RuntimeError) as excinfo:
        for _sid, runner in live_registry.items():
            runner.get_events()
    assert "changed size during iteration" in str(excinfo.value), excinfo.value
    assert mutator.fired == 1, (
        "precondition: the mutating runner fired %d times, not once"
        % mutator.fired)
    assert len(live_registry) == before + 1, (
        "precondition: the registry did not actually grow (%d -> %d)"
        % (before, len(live_registry)))


def test_a_create_landing_mid_walk_does_not_500_the_events_route(
        live_registry):
    """The subject, at the operator's boundary: JSON and 200, not an HTML 500."""
    from flask import Flask
    from bulk_downloader.app_events_all import events_all_bp

    mutator = _seed(live_registry)
    before = len(live_registry)

    app = Flask(__name__)
    app.config["TESTING"] = False
    # An escaping RuntimeError must be OBSERVED as the operator's 500, not
    # re-raised into the test: the claim is about what the route returns.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.register_blueprint(events_all_bp)

    resp = app.test_client().get("/api/events_all")

    assert mutator.fired == 1, (
        "precondition: the mid-walk create never fired (%d), so the route was "
        "never asked the question" % mutator.fired)
    assert len(live_registry) == before + 1, (
        "precondition: the registry did not grow during the request (%d -> %d)"
        % (before, len(live_registry)))
    assert resp.status_code == 200, (
        "a site created while /api/events_all walked the fleet returned %s; "
        "before the fix this was RuntimeError 'dictionary changed size during "
        "iteration' rendered as an HTML 500 with no JSON body"
        % resp.status_code)
    assert resp.is_json, resp.data[:200]
    body = resp.get_json()
    assert body.get("ok") is True, body
    # The walk saw ONE stable generation: the site created mid-walk is absent
    # from this response and present in the next one.
    assert "row634-created-mid-walk" not in (body.get("cursor") or {}), body
    assert len(body.get("cursor") or {}) == before, body
