"""v3.66.935 -- a scan wait that gave up was indistinguishable from one that
finished.

THE DEFECT. Eight copies of this loop lived across three test files:

    for _ in range(30):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)

Two exits, and the code after it cannot tell them apart: the scan finished, or
the budget -- thirty polls of 50ms, so 1.5 SECONDS -- ran out while the scan was
still going. The caller asserts on the snapshot either way.

MEASURED on the operator's box 2026-08-07 (capture at 48707ad, v3.66.932):
`test_scanner_idempotency` failed with `assert 4 == 5`, one file short of a scan
that had not finished. In a quiet container that scan takes 0.098s over two
polls -- fifteen times the headroom -- so the defect is invisible everywhere
except the machine that gates the release.

Demonstrated by holding the budget fixed and widening the tree:

    PROBE running=True added=395/4000 errors=0
    REAL-TEST-STYLE ASSERT would compare added == 4000 ->  False

THE TESTS BELOW ARE MOSTLY AGAINST A STUB, ON PURPOSE. The helper's subject is
what happens when a scan does NOT converge, and the real scanner converges in
0.098s here -- so a suite built only on the real scanner could not reach the
branch it exists to test, which is the shape it was written to remove. The stub
lets the non-convergent case be exercised deterministically; the last two tests
pin the helper against the real library so the stub cannot drift away from it.
"""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

import scan_wait                                    # noqa: E402

pytestmark = pytest.mark.bd_module_wipe


class _StubLib:
    """A scan_status()/scan_start() pair whose behaviour is scripted.

    Mirrors the real ScanState dict shape, including the two details the
    hand-rolled loops tripped over: `running` is derived rather than stored,
    and the never-run stub carries no `finished_at` key at all.
    """

    def __init__(self, *, statuses=None, start_result=None):
        self._statuses = list(statuses or [])
        self._start_result = start_result
        self.start_calls = []

    def scan_status(self):
        if not self._statuses:
            raise AssertionError("stub ran out of scripted statuses")
        return (self._statuses.pop(0) if len(self._statuses) > 1
                else self._statuses[0])

    def scan_start(self, roots):
        self.start_calls.append(list(roots))
        return self._start_result


def _state(**kw):
    st = {"started_at": 1000.0, "finished_at": None, "cancelled": False,
          "current_root": "/r", "roots": ["/r"], "seen": 0, "added": 0,
          "updated": 0, "missing_marked": 0, "errors": 0, "error_samples": [],
          "elapsed_s": 0.1}
    st.update(kw)
    st["running"] = st["finished_at"] is None and not st["cancelled"]
    return st


# ── the defect: exhaustion must FAIL, not fall through ───────────────────────

def test_a_scan_that_does_not_converge_raises():
    lib = _StubLib(statuses=[_state(seen=395, added=395)])
    with pytest.raises(AssertionError) as ei:
        scan_wait.wait_for_scan(lib, timeout=0.15)
    msg = str(ei.value)
    assert "did NOT converge" in msg
    assert "mid-scan snapshot" in msg


def test_the_failure_names_the_state_the_caller_was_about_to_assert_on():
    """A bare 'timed out' would move the failure without explaining it. The
    box's symptom was `assert 4 == 5` in someone else's assertion; the whole
    point is that the wait says so itself."""
    lib = _StubLib(statuses=[_state(seen=395, added=395, errors=2,
                                    error_samples=["record_failed /r/v1.mp4"])])
    with pytest.raises(AssertionError) as ei:
        scan_wait.wait_for_scan(lib, timeout=0.15)
    msg = str(ei.value)
    for field in ("added", "seen", "errors", "error_samples", "elapsed_s",
                  "current_root"):
        assert field in msg, f"{field} missing from the failure message"
    assert "395" in msg and "record_failed" in msg


# ── `running` is not the predicate; `finished_at` is ─────────────────────────

