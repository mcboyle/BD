"""v3.66.941 -- a cancelled scan's worker writes its counters into the NEXT scan.

THE DEFECT, in three facts that are individually reasonable.

1. `scan_cancel()` sets `cancelled = True` and returns. Nothing stops the
   thread; it breaks at the next file or root boundary, and a large tree can
   keep it walking for a long time after the call returns True.
2. `scan_start()` refuses only while
   `_scan_state.finished_at is None and not _scan_state.cancelled`. After a
   cancel the second clause is False, so a NEW scan is ACCEPTED while the old
   worker is still alive.
3. `_scan_worker`'s `_mut`, `_bump` and `_record_error` all resolve the MODULE
   GLOBAL `_scan_state` at call time -- thirteen references, none of them bound
   to the state the worker was started for. So the old worker's `seen`,
   `added`, `updated`, `errors` and finally its `finished_at` land in the NEW
   ScanState.

MEASURED at v3.66.935, after `scan_cancel()`: `running` read False while `seen`
climbed 70 -> 190 and went on to 4000, with `finished_at` still None.

WHAT IT LOOKS LIKE TO AN OPERATOR. The second scan reports counts it did not
produce, and then the FIRST worker's `finally: _mut(finished_at=...)` marks the
second scan finished while it is still walking. Both routes are live --
`app_library.py:244` (`scan_start`) and `:278` (`scan_cancel`).

NO TEST IN THE SUITE CALLS `scan_cancel`. An AST census over the tracked
`tests/*.py` finds zero call sites, which is why three years of green bands
never touched this interleaving. `tests/scan_wait.py:start_and_wait` refuses to
start on top of an unfinished worker -- that helper was written at v3.66.935
precisely because the product would not refuse -- so the harness was safe and
the product was not.

THE FIX IS BOTH HALVES, and the second is the one that matters.

  * `scan_start` refuses while the previous worker thread is alive, so two
    walks never run at once.
  * `_scan_worker` is BOUND to the ScanState it was started for, so even if a
    thread outlives its refusal window its writes cannot reach another scan's
    counters. A refusal alone leaves a race between the liveness check and the
    thread actually exiting; binding removes the corruption outright rather
    than narrowing the window and calling it fixed.

The binding is by reference to the same object `scan_start` publishes as the
global, so `scan_cancel()` -- which sets `cancelled` on the global -- still
reaches the running worker. Nothing about the cancel path changes.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests import scan_wait

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

_LIB_SRC = _REPO / "bulk_downloader" / "library.py"


@pytest.fixture
def lib(monkeypatch, tmp_path):
    """The library module against an isolated database.

    `db._resolve_db_path()` falls back to a RELATIVE path resolved against the
    cwd, so without both of these a probe writes downloader_history.db into the
    repo -- gitignored, so nothing warns, and rows then accumulate across runs
    and the next probe reads them (CLAUDE.md section 5). conftest's
    clean_workdir does both; a hand-rolled fixture gets neither for free.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    from bulk_downloader import library as _lib
    # Join BEFORE resetting: a worker left running by an earlier test resolves
    # the module global, so clearing it out from under one raises
    # AttributeError on a daemon thread -- which is the defect under test
    # showing up as harness noise and obscuring the real failures.
    _quiesce(_lib)
    _lib._scan_state = None
    _lib._scan_thread = None
    yield _lib
    _quiesce(_lib)


def _quiesce(_lib) -> None:
    """Cancel any running scan and WAIT for the thread to actually leave.

    `scan_cancel` only sets a flag; a test that returns without joining leaves
    a walker mutating module state into the next test -- the same
    one-variable-at-a-time rule CLAUDE.md section 5 states for flags, applied
    to threads.
    """
    st = getattr(_lib, "_scan_state", None)
    if st is not None:
        st.cancelled = True
    t = getattr(_lib, "_scan_thread", None)
    if t is not None and t.is_alive():
        t.join(timeout=60)


def _big_tree(base: Path, n: int = 4000) -> str:
    """A tree big enough that the walk is still running a moment later.

    Not a sleep and not a monkeypatched os.walk: the defect is about a real
    worker still holding the global, and a stubbed walk would let the test pass
    against an implementation that never actually races.
    """
    root = base / "media"
    for i in range(n):
        d = root / f"d{i // 200:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"f{i:05d}.mp4").write_bytes(b"x")
    return str(root)


# ── structure: the worker must not resolve the global ────────────────────────

def test_the_worker_is_bound_to_the_state_it_was_started_for():
    """RED on pristine: thirteen `_scan_state` references inside the worker.

    AST, not grep: a comment mentioning the global is not a reference to it,
    and this file's own docstring names it repeatedly.
    """
    tree = ast.parse(_LIB_SRC.read_text("utf-8"))
    worker = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_scan_worker"),
                  None)
    assert worker is not None, (
        "_scan_worker is gone -- this file's subject no longer exists, which "
        "is a failure, not a pass")

    globals_used = [n for n in ast.walk(worker)
                    if isinstance(n, ast.Name) and n.id == "_scan_state"]
    assert not globals_used, (
        f"_scan_worker still resolves the module global _scan_state at "
        f"{len(globals_used)} site(s), at line(s) "
        f"{sorted({n.lineno for n in globals_used})}. A worker that outlives "
        f"its scan writes those counters into whichever ScanState is current "
        f"when it gets there.")

    params = [a.arg for a in worker.args.args]
    assert len(params) >= 2, (
        f"_scan_worker takes {params}; it must receive the ScanState it is "
        f"walking for, so its writes cannot reach another scan.")


