"""Row 232 -- every scanner that reads frontend/src TEXT declares its population.

THE DEFECT, MEASURED TWICE IN OPPOSITE DIRECTIONS. v3.66.1217 and v3.66.1218
each fixed one scanner that walked ``frontend/src`` with ``rglob("*.ts*")`` and
so judged a PRODUCT property over a population that also contains Vitest specs.
In the parity inventory a FIXTURE vouched for 14 endpoints no product code
called; in the t5/t6 scanner a spec's deliberate tokenless-fetch NEGATIVE
CONTROL manufactured a CI failure on a correct cut. Same glob, same
over-inclusion, opposite consequence -- and reshaping the control to dodge the
second one would have been evading the gate rather than fixing it.

Row 232 filed the remainder as a CLASSIFICATION task, not a blanket patch. Row
184 later retired all three t5/t6 Python scan sites: endpoint and CSRF claims
are executed through Vitest, while the unavoidable global raw-fetch absence
floor parses TypeScript/TSX structurally. The right population still depends
on the question; the census below now measures the remaining Python sites.

WHAT THIS GATE ASSERTS.

1. The site list is DERIVED, not typed. Every ``.rglob``/``.glob`` call over a
   ``*.ts``/``*.tsx`` pattern in tracked ``tests/`` and ``tools/`` is found by
   parsing the AST -- not by ``source.count(...)``, which counts the string
   inside a comment too (CLAUDE.md A7's inverse trap; v3.66.1217's own census
   has that shape).
2. Every found site is CLASSIFIED with a recorded reason, and the table carries
   no stale rows. A new scanner appearing anywhere in that population fails
   here until someone decides what its population is.
3. Product-only sites route through ``tools/spa_population.py``, so there is ONE
   definition of "is this SPA product source" instead of fourteen copies.
4. PER SITE, BEHAVIOURALLY: the site's own assertion function is run against a
   planted tree. The laundering arm and the positive arm plant the IDENTICAL
   BYTES and differ only in the FILENAME, which is the sharpest available
   statement of the defect.
5. Both halves of the real population are nonzero, and an empty population is
   UNKNOWN rather than a vacuous pass.

WHAT THIS DOES NOT CLAIM. Measured at v3.66.1225 against the real tree, the
narrowing changed NO verdict: every existence token still resolves in the
product-only half, ``nav_reachability.check_spa`` reports the same zero orphans,
``config_surface_inventory`` reports the same exposure for all 96 global_config
rows, and the one harness file carries zero ``/api/`` literals and zero links.
These are LATENT fail-opens closed before they fired, stated that way rather
than dressed up as live bugs -- the same honesty v3.66.1217 used.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TESTS = ROOT / "tests"
SPA_SRC = ROOT / "frontend" / "src"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import spa_population as SP  # noqa: E402  (needs the sys.path insert above)


# ── the classification table ────────────────────────────────────────────
#
# KEY: (repo-relative path, enclosing def name or "<module>").
# VALUE: (class, reason). Line numbers are deliberately NOT the key -- they
# move under any edit and would turn this into a churn gate instead of a
# population gate.
PRODUCT_ONLY = "product-only"
ALL_SOURCE = "all-source"
GUARDED_ELSEWHERE = "guarded-elsewhere"
OUT_OF_ROW = "out-of-row"
SELF = "self"

_CLASSIFIED: dict[tuple[str, str], tuple[str, str]] = {
    # ── product-only: the question is about the SHIPPED app ──
    ("tools/config_surface_inventory.py", "_global_config_note"): (
        PRODUCT_ONLY,
        "Derives gui_exposure from 'does a control reference this key'. A spec "
        "naming a key would report a control that does not exist -- the exact "
        "1217 laundering, in a second inventory."),
    ("tools/nav_reachability.py", "_source_has_prefix"): (
        PRODUCT_ONLY,
        "Fallback evidence that a server route prefix is linked from source. "
        "Its Python half already excludes tests/, scanning only "
        "bulk_downloader/ and tools/; the frontend half must match. Stays "
        "*.tsx-only: widening to *.ts would admit more voucher files and "
        "silently REDUCE the orphans it reports."),
    ("tools/nav_reachability.py", "check_spa"): (
        PRODUCT_ONLY,
        "'Can a user click to this route?' A <Link> inside a spec is not a "
        "click path in the shipped app."),
    ("tests/test_f2_3_needs_review_wired.py",
     "test_no_raw_mutating_fetch_to_action_paths"): (
        PRODUCT_ONLY,
        "A raw mutating fetch() ships without X-CSRF-Token and 403s on a real "
        "cookie session. A spec never ships and stubs global fetch -- this is "
        "the v3.66.1218 defect, unfixed, in a second file."),
    ("tests/test_v3_66_292_dom_analyzer_link.py", "test_spa_src_present"): (
        PRODUCT_ONLY,
        "The nonzero guard for _tsx_files. It has to measure the SAME "
        "population the scan uses, or it would certify a denominator the scan "
        "does not have. Its behavioural control is the _tsx_files row's."),
    ("tests/test_v3_66_292_dom_analyzer_link.py", "_tsx_files"): (
        PRODUCT_ONLY,
        "href=\"#/x\" is a dead link on a path-based BrowserRouter -- a "
        "property of what ships. A spec planting one as a fixture is a "
        "negative control, not a broken link."),
    ("tests/test_v3_66_310_site_editor_parity.py",
     "test_spa_site_editor_wires_endpoints"): (
        PRODUCT_ONLY,
        "Positive-existence: 'no SPA consumer of the editable endpoint'. A "
        "spec naming the endpoint satisfies it while product code stops "
        "calling it."),
    ("tests/test_v3_66_312_parity_abc.py", "test_b_spa_wires_widgets_store"): (
        PRODUCT_ONLY, "Positive-existence over /api/widgets/*; a spec must not "
        "vouch for a wired store."),
    ("tests/test_v3_66_312_parity_abc.py",
     "test_c_spa_read_panel_wires_env_effective"): (
        PRODUCT_ONLY, "Positive-existence over /api/settings/env/effective; a spec naming "
        "the endpoint is not a read panel that fetches it."),
    ("tests/test_v3_66_313_challenge_parity.py",
     "test_spa_challenge_controls_present"): (
        PRODUCT_ONLY, "'the SPA control exists' -- a spec mentioning the key "
        "is not a control."),
    ("tests/test_v3_66_314_challenge_wait_guard.py",
     "test_spa_challenge_wait_control_present"): (
        PRODUCT_ONLY, "'the SPA control exists' for challenge_wait_s. A spec mentioning the "
        "key would satisfy this while no control shipped."),
    ("tests/test_v3_66_315_advanced_env_tranche.py", "test_spa_controls_present"): (
        PRODUCT_ONLY, "'the SPA control exists' for 15 store keys. A spec mentioning any of "
        "them would vouch for a control that does not exist."),
    ("tests/test_v3_66_316_bucket3_guard.py", "test_spa_controls_present"): (
        PRODUCT_ONLY, "'the SPA control exists' for hud_overlay and lint_kb_allow; a spec "
        "naming either is not a control the user can operate."),
    ("tests/test_v3_66_507_bucket3b_store_raw.py", "test_spa_wires_store_raw"): (
        PRODUCT_ONLY, "Positive-existence over /api/settings/store-raw; a spec naming the "
        "endpoint must not stand in for the raw store editor calling it."),

    # ── all-source: the question is about the SOURCE TREE ──
    ("tests/test_no_raw_unicode_escape_in_jsx.py",
     "test_no_raw_unicode_escape_in_jsx_text"): (
        ALL_SOURCE,
        "A raw \\uXXXX in JSX TEXT renders as eight literal characters "
        "wherever React renders it -- in the browser and in a spec's jsdom "
        "alike. Both are defects, so the population stays wide. Its real "
        "defect was the SILENT RETURN on a missing tree, "
        "which is fixed here: an unmeasurable claim is UNKNOWN, not a pass."),

    # ── already guarded by their own cut; deliberately not re-touched ──
    ("tools/gui_parity_inventory.py", "_spa_wiring"): (
        GUARDED_ELSEWHERE,
        "v3.66.1217's _is_spa_source. tests/test_v3_66_1217_*::"
        "test_both_scan_sites_share_the_population_rule pins the literal text "
        "of this file, so routing it through the shared helper would break "
        "that pin and widen this cut. Equality of the two rules is asserted "
        "by test_the_shared_rule_matches_the_parity_inventory below."),
    ("tools/gui_parity_inventory.py", "spa_wiring_unresolved"): (
        GUARDED_ELSEWHERE, "The second scan site in the same file, behind the same _is_spa_source "
        "guard and inside the same v3.66.1217 text pin."),
    # ── outside row 232's measured population; frozen, not fixed ──
    ("tools/body_contract.py", "fe_calls"): (
        OUT_OF_ROW,
        "FOUND BY THIS GATE, MISSED BY ROW 232's rg: it uses glob.glob, not "
        "rglob, so the row's 14-site measurement never saw it. It applies a "
        "PARTIAL rule -- .test.ts/.test.tsx/.d.ts excluded, .spec.ts/.spec.tsx "
        "NOT -- so a spec can still enter the body-contract population. "
        "MEASURED at v3.66.1225: no *.spec.ts(x) under frontend/src contains "
        "an apiPost/apiPut/apiPatch/apiDelete literal, so the hole is latent. "
        "It is frozen here rather than fixed because toolchain/bin/"
        "bd-body-contract carries a second copy of the same logic and "
        "changing one alone would split them; that is the integrator's "
        "disposition, not this cut's."),

    # ── this gate's own scans, listed rather than exempted ──
    #
    # A census that quietly skipped its own file would be the fail-open it
    # exists to catch. These three walk PLANTED trees under tmp_path, never
    # frontend/src, so no population rule applies -- but a new scan added here
    # still has to say so.
    ("tests/test_v3_66_1225_spa_scanner_populations.py", "_plant"): (
        SELF,
        "Reads back the planted fixture to assert the control actually built "
        "the shape it claims. Population is the tmp tree, by construction."),
    ("tests/test_v3_66_1225_spa_scanner_populations.py",
     "test_select_neither_widens_nor_narrows_the_underlying_glob"): (
        SELF,
        "The *.ts* / explicit-suffix equality proof; it MUST use the old glob "
        "shape, since proving the two agree is the whole point."),
    ("tests/test_v3_66_1225_spa_scanner_populations.py",
     "test_the_all_source_site_fails_closed_on_a_missing_tree"): (
        SELF,
        "Asserts the planted all-source fixture is nonempty before trusting "
        "the site's verdict on it. Population is the tmp tree."),
    ("tests/test_v3_66_1225_spa_scanner_populations.py",
     "test_a_spec_cannot_decide_a_product_question"): (
        SELF,
        "Reads the REAL product text once, to use as the planted payload; the "
        "population is product-only by construction and asserted nonzero "
        "before it is planted."),
    ("tests/test_v3_66_1225_spa_scanner_populations.py",
     "test_the_real_tree_has_every_half_nonzero"): (
        SELF,
        "Measures all three halves of the real tree. It must see the whole "
        "population, since proving each half nonzero is its entire job."),
    ("tests/test_v3_66_1225_spa_scanner_populations.py",
     "test_an_empty_population_is_unknown_not_a_pass"): (
        SELF,
        "The fail-open negative control. Its population is a tmp tree built "
        "empty on purpose, which is the condition under test."),
    ("tests/test_v3_66_1225_spa_scanner_populations.py",
     "test_an_absent_tree_does_not_raise_but_reports_nothing"): (
        SELF,
        "The absent-tree arm of the same control; population is a path that "
        "deliberately does not exist."),
}

#: Product-only sites whose behavioural arm is a SIBLING row's, recorded here
#: rather than left to inference. A site with no control anywhere is narrowed
#: on structural evidence alone, and this is the list of the exceptions and why.
_CONTROL_PROVIDED_BY_SIBLING = {
    ("tests/test_v3_66_292_dom_analyzer_link.py", "test_spa_src_present"):
        "controlled through _tsx_files by test_no_dead_hash_path_hrefs",
}

_MUST_ROUTE_THROUGH_HELPER = {PRODUCT_ONLY}

#: Names that count as routing through the shared rule.
_HELPER_NAMES = frozenset(SP.__all__) | {"spa_population", "SP"}

#: Floor for the derived site list, MEASURED at v3.66.1225: 23 sites. Row 232's
#: rg found 22 hits over tests/+tools/, three of which were a comment, a
#: docstring and a ``source.count`` argument rather than calls; the AST sees
#: only real calls, and additionally finds tools/body_contract.py::fe_calls
#: (glob.glob, which the row's rg could not match), the fixed sites in their
#: helper-call form, and this file's own controls: 28 after the cut. Row 184
#: retired three t5/t6 Python text scans in favor of executed/TypeScript-AST
#: gates; re-derived from _scan_sites() in that cut: 25 remaining sites.
_MIN_SITES = 25


def _tracked_python() -> list[Path]:
    """The population this census is measured over.

    Derived from the filesystem rather than from the artifact under test, and
    asserted nonzero by its caller: an empty file list would make every claim
    below vacuously true.
    """
    out: list[Path] = []
    for base in (TESTS, TOOLS):
        out.extend(p for p in base.rglob("*.py") if p.is_file())
    return sorted(out)


def _string_constants(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _helper_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(module aliases, directly-imported selector names) for one parsed file.

    Resolved per file rather than by matching a bare ``select(...)`` anywhere:
    ``selectors.select`` and ``select.select`` are common in this repository's
    subprocess tests, and counting them would fill the census with files that
    have never heard of the SPA. A denominator that admits the wrong population
    is the defect this cut exists to fix; the census must not commit it.
    """
    aliases: set[str] = set()
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "spa_population":
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "spa_population":
                for alias in node.names:
                    direct.add(alias.asname or alias.name)
    return aliases, direct


