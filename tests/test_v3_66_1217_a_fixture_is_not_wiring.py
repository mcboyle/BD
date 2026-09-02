"""A test fixture must never count as evidence that the SPA wires an endpoint.

WHAT `_spa_wiring` IS FOR. It answers "does the React SPA actually call this
/api/* endpoint?", and the parity gates treat a YES as coverage. Its population
is therefore its denominator, and anything in that population can vouch for a
route.

THE DEFECT. It scanned `frontend/src` with `rglob("*.ts*")`, which includes
every *.test.tsx and *.spec.tsx file. A test FIXTURE could therefore supply the
evidence that product code wires a route. Measured at v3.66.1216: 457 wired
endpoints with test files in the population, 443 without -- FOURTEEN present
only because a test named them, every one an obvious fixture:

    /api/auth/users/bob/role          /api/auth/users/carol/password
    /api/auth/users/dave              /api/auth/users/a%20b%2Fc/role
    /api/sites/alpha/ai_reanalyze     /api/sites/s1/session/reuse_onboarding
    /api/daily_budget/history/ex.com  /api/knowledge/notes/7
    /api/queue_templates/7            /api/queue_templates/7/apply/alpha
    /api/queue_templates/7/apply/beta /api/cookie_clipboard/save
    /api/cookie_clipboard/save/alpha  /api/webhooks/1

THIS IS THE SAME LAUNDERING v3.66.754b CLOSED, THROUGH THE OTHER DOOR. That cut
began stripping TS COMMENTS so a path merely NAMED could not count as called.
Comments are one way to name without calling; test files are the other, and only
the first was closed. The scanner's own docstring at :535 records the comment
case; nothing recorded this one.

WHAT THIS CUT DOES NOT CLAIM. Removing the fourteen changed no verdict --
tests/test_parity_method_aware.py and tests/test_gui_parity.py are 19/19 both
before and after -- so no gap was being MASKED today. The defect is that the
evidence was admissible at all: a future fixture naming a route that product
code had stopped calling would have silently kept that route looking covered.
That is a latent fail-open, fixed before it fired, and it is stated that way
rather than dressed up as a live bug.
"""
from __future__ import annotations

import pathlib
import re
from importlib import util

import pytest

BD_GATE_SCOPE = "module"

ROOT = pathlib.Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "tools" / "gui_parity_inventory.py"
SPA_SRC = ROOT / "frontend" / "src"


def _load():
    spec = util.spec_from_file_location("bd_gui_parity_inventory", INVENTORY)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_population_predicate_classifies_both_kinds():
    """The rule itself, before any scan: product in, fixtures out."""
    mod = _load()
    keep = ["App.tsx", "hooks/useDashboardData.ts", "routes/Advanced.tsx",
            "lib/api-client.ts", "components/AddUrlDialog.tsx"]
    drop = ["a11y.fills.test.ts", "styles.bd-table.test.ts",
            "routes/Dashboard.wired.test.tsx", "components/AuthGate.test.tsx",
            "something.spec.ts", "something.spec.tsx"]
    for name in keep:
        assert mod._is_spa_source(pathlib.Path(name)), name
    for name in drop:
        assert not mod._is_spa_source(pathlib.Path(name)), name


