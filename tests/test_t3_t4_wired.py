"""T3/T4 library, tags, sites and runner-ops contract -- EXECUTED.

WHAT THIS FILE USED TO BE (backlog row 183).  Its three tests read raw TSX/TS
source.  Two endpoint tests credited a family when its literal appeared in a
hook, even if no route ever called that hook.  The safety test searched
``onClick={...}`` text for ``.mutate``.  That is formatting, not behaviour:

* ``regenNfos.mutate({overwrite: true})`` evaded the literal-space requirement;
* ``const fire = () => regenNfos.mutate(...)`` plus ``onClick={fire}`` evaded
  the lexical onClick body entirely;
* a destructive handler on ``onMouseDown`` sat outside the regex's event-name
  denominator.

All three were replayed against the untouched base with ``bd-mutate``.  The old
three-test band stayed green for every live violation.

WHAT IT IS NOW.  Following rows 182 and 185, the runtime claims delegate through
the fail-closed Vitest bridge.  ``T3T4.endpoints.test.tsx`` renders and drives
Library, RebalanceCenter, Maintenance and ImportsCenter, then reconciles the
METHOD + NORMALISED PATHS actually handed to the transport (or browser download
anchor) to an exact 12 + 11 = 23-family population.  ``T3T4.confirm.test.tsx``
drives all three dangerous-selection writes and invokes every pointer, mouse and
keyboard handler React bound to the initial non-presentational DOM under each
route's own content subtree.

DECLARED EVASION SURFACE.  The exhaustive safety sweep covers React handlers
bound on the initial non-SVG, non-``aria-hidden`` DOM under those three route
roots for pointerDown, mouseDown, click, mouseUp, pointerUp, Enter or Space
keyDown, and Enter keyUp.  SVG/presentational descendants are excluded because
their parent controls are driven; an event attached only to such a descendant,
or a control rendered only after another interaction, is outside the claim.  It
also does not claim to cover document/window handlers, timers,
observers, hover/touch-only events, form submission without one of those
stimuli, or a raw transport that bypasses both api-client and ``fetch``.  The endpoint seam is the mocked
api-client plus mocked ``fetch`` and captured template anchor clicks, not the
network; CSRF, cookies and server authorization belong to their own gates.

``T3T4.transform.test.tsx`` deliberately imports the four subjects without
asserting their behaviour.  It is the mutation transform control: a valid
one-click mutant must ESCAPE that nodeid, proving the behavioural battery's
CAUGHT verdicts are assertion failures rather than TSX transform failures.

run_tests.py conventions: zero-arg test functions; no pytest builtins.
"""
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from tests.frontend_vitest import FRONTEND, VITEST, run_vitest

BD_GATE_SCOPE = "repo-wide"

_VITEST_LOADED_WORST_MS = 8_841
_VITEST_TEST_TIMEOUT_MS = 13_262
_VITEST_HANG_OUTER_TIMEOUT_S = 30
_GOVERNING_PYTEST_ITEM_TIMEOUT_MS = 240_000

# Independent pinned denominator: do not derive this population from the
# route/hook artifact under test.  The Vitest spec carries its own duplicate
# method-aware population and asserts the same 12 + 11 = 23 arithmetic.
T3_ENDPOINTS = [
    "/api/library/audit",
    "/api/library/orphans",
    "/api/library/regen_nfos",
    "/api/library/stats",
    "/api/tags/add",
    "/api/tags/for_many",
    "/api/tags/remove",
    "/api/tags/rename",
    "/api/tags/rows/*",
    "/api/tags/suggest/*",
    "/api/scene_score/bottom",
    "/api/storage_rebalance/inventory",
]
T4_ENDPOINTS = [
    "/api/sites/bulk_csv",
    "/api/sites/csv_template",
    "/api/sites/xlsx_template",
    "/api/runners/pause_all",
    "/api/runners/resume_all",
    "/api/concurrent/*",
    "/api/rate_limit/status",
    "/api/retry_policy",
    "/api/crash_recovery/scan",
    "/api/crash_recovery/*",
    "/api/file/reveal",
]

