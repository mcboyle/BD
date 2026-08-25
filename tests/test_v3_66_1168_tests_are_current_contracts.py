"""Cut 8: live tests state current contracts and skip evidence is exact."""

import importlib
import os
import subprocess
import sys
from pathlib import Path

from tools import check_skip_baseline as SB

ROOT = Path(__file__).resolve().parents[1]
BD_GATE_SCOPE = "repo-wide"

RETIRED = {
    "docs/LEGACY_MIGRATION_PLAN.md",
    "reports/legacy_parity_baseline.json",
    "tools/legacy_parity.py",
    "tools/legacy_pin_scan.py",
    "tests/test_legacy_parity.py",
    "tests/test_legacy_pin_scan.py",
    "tests/test_p01_csrf_bootstrap.py",
    "tests/test_phase1_root_flip.py",
    "tests/test_phase4_retired.py",
    "tests/test_p4_cockpit_home.py",
    "tests/test_cut35_csrf_meta_contract_retired.py",
    "tests/test_cut35_csrf_meta_premise_retired_in_tools.py",
    "tests/SKIP_BASELINE.txt",
}

DIRECT = {
    "tests/test_t1_dashboard_wired.py",
    "tests/test_t2_history_wired.py",
    "tests/test_t3_t4_wired.py",
    "tests/test_t5_t6_wired.py",
    "tests/test_t7_notifications_wired.py",
    "tests/test_t8_cluster_wired.py",
    "tests/test_t9a_live_stream_wired.py",
    "tests/test_t9b_push_wired.py",
    "tests/test_t10_devtools_wired.py",
    "tests/test_t11_approval_wired.py",
    "tests/test_csrf_session_bootstrap.py",
    "tests/test_csrf_contract_reachability.py",
    "tests/test_csrf_tool_contracts.py",
    "tests/test_spa_root_routing_contract.py",
    "tests/test_cockpit_route_contract.py",
    "tests/test_cockpit_navigation_contract.py",
    "tests/test_skip_baseline.py",
}


def _tracked() -> set[str]:
    run = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    paths = {p.decode() for p in run.stdout.split(b"\0") if p}
    assert len(paths) > 1000
    return paths


def test_historical_ratchets_and_old_contract_names_are_physically_absent():
    tracked = _tracked()
    bad = sorted(path for path in RETIRED
                 if path in tracked or os.path.lexists(ROOT / path))
    assert not bad, f"retired test authority returned: {bad}"


def _suites_in(text: str) -> set[str]:
    """Suites CI will ACTUALLY RUN, read line-aware from the workflow.

    WHY NOT `workflow.count(path)`. That counted a COMMENTED mention. Measured
    2026-08-24: turning
        `              tests/test_t1_dashboard_wired.py`
    into
        `              # tests/test_t1_dashboard_wired.py`
    -- two characters -- de-wires a required live-contract test from CI while
    this gate, whose entire job is proving it IS wired, stays green. CLAUDE.md
    A5's "a gate CI does not run does not exist", defeated by the gate written
    to prevent it.

    WHY NOT yaml.safe_load EITHER, which was this fix's first draft. `suites`
    is a FOLDED scalar (`>-`), and inside a folded scalar `#` is ordinary text,
    not a comment -- so the loader returns the path plus a stray `#` token and
    the evasion survives structural parsing. The evasion fixture below caught
    that draft, which is precisely why the fixture ships with the fix.

    So: find each `suites:` block, take its indented continuation lines, and
    drop what a shell/YAML reader would treat as commented on EACH LINE before
    tokenising. Line structure is the thing that matters here, and folding
    destroys it -- so it is read before the fold.
    """
    lines = text.splitlines()
    suites: set[str] = set()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("suites:"):
            base = len(lines[i]) - len(lines[i].lstrip())
            j = i + 1
            while j < len(lines):
                raw = lines[j]
                if not raw.strip():
                    j += 1
                    continue
                if (len(raw) - len(raw.lstrip())) <= base:
                    break
                live = raw.split("#", 1)[0]
                suites.update(tok for tok in live.split() if tok.endswith(".py"))
                j += 1
            i = j
            continue
        i += 1
    return suites


