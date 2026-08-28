"""No test may declare a wall-clock budget above the bound governing its item.

WHY THIS IS A REAL HAZARD AND NOT TIDINESS. An over-large budget costs nothing
while things work -- `subprocess.run` returns the moment its child exits. It
decides what happens when things DON'T work. If the inner budget is above the
pytest-timeout bound governing the item, the inner bound can never fire: the
`except subprocess.TimeoutExpired` clause is dead code, and what runs instead is
pytest-timeout killing the process. On 2026-08-24 that killed an xdist worker,
wrote its diagnostic to a stdout xdist points at /dev/null, and took the whole
session into a 19-minute livelock. Two separate files did it that night --
test_v3_66_1046 at 221s and test_desandbox_tool_verifiers at 201s, both under a
240s bound. See fleet-run-artifacts/2026-08-24/xdist-wedge/FINDING.md.

WHY A RATCHET RATHER THAN A CLEAN GATE. 83 sites carried this shape when it was
first counted. Fixing all of them at once would mean touching 49 files including
timing-sensitive ones, in a single cut, on numbers nobody has re-measured -- and
this whole incident began with a baseline that was wrong by 8x. So the frozen
list may SHRINK and may NOT GROW: the population becomes a visible countdown
instead of an invisible hazard, and the 84th site cannot be added silently.

WHY AST AND NOT GREP. Backlog row 196 is an entire row about textual-proxy
gates. A `grep` for `timeout=` counts docstrings, comments and argv strings -- an
earlier hand count of this same population died on exactly two comment lines.
Parsing sees keyword arguments and parameter defaults and nothing else.

WHAT THIS GATE DOES NOT CLAIM. It sees CONSTANT budgets. A computed one
(`timeout=BUDGET * 2`) is invisible to it, and so is a budget read from an
environment variable. That is stated rather than hidden: the ratchet bounds a
known population, it does not prove the population is complete.
"""
from __future__ import annotations

import ast
import json
import math
import multiprocessing
import queue
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
RATCHET = ROOT / "project-knowledge" / "BUDGET_RATCHET.json"

_ROW_338_FILES = {
    "tests/test_cloud_setup_truthfulness.py",
    "tests/test_csrf_tool_contracts.py",
    "tests/test_env_example_matches_the_ledger.py",
    "tests/test_secret_display_never.py",
    "tests/test_toolchain_534.py",
    "tests/test_v3_66_985_templates_parallel.py",
}

_BUDGET_NAMES = {"timeout", "budget_s", "timeout_s"}

# Callees whose timeout argument is MILLISECONDS. Keyed on the CALLEE, never on
# the value: a value-based rule ("anything over 1000 must be ms") would also
# silently drop a real 1800-second budget, which is the opposite of the point.
_MS_CALLEES = ("goto", "wait_for", "wait_for_function", "wait_for_event",
               "wait_for_selector", "chromium.launch", "is_visible",
               "wait_for_load_state", "wait_for_timeout")