def test_a_fixture_endpoint_in_a_test_file_is_not_counted(tmp_path):
    """THE RED CONTROL. Plant an endpoint that exists ONLY in a test file.

    It must be absent from the wired set. The same endpoint planted in a PRODUCT
    file must be present -- without that second half this test would pass on a
    scanner that had simply stopped working.
    """
    mod = _load()
    sentinel = "/api/bd1217/fixture_only_endpoint"
    control = "/api/bd1217/product_control_endpoint"
    body = 'export const x = () => apiGet("%s");\n'

    # THE PROBE IS PLANTED IN A TEMP TREE, NEVER IN THE REAL frontend/src.
    # `_spa_wiring` takes its root as an argument, so it can be pointed at one.
    # Writing the probe into the real tree held two files that do not typecheck
    # (the body calls apiGet without importing it) across a full tree scan,
    # and tests/frontend_vitest.py copies that same directory and runs `tsc -b`
    # over the copy -- so a sibling xdist worker building during the window
    # failed on a file tracked nowhere. See the guard test below.
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True)
    fixture = src / "bd1217_probe.test.ts"
    product = src / "bd1217_probe.ts"

    # A PRODUCT CONTROL IN EVERY SCAN. `_spa_wiring` returns an EMPTY set for a
    # root with no frontend/src, so on a temp root `sentinel not in eps` would
    # pass just as loudly over a scan that read nothing at all. This file makes
    # the first scan's denominator provably nonzero.
    (src / "bd1217_control.ts").write_text(body % control, encoding="utf-8")

    fixture.write_text(body % sentinel, encoding="utf-8")
    eps, _ = mod._spa_wiring(tmp_path)
    assert control in eps, (
        "the scanner harvested nothing from the planted tree, so this half "
        "would report a fixture uncounted merely because nothing was read")
    assert sentinel not in eps, (
        "an endpoint named ONLY in a *.test.ts file was counted as SPA "
        "wiring, so a fixture can vouch for a route no product code calls")

    fixture.unlink()
    product.write_text(body % sentinel, encoding="utf-8")
    eps, _ = mod._spa_wiring(tmp_path)
    assert control in eps, (
        "the scanner stopped reading the planted tree between the two scans")
    assert sentinel in eps, (
        "the same endpoint in a PRODUCT file was NOT counted, so this gate "
        "would pass over a scanner that had stopped seeing anything at all")


def test_the_known_fixture_endpoints_are_gone_and_the_set_is_still_large():
    """The measured fourteen are absent, and the denominator did not collapse.

    Both halves matter: deleting the whole population would also remove the
    fourteen, so the surviving count is asserted to be substantial and to still
    contain endpoints only product code calls.
    """
    mod = _load()
    eps, _ = mod._spa_wiring(ROOT)

    for fixture in ("/api/auth/users/bob/role",
                    "/api/auth/users/carol/password",
                    "/api/auth/users/dave",
                    "/api/sites/alpha/ai_reanalyze",
                    "/api/daily_budget/history/ex.com",
                    "/api/knowledge/notes/7",
                    "/api/queue_templates/7/apply/beta",
                    "/api/cookie_clipboard/save/alpha"):
        assert fixture not in eps, (
            "%s is a TEST FIXTURE and is being counted as SPA wiring" % fixture)

    assert len(eps) > 350, (
        "the wired set collapsed to %d endpoints; excluding test files was "
        "supposed to remove ~14, not gut the denominator" % len(eps))
    for real in ("/api/health", "/api/sites", "/api/stats"):
        assert real in eps, (
            "%s is called by product code and must still be counted" % real)


def test_no_surviving_endpoint_looks_like_a_test_fixture():
    """A CENSUS RATHER THAN A BLOCKLIST. The eight names above are a sample; if
    a NEW fixture-shaped path appears, listing known ones would not catch it.

    The heuristic is deliberately narrow -- personal-name and encoded-space
    markers that a real route pattern would never carry -- so it names anything
    it finds instead of failing on a bare count.
    """
    mod = _load()
    eps, _ = mod._spa_wiring(ROOT)
    fixture_like = re.compile(r"/(bob|carol|dave|alpha|beta)(/|$)|%20|%2F", re.I)
    offenders = sorted(e for e in eps if fixture_like.search(e))
    assert not offenders, (
        "endpoints that look like test fixtures are being counted as SPA "
        "wiring: %r" % offenders)


def test_both_scan_sites_share_the_population_rule():
    """gui_parity_inventory scans frontend/src TWICE. Fixing one and not the
    other would leave the second free to re-admit fixtures, and nothing else in
    this file would notice because it only exercises _spa_wiring."""
    source = INVENTORY.read_text(encoding="utf-8")
    scan_sites = source.count('src.rglob("*.ts*")')
    guarded = source.count("_is_spa_source(f)")
    assert scan_sites >= 2, (
        "expected at least two rglob scan sites over frontend/src; the file "
        "changed shape and this gate's premise needs re-deriving (found %d)"
        % scan_sites)
    assert guarded == scan_sites, (
        "%d scan site(s) over frontend/src but only %d guarded by "
        "_is_spa_source -- an unguarded site can still admit test fixtures"
        % (scan_sites, guarded))


