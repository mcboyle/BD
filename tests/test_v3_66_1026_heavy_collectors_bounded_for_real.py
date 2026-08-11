"""v3.66.1026 -- the heavy-collector budget could not stop a diagnose already
running, and zero budget meant NO budget in two of the three collectors.

Measured on test5 (machine-id sha256/12 7b4ea932c297) at 6728dc8 (v3.66.1025),
against the operator's real 2.5GB capture store, and cross-checked on test4
(102b31c04e7b) with near-identical numbers:

  * GET /api/data/capture_diagnostics COLD took 17.36s -- 2.2x L34's 8s serial
    route gate -- with budget_s=5 exhausted and useless, because collect()
    checks its deadline only BETWEEN files and one diagnose() of the newest
    <=25MB .wacz took 16.1s alone (the 3rd-newest: 37.9s, at 2.0MB -- size
    does not predict cost). @1023's "the overrun is ONE FILE, 0.233s" was
    measured on capture_analytics' JSON parse; a diagnostics "file" is two
    full zip parses + whole-dom-log HTML serialization + the recognizer
    batteries + a sha256 of the archive.
  * capture_diagnostics.py and replay_validator.py both guarded the deadline
    with `if budget_s` -- falsy for 0 -- so budget_s=0 meant UNBOUNDED
    (measured: >10 minutes before the probe was killed), while their sibling
    capture_analytics._artifacts guards `is not None` and treats 0 as "no
    time at all". Same parameter, opposite semantics.
  * app_data_layer._cached had no lock, so L34's phase-1 probe and its serial
    re-probe each ran the full ~17s compute concurrently.
  * tests/test_v3_66_1023_heavy_budget_fits_the_route_gate.py reads
    capture_analytics.py ONLY, and test_bug_capture_diagnostics_bounded.py
    stubs diagnose() to near-zero cost -- so no test in the tree could see
    any of this. This file is those tests' missing half.

The fix this battery constrains: `is not None` deadline guards in both
files; an opt-in `isolate=True` mode (used by the route layer only) that runs
each diagnose in a child process killed at the deadline, so the overrun past
budget_s is bounded by _KILL_GRACE_S rather than by whatever one file costs;
and a single-flight `_cached`. The in-process path stays byte-compatible for
the CLI and for the existing stub-based batteries.
"""
from __future__ import annotations

import ast
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import zipfile

_REPO = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import capture_diagnostics as CD  # type: ignore
import replay_validator as RV  # type: ignore

# A synthetic capture that diagnose() completes on in ~0.05s (measured), so
# the isolated path can prove it returns REAL rows, not only kills.
_GOOD_WACZ = _REPO / "tests" / "capture_corpus_synthetic" / "bang247_redacted_strict.wacz"