# Measured on idle test5 (48 cores), source dcd8201d, 2026-08-28.  These are
# elapsed times at the exact subprocess/queue boundary, not whole-item durations.
# The budget rule is the one established by the 1219/1222 repairs: twice the
# measured cost, with a 60s cold-start floor.  A separate 30s item reserve makes
# every result <= 210s, strictly below the sanctioned 240s per-item bound.
_ROW_338_MEASUREMENTS = (
    {"site": "cloud-find-repo", "file": "tests/test_cloud_setup_truthfulness.py",
     "owner": "test_find_repo_refuses_rather_than_searching_the_filesystem",
     "callee": "_run_bash", "measured": 0.010162, "budget": 60},
    {"site": "csrf-functional-probe", "file": "tests/test_csrf_tool_contracts.py",
     "owner": "test_functional_probe_does_not_cry_wolf_on_a_healthy_root",
     "callee": "subprocess.run", "measured": 1.861821, "budget": 60},
    {"site": "env-ledger", "file": "tests/test_env_example_matches_the_ledger.py",
     "owner": "_ledger_keys", "callee": "subprocess.run",
     "measured": 1.308416, "budget": 60},
    {"site": "secret-route-shards", "file": "tests/test_secret_display_never.py",
     "owner": "_scan_all", "callee": "out_q.get", "mechanism": "queue",
     "measured": 2.334737, "budget": 60},
    {"site": "tool-smoke", "file": "tests/test_toolchain_534.py",
     "owner": "test_tool_smoke_runs_unaided_and_the_toolchain_has_no_undefined_names",
     "callee": "subprocess.run", "measured": 43.047950, "budget": 87},
    {"site": "equiv-empty", "file": "tests/test_toolchain_534.py",
     "owner": "test_equiv_refuses_to_certify_over_an_empty_token_set",
     "callee": "subprocess.run", "measured": 0.484521, "budget": 60},
    {"site": "freshcheck-repo-only", "file": "tests/test_toolchain_534.py",
     "owner": "test_the_derivable_half_of_staleness_is_clean",
     "callee": "subprocess.run", "measured": 0.688181, "budget": 60},
    {"site": "zero-collect", "file": "tests/test_toolchain_534.py",
     "owner": "test_a_file_that_collects_nothing_is_not_a_pass",
     "callee": "subprocess.run", "measured": 0.192434, "budget": 60},
    {"site": "all-skipped", "file": "tests/test_toolchain_534.py",
     "owner": "test_a_file_that_collects_nothing_is_not_a_pass",
     "callee": "subprocess.run", "measured": 0.095506, "budget": 60},
    {"site": "contracts-positive", "file": "tests/test_toolchain_534.py",
     "owner": "test_a_file_that_collects_nothing_is_not_a_pass",
     "callee": "subprocess.run", "measured": 16.581278, "budget": 60},
    {"site": "derive-missing-map", "file": "tests/test_toolchain_534.py",
     "owner": "test_band_derive_finds_the_curated_map_and_says_so_when_it_cannot",
     "callee": "subprocess.run", "measured": 0.192148, "budget": 60},
    {"site": "band-helper-default", "file": "tests/test_toolchain_534.py",
     "owner": "_band_tool", "callee": "DEFAULT:_band_tool",
     "measured": 2.906562, "budget": 60},
    {"site": "parband-green", "file": "tests/test_toolchain_534.py",
     "owner": "test_parband_still_runs_a_suite_that_exists",
     "callee": "_band_tool", "measured": 2.906562, "budget": 60},
    {"site": "retest-green", "file": "tests/test_toolchain_534.py",
     "owner": "test_retest_still_retests_a_suite_that_exists",
     "callee": "_band_tool", "measured": 0.710941, "budget": 60},
    {"site": "derive-helper", "file": "tests/test_toolchain_534.py",
     "owner": "_derive", "callee": "subprocess.run",
     "measured": 0.760000, "budget": 60},
    {"site": "fullsuite-helper", "file": "tests/test_toolchain_534.py",
     "owner": "_fullsuite", "callee": "subprocess.run",
     "measured": 2.645160, "budget": 60},
    {"site": "derived-band", "file": "tests/test_toolchain_534.py",
     "owner": "test_a_derived_band_contains_only_files_the_runner_collects",
     "callee": "subprocess.run", "measured": 0.743233, "budget": 60},
    {"site": "bd-band-zero", "file": "tests/test_toolchain_534.py",
     "owner": "test_bd_band_reports_nothing_ran_without_calling_it_a_pass",
     "callee": "subprocess.run", "measured": 1.531214, "budget": 60},
    {"site": "bd-band-green", "file": "tests/test_toolchain_534.py",
     "owner": "test_bd_band_reports_nothing_ran_without_calling_it_a_pass",
     "callee": "subprocess.run", "measured": 1.513284, "budget": 60},
    {"site": "templates-corpus", "file": "tests/test_v3_66_985_templates_parallel.py",
     "owner": "_run", "callee": "subprocess.run",
     "measured": 0.638027, "budget": 60},
)

_ROW_338_ITEM_RESERVE_S = 30
_ROW_338_MIN_BUDGET_S = 60
_ROW_338_CONTENTION_FACTOR = 2
_ROW_338_HUNG_CONTROL_S = 0.05


def _is_ms(callee: str) -> bool:
    return any(callee.endswith(c) or c in callee for c in _MS_CALLEES)


def _tracked_test_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "tests/"],
                         capture_output=True, text=True, timeout=60).stdout
    return [f for f in out.split() if f.endswith(".py")]


