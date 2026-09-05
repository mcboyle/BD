"""Current history, logs, and saved-search SPA contract -- EXECUTED.

WHAT THIS FILE USED TO BE, AND WHY IT CHANGED (backlog row 182). Three of its
tests judged RUNTIME properties by substring search over ``frontend/src``:

* the 12 endpoint families were "wired" if ``"/api/logs/clear"`` and friends
  appeared as literals in useHistoryData.ts;
* /history was "lazy with an inbound nav link" if a ``lazy(() =>
  import("./routes/History"))`` regex matched App.tsx and ``go("/history")``
  appeared in CommandPalette.tsx;
* the four writes were "never one-click" if a regex over ``onClick={...}``
  found no ``.mutate`` inside the braces.

MEASURED, not argued: that last regex is blind on the CURRENT tree. History.tsx
defines ``onToggleAction`` (a named handler) which dispatches
``savedUpdate.mutate`` with no dialog, and wires it to a button's onClick as a
bare identifier. ``.mutate`` never appears inside an ``onClick={...}`` brace, so
``re.search(r"onClick=\\{[^}]*\\.mutate", route)`` reports clean over a real
one-click write. Every evasion of that shape -- a named handler, a prop-level
wrapper, an ``arm`` that dispatches instead of arming, an array/``map``
indirection -- is a genuine unconfirmed destructive write the scan consents to.

WHAT IT IS NOW. The same three claims are exercised against the real components
through Vitest, on the pattern tests/test_t7_notifications_wired.py established
(row 188), via the fail-closed bridge in ``tests/frontend_vitest.py``:

* ``History.endpoints.test.tsx`` -- the route is mounted and every tab driven,
  and the paths the TRANSPORT was actually handed are reconciled to the declared
  families by SET EQUALITY, so a dropped family and an undeclared new one both
  go red; the declared set is also closed against the hook's own comment-
  stripped literals, so a new family cannot be added to the hook silently;
* ``History.confirm.test.tsx``   -- all four gated writes are clicked for real
  and the transport is asked whether anything was sent BEFORE the dialog is
  confirmed, plus a CLOSED-ALLOWLIST SWEEP that clicks every enabled button on
  every tab, one per fresh mount, and asserts the set of writes that reached the
  transport without a dialog equals the one deliberate exception exactly;
* ``History.route.test.tsx``     -- /history is resolved through the REAL App
  <Routes> table (not a hand-placed component), with a negative control at
  another path, and the command palette's item is SELECTED and the resulting
  pathname asserted.

The lazy-chunk half of the routing claim is proved by the Vite manifest, in its
own nodeid: build_manifest() runs ``tsc -b`` plus a full ``vite build``, and a
mutation battery re-runs its band once per mutant, so folding it into a
run_vitest test would make every mutant pay for a build.

WHAT IS STILL NOT CONSTRAINED, stated rather than implied by a green run:

* THE SWEEP'S POPULATION IS ``role=button`` on the four tabs of /history. A
  write wired to a table-row/cell onClick, an <a>, a keydown, a form onSubmit,
  an IntersectionObserver, or navigator.sendBeacon is outside it. Measured at
  this candidate: no such write exists on this route.
* THE SEAM IS THE MOCKED api-client, NOT THE WIRE. Nothing here judges CSRF
  headers, cookies, or server-side authorization; that stays with
  frontend/src/lib/api-client.csrf.test.ts. A raw mutating ``fetch`` is caught
  only by the sweep's method assertion.
* THE FAMILY LIST IS PINNED (with a closure against the hook), so a deliberate
  co-edited expansion consents while a drop or a drift does not. Widening the
  constant to a wildcard to clear a future red would retire the claim.
* tests/test_confirm_tiers_209.py still runs the same evadable
  ``onClick={...mutate...}`` regex over Maintenance.tsx, History.tsx and
  ImportsCenter.tsx. It is DELIBERATELY LEFT: it is the only authority over the
  other two routes, and this cut converts History alone. For History it is now a
  redundant floor with known-zero discriminating power -- do not read a green
  there as evidence about this route.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
from tests.frontend_vitest import build_manifest, run_vitest

BD_GATE_SCOPE = "repo-wide"

# MEASURED, NOT GUESSED. Each count is what Vitest reported for that spec on
# test5 at this candidate; run_vitest asserts passed == collected == expected,
# so a spec that silently loses a test fails closed rather than shrinking its
# own denominator.
_SPEC_DENOMINATORS = {
    "src/routes/History.endpoints.test.tsx": 4,
    "src/routes/History.confirm.test.tsx": 7,
    "src/routes/History.route.test.tsx": 3,
    "src/routes/DryRunInspector.contract.test.tsx": 2,
}


def test_t2_endpoint_families_are_consumed_at_runtime():
    """The 12 history/logs/saved-search families are CALLED, not merely named,
    and the declared set is closed against the hook's own literals."""
    spec = "src/routes/History.endpoints.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t2_writes_are_confirmation_gated_at_runtime():
    """Every destructive write is clicked and observed to send nothing until the
    dialog is confirmed, and a closed-allowlist sweep bounds the writes that may
    fire from a single click at all."""
    spec = "src/routes/History.confirm.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t2_route_is_reachable_at_runtime():
    """/history resolves through the real App route table, and selecting the
    palette item lands on that pathname."""
    spec = "src/routes/History.route.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_row734_dry_run_inspector_reads_the_emitted_candidate_key():
    spec = "src/routes/DryRunInspector.contract.test.tsx"
    receipt = run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])
    assert receipt["files_passed"] == receipt["files_collected"] == 1
    assert receipt["tests_passed"] == receipt["tests_collected"] == 2


def test_t2_route_is_a_lazy_dynamic_entry():
    """The BUILD half of the routing claim, which no jsdom render can answer.

    A separate nodeid on purpose -- see the docstring at the top of this file.
    """
    manifest = build_manifest()
    entry = manifest.get("src/routes/History.tsx")
    assert isinstance(entry, dict), (
        "src/routes/History.tsx is absent from the Vite manifest, so this gate "
        "cannot see the subject it claims to judge")
    assert entry.get("isDynamicEntry") is True, (
        "History must remain a lazy, separately built route; the manifest entry "
        "reports isDynamicEntry=%r" % (entry.get("isDynamicEntry"),))