def _ci_wired_suites() -> set[str]:
    return _suites_in((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_current_behavior_contracts_are_tracked_and_directly_ci_wired():
    tracked = _tracked()
    assert DIRECT <= tracked
    wired = _ci_wired_suites()
    # PRECONDITION: the parser must have found a real workflow, or "nothing is
    # missing" would be vacuously true over an empty set.
    assert len(wired) > 50, (
        f"structural CI parse produced only {len(wired)} suites; the workflow "
        "shape changed and this gate is judging an empty denominator")
    missing = sorted(DIRECT - wired)
    assert not missing, f"current contract not wired in CI: {missing}"


def test_a_commented_out_ci_wiring_line_does_not_count_as_wired():
    """EVASION FIXTURE for the two-character de-wiring.

    The original textual gate passed on this input, and so did the first
    structural draft of the fix. It ships so that any future edit which
    reintroduces either shape goes RED here."""
    source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    victim = sorted(DIRECT)[0]
    assert victim in _suites_in(source), (
        f"{victim} is not wired to begin with; this fixture has no subject")
    evaded = source.replace("              " + victim,
                            "              # " + victim, 1)
    assert evaded != source, "could not build the evasion fixture"
    assert evaded.count(victim) >= 1, (
        "the commented form no longer contains the path, so this fixture is "
        "not reproducing the evasion it exists to pin")
    assert victim not in _suites_in(evaded), (
        f"a commented-out CI wiring line still reads as wired for {victim}")


def test_skip_baseline_is_exact_identity_reason_data_not_a_count():
    path = ROOT / "tests/SKIP_BASELINE.json"
    ordinary, collection = SB._read_baseline(path)

    assert (len(ordinary), len(collection)) == (39, 0)
    assert len(ordinary | collection) == 39
    assert set(ordinary).isdisjoint(collection)
    # The namespace rule stays -- it is the standing invariant for any future
    # row -- but an all() over an EMPTY map proves nothing, so the emptiness is
    # asserted by name as well (CLAUDE.md A7). v3.66.1229 (backlog row 215)
    # emptied this namespace: the two SSRF modules that occupied it now execute
    # without the optional `requests` distribution, so leaving a policy row in
    # place would make the whole-file skip legal again.
    assert all(identity.startswith("<collection>::") for identity in collection)
    assert not collection, (
        "a collection-skip policy row is back -- a whole-file skip is exactly "
        f"the shape row 215 removed: {sorted(collection)}")


def test_config_parity_parking_is_visible_as_skip_not_pass():
    """The parked ratchet must REPORT as skipped, observed by running it.

    WHY NOT A SOURCE SCAN. The old form asserted
    `source.count("pytest.skip(") == 2` and banned one exact comment spelling,
    `"return  # ratchet parked"`. Measured 2026-08-24: inserting
    `return  # parked by operator` after each docstring leaves both
    `pytest.skip(` occurrences in the file and uses a DIFFERENT comment, so the
    scan stays green while both parked tests launder from SKIP into PASS --
    defeating the exact property the gate is named for. Banning one spelling
    bans one spelling.

    So the outcome is OBSERVED. pytest is asked what these tests actually
    report, and a parked test that returns early reports `passed`, not
    `skipped`, no matter how it is written."""
    import json
    import tempfile
    target = ROOT / "tests/test_config_parity_ratchet.py"
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "r.json"
        subprocess.run(
            [sys.executable, "-m", "pytest", str(target), "-p", "no:randomly",
             "-q", "--timeout=120", "--json-report" if False else "-rA",
             "--tb=no", f"--junitxml={report.with_suffix('.xml')}"],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "BD_DISABLE_KEEPALIVE": "1", "LC_ALL": "C"},
            timeout=300)
        import xml.etree.ElementTree as ET
        root = ET.parse(report.with_suffix(".xml")).getroot()
        suite = root.find("testsuite") if root.tag == "testsuites" else root
        cases = list(suite.iter("testcase"))

    # PRECONDITION: a zero-length or unparsed run would make every claim below
    # vacuously true.
    assert len(cases) >= 2, f"parked ratchet produced {len(cases)} cases"
    skipped = {c.get("name") for c in cases if c.find("skipped") is not None}
    assert len(skipped) == 2, (
        "the parked ratchet tests no longer REPORT as skipped -- they may have "
        f"laundered into passes: skipped={sorted(skipped)} of "
        f"{sorted(c.get('name') for c in cases)}")
    failed = [c.get("name") for c in cases
              if c.find("failure") is not None or c.find("error") is not None]
    assert not failed, failed


def test_a_parked_test_that_returns_early_is_not_mistaken_for_skipped():
    """EVASION FIXTURE. The old scan passed on this input.

    Proves the ban-one-spelling shape is genuinely defeated, so nobody
    reintroduces it: the evaded source still contains both `pytest.skip(`
    occurrences and does not contain the one banned comment."""
    source = (ROOT / "tests/test_config_parity_ratchet.py").read_text(
        encoding="utf-8")
    evaded = source.replace('"""\n', '"""\n    return  # parked by operator\n', 2)
    assert evaded.count("pytest.skip(") == source.count("pytest.skip(") == 2, (
        "the evasion changed the skip count, so it is not reproducing the "
        "shape that defeated the old gate")
    assert "return  # ratchet parked" not in evaded, (
        "the evasion used the one banned spelling; it must use a different one")
    assert "return  # parked by operator" in evaded


# ── Row 215: an SSRF guard must not vanish with an optional distribution ─────
#
# `requests` is declared in NO requirements manifest -- it arrives only
# transitively through requirements-cloak.txt, whose install step is NON-FATAL
# by design -- so a box without it is a SUPPORTED posture, not a broken one.
# Until v3.66.1229 the two subscription/weather SSRF modules answered that
# posture with a module-level `pytest.importorskip("requests")`, so on exactly
# the install where nobody is watching, every SSRF guard they exist to prove
# reported nothing at all. A whole-file collection skip is the most complete
# form of CLAUDE.md A7's central failure: a gate that cannot see its subject.
#
# The product seams are unchanged -- site_weather.probe_http and
# webhooks._deliver_one still soft-import `requests` and still return
# {"ok": False, "error": "requests not installed"} without it. What changed is
# that the TESTS now inject the minimal API those seams use, so the guard's own
# logic (URL parsing, address classification, per-hop redirect policy,
# allow/deny) is proved with no HTTP distribution present.
#
# WHY A CHILD PROCESS. The posture is a property of the interpreter's import
# system, and the parent here may well have `requests` installed. Only a child
# whose meta_path refuses the name can answer the question, and it must prove
# the refusal IS EFFECTIVE in the same process that ran the tests -- an
# installed-but-inert blocker would make every claim below vacuous.
_SSRF_FILES = (
    "tests/test_v3_66_550_weather_ssrf.py",
    "tests/test_webhooks_subscription_ssrf.py",
)

# INDEPENDENT DENOMINATOR. Pinned here rather than read back from the files
# under test, so that deleting a guard fails this gate instead of quietly
# shrinking its own expectation (CLAUDE.md A7). Kept a REQUIRED SUBSET, not an
# equality: adding a guard must not be a failure, and the zero-skip plus
# collected==executed assertions below close the drift the subset leaves open.
_SSRF_REQUIRED_NODEIDS = frozenset({
    "tests/test_v3_66_550_weather_ssrf.py::test_probe_http_blocks_loopback",
    "tests/test_v3_66_550_weather_ssrf.py::test_probe_http_blocks_cgnat",
    "tests/test_v3_66_550_weather_ssrf.py::test_probe_http_blocks_link_local_metadata",
    "tests/test_v3_66_550_weather_ssrf.py::test_probe_http_blocks_redirect_to_private",
    "tests/test_v3_66_550_weather_ssrf.py::test_probe_http_allows_public_reaches_fetch",
    "tests/test_v3_66_550_weather_ssrf.py::test_probe_http_reports_supported_missing_requests_posture",
    "tests/test_v3_66_550_weather_ssrf.py::test_requests_seam_restores_sys_modules_in_either_order",
    "tests/test_webhooks_subscription_ssrf.py::test_add_subscription_rejects_ssrf_hosts",
    "tests/test_webhooks_subscription_ssrf.py::test_add_subscription_allows_lan_and_public",
    "tests/test_webhooks_subscription_ssrf.py::test_deliver_blocks_ssrf_even_if_stored",
    "tests/test_webhooks_subscription_ssrf.py::test_deliver_allows_lan_receiver",
    "tests/test_webhooks_subscription_ssrf.py::test_deliver_allows_public_receiver",
    "tests/test_webhooks_subscription_ssrf.py::test_deliver_reports_supported_missing_requests_posture",
    "tests/test_webhooks_subscription_ssrf.py::test_requests_seam_restores_sys_modules_in_either_order",
})

_SSRF_COLLECTION_IDENTITIES = (
    "<collection>::tests.test_v3_66_550_weather_ssrf",
    "<collection>::tests.test_webhooks_subscription_ssrf",
)

# Loaded with `-p`, so it runs before conftest collection. It writes its own
# verdict to a marker file: the gate reads that marker rather than trusting
# that installing a finder made the name unimportable.
_POSTURE_PLUGIN = '''"""Reproduce (or merely record) the optional-`requests` posture."""
import importlib
import os
import sys

_BLOCK = os.environ.get("BD_REQUESTS_BLOCK") == "1"
_NAME = "requests"


class _Refuse:
    """A meta_path finder that refuses one distribution by name."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == _NAME or fullname.startswith(_NAME + "."):
            raise ModuleNotFoundError(
                "No module named %r" % fullname, name=fullname)
        return None


if _BLOCK:
    for _loaded in [m for m in list(sys.modules)
                    if m == _NAME or m.startswith(_NAME + ".")]:
        del sys.modules[_loaded]
    sys.meta_path.insert(0, _Refuse())

try:
    importlib.import_module(_NAME)
except ModuleNotFoundError as _exc:
    _VERDICT = "ABSENT"
else:
    _VERDICT = "PRESENT"

with open(os.environ["BD_REQUESTS_MARKER"], "w", encoding="utf-8") as _fh:
    _fh.write(_VERDICT)
'''


def _run_ssrf_lane(tmp_path, *, block):
    """Run both SSRF modules in a child interpreter and return
    (posture, nodeid -> outcome).  `posture` is what the CHILD measured."""
    import xml.etree.ElementTree as ET

    lane = tmp_path / ("blocked" if block else "ambient")
    (lane / "plug").mkdir(parents=True)
    (lane / "plug" / "bd_requests_posture.py").write_text(
        _POSTURE_PLUGIN, encoding="utf-8")
    marker = lane / "posture.txt"
    report = lane / "report.xml"
    home = lane / "home"
    home.mkdir()

    env = {k: v for k, v in os.environ.items() if k != "BD_INSTALL_DIR"}
    env.update({
        "BD_DISABLE_KEEPALIVE": "1",
        "BD_HOME": str(home),
        "BD_REQUESTS_MARKER": str(marker),
        "BD_REQUESTS_BLOCK": "1" if block else "0",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (str(lane / "plug") + os.pathsep + inherited
                         if inherited else str(lane / "plug"))

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *(_SSRF_FILES), "-p", "no:randomly",
         "-p", "bd_requests_posture", "-q", "--timeout=120", "--tb=short",
         f"--junitxml={report}", f"--basetemp={lane / 'pt'}"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=600)

    # PRECONDITION: the child says what it could import. Without this the whole
    # lane could be measuring the wrong interpreter posture.
    assert marker.exists(), (
        "the posture plugin never ran, so nothing below is about the "
        f"requests-less posture. rc={proc.returncode}\n{proc.stdout[-3000:]}")
    posture = marker.read_text(encoding="utf-8").strip()
    assert posture in {"ABSENT", "PRESENT"}, posture

    assert report.exists(), (
        f"no JUnit report from the SSRF lane. rc={proc.returncode}\n"
        f"{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}")
    root = ET.parse(report).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    outcomes = {}
    for case in suite.iter("testcase"):
        # A module-level collection skip has an EMPTY classname and carries the
        # dotted module path in `name`; naming it as `<classname>.py::<name>`
        # would print a nonsense identity for the one row that matters most.
        classname, name = case.get("classname", ""), case.get("name", "")
        nodeid = (classname.replace(".", "/") + ".py::" + name
                  if classname else name)
        if case.find("skipped") is not None:
            outcomes[nodeid] = "skipped"
        elif case.find("failure") is not None:
            outcomes[nodeid] = "failed"
        elif case.find("error") is not None:
            outcomes[nodeid] = "error"
        else:
            outcomes[nodeid] = "passed"
    return posture, outcomes


def _parent_can_import_requests():
    """Measure THIS interpreter the same way the child plugin measures its own.

    NOT importlib.util.find_spec: a meta_path finder that refuses a name by
    RAISING -- which is how this posture is reproduced here, and how a capture
    box that hides the package behaves -- makes find_spec propagate
    ModuleNotFoundError instead of returning None, so the probe would FAIL
    rather than answer. MEASURED at v3.66.1229: the first draft of the control
    below did exactly that, erroring on the requests-less posture it exists to
    describe. Asking for the import is the question actually being asked."""
    try:
        importlib.import_module("requests")
    except ImportError:
        return False
    return True


def test_the_ssrf_guards_execute_with_requests_unimportable(tmp_path):
    """The row-215 subject, measured rather than read.

    RED before v3.66.1229: both modules carried a module-level
    `pytest.importorskip("requests")`, so this lane collected TWO module skips
    and executed ZERO guards -- an SSRF check reporting OK over a denominator
    that structurally excluded every one of its assertions."""
    posture, outcomes = _run_ssrf_lane(tmp_path, block=True)

    # PRECONDITION, from the child that ran the tests: the name really is gone.
    assert posture == "ABSENT", (
        "the blocker did not make `requests` unimportable in the child, so "
        "this lane is not the supported requests-less posture")

    skipped = sorted(n for n, o in outcomes.items() if o == "skipped")
    assert not skipped, (
        "SSRF guards skipped on the requests-less posture -- the exact "
        f"deferred-coverage shape row 215 exists to remove: {skipped}")

    executed = sorted(outcomes)
    # NONZERO DENOMINATOR, asserted as a count so an empty lane cannot pass.
    assert len(executed) >= len(_SSRF_REQUIRED_NODEIDS), (
        f"only {len(executed)} SSRF nodeids executed without `requests`; "
        f"expected at least {len(_SSRF_REQUIRED_NODEIDS)}: {executed}")
    missing = sorted(_SSRF_REQUIRED_NODEIDS - set(executed))
    assert not missing, f"required SSRF guards did not run: {missing}"
    bad = sorted(n for n, o in outcomes.items() if o != "passed")
    assert not bad, f"SSRF guards did not pass without `requests`: {bad}"


def test_the_same_ssrf_guards_execute_when_requests_is_installed(tmp_path):
    """OVER-SENSITIVITY CONTROL. Injecting a fake must not cost the ambient
    posture: with whatever the host actually has, the same guards run and pass,
    and none of them silently skips.

    The two arms coincide on a host that genuinely lacks `requests`; that is a
    true statement about such a host, not a hidden skip, and the postures are
    asserted against each other so the coincidence is visible."""
    ambient, outcomes = _run_ssrf_lane(tmp_path, block=False)
    blocked_posture = "ABSENT"

    parent_has = _parent_can_import_requests()
    assert ambient == ("PRESENT" if parent_has else "ABSENT"), (
        f"child measured requests={ambient} while the parent measured "
        f"{'PRESENT' if parent_has else 'ABSENT'}; the lanes do not share an "
        "import posture and the control proves nothing")
    if ambient == blocked_posture:
        # Say so rather than imply a comparison that was not made.
        assert not parent_has, "posture bookkeeping is inconsistent"

    skipped = sorted(n for n, o in outcomes.items() if o == "skipped")
    assert not skipped, (
        f"SSRF guards skipped on the ambient ({ambient}) posture: {skipped}")
    missing = sorted(_SSRF_REQUIRED_NODEIDS - set(outcomes))
    assert not missing, f"required SSRF guards did not run: {missing}"
    bad = sorted(n for n, o in outcomes.items() if o != "passed")
    assert not bad, f"SSRF guards did not pass with requests={ambient}: {bad}"


def test_neither_ssrf_module_is_allowlisted_as_a_collection_skip():
    """The policy exemption must be GONE, not merely unused.

    Row 215's acceptance is two-sided: the guards execute, AND the baseline
    stops permitting them not to. While the rows stand, a reintroduced
    module-level skip is silently legal again."""
    ordinary, collection = SB._read_baseline(ROOT / "tests/SKIP_BASELINE.json")
    assert ordinary, "skip baseline parsed no ordinary rows -- nothing examined"
    still_permitted = [i for i in _SSRF_COLLECTION_IDENTITIES if i in collection]
    assert not still_permitted, (
        "requests-less SSRF guards are still allowlisted at collection: "
        f"{still_permitted}")