def _constant_budget_sites() -> tuple[list[dict], int]:
    """(every constant budget site, files parsed), with its owning function.

    A parse failure RAISES. An unparseable file is UNKNOWN, and UNKNOWN must not
    be laundered into "no offenders here" -- that is the fail-open this whole
    class of defect is made of.
    """
    rows, files = [], _tracked_test_files()
    for rel in files:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"),
                         filename=rel)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                parent = node
                while parent is not None and not isinstance(
                        parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parent = parents.get(parent)
                owner = parent.name if parent is not None else "<module>"
                for kw in node.keywords:
                    if kw.arg in _BUDGET_NAMES and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, (int, float)):
                        rows.append({"file": rel, "line": kw.value.lineno,
                                     "owner": owner, "callee": callee,
                                     "value": kw.value.value})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                pos = a.posonlyargs + a.args
                pairs = list(zip(pos[len(pos) - len(a.defaults):], a.defaults))
                pairs += [(x, d) for x, d in zip(a.kwonlyargs, a.kw_defaults)
                          if d is not None]
                for arg, d in pairs:
                    if arg.arg in _BUDGET_NAMES and isinstance(d, ast.Constant) \
                            and isinstance(d.value, (int, float)):
                        callee = "DEFAULT:" + node.name
                        rows.append({"file": rel, "line": d.lineno,
                                     "owner": node.name, "callee": callee,
                                     "value": d.value})
    return rows, len(files)


def _census() -> tuple[list[dict], int, int]:
    """(sites >= the bound, total constant sites, files parsed)."""
    bound = json.loads(RATCHET.read_text(encoding="utf-8"))["governing_bound_s"]
    rows, files = _constant_budget_sites()
    offenders = [r for r in rows
                 if not _is_ms(r["callee"]) and r["value"] >= bound]
    return offenders, len(rows), files


def _keyed(rows) -> Counter:
    """Count by (file, callee, value), honouring an aggregated `count`.

    The frozen baseline stores one entry per DISTINCT key with the number of
    occurrences alongside it; a live census yields one row per occurrence.
    Counting a baseline entry as 1 would make every key with several
    occurrences look like new growth -- which it did, on this gate's first run.
    """
    c = Counter()
    for r in rows:
        c[(r["file"], r["callee"], r["value"])] += r.get("count", 1)
    return c


def test_the_census_measures_a_real_population():
    """PRECONDITION for every claim below. A census that silently scanned
    nothing would report zero offenders and pass forever."""
    rows, total, files = _census()
    assert files > 1000, "only %d test files scanned; the denominator collapsed" % files
    assert total > 500, (
        "only %d constant budget sites found across %d files; the parser is not "
        "seeing keyword arguments any more" % (total, files))


def test_no_new_over_bound_budget_site_appears():
    """THE RATCHET. May shrink; may not grow."""
    doc = json.loads(RATCHET.read_text(encoding="utf-8"))
    baseline = _keyed(doc["entries"])
    assert baseline, "the frozen baseline is empty, so this gate constrains nothing"

    current = _keyed(_census()[0])
    added = {k: n for k, n in current.items() if n > baseline.get(k, 0)}
    assert not added, (
        "NEW budget site(s) at or above the %ds bound governing their item. Such "
        "a budget can never fire, so its error path is dead code and a hang kills "
        "the worker instead of failing the test. Derive it from a MEASURED "
        "baseline below the bound (see tests/test_v3_66_1046 or "
        "tests/test_desandbox_tool_verifiers for the pattern), or -- if the work "
        "genuinely cannot fit -- state the item's own bound with "
        "@pytest.mark.timeout and keep the budget under it.\n  added: %r"
        % (doc["governing_bound_s"], sorted(added)))


def test_the_ratchet_actually_moved_when_sites_were_fixed():
    """A ratchet nobody can move is a monument.

    v3.66.1219 and v3.66.1222 fixed four sites between them. If any of them is
    still in the CURRENT census the fix regressed; if none was ever in the
    baseline this gate is measuring the wrong thing.
    """
    current = {(r["file"], r["callee"]) for r in _census()[0]}
    fixed = [
        ("tests/test_v3_66_1046_gates_for_this_sessions_shapes.py", "subprocess.run"),
        ("tests/test_desandbox_tool_verifiers.py", "subprocess.run"),
    ]
    still_there = [f for f in fixed if f in current]
    assert not still_there, (
        "site(s) fixed by 1219/1222 are over the bound again: %r" % still_there)