_SPEC_DENOMINATORS = {
    "src/routes/T3T4.endpoints.test.tsx": 4,
    "src/routes/T3T4.confirm.test.tsx": 4,
    "src/routes/T3T4.transform.test.tsx": 1,
}


def test_t3_t4_family_denominator_is_exact_and_nonzero():
    """The declared population is independently pinned at 12 + 11 = 23."""
    assert len(T3_ENDPOINTS) == 12, "T3 family denominator changed from 12"
    assert len(T4_ENDPOINTS) == 11, "T4 family denominator changed from 11"
    population = T3_ENDPOINTS + T4_ENDPOINTS
    assert population, "T3/T4 family denominator is zero"
    assert len(population) == len(set(population)) == 23, (
        "T3/T4 families must be 23 unique endpoint families")


def test_t3_t4_endpoint_families_are_consumed_at_runtime():
    """All 23 families are reached by rendering and driving their SPA routes."""
    spec = "src/routes/T3T4.endpoints.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_dangerous_selection_writes_are_confirmation_gated_at_runtime():
    """Crash-file delete, overwrite regen and bulk import cannot fire from one
    pointer/keyboard interaction; their explicit confirm paths fire exactly
    once, so a missing/dead action cannot manufacture green."""
    spec = "src/routes/T3T4.confirm.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t3_t4_transform_control_imports_subjects_without_judging_behaviour():
    """Mutation-only transform control; this is deliberately not safety proof."""
    spec = "src/routes/T3T4.transform.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_the_vitest_budget_is_derived_from_the_heavier_loaded_measurement():
    """Row 339: the wall retains 50% headroom over the loaded 617-case worst.

    The retained runtime report measured History.confirm at 8840.502ms while
    test5's 48-core load average rose from 5.95 to 26.18.  Milliseconds are
    rounded up before applying the safety factor, so no fractional measurement
    is silently truncated.  The independent 240s pytest item remains the owner.
    """
    derived = math.ceil(_VITEST_LOADED_WORST_MS * 1.5)
    assert derived == 13_262
    assert _VITEST_TEST_TIMEOUT_MS == derived
    assert _VITEST_TEST_TIMEOUT_MS < _GOVERNING_PYTEST_ITEM_TIMEOUT_MS


def test_the_measured_vitest_budget_still_fires_for_genuinely_hung_work():
    """The explicit frontend budget must fail a real never-settling case.

    The fixture runs through a copy of the tracked config, rather than reading
    the config as text.  The outer subprocess bound governs this negative
    control if Vitest ever loses its own timer entirely.
    """
    assert VITEST.is_file(), f"Vitest unavailable at {VITEST}"
    with tempfile.TemporaryDirectory(prefix="bd_vitest_timeout_control_") as raw:
        root = Path(raw)
        (root / "src").mkdir()
        shutil.copy2(FRONTEND / "vitest.config.ts", root / "vitest.config.ts")
        shutil.copy2(FRONTEND / "vitest.setup.ts", root / "vitest.setup.ts")
        os.symlink(
            FRONTEND / "node_modules",
            root / "node_modules",
            target_is_directory=True,
        )
        spec = root / "src" / "timeout-control.test.ts"
        spec.write_text(
            'import { it } from "vitest";\n'
            'it("never-settling negative control", async () => {\n'
            '  await new Promise<void>(() => {});\n'
            '});\n',
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                str(VITEST),
                "run",
                "src/timeout-control.test.ts",
                "--reporter=verbose",
                "--no-color",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_VITEST_HANG_OUTER_TIMEOUT_S,
        )

    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "never-settling negative control" in output, output
    assert "Tests  1 failed (1)" in output, output
    assert f"Test timed out in {_VITEST_TEST_TIMEOUT_MS}ms." in output, output
