"""Importing bulk_downloader.app must not touch the database.

Item 11, and it is a DATA-INTEGRITY defect that happens to also cap concurrency
-- not the other way round. SESSION_CARRY 15.48 filed it as "all-parallel
aborts at collection at -n 64+", which is the symptom. 15.49 records what it
actually cost: on 2026-08-07 the operator's live history DB was quarantined ten
times in twenty-five minutes and replaced with an empty one, because

  * `install_service.sh:214` sets `WorkingDirectory=${APP_DIR}` and
    `constants.py:24` is a BARE RELATIVE `DB_PATH`, so the service's DB and a
    pytest run started from the deploy directory are THE SAME FILE;
  * `conftest.py`'s `clean_workdir` is opt-in, not autouse -- a test is
    isolated only if it asks;
  * and app.py ran db_init() plus four more DB operations at MODULE SCOPE, so
    every xdist worker did that work concurrently while merely COLLECTING.

`-m` marker filtering happens after collection, so no lane assignment can
prevent it: measured, 22 tracked test files import `bulk_downloader.app` at
module scope, and every worker imports all of them.

THE FIX IS DEFERRED-AND-IDEMPOTENT, NOT SUPPRESSED, and the distinction is the
whole cut. Gating the boot on BD_DISABLE_KEEPALIVE would make capture green and
leave a latch: any test that genuinely needs a booted DB would get an
unmigrated one, silently, and the failure would surface far from here. That
shape -- a guard that satisfies the test by removing the capability -- is what
held v3.66.919 back and is exactly CLAUDE.md section 0's inverse defect.

So the assertions run in BOTH directions:

    NEG  importing the module creates no database file
    POS  boot_once() really does create the schema
    POS  an ordinary request boots it, so the service is unchanged
         (downloader_ui.py:217 already calls db_init() explicitly, so the
         service never depended on the import side effect in the first place)
    POS  concurrent callers do the work exactly once

The import assertions run in a SUBPROCESS. `bulk_downloader.app` is almost
certainly already in sys.modules by the time this file runs, so an in-process
import is a no-op that would pass on the broken tree -- a test that cannot
observe its subject, testing the module cache instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PY = _REPO / "venv" / "bin" / "python"


def _run_isolated(body: str, tmp_path: Path, **env_extra) -> subprocess.CompletedProcess:
    """Run `body` in a fresh interpreter whose DB would land in tmp_path.

    BD_INSTALL_DIR *and* cwd are both set, belt and braces, because
    db._resolve_db_path() consults BD_INSTALL_DIR first and falls back to a
    cwd-relative path -- CLAUDE.md section 5. Getting only one of them lets a
    stray DB land in the repo, which is gitignored and therefore silent.
    """
    env = dict(os.environ)
    env["BD_INSTALL_DIR"] = str(tmp_path)
    env["BD_HOME"] = str(tmp_path)
    env.pop("BD_TEST_MODE", None)
    # INHERITED FLAGS MUST BE CLEARED, and this line is the whole reason the
    # "without the keepalive flag" case is meaningful. `dict(os.environ)`
    # carries whatever the pytest invocation exported, and every band in this
    # repo runs `BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest ...` -- so
    # the subprocess silently inherited the flag and the test that exists to
    # check the UNFLAGGED path was checking the flagged one. It passed, over a
    # denominator that excluded its subject. Caught by an adversarial review
    # agent, not by the test suite and not by review.
    env.pop("BD_DISABLE_KEEPALIVE", None)
    env.update(env_extra)
    interp = _PY if _PY.exists() else Path(sys.executable)
    return subprocess.run(
        [str(interp), "-c", textwrap.dedent(body)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=180,
    )


_LIST_DB = """
    import sys, os, glob
    sys.path.insert(0, {repo!r})
    import bulk_downloader.app          # the subject
    found = sorted(glob.glob('*.db') + glob.glob('*.db-wal') + glob.glob('*.db-shm'))
    print('DBFILES=' + ','.join(found))
"""


_CONFIGURED_SITE_ID = "collection_sentinel"


def _plant_configured_site(tmp_path: Path) -> None:
    """Write the real on-disk shape consumed by app._load_sites_config()."""
    (tmp_path / "sites_config.json").write_text(json.dumps({
        _CONFIGURED_SITE_ID: {
            "name": "Collection Sentinel",
            "url": "https://example.invalid",
            "download_dir": str(tmp_path / "downloads"),
            "headless": True,
            "vpn_enabled": True,
            "vpn_tunnel_id": "tun-collection",
            "vpn_required": True,
        },
    }), encoding="utf-8")


def _keyed_output(cp: subprocess.CompletedProcess) -> dict[str, str]:
    return dict(line.split("=", 1) for line in cp.stdout.splitlines()
                if "=" in line)


_CONFIGURED_IMPORT_STATE = """
    import glob, os, sys, threading
    sys.path.insert(0, {repo!r})
    import bulk_downloader.app as A
    from bulk_downloader import vpn_runtime

    bd_threads = sorted(
        t.name for t in threading.enumerate()
        if t.name.startswith('auto-retry-') or t.name == 'bd-folder-watcher')
    print('SITES=' + ','.join(sorted(A.s_cfg)))
    print('RUNNERS=' + ','.join(sorted(A.runners)))
    print('DBFILES=' + ','.join(sorted(
        glob.glob('*.db') + glob.glob('*.db-wal') + glob.glob('*.db-shm'))))
    print('SCREENSHOT=%s' % os.path.isdir('screenshots/collection_sentinel'))
    print('BD_THREADS=' + ','.join(bd_threads))
    print('VPN_TUNNEL=%s' % (vpn_runtime.get_tunnel_for_site(
        'collection_sentinel') or ''))
