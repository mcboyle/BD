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