def test_a_cancelled_but_still_walking_scan_is_not_converged():
    """MEASURED against the real scanner: `to_dict` computes
    `running = finished_at is None and not cancelled`, so scan_cancel() flips
    `running` to False while the worker keeps walking and keeps mutating the
    counters -- seen climbed 70 -> 190 with running == False, and reached 4000
    by the time finished_at was set. A helper that waited on `running` would
    return a snapshot mid-walk and call it final."""
    cancelled_mid_walk = _state(cancelled=True, finished_at=None, seen=70)
    assert cancelled_mid_walk["running"] is False      # the trap, spelled out
    lib = _StubLib(statuses=[cancelled_mid_walk])
    with pytest.raises(AssertionError) as ei:
        scan_wait.wait_for_scan(lib, timeout=0.15)
    assert "did NOT converge" in str(ei.value)


def test_a_finished_scan_returns_its_status():
    final = _state(finished_at=2000.0, seen=5, added=5)
    lib = _StubLib(statuses=[final])
    got = scan_wait.wait_for_scan(lib, timeout=5)
    assert got["added"] == 5 and got["finished_at"] == 2000.0


def test_it_converges_after_polling_rather_than_only_on_the_first_look():
    lib = _StubLib(statuses=[_state(seen=1), _state(seen=3),
                             _state(finished_at=2000.0, seen=5, added=5)])
    assert scan_wait.wait_for_scan(lib, timeout=5)["added"] == 5


# ── a refused start leaves the PREVIOUS scan's terminal state ────────────────

def test_a_never_run_status_is_not_a_converged_scan():
    """The never-run stub is `{"running": False, "never_run": True}` -- no
    finished_at key at all. The old loop read it as 'finished' on its first
    poll."""
    lib = _StubLib(statuses=[{"running": False, "never_run": True}])
    with pytest.raises(AssertionError) as ei:
        scan_wait.wait_for_scan(lib, timeout=0.15)
    assert "never_run" in str(ei.value)


def test_start_and_wait_refuses_a_rejected_start():
    """MEASURED: scan_start returns {'ok': False, 'error': 'no valid roots
    provided'} and leaves _scan_state untouched, so the previous scan's
    terminal counters stay in place -- added=3 seen=3 from an earlier run,
    reported as final."""
    stale_terminal = _state(finished_at=2000.0, seen=3, added=3)
    lib = _StubLib(statuses=[stale_terminal],
                   start_result={"ok": False,
                                 "error": "no valid roots provided"})
    with pytest.raises(AssertionError) as ei:
        scan_wait.start_and_wait(lib, ["/nope"])
    msg = str(ei.value)
    assert "refused" in msg and "no valid roots provided" in msg


def test_start_and_wait_refuses_while_a_previous_worker_is_alive():
    """The product defect the helper declines to race. scan_start refuses only
    while `finished_at is None and not cancelled`, so after scan_cancel() it
    ACCEPTS a new scan while the cancelled worker is still walking -- and that
    worker's counter writes land in the NEW ScanState. started_at cannot catch
    it, because the new state legitimately carries the new stamp."""
    lib = _StubLib(statuses=[_state(cancelled=True, finished_at=None, seen=70)],
                   start_result={"ok": True, "started": True})
    with pytest.raises(AssertionError) as ei:
        scan_wait.start_and_wait(lib, ["/r"])
    assert "has not finished" in str(ei.value)
    assert lib.start_calls == [], (
        "start_and_wait called scan_start on top of a live worker")


def test_a_converged_status_from_a_different_run_is_refused():
    lib = _StubLib(statuses=[_state(finished_at=2000.0, started_at=999.0,
                                    seen=3, added=3)])
    with pytest.raises(AssertionError) as ei:
        scan_wait.wait_for_scan(lib, timeout=0.15, started_at=1000.0)
    assert "DIFFERENT run" in str(ei.value)


# ── errors: reported always, asserted only on request ────────────────────────

def test_errors_do_not_fail_the_wait_by_default():
    """A crashed worker leaves errors == 0 with partial counts -- the outer
    try: in _scan_worker has no except, only a finally that sets finished_at --
    so `errors == 0` is not evidence of a clean run and asserting on it by
    default would buy less than it looks like while breaking any test that
    deliberately induces record failures."""
    lib = _StubLib(statuses=[_state(finished_at=2000.0, seen=5, added=4,
                                    errors=1)])
    assert scan_wait.wait_for_scan(lib, timeout=5)["errors"] == 1


