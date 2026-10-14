"""ab-fix (bughunt adapters-browser FINDINGS.md, finder bd-worker-S32-A): one RED/GREEN test per fixed finding.

F2 run_capture_flow shared one run_capture_<host>.sh between captures of one host; F3 teach_commit answered
"Selectors saved" when nothing was saved; F4 get_child_pids read only the main thread's children; F5 a sid holding
three double quotes produced a template that parsed but could not import; F6 BrowserContextPool handed a dead
thread's idle context to a new thread that reused its ident. Real bash, real /proc, real threads; no browser.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib.util
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ---- F2 ----------------------------------------------------------------------------------------------------------


def _capture_info(scratch: Path, tag: str, capture_s: float) -> dict:
    log = scratch / "steps.log"
    return {
        "host": "example.com",  # the SAME host for both captures
        "display": ":99",
        "wacz": str(scratch / f"example.com_{tag}_20261013_140000_{tag}.wacz"),
        "draft": str(scratch / f"{tag}.json"),
        "capture_cmd": ["bash", "-c", f"sleep {capture_s}"],
        # $PPID of the step = the wrapper bash running the script: names WHICH capture ran this draft step
        "draft_cmd": ["bash", "-c", f"echo DRAFT-{tag} wrapper=$PPID >> {log}"],
    }


def test_f2_two_captures_of_one_host_each_run_their_own_steps(tmp_path, monkeypatch):
    import tools.onboard_site_template as onboard

    monkeypatch.setattr(onboard, "CAPTURE_DIR", tmp_path)
    a, b = _capture_info(tmp_path, "A", 2.0), _capture_info(tmp_path, "B", 0)
    pid_a = onboard.run_capture_flow(a, run=True)
    time.sleep(
        0.7
    )  # A's bash is inside its [1/2] capture step when B's script is written
    pid_b = onboard.run_capture_flow(b, run=True)
    for pid in (pid_a, pid_b):
        os.waitpid(pid, 0)
    steps = (tmp_path / "steps.log").read_text().split("\n")
    ran = {
        line.split(" wrapper=")[1]: line.split(" ")[0]
        for line in steps
        if " wrapper=" in line
    }
    assert ran.get(str(pid_a)) == "DRAFT-A", (
        f"F2: CAPTURE A RAN ANOTHER CAPTURE'S STEPS: {ran} (A={pid_a} B={pid_b})"
    )
    assert ran.get(str(pid_b)) == "DRAFT-B", ran
    scripts = sorted(p.name for p in tmp_path.glob("run_capture_*.sh"))
    assert len(scripts) == 2, scripts


# ---- F3 ----------------------------------------------------------------------------------------------------------

TEACH = "https://example.invalid/ab-fix-teach-target"


class _Session:
    target_url = TEACH

    def commit(self, timeout=None):
        return True, []


@pytest.fixture
def runner(clean_workdir, monkeypatch):
    from bulk_downloader import run_history
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    monkeypatch.setenv("MOD3_PG_DSN", "")
    monkeypatch.setenv("MOD3_SHADOW_READ", "0")
    db_init()
    run_history.init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    r = SiteRunner("abfix", {"name": "abfix"})
    r._drain_url_queue()
    monkeypatch.setattr(r, "start", lambda *a, **k: None)
    r.jobs[TEACH] = {
        "status": "needs_review",
        "message": "teach me",
        "auto_teach_seen": True,
    }
    r._manual_download_session = _Session()
    return r


def test_f3_teach_commit_with_nothing_picked_does_not_report_selectors_saved(runner):
    ok, msg = runner.teach_commit({})
    assert msg != "Selectors saved", (
        f"F3: NOTHING LEARNED REPORTED AS SELECTORS SAVED: {(ok, msg)}"
    )
    assert ok is True and "No selectors saved" in msg, (ok, msg)
    assert runner.jobs[TEACH]["message"] == "Teach Mode commit — no selectors saved", (
        runner.jobs[TEACH]
    )


def test_f3_teach_commit_whose_merge_raises_is_not_ok(runner, monkeypatch):
    from bulk_downloader import learn

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(learn, "merge_learned", boom)
    ok, msg = runner.teach_commit({"trigger_selectors": [".download"]})
    assert ok is False, f"F3: A FAILED MERGE REPORTED AS SAVED: {(ok, msg)}"
    assert "Selectors NOT saved" in msg and "disk full" in msg, msg


def test_f3_teach_commit_whose_real_config_write_fails_is_not_ok(
    runner, monkeypatch, tmp_path
):
    """ab-fix r2 (cx157 F3): app._save_sites_config catches its own OSError and returns False; that return is the
    only failure signal. The REAL writer is made to fail (its temp path is a directory) after a REAL successful
    write proves the probe can see a saved selector on disk."""
    from bulk_downloader import app

    sites = tmp_path / "sites.json"
    monkeypatch.setattr(app, "SITES_FILE", sites)
    monkeypatch.setitem(app.s_cfg, runner.site_id, runner.config)
    monkeypatch.setitem(app.s_meta, runner.site_id, {})
    ok, msg = runner.teach_commit({"trigger_selectors": [".positive-control"]})
    assert (ok, msg) == (True, "Selectors saved"), (ok, msg)
    assert ".positive-control" in sites.read_text(), (
        "control: the real writer did not persist the selector"
    )
    before = sites.read_bytes()
    sites.with_suffix(
        ".json.tmp"
    ).mkdir()  # the writer's tmp file cannot be opened: IsADirectoryError inside it
    runner.jobs[TEACH] = {
        "status": "needs_review",
        "message": "teach me",
        "auto_teach_seen": True,
    }
    runner._manual_download_session = _Session()
    ok, msg = runner.teach_commit({"trigger_selectors": [".failed-write"]})
    assert sites.read_bytes() == before, (
        "control: the write was expected to fail and leave the file unchanged"
    )
    assert ok is False, f"F3: A FAILED CONFIG WRITE REPORTED AS SAVED: {(ok, msg)}"
    assert "Selectors NOT saved" in msg and "sites config write failed" in msg, msg
    assert runner.jobs[TEACH]["status"] != "done", (
        f"F3: FAILED PERSISTENCE MARKED TARGET DONE: {runner.jobs[TEACH]}"
    )


_FAILED_MSG = "Teach Mode commit failed — selectors NOT saved; retry to resume teach flow"
OTHER = "https://example.invalid/ab-fix-blocked-sibling"


def _break_persistence(failure, monkeypatch, sites):
    """Five ways the selector can fail to reach the durable config (ab-fix r3, cx171 F3)."""
    import errno

    from bulk_downloader import app, learn

    def boom(*a, **k):
        raise OSError(errno.ENOSPC if failure == "disk_full" else errno.EROFS, failure)

    tmp = sites.with_suffix(".json.tmp")
    if failure == "real_write":
        tmp.mkdir()  # the real writer cannot open its temp file
    elif failure == "merge":
        monkeypatch.setattr(learn, "merge_learned", boom)
    elif failure == "raised_save":
        monkeypatch.setattr(app, "_save_sites_config", boom)
    else:  # disk_full / read_only: the error is raised INSIDE the real writer, at its temp path
        real_write = Path.write_text

        def fail_target(path, *a, **k):
            if str(path) == str(tmp):
                return boom()
            return real_write(path, *a, **k)

        monkeypatch.setattr(Path, "write_text", fail_target)


@pytest.mark.parametrize(
    "failure", ["real_write", "raised_save", "merge", "disk_full", "read_only"]
)
@pytest.mark.parametrize("auto_teach", [True, False])
def test_f3_failed_persistence_leaves_the_target_unfinished(
    runner, monkeypatch, tmp_path, failure, auto_teach
):
    """ab-fix r3 (cx171 F3): r2 answered ok=False but still marked the target done and re-queued every blocked
    URL as "after teach completion". A commit whose pick never reached the config finishes nothing: the target
    is never done (auto-teach: back to pending and queued; manual: status kept), blocked siblings stay blocked.
    The same runner first proves a real save -> selector on disk, target done, sibling re-queued."""
    from bulk_downloader import app

    sites = tmp_path / "sites.json"
    monkeypatch.setattr(app, "SITES_FILE", sites)
    monkeypatch.setattr(app, "s_cfg", {runner.site_id: runner.config})
    monkeypatch.setattr(app, "s_meta", {runner.site_id: {}})
    blocked = {"status": "needs_review", "message": "blocked", "auto_teach_seen": True}
    runner.jobs[OTHER] = dict(blocked)
    assert runner.teach_commit({"trigger_selectors": [".positive"]}) == (
        True,
        "Selectors saved",
    )
    assert ".positive" in sites.read_text(), "control: real writer did not persist"
    assert runner.jobs[TEACH]["status"] == "done", runner.jobs[TEACH]
    assert runner.jobs[OTHER]["status"] == "pending", (
        f"control: a saved commit did not re-queue the blocked sibling: {runner.jobs[OTHER]}"
    )
    before = sites.read_bytes()
    runner._drain_url_queue()
    start = "needs_review" if auto_teach else "paused"
    runner.jobs[TEACH] = {"status": start, "message": "teach me", "auto_teach_seen": auto_teach}
    runner.jobs[OTHER] = dict(blocked)
    runner._manual_download_session = _Session()
    _break_persistence(failure, monkeypatch, sites)

    ok, msg = runner.teach_commit({"trigger_selectors": [".failed"]})

    assert ok is False and "Selectors NOT saved" in msg, (ok, msg)
    assert sites.read_bytes() == before, "control: the durable config changed"
    job = runner.jobs[TEACH]
    assert job["status"] != "done", f"F3: FAILED PERSISTENCE MARKED TARGET DONE: {job}"
    assert job["status"] == ("pending" if auto_teach else start), (
        f"F3: FAILED COMMIT LEFT THE TARGET IN THE WRONG STATE: {job}"
    )
    assert job["message"] == _FAILED_MSG, f"F3: TARGET MESSAGE DOES NOT SAY NOT SAVED: {job}"
    assert runner.jobs[OTHER] == blocked, (
        f"F3: FAILED COMMIT RE-QUEUED A BLOCKED SIBLING AS IF TEACH COMPLETED: {runner.jobs[OTHER]}"
    )
    queued = []
    while not runner._url_queue.empty():
        queued.append(runner._url_queue.get_nowait())
    assert queued == ([TEACH] if auto_teach else []), (
        f"F3: FAILED COMMIT QUEUE IS WRONG: {queued}"
    )


def test_f3_failed_persistence_creates_no_job_row_for_an_unqueued_target(
    runner, monkeypatch
):
    """A manual teach on a URL with no job: a failed commit must not invent a finished (or any) row for it."""
    from bulk_downloader import learn

    def boom(*a, **k):
        raise OSError("disk full")

    del runner.jobs[TEACH]
    monkeypatch.setattr(learn, "merge_learned", boom)
    ok, msg = runner.teach_commit({"trigger_selectors": [".download"]})
    assert ok is False and "Selectors NOT saved" in msg, (ok, msg)
    assert TEACH not in runner.jobs, (
        f"F3: FAILED COMMIT CREATED A JOB ROW: {runner.jobs.get(TEACH)}"
    )


def test_f3_control_teach_commit_with_a_pick_reports_selectors_saved(
    runner, monkeypatch
):
    from bulk_downloader import learn

    calls = []
    monkeypatch.setattr(
        learn, "merge_learned", lambda cfg, sels, kind: calls.append((kind, sels))
    )
    ok, msg = runner.teach_commit({"trigger_selectors": [".download"]})
    assert (ok, msg) == (True, "Selectors saved") and len(calls) == 1, (ok, msg, calls)
    assert runner.jobs[TEACH]["message"] == "Teach Mode commit — selectors saved", (
        runner.jobs[TEACH]
    )


# ---- F4 ----------------------------------------------------------------------------------------------------------

_PARENT = r"""
import subprocess, sys, threading
kids, ready = [], threading.Event()
def from_thread():  # forks, then stays alive like Chromium's network-service launcher thread
    kids.append(subprocess.Popen(["sleep", "60"]).pid)
    ready.set()
    threading.Event().wait()