def _store(n_wacz=3, real=False):
    """A tmp capture store. real=True copies the known-good synthetic wacz;
    real=False writes empty zips (fine for count/skip semantics, and for the
    kill test, where the child never gets far enough to care)."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="hcb_"))
    cap = root / "captures"
    cap.mkdir()
    for i in range(n_wacz):
        dst = cap / f"cap_{i:03d}.wacz"
        if real:
            shutil.copyfile(_GOOD_WACZ, dst)
        else:
            dst.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    return root


def _stubbed_cd(root, **kw):
    calls = []
    orig_diag, orig_row = CD.diagnose, CD._row
    try:
        CD.diagnose = lambda ap, **k: (calls.append(ap) or {"stub": True})
        CD._row = lambda rel, v: {"path": rel, "verdict": "MOCK"}
        res = CD.collect(str(root), **kw)
    finally:
        CD.diagnose, CD._row = orig_diag, orig_row
    return res, calls


# ── 1-2. budget_s=0 must mean "no time at all", in BOTH collectors ──────────

def test_zero_budget_is_zero_time_for_capture_diagnostics():
    """budget_s=0 spent the budget before the first file in capture_analytics
    since @1015; the same call into capture_diagnostics ran UNBOUNDED. The
    stub makes the diagnose free, so the only thing measured is the guard."""
    root = _store(4)
    res, calls = _stubbed_cd(root, budget_s=0)
    assert calls == [], (
        f"budget_s=0 still diagnosed {len(calls)} file(s) -- the deadline "
        f"guard treats 0 as falsy, i.e. as NO budget")
    assert res["skipped_wacz"] == 4
    assert res["budget_exhausted"] is True
    assert res["rows"] == []


def test_zero_budget_is_zero_time_for_replay_validator():
    root = _store(4)
    calls = []
    orig_load, orig_val = RV._WD.load_capture, RV.validate_replay
    try:
        RV._WD.load_capture = lambda p: (calls.append(p) or {})
        RV.validate_replay = lambda cap: {"ok": True, "errors": [], "warnings": [],
                                          "stats": {"events": 0}}
        res = RV.collect(str(root), budget_s=0)
    finally:
        RV._WD.load_capture, RV.validate_replay = orig_load, orig_val
    assert calls == [], (
        f"budget_s=0 still validated {len(calls)} file(s) -- falsy-zero guard")
    assert res["skipped_wacz"] == 4
    assert res["budget_exhausted"] is True
    assert res["rows"] == []


# ── 3. the guard SHAPE, mechanically, so the falsy form cannot come back ────

def _deadline_guard_is_is_not_none(path: pathlib.Path) -> bool:
    """True when the `_deadline = ... if <test> else None` assignment's test is
    a `budget_s is not None` Compare node. Read from the AST, not the text --
    a comment quoting the old form must not satisfy or trip this (section 0)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_deadline"
                   for t in node.targets):
            continue
        if not isinstance(node.value, ast.IfExp):
            continue
        test = node.value.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == "budget_s"
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot)
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None):
            return True
        return False  # found the assignment; its test is some other shape
    raise AssertionError(f"no `_deadline = ... if ... else None` in {path.name}; "
                         f"re-derive this test, the shape moved")


def test_the_deadline_guard_is_is_not_none_in_capture_diagnostics():
    assert _deadline_guard_is_is_not_none(
        _REPO / "tools" / "capture_diagnostics.py"), (
        "capture_diagnostics builds _deadline from a truthiness test of "
        "budget_s, so 0 disables the budget instead of exhausting it")


def test_the_deadline_guard_is_is_not_none_in_replay_validator():
    assert _deadline_guard_is_is_not_none(
        _REPO / "tools" / "replay_validator.py"), (
        "replay_validator builds _deadline from a truthiness test of "
        "budget_s, so 0 disables the budget instead of exhausting it")


# ── 4-5. the isolated route path: kills bind the wall clock, and real rows ──

def test_isolated_diagnose_is_killed_at_the_deadline():
    """The property @1023 could not deliver for this collector: wall time is
    bounded by budget + grace even when ONE file costs more than the whole
    budget. budget_s=0.05 is less than a child interpreter's own startup, so
    the first child cannot finish and MUST be killed -- if the in-process
    path runs instead, diagnose() on an empty zip returns an ERROR row and
    killed_in_flight stays 0."""
    root = _store(3)
    t0 = time.monotonic()
    res = CD.collect(str(root), budget_s=0.05, isolate=True)
    wall = time.monotonic() - t0
    assert res.get("killed_in_flight", 0) >= 1, (
        f"no in-flight kill recorded: {res!r} -- the budget still cannot "
        f"stop a diagnose that has already started")
    assert res["rows"] == [], "a killed diagnose must not leave a row"
    assert res["budget_exhausted"] is True
    assert res["skipped_wacz"] == 3, "killed + never-started must all be counted"
    assert wall < 0.05 + CD._KILL_GRACE_S + 2.0, (
        f"wall {wall:.2f}s exceeds budget + kill grace + margin -- the kill "
        f"is not binding the clock")