#: Acquiring a set of SPA source files through the shared helper is a scan
#: site too. Without this the census would go BLIND to every site it just
#: fixed -- the count would fall, the table would look stale, and a later edit
#: could quietly revert one with nothing left to notice. That is precisely the
#: "a gate must see the subject it claims to judge" failure this cut is about.
_SELECTORS = frozenset({"select", "product_files", "product_text"})


def _scan_sites() -> dict[tuple[str, str], list[int]]:
    """{(relpath, enclosing def): [lineno, ...]} for every frontend-text scan.

    Matches every shape a scanner can take:

    * ``x.rglob("*.ts*")`` -- the raw, unclassified form;
    * ``glob.glob(os.path.join(..., "*.ts*"), recursive=True)`` -- the form row
      232's rg could not see, which is how tools/body_contract.py was missed;
    * ``spa_population.select/product_files/product_text(...)`` -- the fixed
      form.

    A census that saw only the first would leave the exact hole it exists to
    close (CLAUDE.md A7: every fix reproduces the defect's shape).
    """
    files = _tracked_python()
    assert len(files) > 100, (
        "only %d python files found under tests/ and tools/; this census has "
        "no denominator and cannot decide anything" % len(files))
    sites: dict[tuple[str, str], list[int]] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        aliases, direct = _helper_bindings(tree)
        parents: dict[ast.AST, str] = {}
        stack: list[tuple[ast.AST, str]] = [(tree, "<module>")]
        while stack:
            node, owner = stack.pop()
            for child in ast.iter_child_nodes(node):
                name = (child.name
                        if isinstance(child, (ast.FunctionDef,
                                              ast.AsyncFunctionDef))
                        else owner)
                parents[child] = name
                stack.append((child, name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            hit = False
            if isinstance(func, ast.Attribute):
                if func.attr in ("rglob", "glob"):
                    hit = any(p.startswith("*.ts")
                              for p in _string_constants(node))
                elif func.attr in _SELECTORS:
                    hit = (isinstance(func.value, ast.Name)
                           and func.value.id in aliases)
            elif isinstance(func, ast.Name):
                hit = func.id in (direct & _SELECTORS)
            if not hit:
                continue
            sites.setdefault((rel, parents.get(node, "<module>")), []).append(
                node.lineno)
    return sites


# ── 1. the census itself ────────────────────────────────────────────────

def test_the_census_has_a_nonzero_independent_denominator():
    """A gate must see the subject it claims to judge (CLAUDE.md A7)."""
    sites = _scan_sites()
    assert len(sites) >= _MIN_SITES, (
        "the AST census found only %d frontend-source scan sites in tests/ and "
        "tools/, below the %d measured at v3.66.1225. Either sites were "
        "deleted (re-derive the floor in the same cut) or the matcher stopped "
        "matching, which would make every assertion below vacuous. Found: %r"
        % (len(sites), _MIN_SITES, sorted(sites)))


def test_every_scan_site_is_classified_with_a_reason():
    """Row 232's acceptance: classified, with the reason recorded PER SITE."""
    sites = _scan_sites()
    unclassified = sorted(k for k in sites if k not in _CLASSIFIED)
    assert not unclassified, (
        "frontend-source scan site(s) with no declared population. Decide what "
        "each one's question is -- product-only, all-source, or "
        "product-plus-harness -- and record it in _CLASSIFIED: %r"
        % (unclassified,))
    for key, (kind, reason) in _CLASSIFIED.items():
        assert kind in (PRODUCT_ONLY, ALL_SOURCE, GUARDED_ELSEWHERE,
                        OUT_OF_ROW, SELF), (
            "%r has an unknown class %r" % (key, kind))
        assert len(reason) >= 40, (
            "%r records no usable reason for its population choice: %r"
            % (key, reason))


def test_the_classification_table_has_no_stale_rows():
    """A table row for a site that no longer exists is a claim about nothing,
    and it would keep the count above the floor while real sites went
    unclassified."""
    sites = _scan_sites()
    stale = sorted(k for k in _CLASSIFIED if k not in sites)
    assert not stale, (
        "_CLASSIFIED rows that match no scan site in the tree -- the code "
        "moved and the classification did not follow it: %r" % (stale,))


def test_product_only_sites_route_through_the_shared_rule():
    """ONE definition, not fourteen copies. Structural, by AST: the enclosing
    function must reference a ``spa_population`` name."""
    sites = _scan_sites()
    offenders = []
    for key, (kind, _reason) in sorted(_CLASSIFIED.items()):
        if kind not in _MUST_ROUTE_THROUGH_HELPER or key not in sites:
            continue
        rel, owner = key
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == owner:
                target = node
                break
        assert target is not None, "%s no longer defines %s()" % (rel, owner)
        names = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(target) if isinstance(n, ast.Attribute)}
        if not (names & _HELPER_NAMES):
            offenders.append(key)
    assert not offenders, (
        "product-only scan site(s) still deciding their own population instead "
        "of calling tools/spa_population.py. Fourteen copies of a corrected "
        "glob is fourteen chances to drift: %r" % (offenders,))


# ── 2. the rule itself ──────────────────────────────────────────────────

def test_the_population_predicate_classifies_all_three_kinds():
    """Product in, spec out, harness out -- and the SPEC suffix wins inside the
    harness directory, so a spec never gets counted as harness."""
    assert SP.classify("routes/Dashboard.tsx") == SP.PRODUCT
    assert SP.classify("lib/api-client.ts") == SP.PRODUCT
    assert SP.classify("lib/api-client.csrf.test.ts") == SP.SPEC
    assert SP.classify("routes/Thing.spec.tsx") == SP.SPEC
    assert SP.classify("test/wiredGateHarness.tsx") == SP.HARNESS
    assert SP.classify("test/thing.test.tsx") == SP.SPEC
    # NEGATIVE CONTROL: a file merely NAMED like a test is not one.
    assert SP.classify("routes/TestRunner.tsx") == SP.PRODUCT
    assert SP.classify("lib/testing.ts") == SP.PRODUCT
    # and a nested directory called test/ is not the harness root
    assert SP.classify("routes/test/Thing.tsx") == SP.PRODUCT


def test_the_shared_rule_matches_the_parity_inventory():
    """v3.66.1217 wrote the spec rule for tools/gui_parity_inventory.py and
    spa_population is its shared consumer. If the inventory's rule changes,
    this one must change with it or the gates begin disagreeing about what the
    SPA actually IS. Row 184's TypeScript-AST gate separately proves the same
    product/spec halves nonzero and forbids product imports of spec modules.

    Read by AST rather than imported: gui_parity_inventory mutates sys.path at
    import time. Absent or unparseable is a FAILURE -- an unmeasurable rule is
    UNKNOWN."""
    src = (TOOLS / "gui_parity_inventory.py").read_text(encoding="utf-8")
    found = None
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_TEST_FILE_RE"
                   for t in node.targets):
            continue
        call = node.value
        if isinstance(call, ast.Call) and call.args:
            arg = call.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found = arg.value
    assert found is not None, (
        "tools/gui_parity_inventory.py no longer defines _TEST_FILE_RE as a "
        "literal re.compile(...); the rules can no longer be compared, so "
        "re-derive this gate rather than deleting the check")
    assert SP.SPEC_FILE_RE.pattern == found, (
        "population rules drifted: spa_population uses %r, "
        "gui_parity_inventory uses %r" % (SP.SPEC_FILE_RE.pattern, found))


