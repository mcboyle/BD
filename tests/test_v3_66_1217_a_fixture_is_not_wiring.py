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
    body = 'export const x = () => apiGet("%s");\n' % sentinel

    fixture = SPA_SRC / "bd1217_probe.test.ts"
    product = SPA_SRC / "bd1217_probe.ts"
    try:
        fixture.write_text(body, encoding="utf-8")
        eps, _ = mod._spa_wiring(ROOT)
        assert sentinel not in eps, (
            "an endpoint named ONLY in a *.test.ts file was counted as SPA "
            "wiring, so a fixture can vouch for a route no product code calls")

        fixture.unlink()
        product.write_text(body, encoding="utf-8")
        eps, _ = mod._spa_wiring(ROOT)
        assert sentinel in eps, (
            "the same endpoint in a PRODUCT file was NOT counted, so this gate "
            "would pass over a scanner that had stopped seeing anything at all")
    finally:
        for path in (fixture, product):
            if path.exists():
                path.unlink()


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