threading.Thread(target=from_thread, daemon=True).start()
ready.wait()
kids.append(subprocess.Popen(["sleep", "60"]).pid)  # main-thread child
print(kids[0], kids[1], flush=True)
sys.stdin.read()
"""


def test_f4_get_child_pids_sees_a_child_forked_by_a_non_main_thread():
    from bulk_downloader.browser_sentinel import get_child_pids

    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        thread_kid, main_kid = (int(x) for x in parent.stdout.readline().split())
        got = get_child_pids(parent.pid, recursive=True)
        assert main_kid in got, f"control: the main-thread child must be found: {got}"
        assert thread_kid in got, (
            f"F4: CHILD OF A NON-MAIN THREAD MISSED: got={got} thread_kid={thread_kid}"
        )
    finally:
        for pid in get_child_pids(parent.pid, recursive=True):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        parent.kill()
        parent.wait(timeout=10)


# ---- F5 ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sid", ['x"""+str(__import__("os").getpid())+"""', 'a"""b', 'x"', "plain-site"]
)  # the first is the finder's: base ast.parse accepts it, compile refuses it
def test_f5_a_written_synthesized_template_imports_whatever_the_sid(tmp_path, sid):
    from bulk_downloader.app_sites_teach import write_synthesized_template

    path, class_name, _src = write_synthesized_template(
        sid, {"row_selectors": ["a.dl"]}, dest_dir=tmp_path
    )
    try:
        compile(path.read_text(), str(path), "exec", dont_inherit=True)
    except SyntaxError as e:
        pytest.fail(f"F5: WRITTEN TEMPLATE CANNOT IMPORT (sid={sid!r}): {e}")
    spec = importlib.util.spec_from_file_location(f"abfix_{abs(hash(sid))}", path)
    mod = importlib.util.module_from_spec(spec)
    # The module table is handed back as found: whatever sat under this name
    # before the test (normally nothing) is what sits there after it.
    saved_modules = {spec.name: sys.modules[spec.name]} if spec.name in sys.modules else {}
    sys.modules[spec.name] = (
        mod  # @dataclass resolves the class's module through sys.modules
    )
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
        sys.modules.update(saved_modules)
    assert getattr(mod, class_name)().row_selectors == ["a.dl"]
    assert repr(sid).replace('"', "") in (mod.__doc__ or "").replace('"', "").replace(
        "\\", ""
    ), mod.__doc__


