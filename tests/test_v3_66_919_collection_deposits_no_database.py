"""Collecting a test suite booted the application and wrote a database.

Item 11 / s4#4, cut 1 of 2 (the collection-time class).

`bulk_downloader/app.py` runs its boot block at MODULE SCOPE: db_init(), the
run-history substrate, an integrity check, an FTS optimize, a queue-recovery
read, a background integrity schedule -- and, 1700 lines further down and
outside every previous attempt to gate this, migrations.apply_pending().

So merely IMPORTING the app boots it. 22 tracked test files import
bulk_downloader.app at module scope, and `pytest --collect-only` imports every
module it collects, so a run that executes ZERO test bodies still boots the
whole application.

MEASURED at v3.66.919, `pytest --collect-only -q tests/test_cap_cancel.py`
with BD_INSTALL_DIR pointed at an empty directory:

    471,095 bytes across 5 paths
      335,872  downloader_history.db
      135,168  downloader_history.db.premigration.bak
           18  .fts_optimize_last
           18  .integrity_check_last
           19  .integrity_last_run

and a plain `import bulk_downloader.app` adds app_config.json, logs/,
live_recordings/ and state/heartbeat.json on top -- 471,992 bytes over 11
paths.

WHY THIS IS NAMED FOR THE -wal. CLAUDE.md section 5 records that an unguarded
probe writes downloader_history.db INTO THE REPO, gitignored, so `git status`
stays clean and nothing warns you. The load-bearing detail: `.gitignore`'s
`*.db` does NOT match `downloader_history.db-wal`, so the WAL and SHM siblings
are the only git-VISIBLE members of the class. The item is named for the one
symptom that could ever surface.

THE SENTINEL IS A `sys` ATTRIBUTE, NOT AN ENV VAR, and that is load-bearing in
two directions. A new BD_-prefixed name enters the config-surface ledger and
bands tests/test_gui_parity.py (CLAUDE.md section 4). And an env var is
INHERITED BY CHILD PROCESSES -- several suites spawn a real server and would
silently get an app that never initialised its database. A sys attribute
cannot cross a process boundary, which is exactly the property wanted.

TWO AMENDMENTS TO THE ORIGINAL SPEC, both measured rather than reasoned:

  (a) It gates SIX writers at app.py:80-140. That is not enough: migrations
      apply_pending() sits far below the gated region and creates the DB by
      itself, so the spec's own RED stays RED. Seven are gated here.

  (b) The child environment below scrubs BD_DISABLE_KEEPALIVE as well as
      BD_INSTALL_DIR. tests/conftest.py forces BD_DISABLE_KEEPALIVE=1 into
      os.environ, so a child that inherits it exercises only the ungated
      subset and the test certifies a denominator that excludes part of its
      own subject.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_PY = REPO_ROOT / "venv" / "bin" / "python"

# A tracked suite that imports bulk_downloader.app at MODULE scope, so
# collecting it is enough to boot the app. Chosen by AST over tests/, not by
# grep: 22 files qualify.
_IMPORTER = "tests/test_cap_cancel.py"

# Anything whose presence means the database layer ran. The .db-wal/.db-shm
# siblings are included because they are the only members `.gitignore` does
# not hide -- `*.db` does not match `downloader_history.db-wal`.
_DB_CLASS = (
    "downloader_history.db",
    "downloader_history.db-wal",
    "downloader_history.db-shm",
    "downloader_history.db.premigration.bak",
    ".integrity_check_last",
    ".integrity_last_run",
    ".fts_optimize_last",
)


def _child_env(install_dir: Path) -> dict:
    """A clean environment for the child collection run.

    BD_DISABLE_KEEPALIVE is scrubbed deliberately -- see amendment (b) in the
    module docstring. PYTEST_CURRENT_TEST is scrubbed because pytest sets it in
    the PARENT and an inherited value confuses the child's own bookkeeping.
    """
    env = dict(os.environ)
    for key in ("BD_DISABLE_KEEPALIVE", "PYTEST_CURRENT_TEST", "BD_HOME"):
        env.pop(key, None)
    env["BD_INSTALL_DIR"] = str(install_dir)
    return env


def _collect_only(install_dir: Path):
    interp = _PY if _PY.exists() else Path(sys.executable)
    return subprocess.run(
        [str(interp), "-m", "pytest", "--collect-only", "-q", _IMPORTER],
        cwd=str(REPO_ROOT), env=_child_env(install_dir),
        capture_output=True, text=True, timeout=600,
    )


def _residue(root: Path):
    return sorted(p.name for p in root.rglob("*") if p.name in _DB_CLASS)


def test_collection_does_not_create_a_database(tmp_path):
    """RED: 471,095 bytes appear with zero test bodies executed.

    The POSITIVE half is not decoration. A residue-only assertion passes
    trivially on a child that failed to import at all -- which is the most
    likely way a wrong fix presents. Both halves are required: the collection
    must SUCCEED and must leave nothing behind.
    """
    install = tmp_path / "install"
    install.mkdir()
    proc = _collect_only(install)

    # can-see: the child really did collect this module.
    assert proc.returncode == 0, (
        "the child collection failed, so this test proves nothing about "
        "residue.\nstdout=%s\nstderr=%s"
        % (proc.stdout[-1500:], proc.stderr[-1500:]))
    assert "collected" in (proc.stdout + proc.stderr), (
        "no collection summary -- the child did not reach %s" % (_IMPORTER,))

    left = _residue(install)
    assert not left, (
        "collecting %s created %r under BD_INSTALL_DIR. --collect-only runs no "
        "test body, so this is the application booting at import time."
        % (_IMPORTER, left))


def test_the_importer_fixture_really_imports_the_app_at_module_scope():
    """HARNESS GUARD, and it is the one that decides whether the test above

    means anything. If _IMPORTER stopped importing bulk_downloader.app at
    module scope -- a refactor moving the import into a fixture would do it --
    the residue assertion would pass for a reason that has nothing to do with
    the gate. That is CLAUDE.md section 0: the denominator would no longer
    contain the subject, and the suite would report clean.
    """
    import ast
    tree = ast.parse((REPO_ROOT / _IMPORTER).read_text(encoding="utf-8"))
    found = False
    for node in tree.body:                       # MODULE scope only
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        if any(n == "bulk_downloader.app" or n.startswith("bulk_downloader.app.")
               for n in names):
            found = True
            break
    assert found, (
        "%s no longer imports bulk_downloader.app at module scope, so "
        "collecting it no longer boots the app and the residue test above is "
        "vacuous. Pick another module-scope importer." % (_IMPORTER,))


def test_the_app_still_boots_when_not_collecting(tmp_path):
    """OVER-CORRECTION GUARD, and the direction that would break production.

    The gate must suppress the boot ONLY under collection. A guard keyed on
    something ambient -- or one left permanently on -- would ship an
    application that never initialises its database, and every symptom would
    appear far from here. So: import the app in a child WITHOUT the sentinel
    and require the database to be created.
    """
    install = tmp_path / "prod"
    install.mkdir()
    interp = _PY if _PY.exists() else Path(sys.executable)
    env = _child_env(install)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [str(interp), "-c", "import bulk_downloader.app"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, (
        "importing the app outside collection failed: %s" % (proc.stderr[-1500:],))
    assert (install / "downloader_history.db").exists(), (
        "a normal import did NOT create the database -- the collection guard "
        "is suppressing the real boot path, which would ship an app that never "
        "initialises its storage")