def test_isolated_diagnose_returns_real_rows_inside_budget():
    """The other half -- kills must not be the only outcome. A capture the
    in-process diagnose completes in ~0.05s must come back as a REAL row
    through the child, aggregated identically."""
    root = _store(1, real=True)
    res = CD.collect(str(root), budget_s=30, isolate=True)
    assert res.get("killed_in_flight", 0) == 0, f"unexpected kill: {res!r}"
    assert len(res["rows"]) == 1, f"expected 1 real row, got {res['rows']!r}"
    row = res["rows"][0]
    assert row["verdict"] != "ERROR", f"child diagnose failed: {row!r}"
    assert res["aggregate"]["n"] == 1


def test_isolated_rows_are_field_identical_to_in_process_rows():
    """The child must be an EXECUTION change, not a MEANING change: the same
    capture through both paths yields the same row, field for field. This is
    the general detector for child-environment divergence (cwd, env, import
    path, JSON round-trip) -- any of those drifting shows up as a field
    mismatch here.

    Pre-merge review found the child's original scratch cwd WOULD diverge on
    cwd-relative gold resolution -- and the follow-up measurement found the
    default gold join is broken for BOTH paths today (the candidate nests
    host under source.host; filed separately), so both report identically
    and parity holds. The child cwd is set to the work root anyway: when the
    join fix lands, cwd-relative resolution starts mattering and THIS test
    plus that cut's gold test become the constraint. A parity test cannot
    prove the cwd is right today (measured: no observable differs); it
    proves nothing else diverged."""
    root = _store(1, real=True)
    res_in = CD.collect(str(root), budget_s=None)
    res_iso = CD.collect(str(root), budget_s=30, isolate=True)
    assert len(res_in["rows"]) == 1 and len(res_iso["rows"]) == 1
    assert res_iso["rows"][0] == res_in["rows"][0], (
        f"isolated row diverged from in-process row for the same capture:\n"
        f"  in-process: {res_in['rows'][0]!r}\n"
        f"  isolated  : {res_iso['rows'][0]!r}")


def test_second_child_gets_the_remaining_budget_not_a_fresh_one():
    """Review catch (v3.66.1026 pre-merge): at both original call sites
    `remaining` and `budget_s` were numerically interchangeable, so a mutant
    passing the FULL budget to every child -- letting the route answer at
    ~2x budget, past the gate -- escaped the battery. Record the timeout
    each child is offered: after the first child consumes a known slice,
    the second's offer must be the remainder."""
    root = _store(2)
    offers = []
    orig = CD._diagnose_isolated

    def recorder(ap, timeout_s, work_root):
        offers.append(timeout_s)
        time.sleep(0.4)
        return None if len(offers) > 1 else {"error": "stub"}

    CD._diagnose_isolated = recorder
    try:
        CD.collect(str(root), budget_s=10, isolate=True)
    finally:
        CD._diagnose_isolated = orig
    assert len(offers) == 2, f"expected 2 child offers, got {offers!r}"
    assert offers[0] > 9.0, f"first child should be offered ~the full budget: {offers!r}"
    assert offers[1] < 9.8, (
        f"second child was offered {offers[1]:.2f}s of a 10s budget after "
        f"the first consumed ~0.4s -- the timeout is not the REMAINING "
        f"budget, so N expensive files can hold the route ~N x budget")


def test_isolate_is_opt_in_and_the_default_path_is_unchanged():
    """The CLI contract and the existing stub-based batteries depend on the
    in-process path: a stubbed CD.diagnose must still be what collect() calls
    when isolate is not requested, budget or no budget."""
    root = _store(2)
    res, calls = _stubbed_cd(root, budget_s=30)
    assert len(calls) == 2, (
        f"in-process diagnose was called {len(calls)}x with isolate unset -- "
        f"the default path no longer honors a monkeypatched diagnose")
    assert all(r["verdict"] == "MOCK" for r in res["rows"])