def test_the_real_tree_has_every_half_nonzero():
    """A rule that quietly matched everything would empty the scan and pass
    forever; one that matched nothing would restore the defect. Measured
    against the real tree, not asserted."""
    prod, rest = SP.select(SPA_SRC, (SP.PRODUCT,))
    specs, _ = SP.select(SPA_SRC, (SP.SPEC,))
    harness, _ = SP.select(SPA_SRC, (SP.HARNESS,))
    assert len(prod) > 100, (
        "only %d product SPA files; the rule is eating product source" % len(prod))
    assert len(specs) > 50, (
        "only %d spec files excluded; the exclusion is barely exercised by the "
        "real tree" % len(specs))
    assert harness, (
        "no file classified HARNESS. frontend/src/test/ held wiredGateHarness."
        "tsx at v3.66.1225; if it moved, re-derive HARNESS_DIRS rather than "
        "deleting this assertion -- an unexercised class proves nothing")
    assert sorted(prod + rest) == sorted(prod + specs + harness), (
        "the three classes do not partition the population")
    assert "lib/api-client.ts" in prod
    assert "lib/api-client.csrf.test.ts" in specs
    assert "test/wiredGateHarness.tsx" in harness


def test_select_neither_widens_nor_narrows_the_underlying_glob():
    """DEFAULT_SUFFIXES replaces ``*.ts*``. Proven equal on the real tree, so
    the tightening is a tightening and not a silent drop of files."""
    star = sorted(p.relative_to(SPA_SRC).as_posix()
                  for p in SPA_SRC.rglob("*.ts*") if p.is_file())
    prod, rest = SP.select(SPA_SRC, (SP.PRODUCT, SP.SPEC, SP.HARNESS))
    assert rest == []
    assert sorted(prod) == star, (
        "explicit suffixes and *.ts* disagree; %r only in one side"
        % (sorted(set(star) ^ set(prod)),))
    assert star, "frontend/src holds no .ts/.tsx files at all"