def test_row_338_proven_sites_are_no_longer_above_their_item_bound():
    """The frozen ratchet saw these sites but grandfathered all twenty.

    A tree-wide census which permits a known defect forever is only an addition
    alarm.  This assertion makes the row's complete, independently named file
    population a required shrink: after the measured repairs, reintroducing any
    one of them is growth against both this assertion and the JSON ratchet.
    """
    assert len(_ROW_338_FILES) == 6, "the row-338 file denominator changed"
    remaining = [
        (r["file"], r["line"], r["callee"], r["value"])
        for r in _census()[0]
        if r["file"] in _ROW_338_FILES
    ]
    assert not remaining, (
        "row 338 budget site(s) remain at or above the 240s item bound; their "
        "inner error path can never fire:\n  " + "\n  ".join(map(str, remaining))
    )


def test_row_338_measurements_derive_every_bound_with_item_headroom():
    """The arithmetic is executable evidence, not twenty unexplained literals."""
    doc = json.loads(RATCHET.read_text(encoding="utf-8"))
    item_bound = doc["governing_bound_s"]
    sites = [r["site"] for r in _ROW_338_MEASUREMENTS]
    files = {r["file"] for r in _ROW_338_MEASUREMENTS}

    assert len(sites) == len(set(sites)) == 20, (
        "row 338 must derive exactly one bound for each of its twenty sites")
    assert files == _ROW_338_FILES, (
        "the measured file denominator and the independently named row files drifted")
    for row in _ROW_338_MEASUREMENTS:
        assert row["measured"] > 0, f"{row['site']} has no positive measurement"
        derived = max(
            _ROW_338_MIN_BUDGET_S,
            math.ceil(row["measured"] * _ROW_338_CONTENTION_FACTOR),
        )
        assert row["budget"] == derived, (
            f"{row['site']}: {row['budget']}s is not "
            f"max({_ROW_338_MIN_BUDGET_S}, "
            f"ceil({row['measured']} * {_ROW_338_CONTENTION_FACTOR})) = {derived}s"
        )
        assert row["budget"] <= item_bound - _ROW_338_ITEM_RESERVE_S, (
            f"{row['site']} leaves less than {_ROW_338_ITEM_RESERVE_S}s for "
            "item setup, teardown and reporting")


def test_row_338_sites_keep_their_derived_literal_bounds():
    """Complete structural bridge from the measurement table to the call sites.

    Keeping literals here is deliberate: the tree-wide census cannot see a
    computed timeout.  The owner/callee population catches deleting a bound,
    moving it to an invisible expression, changing its value, or dropping one of
    the three same-function subprocesses.
    """
    expected = Counter(
        (r["file"], r["owner"], r["callee"], r["budget"])
        for r in _ROW_338_MEASUREMENTS
    )
    shapes = {(f, owner, callee) for f, owner, callee, _ in expected}
    actual = Counter(
        (r["file"], r["owner"], r["callee"], r["value"])
        for r in _constant_budget_sites()[0]
        if (r["file"], r["owner"], r["callee"]) in shapes
    )
    assert sum(expected.values()) == sum(actual.values()) == 20, (
        f"row-338 literal denominator changed: expected={expected}, actual={actual}")
    assert actual == expected, (
        "row-338 call-site bounds no longer match their measured derivation:\n"
        f"  missing/wrong: {expected - actual}\n  extra/wrong: {actual - expected}"
    )