def test_scan_start_passes_the_state_to_the_worker():
    """A parameter nobody fills is not a binding."""
    src = _LIB_SRC.read_text("utf-8")
    tree = ast.parse(src)
    starter = next((n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "scan_start"), None)
    assert starter is not None, "scan_start is gone"
    threads = [n for n in ast.walk(starter)
               if isinstance(n, ast.Call)
               and "Thread" in ast.dump(n.func)]
    assert threads, "scan_start no longer starts a thread"
    for call in threads:
        args_kw = {k.arg: k.value for k in call.keywords}
        assert "args" in args_kw, "the worker thread is started with no args="
        node = args_kw["args"]
        passed = ast.get_source_segment(src, node) or ""
        # ELEMENTS, not commas: on pristine source this is `(roots,)`, whose
        # trailing comma made a `count(",") >= 1` predicate pass vacuously --
        # a check that could not fail, written to prove the thing it could not
        # see (CLAUDE.md section 0).
        assert isinstance(node, ast.Tuple), (
            f"the worker thread's args= is {passed!r}, not a tuple literal; "
            f"this check cannot count what it is handed.")
        assert len(node.elts) >= 2, (
            f"the worker is started with args={passed!r} -- {len(node.elts)} "
            f"element(s). It must receive the ScanState as well as the roots.")


# ── behaviour: no second walk while the first is alive ───────────────────────

def test_scan_start_refuses_while_a_cancelled_worker_is_still_alive(lib, tmp_path):
    """RED on pristine: the second start is ACCEPTED.

    The window is real rather than theoretical -- scan_cancel returns as soon
    as it sets the flag, and the worker only notices at the next boundary.
    """
    root = _big_tree(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    assert lib.scan_start([root]).get("ok") is True, "the first scan did not start"
    scan_wait.wait_for_progress(lib)

    assert lib.scan_cancel() is True, "scan_cancel did not report a running scan"
    thread = lib._scan_thread
    if thread is None or not thread.is_alive():
        pytest.skip("the worker finished before the cancel landed; this "
                    "machine walked 4000 files faster than the test could "
                    "act, so the interleaving under test did not occur")

    second = lib.scan_start([str(other)])
    assert second.get("ok") is False, (
        f"scan_start accepted a new scan while the previous worker was still "
        f"alive: {second!r}. That worker's counter writes resolve the global "
        f"_scan_state at call time, so they would land in this new scan's "
        f"state.")


def test_a_refused_start_does_not_replace_the_running_state(lib, tmp_path):
    """The refusal must be inert.

    A refusal that had already overwritten `_scan_state` would be worse than
    the acceptance it replaces: the running worker would then be writing into
    a state the caller believes was never created.
    """
    root = _big_tree(tmp_path)
    other = tmp_path / "other2"
    other.mkdir()

    lib.scan_start([root])
    scan_wait.wait_for_progress(lib)
    lib.scan_cancel()
    if lib._scan_thread is None or not lib._scan_thread.is_alive():
        pytest.skip("worker finished before the cancel landed")

    before = lib.scan_status()
    lib.scan_start([str(other)])
    after = lib.scan_status()
    assert after.get("started_at") == before.get("started_at"), (
        "a refused scan_start replaced the running scan's state")
    assert after.get("roots") == before.get("roots"), (
        "a refused scan_start rewrote the running scan's roots")


def test_a_normal_sequential_scan_still_works(lib, tmp_path):
    """The over-correction guard.

    'Refuse whenever a thread object exists' would pass every test above and
    break the ordinary case, where one scan follows another.
    """
    small = tmp_path / "small"
    small.mkdir()
    (small / "a.mp4").write_bytes(b"x")

    first = scan_wait.start_and_wait(lib, [str(small)])
    assert first.get("finished_at") is not None

    (small / "b.mp4").write_bytes(b"x")
    second = scan_wait.start_and_wait(lib, [str(small)])
    assert second.get("finished_at") is not None, (
        "a second scan after a cleanly finished first one was refused")
    assert second.get("started_at") != first.get("started_at"), (
        "the second scan reused the first scan's state")


def test_the_cancel_path_still_reaches_the_running_worker(lib, tmp_path):
    """Binding the state must not sever scan_cancel.

    scan_cancel sets `cancelled` on the module global; the worker is bound to
    the SAME object, so the flag still reaches it. If the fix had copied the
    state instead of passing the reference, cancel would silently stop working
    and every test above would still pass.
    """
    root = _big_tree(tmp_path)
    lib.scan_start([root])
    scan_wait.wait_for_progress(lib)

    assert lib.scan_cancel() is True
    t = lib._scan_thread
    if t is not None:
        t.join(timeout=30)
        assert not t.is_alive(), (
            "the worker did not stop after scan_cancel -- the cancel flag no "
            "longer reaches the thread, which binding the state must not have "
            "broken")
    assert lib.scan_status().get("finished_at") is not None, (
        "the cancelled worker never recorded finished_at")