def test_an_empty_population_is_unknown_not_a_pass(tmp_path):
    """CLAUDE.md A7's fail-open case, as an explicit negative control."""
    empty = tmp_path / "src"
    empty.mkdir()
    sel, exc = SP.select(empty)
    assert sel == [] and exc == []          # precondition: the fixture is empty
    with pytest.raises(AssertionError) as e:
        SP.require_nonzero(sel, "control")
    assert "EMPTY" in str(e.value)
    (empty / "a.tsx").write_text("x", encoding="utf-8")
    sel, exc = SP.select(empty)
    assert sel == ["a.tsx"], sel            # precondition: the fixture is not
    assert SP.require_nonzero(sel, "control") == 1
    with pytest.raises(AssertionError) as e:
        SP.require_both_halves(sel, exc, "control")
    assert "EXCLUDED" in str(e.value)
    (empty / "a.test.tsx").write_text("x", encoding="utf-8")
    sel, exc = SP.select(empty)
    assert SP.require_both_halves(sel, exc, "control") == (1, 1)


def test_an_absent_tree_does_not_raise_but_reports_nothing(tmp_path):
    """The tools half keeps _spa_wiring's behaviour-preserving contract: an
    absent tree is an empty result, and the FAIL-CLOSED decision is the
    caller's separate require_nonzero call. Both halves proven."""
    missing = tmp_path / "nope"
    assert not missing.exists()
    assert SP.select(missing) == ([], [])
    assert SP.product_files(missing) == []
    assert SP.product_text(missing) == ""