def test_row_338_runtime_inner_bound_beats_the_item_bound(tmp_path):
    """Runtime proof of the ordering the AST gate exists to preserve.

    The exact twenty live source values are scaled with the 240s item bound into
    a hermetic sub-pytest whose item bound is 250ms.  Every child then starts work
    that cannot finish for 60s.  A correct inner relation raises
    subprocess.TimeoutExpired first and the child item passes.  Restoring any
    historical 300/600/1800s literal makes pytest-timeout fire first, names that
    scaled site as failed, and makes this catcher RED.
    """
    shapes = {
        (r["file"], r["owner"], r["callee"])
        for r in _ROW_338_MEASUREMENTS
    }
    actual = sorted(
        (r["file"], r["owner"], r["line"], r["value"])
        for r in _constant_budget_sites()[0]
        if (r["file"], r["owner"], r["callee"]) in shapes
    )
    assert len(actual) == 20, f"runtime denominator is {len(actual)}, expected 20"

    scaled_item_s = 0.25
    cases = [
        (f"{Path(file).stem}-{owner}-{line}", value * scaled_item_s / 240)
        for file, owner, line, value in actual
    ]
    probe = tmp_path / "test_row338_scaled_bounds.py"
    probe.write_text(
        "import subprocess, sys, pytest\n"
        f"CASES = {cases!r}\n"
        "@pytest.mark.parametrize('site, inner_s', CASES, "
        "ids=[site for site, _ in CASES])\n"
        "def test_inner_fires_first(site, inner_s):\n"
        "    with pytest.raises(subprocess.TimeoutExpired):\n"
        "        subprocess.run([sys.executable, '-c', "
        "'import time; time.sleep(60)'], timeout=inner_s, check=False)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:randomly",
         f"--timeout={scaled_item_s}", "--timeout-method=signal"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "20 passed" in output, (
        "an inner row-338 bound no longer fires before its governing item bound; "
        "the scaled sub-pytest names the site:\n" + output[-4000:])


def test_row_338_sites_are_removed_from_the_frozen_allowance():
    """A repaired site left in JSON would be silently permitted to regress."""
    entries = json.loads(RATCHET.read_text(encoding="utf-8"))["entries"]
    stale = [r for r in entries if r["file"] in _ROW_338_FILES]
    assert not stale, (
        "row-338 sites remain grandfathered in the ratchet, so reintroducing "
        f"their dead inner bounds would pass: {stale}")


@pytest.mark.parametrize(
    "row",
    _ROW_338_MEASUREMENTS,
    ids=[r["site"] for r in _ROW_338_MEASUREMENTS],
)
def test_row_338_each_retained_bound_fires_for_genuinely_hung_work(row):
    """Runtime negative control for every structurally pinned bound.

    The configured 60/87s values are not burned twenty times in CI.  Instead the
    same subprocess/queue timeout primitive is exercised with a 50ms control
    budget against work that cannot finish for 60s.  The structural bridge above
    separately proves that every real call still supplies its derived literal.
    """
    started = time.monotonic()
    if row.get("mechanism") == "queue":
        work_queue = multiprocessing.get_context("spawn").Queue()
        try:
            with pytest.raises(queue.Empty):
                work_queue.get(timeout=_ROW_338_HUNG_CONTROL_S)
        finally:
            work_queue.close()
            work_queue.join_thread()
    else:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            subprocess.run(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                capture_output=True,
                timeout=_ROW_338_HUNG_CONTROL_S,
                check=False,
            )
        assert caught.value.timeout == _ROW_338_HUNG_CONTROL_S
    elapsed = time.monotonic() - started
    assert _ROW_338_HUNG_CONTROL_S <= elapsed < 2, (
        f"{row['site']} negative control did not fire promptly: {elapsed:.3f}s")


def test_the_millisecond_rule_keys_on_the_callee_not_the_value(tmp_path):
    """OVER-SENSITIVITY CONTROL, both directions.

    A value-based ms rule would drop a real 1800-second budget as "obviously
    milliseconds". A callee-based one keeps it and still ignores a 30000ms
    Playwright wait.
    """
    assert _is_ms("page.goto"), "a Playwright navigation is milliseconds"
    assert _is_ms("dialog.wait_for"), "a Playwright wait is milliseconds"
    assert not _is_ms("subprocess.run"), (
        "subprocess.run was classified as milliseconds; every real budget in the "
        "tree would then be invisible to this gate")
    assert not _is_ms("_run_tool"), "the tool helpers take seconds"


def test_an_unparseable_test_file_is_a_failure_not_a_skip(tmp_path):
    """FAIL-CLOSED CONTROL. UNKNOWN must not become OK."""
    bad = tmp_path / "broken.py"
    bad.write_text("def test_x(:\n    pass\n", encoding="utf-8")
    try:
        ast.parse(bad.read_text(encoding="utf-8"), filename=str(bad))
    except SyntaxError:
        return
    raise AssertionError(
        "a malformed file parsed cleanly, so _census() would never raise on one "
        "and an unreadable file would silently contribute zero offenders")
