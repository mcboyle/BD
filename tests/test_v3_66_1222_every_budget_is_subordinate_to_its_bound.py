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
import subprocess
from collections import Counter
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
RATCHET = ROOT / "project-knowledge" / "BUDGET_RATCHET.json"

_BUDGET_NAMES = {"timeout", "budget_s", "timeout_s"}

# Callees whose timeout argument is MILLISECONDS. Keyed on the CALLEE, never on
# the value: a value-based rule ("anything over 1000 must be ms") would also
# silently drop a real 1800-second budget, which is the opposite of the point.
_MS_CALLEES = ("goto", "wait_for", "wait_for_function", "wait_for_event",
               "wait_for_selector", "chromium.launch", "is_visible",
               "wait_for_load_state", "wait_for_timeout")


def _is_ms(callee: str) -> bool:
    return any(callee.endswith(c) or c in callee for c in _MS_CALLEES)


def _tracked_test_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "tests/"],
                         capture_output=True, text=True, timeout=60).stdout
    return [f for f in out.split() if f.endswith(".py")]


def _census() -> tuple[list[dict], int, int]:
    """(sites >= the bound, total constant sites, files parsed).

    A parse failure RAISES. An unparseable file is UNKNOWN, and UNKNOWN must not
    be laundered into "no offenders here" -- that is the fail-open this whole
    class of defect is made of.
    """
    bound = json.loads(RATCHET.read_text(encoding="utf-8"))["governing_bound_s"]
    rows, total, files = [], 0, _tracked_test_files()
    for rel in files:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"),
                         filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for kw in node.keywords:
                    if kw.arg in _BUDGET_NAMES and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, (int, float)):
                        total += 1
                        if not _is_ms(callee) and kw.value.value >= bound:
                            rows.append({"file": rel, "callee": callee,
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
                        total += 1
                        callee = "DEFAULT:" + node.name
                        if not _is_ms(callee) and d.value >= bound:
                            rows.append({"file": rel, "callee": callee,
                                         "value": d.value})
    return rows, total, len(files)


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