"""


@pytest.mark.parametrize("keepalive", ["1", None],
                         ids=["keepalive-disabled", "service-environment"])
def test_importing_app_does_not_activate_a_configured_site(tmp_path, keepalive):
    """A configured host must not restore runtime state during collection.

    Pytest imports app-using test modules before function-scoped
    isolation runs.  If a bare import calls _load_sites_config(), SiteRunner's
    constructor restores its queue, creates its screenshot directory, and starts
    auto-retry against whichever install directory collection inherited.
    """
    _plant_configured_site(tmp_path)
    env = {"BD_DISABLE_VPN_RUNTIME": "0"}
    if keepalive is not None:
        env["BD_DISABLE_KEEPALIVE"] = keepalive
    cp = _run_isolated(
        _CONFIGURED_IMPORT_STATE.format(repo=str(_REPO)), tmp_path,
        **env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("SITES") == "", cp.stdout
    assert out.get("RUNNERS") == "", cp.stdout
    assert out.get("DBFILES") == "", cp.stdout
    assert out.get("SCREENSHOT") == "False", cp.stdout
    assert out.get("BD_THREADS") == "", cp.stdout
    assert out.get("VPN_TUNNEL") == "", cp.stdout


def test_boot_once_activates_a_configured_site_exactly_once(tmp_path):
    """Deferral preserves startup and force-reboot cannot duplicate runners."""
    _plant_configured_site(tmp_path)
    cp = _run_isolated("""
        import glob, os, sqlite3, sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        import bulk_downloader.db as D
        from bulk_downloader import vpn_runtime

        first = A.boot_once()
        runner = A.runners.get('collection_sentinel')
        runner_id = id(runner)
        forced = A.boot_once(force=True)
        second_db = os.path.join(os.getcwd(), 'second', 'history.db')
        os.makedirs(os.path.dirname(second_db), exist_ok=True)
        D.DB_PATH = second_db
        changed_path = A.boot_once()
        auto_threads = [
            t for t in threading.enumerate()
            if t.name == 'auto-retry-collection_sentinel']
        watcher_threads = [
            t for t in threading.enumerate()
            if t.name == 'bd-folder-watcher']
        found = sorted(glob.glob('*.db'))
        cx = sqlite3.connect(found[0]) if found else None
        names = sorted(r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")) if cx else []
        if cx: cx.close()
        cx2 = sqlite3.connect(second_db)
        names2 = sorted(r[0] for r in cx2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        cx2.close()
        print('FIRST=%s' % first)
        print('FORCED=%s' % forced)
        print('CHANGED_PATH=%s' % changed_path)
        print('SITES=' + ','.join(sorted(A.s_cfg)))
        print('RUNNERS=' + ','.join(sorted(A.runners)))
        print('SAME_RUNNER=%s' % (id(A.runners.get('collection_sentinel')) == runner_id))
        print('AUTO_THREADS=%d' % len(auto_threads))
        print('WATCHER_THREADS=%d' % len(watcher_threads))
        print('SCREENSHOT=%s' % os.path.isdir('screenshots/collection_sentinel'))
        print('HAS_HISTORY=%s' % ('history' in names))
        print('CHANGED_HAS_HISTORY=%s' % ('history' in names2))
        print('VPN_TUNNEL=%s' % (vpn_runtime.get_tunnel_for_site(
            'collection_sentinel') or ''))
        if runner is not None:
            runner.stop()
            runner._stop_auto_retry()
        A._watcher_stop.set()
        if A._watcher_thread:
            A._watcher_thread.join(timeout=2)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="0")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("FIRST") == "True", cp.stdout
    assert out.get("FORCED") == "True", cp.stdout
    assert out.get("CHANGED_PATH") == "True", cp.stdout
    assert out.get("SITES") == _CONFIGURED_SITE_ID, cp.stdout
    assert out.get("RUNNERS") == _CONFIGURED_SITE_ID, cp.stdout
    assert out.get("SAME_RUNNER") == "True", cp.stdout
    assert out.get("AUTO_THREADS") == "1", cp.stdout
    assert out.get("WATCHER_THREADS") == "1", cp.stdout
    assert out.get("SCREENSHOT") == "True", cp.stdout
    assert out.get("HAS_HISTORY") == "True", cp.stdout
    assert out.get("CHANGED_HAS_HISTORY") == "True", cp.stdout
    assert out.get("VPN_TUNNEL") == "tun-collection", cp.stdout


def test_boot_rejects_a_different_sites_file_without_replacing_live_runtime(tmp_path):
    """One process cannot silently bind one live runner set to two configs."""
    _plant_configured_site(tmp_path)
    second_config = tmp_path / "second_sites_config.json"
    second_config.write_text(json.dumps({
        "collection_second": {
            "name": "Collection Second",
            "url": "https://second.example.invalid",
            "download_dir": str(tmp_path / "downloads-second"),
            "headless": True,
        },
    }), encoding="utf-8")
    cp = _run_isolated("""
        import sys
        from pathlib import Path
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A

        first = A.boot_once()
        original_runner = A.runners.get('collection_sentinel')
        original_sites_file = A.SITES_FILE
        A.SITES_FILE = Path('second_sites_config.json')
        try:
            A.boot_once()
        except RuntimeError as exc:
            failure_type = type(exc).__name__
        else:
            failure_type = 'missing'
        A.SITES_FILE = original_sites_file
        original_again = A.boot_once()
        A.SITES_FILE = Path('second_sites_config.json')
        try:
            A.boot_once()
        except RuntimeError as exc:
            repeated_failure_type = type(exc).__name__
        else:
            repeated_failure_type = 'missing'
        print('FIRST=%s' % first)
        print('FAILURE_TYPE=%s' % failure_type)
        print('ORIGINAL_AGAIN=%s' % original_again)
        print('REPEATED_FAILURE_TYPE=%s' % repeated_failure_type)
        print('SITES=' + ','.join(sorted(A.s_cfg)))
        print('RUNNERS=' + ','.join(sorted(A.runners)))
        print('SAME_RUNNER=%s' % (
            A.runners.get('collection_sentinel') is original_runner))
        if original_runner is not None:
            original_runner.stop_scheduler()
            original_runner.stop()
        A._watcher_stop.set()
        if A._watcher_thread:
            A._watcher_thread.join(timeout=2)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("FIRST") == "True", cp.stdout
    assert out.get("FAILURE_TYPE") == "RuntimeError", cp.stdout
    assert out.get("ORIGINAL_AGAIN") == "False", cp.stdout
    assert out.get("REPEATED_FAILURE_TYPE") == "RuntimeError", cp.stdout
    assert out.get("SITES") == _CONFIGURED_SITE_ID, cp.stdout
    assert out.get("RUNNERS") == _CONFIGURED_SITE_ID, cp.stdout
    assert out.get("SAME_RUNNER") == "True", cp.stdout


def test_failed_configured_site_restore_rolls_back_before_retry(tmp_path):
    """A partial SiteRunner restore cannot survive an unlatchable boot."""
    _plant_configured_site(tmp_path)
    config_path = tmp_path / "sites_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config[_CONFIGURED_SITE_ID]["accounts"] = [{
        "username": "collection-user",
        "password": "collection-password",
    }]
    config["collection_second"] = {
        "name": "Collection Second",
        "url": "https://second.example.invalid",
        "download_dir": str(tmp_path / "downloads-second"),
        "headless": True,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    cp = _run_isolated("""
        import sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        from bulk_downloader import account_pool

        real_runner = A.SiteRunner
        failed = [False]
        def fail_second_once(site_id, config):
            if site_id == 'collection_second' and not failed[0]:
                failed[0] = True
                raise RuntimeError('planted second-runner failure')
            return real_runner(site_id, config)

        A.SiteRunner = fail_second_once
        try:
            A.boot_once()
        except RuntimeError as exc:
            print('FAILURE=%s' % exc)
        else:
            print('FAILURE=missing')
        failed_auto = [
            t for t in threading.enumerate()
            if t.name.startswith('auto-retry-collection_')]
        print('FAILED_SITES=' + ','.join(sorted(A.s_cfg)))
        print('FAILED_RUNNERS=' + ','.join(sorted(A.runners)))
        print('FAILED_AUTO=%d' % len(failed_auto))
        print('FAILED_POOLS=' + ','.join(sorted(
            item['site_id'] for item in account_pool.get_all_pools_status())))

        A.SiteRunner = real_runner
        retried = A.boot_once()
        retry_auto = [
            t for t in threading.enumerate()
            if t.name.startswith('auto-retry-collection_')]
        print('RETRIED=%s' % retried)
        print('RETRY_SITES=' + ','.join(sorted(A.s_cfg)))
        print('RETRY_RUNNERS=' + ','.join(sorted(A.runners)))
        print('RETRY_AUTO=%d' % len(retry_auto))
        print('RETRY_POOLS=' + ','.join(sorted(
            item['site_id'] for item in account_pool.get_all_pools_status())))
        for runner in list(A.runners.values()):
            runner.stop()
            runner._stop_auto_retry()
        A._watcher_stop.set()
        if A._watcher_thread:
            A._watcher_thread.join(timeout=2)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="0")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("FAILURE") == "planted second-runner failure", cp.stdout
    assert out.get("FAILED_SITES") == "", cp.stdout
    assert out.get("FAILED_RUNNERS") == "", cp.stdout
    assert out.get("FAILED_AUTO") == "0", cp.stdout
    assert out.get("FAILED_POOLS") == "", cp.stdout
    assert out.get("RETRIED") == "True", cp.stdout
    assert out.get("RETRY_SITES") == "collection_second,collection_sentinel", cp.stdout
    assert out.get("RETRY_RUNNERS") == "collection_second,collection_sentinel", cp.stdout
    assert out.get("RETRY_AUTO") == "2", cp.stdout
    assert out.get("RETRY_POOLS") == _CONFIGURED_SITE_ID, cp.stdout


def test_failed_dependent_startup_retires_before_clean_retry(tmp_path):
    """A dependent failure cannot leave request-capable runners published."""
    _plant_configured_site(tmp_path)
    cp = _run_isolated("""
        import sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A

        real_start = A._start_session_keepers
        calls = [0]
        def fail_once():
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('planted dependent-startup failure')
            return real_start()

        A._start_session_keepers = fail_once
        try:
            A.boot_once()
        except RuntimeError as exc:
            print('FAILURE=%s' % exc)
        else:
            print('FAILURE=missing')
        first_runner = A.runners.get('collection_sentinel')
        failed_auto = [
            t for t in threading.enumerate()
            if t.name == 'auto-retry-collection_sentinel']
        failed_complete = (
            sorted(A.s_cfg) == ['collection_sentinel'] and
            first_runner is not None)

        retried = A.boot_once()
        retry_auto = [
            t for t in threading.enumerate()
            if t.name == 'auto-retry-collection_sentinel']
        print('FAILED_COMPLETE=%s' % failed_complete)
        print('FAILED_AUTO=%d' % len(failed_auto))
        print('RETRIED=%s' % retried)
        print('START_CALLS=%d' % calls[0])
        print('SAME_RUNNER=%s' % (
            A.runners.get('collection_sentinel') is first_runner))
        print('RETRY_AUTO=%d' % len(retry_auto))
        print('LATCHED=%s' % (A.boot_once() is False))
        if first_runner is not None:
            first_runner.stop_scheduler()
            first_runner.stop()
        A._watcher_stop.set()
        if A._watcher_thread:
            A._watcher_thread.join(timeout=2)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="0")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("FAILURE") == "planted dependent-startup failure", cp.stdout
    assert out.get("FAILED_COMPLETE") == "False", cp.stdout
    assert out.get("FAILED_AUTO") == "0", cp.stdout
    assert out.get("RETRIED") == "True", cp.stdout
    assert out.get("START_CALLS") == "2", cp.stdout
    assert out.get("SAME_RUNNER") == "False", cp.stdout
    assert out.get("RETRY_AUTO") == "1", cp.stdout
    assert out.get("LATCHED") == "True", cp.stdout


def test_watch_folder_thread_start_failure_leaves_no_false_registry_entry(tmp_path):
    """A failed Thread.start must not make the retry skip that site forever."""
    _plant_configured_site(tmp_path)
    cp = _run_isolated("""
        import os, sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        from bulk_downloader.db import _resolve_db_path

        # Isolate this configured-site service from unrelated global daemons.
        A._start_background_services = lambda: None
        A._start_watcher = lambda: None
        A._start_session_keepers = lambda: None
        os.environ.pop('BD_DISABLE_KEEPALIVE', None)

        real_start = threading.Thread.start
        failed = [False]
        def fail_watch_folder_once(thread):
            if (thread.name == 'watch-folder-collection_sentinel'
                    and not failed[0]):
                failed[0] = True
                raise RuntimeError('planted watch-folder start failure')
            return real_start(thread)

        threading.Thread.start = fail_watch_folder_once
        try:
            A.boot_once()
        except RuntimeError as exc:
            print('FAILURE=%s' % exc)
        else:
            print('FAILURE=missing')
        finally:
            threading.Thread.start = real_start

        key = os.path.abspath(_resolve_db_path())
        print('FAILED_LATCHED=%s' % (key in A._BOOTED_PATHS))
        print('FAILED_ENTRY=%s' % (
            'collection_sentinel' in A._watch_threads))
        retried = A.boot_once()
        watcher = A._watch_threads.get('collection_sentinel')
        print('RETRIED=%s' % retried)
        print('RETRY_READY=%s' % A._SITE_RUNTIME_READY)
        print('RETRY_ALIVE=%s' % bool(watcher and watcher.is_alive()))
        stop = A._watch_stops.get('collection_sentinel')
        if stop is not None:
            stop.set()
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=2)
        runner = A.runners.get('collection_sentinel')
        if runner is not None:
            runner.stop_scheduler()
            runner.stop()
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("FAILURE") == "planted watch-folder start failure", cp.stdout
    assert out.get("FAILED_LATCHED") == "False", cp.stdout
    assert out.get("FAILED_ENTRY") == "False", cp.stdout
    assert out.get("RETRIED") == "True", cp.stdout
    assert out.get("RETRY_READY") == "True", cp.stdout
    assert out.get("RETRY_ALIVE") == "True", cp.stdout


def test_concurrent_watch_folder_starters_publish_exactly_one_worker(tmp_path):
    """Concurrent runtime updates cannot create an unregistered watcher."""
    _plant_configured_site(tmp_path)
    cp = _run_isolated("""
        import os, sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        from bulk_downloader import watch_folder as wf

        A._start_background_services = lambda: None
        A._start_watcher = lambda: None
        A._start_session_keepers = lambda: None
        os.environ.pop('BD_DISABLE_KEEPALIVE', None)
        A.boot_once()

        old_stop = A._watch_stops.pop('collection_sentinel', None)
        old_thread = A._watch_threads.pop('collection_sentinel', None)
        if old_stop is not None:
            old_stop.set()
        if old_thread is not None:
            old_thread.join(timeout=2)

        release_workers = threading.Event()
        wf.watch_loop_for_site = lambda _runner, _stop: release_workers.wait(2)
        real_start = threading.Thread.start
        start_gate = threading.Barrier(2)
        call_gate = threading.Barrier(3)
        started = []
        started_lock = threading.Lock()

        def gated_start(thread):
            if thread.name == 'watch-folder-collection_sentinel':
                with started_lock:
                    started.append(thread)
                try:
                    start_gate.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass
            return real_start(thread)

        def call_starter():
            call_gate.wait()
            A._start_watch_folder_threads()

        threading.Thread.start = gated_start
        callers = [threading.Thread(target=call_starter) for _ in range(2)]
        try:
            for caller in callers:
                caller.start()
            call_gate.wait()
            for caller in callers:
                caller.join(timeout=3)
        finally:
            threading.Thread.start = real_start

        registered = A._watch_threads.get('collection_sentinel')
        print('STARTED=%d' % len(started))
        print('REGISTERED=%d' % int(registered is not None))
        print('LIVE=%d' % sum(int(t.is_alive()) for t in started))
        release_workers.set()
        stop = A._watch_stops.get('collection_sentinel')
        if stop is not None:
            stop.set()
        for thread in started:
            thread.join(timeout=2)
        runner = A.runners.get('collection_sentinel')
        if runner is not None:
            runner.stop_scheduler()
            runner.stop()
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("STARTED") == "1", cp.stdout
    assert out.get("REGISTERED") == "1", cp.stdout
    assert out.get("LIVE") == "1", cp.stdout


def test_exited_watch_folder_worker_deregisters_and_retry_is_live(tmp_path):
    """An early target exit must not suppress every later watch retry."""
    _plant_configured_site(tmp_path)
    cp = _run_isolated("""
        import os, sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        from bulk_downloader import watch_folder as wf

        A._start_background_services = lambda: None
        A._start_watcher = lambda: None
        A._start_session_keepers = lambda: None
        os.environ.pop('BD_DISABLE_KEEPALIVE', None)
        A.boot_once()

        old_stop = A._watch_stops.pop('collection_sentinel', None)
        old_thread = A._watch_threads.pop('collection_sentinel', None)
        if old_stop is not None:
            old_stop.set()
        if old_thread is not None:
            old_thread.join(timeout=2)

        created = []
        real_start = threading.Thread.start
        def remember_start(thread):
            if thread.name == 'watch-folder-collection_sentinel':
                created.append(thread)
            return real_start(thread)
        threading.Thread.start = remember_start
        try:
            wf.watch_loop_for_site = lambda _runner, _stop: None
            A._start_watch_folder_threads()
            first = created[-1]
            first.join(timeout=2)
            print('EXITED_ENTRY=%s' % (
                'collection_sentinel' in A._watch_threads))

            release = threading.Event()
            wf.watch_loop_for_site = lambda _runner, _stop: release.wait(2)
            A._start_watch_folder_threads()
            second = A._watch_threads.get('collection_sentinel')
            print('REPLACED=%s' % bool(second is not None and second is not first))
            print('RETRY_ALIVE=%s' % bool(second and second.is_alive()))
            release.set()
            stop = A._watch_stops.get('collection_sentinel')
            if stop is not None:
                stop.set()
            if second is not None:
                second.join(timeout=2)
        finally:
            threading.Thread.start = real_start
        runner = A.runners.get('collection_sentinel')
        if runner is not None:
            runner.stop_scheduler()
            runner.stop()
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("EXITED_ENTRY") == "False", cp.stdout
    assert out.get("REPLACED") == "True", cp.stdout
    assert out.get("RETRY_ALIVE") == "True", cp.stdout


def test_starter_overlapping_watch_target_exit_hands_off_to_new_worker(tmp_path):
    """A starter that sees the old target in finalization cannot be lost."""
    _plant_configured_site(tmp_path)
    cp = _run_isolated("""
        import os, sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        from bulk_downloader import watch_folder as wf

        A._start_background_services = lambda: None
        A._start_watcher = lambda: None
        A._start_session_keepers = lambda: None
        os.environ.pop('BD_DISABLE_KEEPALIVE', None)
        A.boot_once()

        old_stop = A._watch_stops.pop('collection_sentinel', None)
        old_thread = A._watch_threads.pop('collection_sentinel', None)
        if old_stop is not None:
            old_stop.set()
        if old_thread is not None:
            old_thread.join(timeout=2)

        allow_first_return = threading.Event()
        first_body_done = threading.Event()
        second_started = threading.Event()
        release_second = threading.Event()
        calls = []

        def controlled_loop(_runner, _stop):
            calls.append(threading.current_thread())
            if len(calls) == 1:
                allow_first_return.wait(2)
                first_body_done.set()
                return
            second_started.set()
            release_second.wait(2)

        wf.watch_loop_for_site = controlled_loop
        A._start_watch_folder_threads()
        first = A._watch_threads['collection_sentinel']

        # Keep the old target blocked in its registry-finally while the
        # overlapping starter observes that Thread.is_alive() is still true.
        with A._watch_registry_lock:
            allow_first_return.set()
            assert first_body_done.wait(2)
            assert first.is_alive()
            A._start_watch_folder_threads()

        assert second_started.wait(2)
        second = A._watch_threads.get('collection_sentinel')
        print('OLD_ALIVE=%s' % first.is_alive())
        print('REPLACED=%s' % bool(second is not None and second is not first))
        print('REGISTERED=%s' % bool(second is not None and second.is_alive()))
        print('STOP_REGISTERED=%s' % (
            'collection_sentinel' in A._watch_stops))

        release_second.set()
        stop = A._watch_stops.get('collection_sentinel')
        if stop is not None:
            stop.set()
        first.join(timeout=2)
        if second is not None:
            second.join(timeout=2)
        runner = A.runners.get('collection_sentinel')
        if runner is not None:
            runner.stop_scheduler()
            runner.stop()
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("REPLACED") == "True", cp.stdout
    assert out.get("REGISTERED") == "True", cp.stdout
    assert out.get("STOP_REGISTERED") == "True", cp.stdout


def test_watch_start_racing_delete_cannot_publish_an_orphan(tmp_path):
    """Delete detaches the runner and watcher under one lifecycle lock."""
    cp = _run_isolated("""
        import os, sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        from bulk_downloader import app_sites_id_core as site_routes
        from bulk_downloader import watch_folder as wf

        sid = 'delete-race'
        os.environ.pop('BD_DISABLE_KEEPALIVE', None)

        class Runner:
            def retire_scheduler(self, timeout=12.0):
                return True
            def retire_auto_retry(self, timeout=2.0):
                return True
            def retire_workers(self, timeout=5.0):
                self.stop()
                return True
            def stop(self):
                pass
            def _stop_auto_retry(self):
                pass

        A.runners[sid] = Runner()
        A.s_cfg[sid] = {{'name': 'Delete Race'}}
        A.s_meta[sid] = {{'name': 'Delete Race'}}
        wf.watch_loop_for_site = lambda _runner, stop: stop.wait(2)

        real_start = threading.Thread.start
        start_entered = threading.Event()
        allow_start = threading.Event()
        delete_entered = threading.Event()
        delete_done = threading.Event()
        started = []

        def gated_start(thread):
            if thread.name == 'watch-folder-delete-race':
                started.append(thread)
                start_entered.set()
                allow_start.wait(2)
            return real_start(thread)

        def delete_site():
            delete_entered.set()
            with A.app.test_request_context('/api/sites/' + sid,
                                            method='DELETE'):
                site_routes.api_delete(sid)
            delete_done.set()

        threading.Thread.start = gated_start
        starter = threading.Thread(target=A._start_watch_folder_threads)
        deleter = threading.Thread(target=delete_site)
        try:
            starter.start()
            assert start_entered.wait(2)
            deleter.start()
            assert delete_entered.wait(2)
            delete_done.wait(0.25)
            allow_start.set()
            starter.join(timeout=3)
            deleter.join(timeout=3)
        finally:
            threading.Thread.start = real_start

        print('RUNNER_PRESENT=%s' % (sid in A.runners))
        print('THREAD_PRESENT=%s' % (sid in A._watch_threads))
        print('STOP_PRESENT=%s' % (sid in A._watch_stops))
        print('LIVE=%d' % sum(int(thread.is_alive()) for thread in started))
        stop = A._watch_stops.get(sid)
        if stop is not None:
            stop.set()
        for thread in started:
            thread.join(timeout=2)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("RUNNER_PRESENT") == "False", cp.stdout
    assert out.get("THREAD_PRESENT") == "False", cp.stdout
    assert out.get("STOP_PRESENT") == "False", cp.stdout
    assert out.get("LIVE") == "0", cp.stdout


def test_fresh_app_teardown_waits_for_detached_runner_workers(
        tmp_path, monkeypatch):
    """A process-reset fixture cannot discard its only join handles."""
    import threading
    import time
    import conftest as fixture_module
    import bulk_downloader.app as app_module

    fixture_gen = fixture_module.fresh_app.__wrapped__(tmp_path, monkeypatch)
    next(fixture_gen)

    class SlowCleanupRunner:
        def __init__(self):
            self.stop_event = threading.Event()
            self.worker = threading.Thread(target=self._finish_after_stop)
            self._worker_threads = [self.worker]
            self.worker.start()

        def _finish_after_stop(self):
            self.stop_event.wait(2)
            time.sleep(0.25)

        def stop_scheduler(self):
            pass

        def stop(self):
            self.stop_event.set()

        def _stop_auto_retry(self):
            pass

    runner = SlowCleanupRunner()
    app_module.runners["slow-cleanup"] = runner
    with pytest.raises(StopIteration):
        next(fixture_gen)
    try:
        assert not runner.worker.is_alive()
    finally:
        runner.stop_event.set()
        runner.worker.join(timeout=2)


def test_watcher_start_failure_leaves_boot_retryable(tmp_path):
    """A fallible final service start cannot publish a completed DB boot."""
    cp = _run_isolated("""
        import os, sys
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        from bulk_downloader.db import _resolve_db_path

        real_start = A._start_watcher
        calls = [0]
        def fail_once():
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('planted watcher-start failure')
            return real_start()

        A._start_watcher = fail_once
        try:
            A.boot_once()
        except RuntimeError as exc:
            print('FAILURE=%s' % exc)
        else:
            print('FAILURE=missing')
        key = os.path.abspath(_resolve_db_path())
        print('FAILED_LATCHED=%s' % (key in A._BOOTED_PATHS))
        retried = A.boot_once()
        print('RETRIED=%s' % retried)
        print('CALLS=%d' % calls[0])
        print('RETRY_LATCHED=%s' % (key in A._BOOTED_PATHS))
        print('WATCHER_ALIVE=%s' % bool(
            A._watcher_thread and A._watcher_thread.is_alive()))
        A._watcher_stop.set()
        if A._watcher_thread:
            A._watcher_thread.join(timeout=2)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out.get("FAILURE") == "planted watcher-start failure", cp.stdout
    assert out.get("FAILED_LATCHED") == "False", cp.stdout
    assert out.get("RETRIED") == "True", cp.stdout
    assert out.get("CALLS") == "2", cp.stdout
    assert out.get("RETRY_LATCHED") == "True", cp.stdout
    assert out.get("WATCHER_ALIVE") == "True", cp.stdout


def test_retired_global_watcher_cannot_be_replaced_before_next_boot(
        monkeypatch,
):
    import bulk_downloader.app as app_module

    def wait_for_stop(stop_event=None):
        (stop_event or app_module._watcher_stop).wait(2)

    monkeypatch.setattr(app_module, "_watcher_loop", wait_for_stop)

    def boot_admitted_start():
        app_module._watcher_boot_context.reopen_allowed = True
        try:
            return app_module._start_watcher()
        finally:
            del app_module._watcher_boot_context.reopen_allowed

    try:
        assert boot_admitted_start() is True
        first = app_module._watcher_thread
        assert first is not None and first.is_alive()
        assert app_module._stop_watcher(timeout=2, retire=True) is True
        assert not first.is_alive()

        replacement = []
        contender = __import__("threading").Thread(
            target=lambda: replacement.append(app_module._start_watcher()))
        contender.start(); contender.join(timeout=2)
        assert replacement == [False]
        assert app_module._watcher_thread is None

        assert boot_admitted_start() is True
        assert app_module._stop_watcher(timeout=2) is True
        # An ordinary non-retiring stop, including its no-generation case,
        # must leave direct idempotent starts usable.
        assert app_module._stop_watcher(timeout=2) is True
        assert app_module._start_watcher() is True
    finally:
        app_module._stop_watcher(timeout=2, retire=True)


def test_importing_app_creates_no_database(tmp_path):
    """RED. This is the defect, in one assertion.

    BD_DISABLE_KEEPALIVE=1 matches capture.sh (:512, :519) -- the exact
    condition under which the operator's DB was destroyed. The startup selftest
    is already skipped under that flag; db_init() and its four companions were
    not, and they are what raced.
    """
    cp = _run_isolated(_LIST_DB.format(repo=str(_REPO)), tmp_path,
                       BD_DISABLE_KEEPALIVE="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    line = [l for l in cp.stdout.splitlines() if l.startswith("DBFILES=")]
    assert line, cp.stdout + cp.stderr
    files = [f for f in line[0].split("=", 1)[1].split(",") if f]
    assert files == [], (
        f"importing bulk_downloader.app created {files}. Every xdist worker "
        f"does this during COLLECTION, against whatever DB the cwd resolves "
        f"to -- in the deploy directory that is the operator's live history.")


def test_importing_app_creates_no_database_without_the_keepalive_flag(tmp_path):
    """The service's own condition, not just capture's.

    A fix that only holds when BD_DISABLE_KEEPALIVE is set would be the latch
    this cut exists to avoid. Stated separately rather than folded into the
    test above so a partial fix fails ONE test and names which half.
    """
    cp = _run_isolated(_LIST_DB.format(repo=str(_REPO)), tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    line = [l for l in cp.stdout.splitlines() if l.startswith("DBFILES=")]
    assert line, cp.stdout + cp.stderr
    files = [f for f in line[0].split("=", 1)[1].split(",") if f]
    assert files == [], f"importing app created {files} with the selftest live"


def test_boot_once_creates_the_schema(tmp_path):
    """OVER-CORRECTION GUARD, and the important half.

    Deleting the boot entirely satisfies both tests above. This fails it: after
    boot_once() the DB must exist AND carry real tables, not merely be a file.
    """
    cp = _run_isolated("""
        import sys, glob, sqlite3
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        did = A.boot_once()
        found = sorted(glob.glob('*.db'))
        cx = sqlite3.connect(found[0]) if found else None
        names = sorted(r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")) if cx else []
        print('DID=%s' % did)
        print('DBFILES=' + ','.join(found))
        print('TABLES=%d' % len(names))
        print('HAS_HISTORY=%s' % ('history' in names))
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = dict(l.split("=", 1) for l in cp.stdout.splitlines() if "=" in l)
    assert out.get("DID") == "True", f"boot_once did no work: {cp.stdout}"
    assert out.get("DBFILES"), f"boot_once created no database: {cp.stdout}"
    assert int(out.get("TABLES", 0)) > 5, (
        f"boot_once made a file but no schema ({out.get('TABLES')} tables) -- "
        f"a deferred boot that defers forever is not a fix")
    assert out.get("HAS_HISTORY") == "True", cp.stdout


def test_boot_selftest_reads_the_same_sites_identity_as_runtime(tmp_path):
    """Import-time cwd must not freeze config evidence for a later boot."""
    cp = _run_isolated("""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {repo!r})
        root = Path.cwd()
        import_dir = root / 'import-a'
        boot_dir = root / 'boot-b'
        import_dir.mkdir(); boot_dir.mkdir()
        (import_dir / 'sites_config.json').write_text(json.dumps({{
            'sites': [{{'download_dir': 'IMPORT_A_ONLY'}}]
        }}), encoding='utf-8')
        (boot_dir / 'sites_config.json').write_text(json.dumps({{
            'sites': [{{'download_dir': 'BOOT_B_ONLY'}}]
        }}), encoding='utf-8')
        os.chdir(import_dir)
        import bulk_downloader.app as A
        observed = {{}}
        def record_selftest(**kwargs):
            observed.update(kwargs)
            return {{'ok': True, 'checks': [],
                    'summary': {{'ok': 0, 'warn': 0, 'fail': 0}},
                    'elapsed_ms': 0.0}}
        A._selftest.run_all = record_selftest
        A._selftest.log_to_stderr = lambda _result: None
        A._activate_configured_runtime_once = lambda _path: None
        A._start_watcher = lambda: None
        A._start_background_services = lambda: None
        os.chdir(boot_dir)
        A.boot_once()
        print('SITES_PATH=' + observed['sites_config_path'])
        print('DOWNLOAD_DIRS=' + ','.join(observed['download_dirs']))
    """.format(repo=str(_REPO)), tmp_path, BD_INSTALL_DIR="",
               BD_HOME=str(tmp_path), BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert Path(out["SITES_PATH"]).parent.name == "boot-b", cp.stdout
    assert out["DOWNLOAD_DIRS"] == "BOOT_B_ONLY", cp.stdout


def test_first_boot_rebinds_import_frozen_install_root(tmp_path):
    """Collection-time BD_INSTALL_DIR must not choose first-boot runtime."""
    cp = _run_isolated("""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        import_root = Path(A.SITES_FILE).parent.resolve()
        boot_root = import_root / 'first-boot-root'
        boot_root.mkdir()
        (boot_root / 'sites_config.json').write_text(json.dumps({{
            'sites': [{{'download_dir': 'BOOT_ROOT_ONLY'}}]
        }}), encoding='utf-8')
        os.environ['BD_INSTALL_DIR'] = str(boot_root)
        observed = {{}}
        activated = []
        def record_selftest(**kwargs):
            observed.update(kwargs)
            return {{'ok': True, 'checks': [],
                    'summary': {{'ok': 0, 'warn': 0, 'fail': 0}},
                    'elapsed_ms': 0.0}}
        A._selftest.run_all = record_selftest
        A._selftest.log_to_stderr = lambda _result: None
        A._activate_configured_runtime_once = activated.append
        A._start_watcher = lambda: None
        A._start_background_services = lambda: None
        A.boot_once()
        print('IMPORT_ROOT=' + str(import_root))
        print('BOOT_ROOT=' + str(boot_root.resolve()))
        print('PUBLIC_FILE=' + str(Path(A.SITES_FILE).resolve()))
        print('SELFTEST_FILE=' + observed['sites_config_path'])
        print('ACTIVATED_FILE=' + activated[0])
        print('DOWNLOAD_DIRS=' + ','.join(observed['download_dirs']))
    """.format(repo=str(_REPO)), tmp_path,
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out["IMPORT_ROOT"] != out["BOOT_ROOT"], cp.stdout
    expected = str(Path(out["BOOT_ROOT"]) / "sites_config.json")
    assert out["PUBLIC_FILE"] == expected, cp.stdout
    assert out["SELFTEST_FILE"] == expected, cp.stdout
    assert out["ACTIVATED_FILE"] == expected, cp.stdout
    assert out["DOWNLOAD_DIRS"] == "BOOT_ROOT_ONLY", cp.stdout


def test_rejected_install_root_does_not_retarget_live_config_saves(tmp_path):
    cp = _run_isolated("""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {repo!r})
        root_a = Path(os.environ['BD_INSTALL_DIR']).resolve()
        root_b = root_a / 'rejected-root-b'
        root_b.mkdir()
        (root_a / 'sites_config.json').write_text('{{}}', encoding='utf-8')
        (root_b / 'sites_config.json').write_text(json.dumps({{
            'b_only': {{'name': 'must survive'}}
        }}), encoding='utf-8')
        import bulk_downloader.app as A
        A.boot_once()
        accepted_file = Path(A.SITES_FILE).resolve()
        os.environ['BD_INSTALL_DIR'] = str(root_b)
        try:
            A.boot_once()
        except RuntimeError:
            rejected = True
        else:
            rejected = False
        A.s_cfg['a_live'] = {{'name': 'live A'}}
        A._save_sites_config()
        print('REJECTED=%s' % rejected)
        print('PUBLIC_FILE=' + str(Path(A.SITES_FILE).resolve()))
        print('ACCEPTED_FILE=' + str(accepted_file))
        print('A_KEYS=' + ','.join(sorted(json.loads(
            (root_a / 'sites_config.json').read_text()).keys())))
        print('B_KEYS=' + ','.join(sorted(json.loads(
            (root_b / 'sites_config.json').read_text()).keys())))
        A._stop_watcher(timeout=2, retire=True)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1",
               BD_DISABLE_VPN_RUNTIME="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = _keyed_output(cp)
    assert out["REJECTED"] == "True", cp.stdout
    assert out["PUBLIC_FILE"] == out["ACCEPTED_FILE"], cp.stdout
    assert out["A_KEYS"] == "a_live", cp.stdout
    assert out["B_KEYS"] == "b_only", cp.stdout


def test_boot_once_is_idempotent(tmp_path):
    """Second call must be a no-op, not a second migration run."""
    cp = _run_isolated("""
        import sys
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        print('FIRST=%s' % A.boot_once())
        print('SECOND=%s' % A.boot_once())
        print('THIRD=%s' % A.boot_once())
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = dict(l.split("=", 1) for l in cp.stdout.splitlines() if "=" in l)
    assert out.get("FIRST") == "True", cp.stdout
    assert out.get("SECOND") == "False", cp.stdout
    assert out.get("THIRD") == "False", cp.stdout


def test_a_different_database_boots_again(tmp_path):
    """The latch is keyed on WHICH database, not on a process-wide bool.

    Found while fixing the fallout from this very cut, not by design. A bare
    `_BOOTED = True` answers "already booted" for a database this process has
    never opened: boot tmpdir A, point DB_PATH at tmpdir B, and the second
    caller is told the work is done and gets an EMPTY SCHEMA, silently. That
    is the same shape as the defect being fixed -- a check that cannot see its
    subject reporting OK.

    Real instance: tests/test_library_forward_path_records_an_absolute_path.py
    uses clean_workdir, so every test gets its own tmpdir; with a bool latch
    only the first test in the file would have had a booted database.
    """
    cp = _run_isolated("""
        import sys, os, sqlite3
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        import bulk_downloader.db as D

        a = os.path.join(os.getcwd(), 'a', 'q.db')
        b = os.path.join(os.getcwd(), 'b', 'q.db')
        os.makedirs(os.path.dirname(a)); os.makedirs(os.path.dirname(b))

        D.DB_PATH = a
        print('A_FIRST=%s' % A.boot_once())
        print('A_AGAIN=%s' % A.boot_once())
        D.DB_PATH = b
        print('B_FIRST=%s' % A.boot_once())
        cx = sqlite3.connect(b)
        n = len(cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        print('B_TABLES=%d' % n)
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = dict(l.split("=", 1) for l in cp.stdout.splitlines() if "=" in l)
    assert out.get("A_FIRST") == "True", cp.stdout
    assert out.get("A_AGAIN") == "False", cp.stdout
    assert out.get("B_FIRST") == "True", (
        "a SECOND database was reported already-booted. The latch is keyed on "
        f"the process rather than the database.\n{cp.stdout}")
    assert int(out.get("B_TABLES", 0)) > 5, (
        f"the second database has {out.get('B_TABLES')} tables -- it was "
        f"latched out of its own boot and left empty")


def test_concurrent_boot_runs_the_work_exactly_once(tmp_path):
    """The property the whole cut is about: N callers, one boot.

    Deferring without a lock would move the race rather than remove it -- the
    first request in each of several threads would boot concurrently, which is
    the same concurrent-db_init that quarantined the operator's database.
    """
    cp = _run_isolated("""
        import sys, threading
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        results = []
        barrier = threading.Barrier(12)
        def go():
            barrier.wait()
            results.append(A.boot_once())
        ts = [threading.Thread(target=go) for _ in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        print('WINNERS=%d' % sum(1 for r in results if r))
        print('TOTAL=%d' % len(results))
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = dict(l.split("=", 1) for l in cp.stdout.splitlines() if "=" in l)
    assert out.get("TOTAL") == "12", cp.stdout
    assert out.get("WINNERS") == "1", (
        f"{out.get('WINNERS')} threads each ran the boot. Deferring without a "
        f"lock moves the race instead of removing it.")


def test_an_ordinary_request_boots_the_database(tmp_path):
    """POS: the service is unchanged.

    Nothing about this cut may require the operator to call anything new. A
    plain request through the app must find a booted database.
    """
    cp = _run_isolated("""
        import sys, glob
        sys.path.insert(0, {repo!r})
        import bulk_downloader.app as A
        assert glob.glob('*.db') == [], 'import already booted it'
        c = A.app.test_client()
        r = c.get('/api/health')
        print('STATUS=%d' % r.status_code)
        print('DBFILES=' + ','.join(sorted(glob.glob('*.db'))))
    """.format(repo=str(_REPO)), tmp_path, BD_DISABLE_KEEPALIVE="1")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = dict(l.split("=", 1) for l in cp.stdout.splitlines() if "=" in l)
    assert out.get("STATUS") == "200", cp.stdout + cp.stderr
    assert out.get("DBFILES"), (
        "a request did not boot the database -- deferring must not mean "
        "never. The service would serve against an unmigrated schema.")


def test_isolated_home_restores_process_state_after_runtime_reset_failure(
        tmp_path, monkeypatch):
    """Fail-loud worker teardown must not poison the next pytest item."""
    from types import ModuleType
    import conftest as fixture_module

    nested_home = tmp_path / "nested-fixture-home"
    nested_home.mkdir()
    module_key = "bulk_downloader._fixture_restore_probe"
    saved_cwd = os.getcwd()
    env_keys = (
        "BD_HOME", "BD_DISABLE_KEEPALIVE", "BD_DEV_MODE",
        "BD_DEV_MODE_DISABLE", "BD_AUTH_TOKEN", "BD_COCKPIT_TASKS",
        "BD_INSTALL_DIR",
    )
    saved_env = {key: os.environ.get(key) for key in env_keys}
    saved_modules = {
        key: value for key, value in sys.modules.items()
        if key.startswith("bulk_downloader")
    }
    original_probe = ModuleType(module_key)
    sys.modules[module_key] = original_probe

    class _Node:
        @staticmethod
        def get_closest_marker(name):
            return object() if name == "bd_module_wipe" else None

    class _Request:
        node = _Node()

    calls = 0

    def fail_second_reset(*, reopen=False):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("planted runtime reset failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                fixture_module,
                "_reset_loaded_app_site_runtime",
                fail_second_reset,
            )
            fixture = fixture_module.isolated_bd_home.__wrapped__(
                _Request(), nested_home)
            assert next(fixture) == nested_home
            replacement_probe = ModuleType(module_key)
            sys.modules[module_key] = replacement_probe

            with pytest.raises(
                RuntimeError, match="planted runtime reset failure"
            ):
                next(fixture)

            assert os.getcwd() == saved_cwd
            for key, value in saved_env.items():
                assert os.environ.get(key) == value
            assert sys.modules.get(module_key) is original_probe
    finally:
        # The RED version deliberately leaves process globals changed.  Repair
        # them here so this regression cannot poison its own worker on failure.
        os.chdir(saved_cwd)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for key in [
            name for name in list(sys.modules)
            if name.startswith("bulk_downloader")
        ]:
            del sys.modules[key]
        sys.modules.update(saved_modules)
        sys.modules.pop(module_key, None)
        fixture_module._canonicalize_package_children("bulk_downloader")