# ── 3. per-site behavioural controls ────────────────────────────────────
#
# THE TWO ARMS PLANT THE IDENTICAL BYTES AND DIFFER ONLY IN THE FILENAME.
# That is the whole claim: a narrowing that reported the same verdict for both
# would have disarmed the gate, and one that reported neither would have left
# the defect in place.

_ACTION_FETCH = (
    'await fetch("/api/needs_review/bulk_approve", {\n'
    '  method: "POST",\n  body: JSON.stringify({ids: [1]}),\n});\n')
_DEAD_HASH = '<a href="#/dom-analyzer">open</a>\n'
#: Carries neither the payload nor the offender, so BOTH arms keep a nonzero
#: product half AND a nonzero excluded half. Without it the positive arm would
#: trip require_both_halves and the control would fail for the wrong reason.
_INERT = "export const inert = 1;\n"

# (module stem, test function, {module attr -> what to point at},
#  planted-text, "existence"|"offender")
_SITE_CONTROLS = [
    ("test_v3_66_310_site_editor_parity", "test_spa_site_editor_wires_endpoints",
     ("_REPO",), None, "existence"),
    ("test_v3_66_312_parity_abc", "test_b_spa_wires_widgets_store",
     ("_REPO",), None, "existence"),
    ("test_v3_66_312_parity_abc", "test_c_spa_read_panel_wires_env_effective",
     ("_REPO",), None, "existence"),
    ("test_v3_66_313_challenge_parity", "test_spa_challenge_controls_present",
     ("_REPO",), None, "existence"),
    ("test_v3_66_314_challenge_wait_guard", "test_spa_challenge_wait_control_present",
     ("_REPO",), None, "existence"),
    ("test_v3_66_315_advanced_env_tranche", "test_spa_controls_present",
     ("_REPO",), None, "existence"),
    ("test_v3_66_316_bucket3_guard", "test_spa_controls_present",
     ("_REPO",), None, "existence"),
    ("test_v3_66_507_bucket3b_store_raw", "test_spa_wires_store_raw",
     ("_REPO",), None, "existence"),
    ("test_f2_3_needs_review_wired", "test_no_raw_mutating_fetch_to_action_paths",
     ("REPO", "SPA"), _ACTION_FETCH, "offender"),
    ("test_v3_66_292_dom_analyzer_link", "test_no_dead_hash_path_hrefs",
     ("REPO", "SPA_SRC"), _DEAD_HASH, "offender"),
]