def test_f5_the_write_gate_refuses_source_that_parses_but_cannot_compile(
    tmp_path, monkeypatch
):
    """ab-fix F5 gate: ast.parse accepts a `from __future__` import after a statement; compile() does not. A source
    like that must raise before the write -- no file lands."""
    from bulk_downloader import app_sites_teach as teach

    bad = "x = 1\nfrom __future__ import annotations\n"
    monkeypatch.setattr(
        teach,
        "synthesize_template_source",
        lambda sid, picks=None, raw_events=None: ("C", "_data_gate_auto", bad),
    )
    with pytest.raises(SyntaxError):
        teach.write_synthesized_template(
            "gate", {"row_selectors": ["a"]}, dest_dir=tmp_path
        )
    assert not list(tmp_path.iterdir()), (
        f"F5: A SOURCE THAT CANNOT IMPORT WAS WRITTEN: {list(tmp_path.iterdir())}"
    )


# ---- F6 ----------------------------------------------------------------------------------------------------------


class _Ctx:
    def __init__(self):
        self.pages, self.closed, self.created_on = [], False, threading.current_thread()

    def clear_cookies(self):
        pass

    def storage_state(self):
        return {"cookies": [], "origins": []}

    def close(self):
        self.closed = True


def test_f6_a_new_thread_never_gets_a_dead_threads_idle_context():
    from bulk_downloader.browser_pool import BrowserContextPool

    pool = BrowserContextPool(_Ctx)
    handed, same_ident = [], 0
    for _ in range(50):
        box = queue.Queue()

        def use(box=box):
            with pool.acquire() as ctx:
                box.put((threading.get_ident(), ctx))

        a = threading.Thread(target=use)
        a.start()
        a.join()
        ident_a, ctx_a = box.get()
        b = threading.Thread(target=use)
        b.start()
        b.join()
        ident_b, ctx_b = box.get()
        same_ident += ident_a == ident_b
        if ctx_b is ctx_a:
            handed.append(ident_b)
    assert same_ident, (
        "control: CPython reused no thread ident in 50 trials -- the probe could not say yes"
    )
    assert not handed, (
        f"F6: DEAD THREAD'S CONTEXT HANDED TO A NEW THREAD in {len(handed)}/50 trials"
    )
    pool.close()