def test_the_probe_is_never_planted_in_the_real_spa_source(tmp_path, monkeypatch):
    """The fixture probe must be planted in a TEMP tree, never in `frontend/src`.

    THE DEFECT THIS GUARDS. The test above wrote `bd1217_probe.test.ts` and
    `bd1217_probe.ts` into the REAL `frontend/src`, held them across a full
    `_spa_wiring` scan of the tree, and unlinked them in a `finally`. Neither
    file typechecks -- the body calls `apiGet` without importing it -- and
    `tests/frontend_vitest.py::isolated_spa_dist` copies that same directory and
    runs `tsc -b` over the copy. A sibling worker that copies during the window
    builds an untypecheckable tree. MEASURED at 9e7031fb on the full suite:
    `test_t7_notifications_wired.py::test_t7_route_is_a_lazy_dynamic_entry`
    failed with `src/bd1217_probe.test.ts(1,24): error TS2304: Cannot find name
    'apiGet'` -- a file tracked nowhere in the repository.

    WHY THIS GUARD OBSERVES DURING THE SCAN. A before/after snapshot of
    `frontend/src` cannot see the defect at all: the `finally` unlinks both
    files, so the tree is clean again by the time the test returns and the
    snapshot is green on the defective base. That is teardown restoration
    manufacturing green, which CLAUDE.md A7 names explicitly. The only moment
    the defect exists is DURING the scan, so the scan is where this looks.
    """
    leftover = sorted(p.name for p in SPA_SRC.glob("bd1217_probe*"))
    assert leftover == [], (
        "precondition: the real frontend/src already holds %r, so this guard "
        "cannot attribute what it finds to the test under observation. Remove "
        "the residue of the earlier crashed run first." % leftover)

    real = _load()
    at_real, at_target = [], []

    class _WatchedInventory:
        """Delegates everything, and records WHERE the probe is at scan time."""

        def __getattr__(self, name):
            return getattr(real, name)

        def _spa_wiring(self, root):
            at_real.append(sorted(p.name for p in SPA_SRC.glob("bd1217_probe*")))
            target = pathlib.Path(root) / "frontend" / "src"
            at_target.append(sorted(p.name for p in target.glob("bd1217_probe*")))
            return real._spa_wiring(root)

    monkeypatch.setitem(globals(), "_load", lambda: _WatchedInventory())
    test_a_fixture_endpoint_in_a_test_file_is_not_counted(tmp_path)

    # Preconditions before the verdict: the seam fired, and it fired the exact
    # number of times the test under observation is supposed to scan. A guard
    # that observed nothing would otherwise report a clean tree.
    assert len(at_real) == 2, (
        "expected exactly 2 scans from the fixture test, observed %d; this "
        "guard is measuring something other than its subject" % len(at_real))
    # ...and the probe really was planted SOMEWHERE, so a fixture test degraded
    # into a no-op cannot pass this by writing no files at all.
    assert at_target[0] == ["bd1217_probe.test.ts"], (
        "the fixture half of the probe was not planted at the scanned root "
        "(observed %r), so the test under observation proved nothing and this "
        "guard must not report it clean" % at_target[0])
    assert at_target[1] == ["bd1217_probe.ts"], (
        "the product half of the probe was not planted at the scanned root "
        "(observed %r)" % at_target[1])

    assert at_real == [[], []], (
        "the fixture test planted %r in the REAL frontend/src and held it "
        "across a tree scan. tests/frontend_vitest.py copies that directory "
        "and runs `tsc -b` over the copy, and neither probe file typechecks, "
        "so any sibling worker building during this window fails on a file "
        "that is tracked nowhere. Plant the probe under tmp_path." % at_real)
