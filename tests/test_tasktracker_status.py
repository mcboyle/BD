"""#7: tasktracker_gen.py --status -- a stdlib-only counts rollup that a fresh
sandbox session can run to report tracker state, and that DEGRADES GRACEFULLY
(clear message, no traceback, exit 0) when TASK_TRACKER_DATA.json was not
bundled into version.zip.

RED-first contract: on pristine source --status is an unrecognized argument, so
both tests fail (argparse exits 2 / no rollup printed) until status() + the
--status flag land. Runner-safe: zero-arg test fns, tempfile.mkdtemp (no
tmp_path), stdout captured, module imported off the repo-root tools/ dir.
"""
import contextlib
import io
import json
import os
import sys
import tempfile


def _load_module():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools = os.path.join(root, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import tasktracker_gen  # noqa: E402
    return tasktracker_gen


def _run_status(d):
    """Invoke `main(['--status', d])`, capturing stdout + the exit code.
    A SystemExit (e.g. argparse 'unrecognized arguments' on pristine) is
    normalized to its int code so the assertions stay readable."""
    m = _load_module()
    buf = io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(buf):
        try:
            rc = m.main(["--status", d])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue()


def _write_data(d, n_inc, n_await, n_comp, n_decided=0):
    inc = [{"ID": f"I{i}", "Sandbox-able?": "Yes", "Tier": "A"}
           for i in range(n_inc)]
    awa = [{"ID": f"W{i}", "Sandbox-able?": "No", "Tier": "B"}
           for i in range(n_await)]
    comp = [{"ID": f"C{i}"} for i in range(n_comp)]
    decided = [{"ID": f"D{i}"} for i in range(n_decided)]
    data = {"columns": {"incomplete": [], "completed": []},
            "incomplete": inc, "awaiting_operator": awa, "completed": comp,
            "decided_against": decided}
    with open(os.path.join(d, "TASK_TRACKER_DATA.json"), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps(data))


def test_status_reports_counts_when_data_present():
    d = tempfile.mkdtemp(prefix="tt_status_present_")
    _write_data(d, 3, 2, 7)            # 3 incomplete, 2 awaiting, 7 completed
    rc, out = _run_status(d)
    assert rc == 0, f"expected exit 0, got {rc!r}; out={out!r}"
    low = out.lower()
    assert "incomplete" in low and "completed" in low
    assert "awaiting" in low
    # the four headline counts must surface (3 inc / 2 await / 7 comp / 12 total)
    assert "3" in out and "7" in out and "12" in out, f"counts missing: {out!r}"
    assert "total" in low


def test_status_degrades_gracefully_when_data_absent():
    d = tempfile.mkdtemp(prefix="tt_status_absent_")   # empty: no DATA.json
    rc, out = _run_status(d)
    assert rc == 0, f"absent data must be graceful (exit 0), got {rc!r}; out={out!r}"
    low = out.lower()
    assert ("not found" in low or "no data" in low or "unavailable" in low), \
        f"expected a clear data-absent message, got: {out!r}"
    assert "traceback" not in low and "error" not in low


def test_status_counts_decided_against_in_grand_total():
    d = tempfile.mkdtemp(prefix="tt_status_decided_")
    _write_data(d, 3, 2, 7, n_decided=4)
    rc, out = _run_status(d)
    assert rc == 0
    assert "decided-against:   4" in out
    assert "total:             16" in out