def test_the_route_layer_actually_requests_isolation():
    """The kill design only binds the ROUTE if the route asks for it. This
    is the wiring a mutant can silently flip (isolate=True -> False) with
    every other test here still green, because they call CD.collect
    directly."""
    sys.path.insert(0, str(_REPO))
    from bulk_downloader import app_data_layer as ADL

    seen = {}
    ADL._ensure_path()
    import tools.capture_diagnostics as TCD

    orig = TCD.collect
    ADL._heavy_cache.pop("capture_diagnostics", None)
    try:
        TCD.collect = lambda *a, **kw: (seen.update(kw) or {"stub": True})
        ADL.collect_capture_diagnostics()
    finally:
        TCD.collect = orig
        ADL._heavy_cache.pop("capture_diagnostics", None)
    assert seen.get("isolate") is True, (
        f"collect_capture_diagnostics called collect with {seen!r} -- the "
        f"route no longer requests the child-process bound, so one expensive "
        f"file again holds the route past L34's gate")
    # Review catch (pre-merge): isolate=True is INERT without a budget --
    # collect() gates the child path on `isolate and _deadline is not None`
    # -- so a budget_s=None mutant here reverts the route to the unbounded
    # in-process walk with every other test green. Assert the whole triple.
    assert seen.get("budget_s") == ADL._HEAVY_BUDGET_S, (
        f"route passed budget_s={seen.get('budget_s')!r}; without the "
        f"budget the isolate flag does nothing")
    assert seen.get("max_bytes") == ADL._HEAVY_MAX_BYTES
    assert seen.get("limit") == ADL._HEAVY_LIMIT


# ── 6. the cache is single-flight ───────────────────────────────────────────

def test_cached_is_single_flight_for_concurrent_cold_misses():
    """L34's phase-1 probe and its serial re-probe both hit a cold cache;
    without a lock each runs the full collector (measured: two overlapping
    ~17s computes on the box). Two concurrent misses on one key must compute
    ONCE and share the result."""
    sys.path.insert(0, str(_REPO))
    from bulk_downloader import app_data_layer as ADL

    key = "test-single-flight-v1026"
    ADL._heavy_cache.pop(key, None)
    ran = []

    def slow():
        ran.append(1)
        time.sleep(0.3)
        return {"ok": True}

    out = [None, None]

    def hit(i):
        out[i] = ADL._cached(key, slow)

    try:
        t1 = threading.Thread(target=hit, args=(0,))
        t2 = threading.Thread(target=hit, args=(1,))
        t1.start(); t2.start(); t1.join(); t2.join()
    finally:
        ADL._heavy_cache.pop(key, None)
    assert len(ran) == 1, (
        f"the collector ran {len(ran)}x for two concurrent misses on one "
        f"key -- _cached is not single-flight, so every route probe pair "
        f"doubles the load that already blows the gate")
    assert out[0] is not None and out[1] is not None


# ── 7. the arithmetic that licenses the design, pinned like @1023's ─────────

def test_budget_plus_kill_grace_fits_inside_the_route_gate():
    """@1023 pinned budget + margin <= gate for the collector whose overrun
    is one PARSE. For the isolated path the overrun bound is _KILL_GRACE_S
    (spawn + kill + reap), and the same relationship must hold or the kill
    design does not actually fit the gate it exists for."""
    sys.path.insert(0, str(_REPO))
    from bulk_downloader import app_data_layer as heavy
    from live_tests import checks
    budget = float(heavy._HEAVY_BUDGET_S)
    grace = float(CD._KILL_GRACE_S)
    gate = float(checks._L34_ROUTE_BUDGET_S)
    assert budget + grace + 1.0 <= gate, (
        f"_HEAVY_BUDGET_S ({budget}) + _KILL_GRACE_S ({grace}) + 1.0s margin "
        f"exceeds _L34_ROUTE_BUDGET_S ({gate}) -- the isolated collector can "
        f"answer later than the gate allows")
