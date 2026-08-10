"""@1010. L34's triage budget is what phase 1 can AFFORD, not a constant.

MEASURED ON THE BOX, v3.66.1007 and v3.66.1009 captures (2026-08-10). L34 failed
both, and live_tests/results/L34.log says why with no ambiguity at all:

    1001 routes total; 264 operator parameter-free GET routes to gate + 259
      diagnostic (/api/dev, /cockpit) routes smoked ADVISORY
    92 route(s) UNPROBED -- phase-1 deadline (40s of the 72s wall) hit before
      they could be probed at all
    phase 1 (concurrent, 8 workers) flagged 47 route(s) in 43s; re-probing
      SERIALLY to strip contention this probe caused (29s of wall left)
    ... 47 x RECOVERED ...
    checked 172 operator in 66s: 0 5xx, 0 unreachable, 0 exceeded,
      47 recovered-on-serial (probe-induced, not findings), 0 unconfirmed,
      92 unprobed

ZERO REAL FINDINGS. Every route it reached is healthy; every one of the 47 it
flagged recovered on the quiet serial re-probe -- /api/data/site_health flagged
as >5s and answering in 107ms, /api/health flagged and answering in 8ms. The
FAIL is arithmetic, not a defect in the app:

    phase 1 budget      39.6s deadline x 8 workers   = 316.8 worker-seconds
    47 suspects x the 5s triage cap                  = 235   worker-seconds
    left for the other ~217 routes                   =  ~82
    result                                             92 never submitted

THE ASYMMETRY IS THE WHOLE ARGUMENT. A suspect costs the FULL triage budget in
phase 1 -- 5 worker-seconds each -- and only its real latency in phase 2, where
the measured 47 were re-probed serially in about two seconds, ~43ms apiece. So
phase 1 pays roughly a hundred times more per suspect than phase 2 does, while
phase 2 is the phase that actually decides. Triage should be the cheap half.

WHY NOT JUST LOWER THE CONSTANT. checks.py says it itself, about the wall:
"The route count only grows, so a bigger ceiling is a treadmill, not a fix." A
smaller literal is the same treadmill facing the other way -- correct for 264
routes and wrong again at 400. The affordable per-route cost is
`deadline * workers / len(targets)`, which is a quantity the check already has
in hand at the moment it needs it, so it is derived rather than guessed.

THIS CANNOT HIDE A DEFECT, which is the objection to answer first, because
checks.py warns in as many words that raising a budget to quiet the check "is
the wrong lever; it hides the defect". This moves the budget the OTHER way.
A shorter triage flags MORE routes, and a flag is not a finding: every one is
re-probed serially against a quiet app at the full _L34_ROUTE_BUDGET_S before
anything is reported. The strictly worse outcome it replaces is UNPROBED, which
is a route nobody looked at at all.

VERIFICATION IS NOT COMPLETE AND THIS FILE DOES NOT PRETEND IT IS. Every
assertion here is arithmetic over the constants, which is checkable in a
container. Whether 264 routes then FIT in phase 1 on a loaded box depends on
per-route latency under the sibling live checks -- L20's 8 parallel readers and
L21's 180 read-rounds run against the app while L34 sweeps -- and only a capture
can answer that. What is proven here: the budget is affordable by construction,
the floor cannot silently swallow a surface too large to sweep, and the derived
value is the one the sweep actually uses.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from live_tests import checks   # noqa: E402


# The operator surface measured on the box at v3.66.1007, from L34's own log.
# Not read from ROUTE_INDEX.json: this file's subject is the arithmetic, and
# coupling it to a generated artifact would make it fail for route additions
# that are not defects.
_BOX_OPERATOR_ROUTES = 264


def _phase1_deadline(wall=None):
    wall = checks._L34_WALL_S if wall is None else wall
    return wall * (1.0 - checks._L34_PHASE2_RESERVE)


def test_the_helper_exists():
    assert hasattr(checks, "_l34_triage_budget_s"), (
        "the triage budget is still a bare constant, so it cannot know how "
        "many routes it has to pay for")
    assert hasattr(checks, "_L34_TRIAGE_FLOOR_S"), (
        "no floor: a large enough surface would derive a budget too short to "
        "distinguish a slow route from a fast one")


def test_phase1_can_afford_every_operator_route_at_the_box_surface():
    """THE BOX'S FAILURE, AS ARITHMETIC.

    Worst case is every route holding a worker for the whole triage budget.
    At the shipped 5s constant that is 264 * 5 = 1320 worker-seconds against
    316.8 available -- over by 4x, which is why 92 went unprobed.
    """
    d = _phase1_deadline()
    budget = d * checks._L34_WORKERS
    triage = checks._l34_triage_budget_s(d, _BOX_OPERATOR_ROUTES)
    assert triage * _BOX_OPERATOR_ROUTES <= budget, (
        "phase 1 cannot pay for its own sweep: %d routes x %.2fs = %.0f "
        "worker-seconds against %.0f available. Routes it cannot reach are "
        "reported UNPROBED, which is a FAIL naming no defect."
        % (_BOX_OPERATOR_ROUTES, triage, triage * _BOX_OPERATOR_ROUTES, budget))


@pytest.mark.parametrize("n", [120, 264, 400, 900])
def test_it_is_affordable_at_every_surface_the_floor_does_not_bind(n):
    """The property has to hold as the surface grows, not just at today's
    count -- that is the difference between a derivation and a smaller
    literal."""
    d = _phase1_deadline()
    budget = d * checks._L34_WORKERS
    triage = checks._l34_triage_budget_s(d, n)
    if triage <= checks._L34_TRIAGE_FLOOR_S:
        pytest.skip("the floor binds at n=%d; that case is asserted separately" % n)
    assert triage * n <= budget + 1e-6, (n, triage, budget)


def test_a_SMALL_surface_keeps_todays_five_second_budget():
    """THE OTHER DIRECTION, and the one that makes this a change rather than a
    rewrite. Where the old constant was affordable it must survive: an
    unconditional shrink would flag slow-but-healthy routes on a small surface
    for no reason, pushing work into phase 2 that phase 1 could pay for.
    Over-sensitivity is a soundness bug, not a safe default."""
    d = _phase1_deadline()
    triage = checks._l34_triage_budget_s(d, 40)
    assert triage == pytest.approx(checks._L34_TRIAGE_BUDGET_S), (
        "a 40-route surface can afford %.1fs each; the ceiling should still "
        "govern, got %.2fs" % (d * checks._L34_WORKERS / 40, triage))


def test_the_floor_holds_and_the_shortfall_is_REPORTED_not_swallowed():
    """A surface so large that even the floor is unaffordable is a real state,
    and clamping silently would make the check claim a sweep it cannot do.
    Unknown is a third state: it has to say so."""
    d = _phase1_deadline()
    huge = 10_000
    triage = checks._l34_triage_budget_s(d, huge)
    assert triage == pytest.approx(checks._L34_TRIAGE_FLOOR_S), (
        "the floor did not hold at n=%d: %.3f" % (huge, triage))

    said = []
    checks._l34_triage_budget_s(d, huge, log=said.append)
    assert said, (
        "the budget collapsed to the floor and nothing was logged -- the "
        "operator reads a long UNPROBED list with no explanation")
    assert any(str(huge) in m or "afford" in m.lower() for m in said), said

    quiet = []
    checks._l34_triage_budget_s(d, 40, log=quiet.append)
    assert not quiet, (
        "it logged a shortfall on a surface it can comfortably afford -- a "
        "warning that fires when nothing is wrong gets ignored: %r" % quiet)


def test_the_sweep_USES_the_derived_value_and_not_the_bare_constant():
    """A derivation nothing calls is a comment. AST over the check's own body:
    phase 1's probe submissions must not name the ceiling constant directly.

    Scoped to the OPERATOR sweep. The diagnostic advisory pass legitimately
    keeps the ceiling: it does not gate the deploy, it runs last on whatever
    wall is left, and shortening it would change what "slow(>Ns)" means in the
    advisory line for no benefit to the gate.

    THE EXEMPTION IS STRUCTURAL, KEYED ON WHAT THE ENCLOSING LOOP ITERATES --
    never on a line number. The first draft of this test flagged line 730, the
    diagnostics probe, for a reason its own docstring already said was
    legitimate; excusing it by location would have re-armed silently the moment
    anything above it moved, which is the trap CLAUDE.md section 0 records for
    location-keyed allowances.
    """
    src = (REPO / "live_tests" / "checks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "l34_full_route_smoke"), None)
    assert fn is not None, "l34_full_route_smoke not found"

    parent = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def _iterates_diagnostics(node):
        """True iff the nearest enclosing for/comprehension walks the advisory
        diagnostic surface rather than the gated operator one."""
        cur = node
        while cur is not None:
            it = None
            if isinstance(cur, (ast.For, ast.AsyncFor)):
                it = cur.iter
            elif isinstance(cur, ast.comprehension):
                it = cur.iter
            if isinstance(it, ast.Name) and it.id == "diagnostic_targets":
                return True
            cur = parent.get(cur)
        return False

    def _budget_args(node):
        if isinstance(node.func, ast.Name) and node.func.id == "_probe":
            return node.args[1:]
        if isinstance(node.func, ast.Attribute) and node.func.attr == "submit":
            return node.args          # pool.submit(_probe, r, <budget>)
        return []

    offenders = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        for arg in _budget_args(node):
            if (isinstance(arg, ast.Name)
                    and arg.id == "_L34_TRIAGE_BUDGET_S"
                    and not _iterates_diagnostics(node)):
                offenders.append(node.lineno)
    assert not offenders, (
        "the OPERATOR sweep still probes at the bare ceiling constant at "
        "line(s) %r -- the derived budget is computed and unused" % offenders)


def test_the_ast_scan_can_see_the_probe_calls():
    """Non-empty denominator, before the verdict above. A walk that found no
    _probe calls would report "no offender" truthfully and uselessly."""
    src = (REPO / "live_tests" / "checks.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "l34_full_route_smoke")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and ((isinstance(n.func, ast.Name) and n.func.id == "_probe")
                  or (isinstance(n.func, ast.Attribute) and n.func.attr == "submit"))]
    assert len(calls) >= 3, (
        "found only %d probe/submit call(s) in l34_full_route_smoke -- the "
        "scan went blind" % len(calls))


def test_the_worst_case_elapsed_still_fits_the_wall():
    """The ceiling arithmetic tests/test_l34_wall_fits_the_harness_timeout.py
    derives, re-checked against the shorter triage. A shorter budget can only
    lower this, but asserting it is cheap and the failure would be a leaked
    thread smoking routes underneath L31/L33."""
    d = _phase1_deadline()
    triage = checks._l34_triage_budget_s(d, _BOX_OPERATOR_ROUTES)
    ceiling = (d                                   # phase 1 stops submitting
               + triage + 2                        # in-flight drain
               + checks._L34_ROUTE_BUDGET_S        # one phase-2 probe
               + checks._L34_TRIAGE_BUDGET_S)      # one diagnostic probe
    assert ceiling <= checks._L34_WALL_S, (
        "worst-case elapsed %.1fs exceeds the %.1fs wall" % (ceiling, checks._L34_WALL_S))