def test_require_clean_opts_in_to_the_errors_assertion():
    lib = _StubLib(statuses=[_state(finished_at=2000.0, seen=5, added=4,
                                    errors=1,
                                    error_samples=["record_failed /r/v4.mp4"])])
    with pytest.raises(AssertionError) as ei:
        scan_wait.wait_for_scan(lib, timeout=5, require_clean=True)
    assert "1 error(s)" in str(ei.value)


# ── the budget is a hang-bound, not a stopwatch ──────────────────────────────

def test_a_slow_but_converging_scan_is_not_failed():
    """Over-correction guard. A gate that failed a scan for being slow would
    fire on identity rather than content, which section 0 counts as a soundness
    bug equal to a false clean."""
    slow = [_state(seen=i) for i in range(8)]
    slow.append(_state(finished_at=2000.0, seen=8, added=8))
    lib = _StubLib(statuses=slow)
    assert scan_wait.wait_for_scan(lib, timeout=10)["added"] == 8


def test_the_default_budget_is_generous_enough_to_be_a_hang_bound():
    assert scan_wait.DEFAULT_TIMEOUT_S >= 20, (
        "the default budget is back in stopwatch territory; the 1.5s budget "
        "this cut removed is what failed on the box")


# ── pinned against the real library, so the stub cannot drift ────────────────

def test_the_real_scanner_converges_and_the_helper_returns_it(tmp_path):
    from bulk_downloader import library as lib
    for i in range(5):
        (tmp_path / f"v{i}.mp4").write_bytes(b"video")
    st = scan_wait.start_and_wait(lib, [str(tmp_path)])
    assert st["added"] == 5 and st["updated"] == 0
    assert st["finished_at"] is not None


def test_the_real_scan_status_shape_matches_what_the_stub_scripts(tmp_path):
    """If ScanState gained or lost a field the helper reads, the stub above
    would keep passing while the helper broke against the real thing."""
    from bulk_downloader import library as lib
    (tmp_path / "a.mp4").write_bytes(b"video")
    st = scan_wait.start_and_wait(lib, [str(tmp_path)])
    for field in ("running", "cancelled", "started_at", "finished_at", "seen",
                  "added", "updated", "missing_marked", "errors",
                  "error_samples", "elapsed_s", "current_root", "roots"):
        assert field in st, f"ScanState no longer exposes {field!r}"
    assert lib.scan_status().get("never_run") is None


def test_the_real_never_run_stub_still_has_no_finished_at():
    """The other half of the shape pin, and the reason `never_run` is checked
    before `finished_at`: this dict has no finished_at key to read."""
    from bulk_downloader import library as _fresh
    st = _fresh.scan_status()
    if st.get("never_run"):
        assert "finished_at" not in st
    else:                       # a scan already ran in this process
        assert "finished_at" in st


# ── the adoption ratchet ─────────────────────────────────────────────────────

def test_no_test_file_hand_rolls_a_scan_poll_loop():
    """AST, not source text: the docstrings in this file and in scan_wait.py
    both QUOTE the defective loop, and a grep would count them. A comment is
    inside the denominator of every gate that reads source text.

    Denominator is every tracked tests/*.py, so a NEW file that hand-rolls the
    loop fails this rather than passing unseen.
    """
    import subprocess
    out = subprocess.run(["git", "ls-files", "-z", "--", "tests/*.py",
                          "tests/**/*.py"], cwd=str(_REPO),
                         capture_output=True, text=True, check=True).stdout
    files = [f for f in out.split("\0") if f]
    assert len(files) > 500, f"denominator collapsed to {len(files)} files"

    offenders = []
    for rel in files:
        if rel == "tests/scan_wait.py":
            continue                     # the helper's own loop is the fix
        try:
            tree = ast.parse((_REPO / rel).read_text("utf-8"))
        except SyntaxError:              # covered by test_all_sources_parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.For, ast.While)):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "scan_status"):
                    offenders.append(f"{rel}:{node.lineno}")
                    break
    assert not offenders, (
        "hand-rolled scan poll loop(s) -- use scan_wait.start_and_wait or "
        "scan_wait.wait_for_scan, which fail on non-convergence instead of "
        "falling through:\n  " + "\n  ".join(sorted(set(offenders))))