def _load_site(stem):
    path = TESTS / (stem + ".py")
    assert path.is_file(), "site module vanished: %s" % path
    spec = importlib.util.spec_from_file_location("_row232_" + stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _plant(root: Path, files: dict[str, str]) -> None:
    src = root / "frontend" / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    # PRECONDITION, asserted rather than assumed: the fixture built the shape.
    written = sorted(q.relative_to(src).as_posix()
                     for q in src.rglob("*.ts*") if q.is_file())
    assert written == sorted(files), (
        "the planted tree is not what this control claims to have built: %r"
        % (written,))


def _point(monkeypatch, mod, attrs, root: Path) -> None:
    for attr in attrs:
        assert hasattr(mod, attr), (
            "%s no longer defines %s; this control can no longer aim the "
            "site at a planted tree" % (mod.__name__, attr))
        monkeypatch.setattr(mod, attr,
                            root / "frontend" / "src" if attr in ("SPA", "SPA_SRC")
                            else root)


@pytest.mark.parametrize("stem,fn,attrs,planted,kind", _SITE_CONTROLS,
                         # NOT "::" as the separator. A param id containing "::"
                         # makes the resulting nodeid unparseable for
                         # toolchain/bin/bd-mutate, which maps a JUnit case back
                         # to a collected nodeid by splitting on "::" -- measured:
                         # the battery reported "maps to 0 collected nodeids" and
                         # graded its own baseline RED over a green run.
                         ids=[f"{s}--{f}" for s, f, _a, _p, _k in _SITE_CONTROLS])
def test_a_spec_cannot_decide_a_product_question(monkeypatch, tmp_path,
                                                 stem, fn, attrs, planted, kind):
    mod = _load_site(stem)
    target = getattr(mod, fn, None)
    assert callable(target), "%s::%s is gone" % (stem, fn)

    if kind == "existence":
        payload = SP.product_text(SPA_SRC)
        assert len(payload) > 10000, (
            "the real product text is only %d bytes; this control would be "
            "planting nothing" % len(payload))
        good = {"routes/Real.tsx": payload, "routes/Decoy.spec.tsx": _INERT}
        bad = {"routes/Real.spec.tsx": payload, "routes/Stub.tsx": _INERT}
        good_raises, bad_raises = False, True
    else:
        good = {"routes/Bad.tsx": planted, "routes/Decoy.spec.tsx": _INERT}
        bad = {"routes/Bad.spec.tsx": planted, "routes/Stub.tsx": _INERT}
        good_raises, bad_raises = True, False

    # POSITIVE ARM -- the narrowing must not have disarmed the site.
    root_a = tmp_path / "a"
    _plant(root_a, good)
    _point(monkeypatch, mod, attrs, root_a)
    if good_raises:
        with pytest.raises(AssertionError):
            target()
    else:
        target()

    # LAUNDERING ARM -- identical bytes, spec filename.
    root_b = tmp_path / "b"
    _plant(root_b, bad)
    _point(monkeypatch, mod, attrs, root_b)
    if bad_raises:
        with pytest.raises(AssertionError):
            target()
    else:
        target()


def test_every_product_only_test_site_has_a_control():
    """The control table is a denominator too. A product-only site with no
    behavioural arm is narrowed on structural evidence alone."""
    covered = {("tests/%s.py" % stem, fn) for stem, fn, _a, _p, _k in _SITE_CONTROLS}
    covered |= set(_CONTROL_PROVIDED_BY_SIBLING)
    missing = sorted(
        key for key, (kind, _r) in _CLASSIFIED.items()
        if kind == PRODUCT_ONLY and key[0].startswith("tests/")
        and key not in covered
        and not key[1].startswith("_"))
    assert not missing, (
        "product-only test site(s) with no planted-offender control: %r"
        % (missing,))


def test_the_all_source_site_fails_closed_on_a_missing_tree(monkeypatch, tmp_path):
    """test_no_raw_unicode_escape_in_jsx stays REPO-WIDE by classification, but
    its ``if not _SRC.is_dir(): return`` was a silent pass -- the fail-open A7
    names. Both directions proven: absent tree raises, present tree does not."""
    mod = _load_site("test_no_raw_unicode_escape_in_jsx")
    missing = tmp_path / "gone" / "src"
    assert not missing.exists()
    monkeypatch.setattr(mod, "_SRC", missing)
    with pytest.raises(AssertionError):
        mod.test_no_raw_unicode_escape_in_jsx_text()

    live = tmp_path / "live" / "src"
    (live / "routes").mkdir(parents=True)
    (live / "routes" / "Ok.tsx").write_text(
        'export const A = () => <p>{"\\u2026"}</p>;\n', encoding="utf-8")
    assert list(live.rglob("*.tsx"))          # precondition
    monkeypatch.setattr(mod, "_SRC", live)
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path / "live")
    mod.test_no_raw_unicode_escape_in_jsx_text()

    # NEGATIVE CONTROL: it still catches what it forbids.
    (live / "routes" / "Bad.tsx").write_text(
        "export const B = () => <p>Loading\\u2026</p>;\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        mod.test_no_raw_unicode_escape_in_jsx_text()
