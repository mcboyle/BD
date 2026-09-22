"""Row 1018 -- the async engine opens the SAME SQLite file the tree already writes.

THE CLAIM UNDER TEST is not "SQLAlchemy is installed". It is that the seam
addresses the live database: the file `bulk_downloader.db` resolves at call
time, carrying the schema `db_init()` created, reached through an async engine
and read back with a typed session -- so a later per-module row can adopt it
without the rows moving.

The probes write nothing. Every assertion is a read of a schema another writer
made, which is what makes "the same file" checkable at all: an engine pointed
at a fresh path would open cleanly and see an empty sqlite_master, so emptiness
is the failure this file is looking for rather than a state it tolerates.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest

from bulk_downloader import db

BD_GATE_SCOPE = "module"

_SCHEMA_TABLES = ("history", "queue")


def _seam():
    """The seam under test, resolved as an ASSERTION rather than a module import.

    At base this row's module does not exist. A module-scope import would make
    that a COLLECTION ERROR and a fixture would make it a SETUP ERROR -- both
    are states pytest reports before any assertion runs, and both read the same
    whether the row was never built or the test file is simply broken. Called
    from the test BODY, the absence is a FAILURE that says what is missing in
    the row's own terms.
    """
    try:
        from bulk_downloader import orm_engine as seam
    except ModuleNotFoundError as exc:
        # TWO CAUSES, ONE EXCEPTION TYPE. `orm_engine` imports sqlalchemy at
        # module scope, so an uninstalled dependency raises the same
        # ModuleNotFoundError from inside the seam that a missing seam raises
        # from this line. Collapsing them costs the investigation: a lens
        # reading "the seam is absent" while the seam sits staged and correct
        # is a diagnostic that sent the reader the wrong way.
        if exc.name not in (None, "bulk_downloader.orm_engine"):
            pytest.fail(
                f"row1018: the seam is present but its dependency {exc.name!r} "
                f"is not installed ({exc}). Install requirements.txt -- this is "
                f"an environment answer, not a statement about the cut.")
        pytest.fail(
            f"row1018: the SQLAlchemy 2.0 async seam is absent ({exc}). "
            f"bulk_downloader/orm_engine.py must expose create_async_engine_for, "
            f"async_session_factory, sqlite_async_url and ORMBase.")
    return seam


def _tables_via_sqlite3(path: str) -> set[str]:
    """The control reading: the same question asked by the code that is in use."""
    with sqlite3.connect(path) as cx:
        return {r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture()
def live_db(tmp_path, monkeypatch):
    """The real schema at a real path, resolved the way the tree resolves it."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bulk.db"))
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    db.db_init()
    path = db._resolve_db_path()
    assert Path(path).is_file(), (
        f"COULD NOT LOOK: db_init() left no file at {path}, so nothing below "
        f"distinguishes an engine that found the database from one that made it")
    return path


def test_the_control_sees_the_schema(live_db):
    """Positive control. The fixture really did create the tables.

    Without this, an async engine reporting the same tables would prove only
    that both readers agree about an empty database.
    """
    found = _tables_via_sqlite3(live_db)
    missing = [t for t in _SCHEMA_TABLES if t not in found]
    assert not missing, f"db_init() created no {missing}; found {sorted(found)}"


def test_async_engine_opens_the_existing_database(live_db):
    """The row's claim: the async engine reads the schema already on disk."""
    orm_engine = _seam()
    async def _read() -> set[str]:
        engine = orm_engine.create_async_engine_for(live_db)
        try:
            async with engine.connect() as cx:
                rows = await cx.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'")
                return {r[0] for r in rows}
        finally:
            await engine.dispose()

    seen = asyncio.run(_read())
    missing = [t for t in _SCHEMA_TABLES if t not in seen]
    assert not missing, (
        f"the async engine opened a database without {missing} -- it addressed "
        f"a different file than db._resolve_db_path() returned. Saw: {sorted(seen)}")
    assert seen == _tables_via_sqlite3(live_db), (
        "the async engine and sqlite3 disagree about the same path's schema")


def test_a_typed_session_reads_through_the_engine(live_db):
    """The declarative half: a typed AsyncSession round-trips on that engine."""
    orm_engine = _seam()
    from sqlalchemy import text

    async def _count() -> int:
        engine = orm_engine.create_async_engine_for(live_db)
        try:
            factory = orm_engine.async_session_factory(engine)
            async with factory() as session:
                return int((await session.execute(
                    text("SELECT count(*) FROM history"))).scalar_one())
        finally:
            await engine.dispose()

    assert asyncio.run(_count()) == 0, (
        "a freshly initialised history table is empty; a nonzero count means "
        "the session reached a database this test did not create")
    assert issubclass(orm_engine.ORMBase, __import__(
        "sqlalchemy.orm", fromlist=["DeclarativeBase"]).DeclarativeBase)


def test_the_url_is_absolute_so_a_chdir_cannot_move_it(tmp_path, monkeypatch):
    """Negative control for the path handling, driven with a relative input.

    The tree's own resolver can return a RELATIVE path (branch 3 of
    `_resolve_db_path`), which sqlite3 binds to the cwd. An engine that passed
    that through would follow a later chdir to a different file, and every
    assertion above would still pass because the fixture never chdirs.
    """
    orm_engine = _seam()
    monkeypatch.chdir(tmp_path)
    url = orm_engine.sqlite_async_url("bulk.db")
    assert url.startswith(f"{orm_engine.ASYNC_SQLITE_DRIVER}:///"), url
    tail = url.split(":///", 1)[1]
    assert Path(tail).is_absolute(), f"relative path survived into the URL: {url}"
    assert Path(tail) == (tmp_path / "bulk.db").resolve(), url


def test_the_seam_imports_nothing_from_the_package():
    """The import-graph promise the module's docstring makes, asserted.

    A later edit adding `from .db import ...` here would be a new edge in the
    frozen baseline, and the remedy that gate names is a re-freeze in a
    SEPARATE cut -- so the constraint is worth a gate rather than a comment.
    """
    orm_engine = _seam()
    import ast

    src = Path(orm_engine.__file__).read_text(encoding="utf-8")
    edges = [
        node for node in ast.walk(ast.parse(src))
        if (isinstance(node, ast.ImportFrom)
            and (node.level > 0 or (node.module or "").startswith("bulk_downloader")))
        or (isinstance(node, ast.Import)
            and any(a.name.startswith("bulk_downloader") for a in node.names))
    ]
    assert not edges, (
        "orm_engine imports from its own package at "
        f"line(s) {[n.lineno for n in edges]}; the path is a parameter so that "
        "this file adds no edge to the frozen import graph")


def test_db_async_engine_binds_the_resolver_not_a_captured_path(live_db, tmp_path):
    """The production caller resolves the path at CALL time, like every writer.

    This is the assertion that makes `db.async_engine` worth having over a
    caller doing `create_async_engine_for(db._resolve_db_path())` itself: the
    binding is re-read per call, so a test or a Docker run that monkeypatches
    DB_PATH after import reaches the file it just pointed at. A helper that
    captured the path at import would pass every other test in this file and
    fail only where it matters.
    """
    _seam()  # the seam must exist for db.async_engine to be importable at all

    async def _tables(engine) -> set[str]:
        try:
            async with engine.connect() as cx:
                rows = await cx.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'")
                return {r[0] for r in rows}
        finally:
            await engine.dispose()

    first = asyncio.run(_tables(db.async_engine()))
    assert _SCHEMA_TABLES[0] in first, (
        f"db.async_engine() opened a database without {_SCHEMA_TABLES[0]}; "
        f"saw {sorted(first)}")

    moved = tmp_path / "moved" / "bulk.db"
    moved.parent.mkdir()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(db, "DB_PATH", str(moved))
        db.db_init()
        second = asyncio.run(_tables(db.async_engine()))
    assert moved.is_file(), "the second db_init() wrote no file to compare against"
    assert second == _tables_via_sqlite3(str(moved)), (
        "db.async_engine() did not follow DB_PATH to the new file -- it captured "
        "a path instead of calling the resolver")


def test_bdctl_doctor_db_engine_check_reads_the_live_database(tmp_path):
    """The product path (O1223 / ORDERS-0805 P4): the feature THROUGH its caller.

    Run as a SUBPROCESS, not by calling the function: the claim is that
    `bdctl doctor --db-engine-check` works for an operator, and that is an argv
    parse, a local code path taken before any HTTP, and an exit status. Calling
    _db_engine_check() directly would assert none of those -- a flag that never
    reached the parser would still pass.

    BD_INSTALL_DIR is how the resolver is pointed at a scratch database, which
    is also the seam this check exists to exercise: no server is started, and
    nothing writes to the operator's real library.
    """
    import json as _json
    import os
    import subprocess
    import sys as _sys

    install = tmp_path / "install"
    install.mkdir()
    env = dict(os.environ, BD_INSTALL_DIR=str(install))
    env.pop("BD_DISABLE_KEEPALIVE", None)
    repo = Path(__file__).resolve().parent.parent

    # The schema first, through the writer that owns it, so a table count of
    # zero below is a real answer and not an empty file this test created.
    pre = subprocess.run(
        [_sys.executable, "-c",
         "from bulk_downloader import db; db.db_init()"],
        cwd=repo, env=env, capture_output=True, text=True)
    assert pre.returncode == 0, f"db_init failed: {pre.stderr}"

    out = subprocess.run(
        [_sys.executable, "bdctl.py", "doctor", "--db-engine-check", "--json"],
        cwd=repo, env=env, capture_output=True, text=True)
    assert out.returncode == 0, (
        f"bdctl doctor --db-engine-check exited {out.returncode}; "
        f"stdout={out.stdout!r} stderr={out.stderr!r}")
    payload = _json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True, payload
    assert payload["tables"] > 0, (
        f"the check reported a database with no tables, so it did not reach the "
        f"schema db_init() just wrote: {payload}")
    assert Path(payload["path"]).resolve() == (
        install / Path(payload["path"]).name).resolve(), (
        f"the check read a database outside BD_INSTALL_DIR: {payload}")


def test_the_db_engine_check_reports_failure_instead_of_raising(tmp_path):
    """Negative control for the caller: a broken database is a status, not a stack.

    A diagnostic that crashes on the condition it diagnoses tells the operator
    nothing they did not already know from the crash.
    """
    import json as _json
    import os
    import subprocess
    import sys as _sys

    install = tmp_path / "install"
    install.mkdir()
    (install / "downloader_history.db").write_bytes(
        b"this is not a sqlite database at all")
    repo = Path(__file__).resolve().parent.parent
    env = dict(os.environ, BD_INSTALL_DIR=str(install))

    out = subprocess.run(
        [_sys.executable, "bdctl.py", "doctor", "--db-engine-check", "--json"],
        cwd=repo, env=env, capture_output=True, text=True)
    assert out.returncode == 1, (
        f"a corrupt database must exit 1, got {out.returncode}; "
        f"stdout={out.stdout!r} stderr={out.stderr!r}")
    assert "Traceback" not in out.stderr, (
        f"the check raised instead of reporting: {out.stderr!r}")
    payload = _json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False and payload["error"], payload


def test_the_check_refuses_a_missing_database_and_creates_nothing(tmp_path):
    """F1 (lens B9-B, HIGH): an absent database is a FAILURE, not an empty one.

    Two assertions, and the second is the one that pins the fix. Reporting
    `ok: false` could be done by a file-exists branch alone; asserting that the
    directory is still empty afterwards is what pins the read-only URL, because
    SQLite creates the file it is asked to open read-write and would otherwise
    convert "this install has no database" into "this install has an empty
    database" -- destroying the single most diagnostic fact about a
    half-restored box, and passing every later existence check.
    """
    import json as _json
    import os
    import subprocess
    import sys as _sys

    install = tmp_path / "install"
    install.mkdir()
    assert not any(install.iterdir()), "the scratch install must start empty"
    repo = Path(__file__).resolve().parent.parent

    out = subprocess.run(
        [_sys.executable, "bdctl.py", "doctor", "--db-engine-check", "--json"],
        cwd=repo, env=dict(os.environ, BD_INSTALL_DIR=str(install)),
        capture_output=True, text=True)

    assert out.returncode == 1, (
        f"a missing database must exit 1, got {out.returncode}; "
        f"stdout={out.stdout!r} stderr={out.stderr!r}")
    payload = _json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False, payload
    assert "no database" in payload["error"], payload
    leftovers = sorted(q.name for q in install.iterdir())
    assert not leftovers, (
        f"the check created {leftovers} while reporting on a database that did "
        f"not exist; it must not be able to manufacture the state it reports on")


def test_the_read_only_url_cannot_create_a_database(tmp_path):
    """The seam half of F1, asserted directly rather than through the CLI.

    The caller's file-exists branch and the read-only URL are two guards for
    one failure, and a later edit could drop either. This node fails if the
    URL loses `mode=ro`, even while the CLI branch still makes the CLI correct.
    """
    orm_engine = _seam()
    missing = tmp_path / "nothing_here.db"

    url = orm_engine.sqlite_async_url(missing, read_only=True)
    assert "mode=ro" in url and "uri=true" in url, url
    assert orm_engine.sqlite_async_url(missing) != url, (
        "read_only=True produced the same URL as the read-write default, so "
        "the flag does nothing")

    async def _open():
        engine = orm_engine.create_async_engine_for(missing, read_only=True)
        try:
            async with engine.connect() as cx:
                await cx.exec_driver_sql("SELECT 1")
        finally:
            await engine.dispose()

    with pytest.raises(Exception):
        asyncio.run(_open())
    assert not missing.exists(), (
        "a read-only engine created the database file it was pointed at")


def test_the_url_follows_a_symlinked_install_dir(tmp_path):
    """F2/M1: `Path.resolve()` and not `os.path.abspath`, which is the whole
    reason that line is written the way it is.

    The deploy path reaches the database through a symlinked install dir.
    `abspath` is a string operation and keeps the symlink in the path;
    `resolve()` follows it. Absoluteness alone -- which the existing node pins
    -- is satisfied by both, so the substitution survived mutation M1 with the
    suite green. This asserts the behaviour the docstring claims.
    """
    orm_engine = _seam()
    real = tmp_path / "real_install"
    real.mkdir()
    link = tmp_path / "linked_install"
    link.symlink_to(real, target_is_directory=True)

    url = orm_engine.sqlite_async_url(link / "downloader_history.db")
    tail = url.split(":///", 1)[1]
    assert Path(tail) == (real / "downloader_history.db"), (
        f"the URL kept the symlink instead of following it to the real file: "
        f"{url} (os.path.abspath would give "
        f"{os.path.abspath(link / 'downloader_history.db')})")


def test_the_session_factory_does_not_expire_on_commit():
    """F2/M2: the factory's one distinguishing setting, pinned.

    Without this the factory is asserted only as "returns a sessionmaker", and
    flipping expire_on_commit to True -- a second SQLite round trip per
    committed object, for values the caller just wrote -- left the suite green.
    """
    orm_engine = _seam()
    factory = orm_engine.async_session_factory(
        orm_engine.create_async_engine_for(":memory:"))
    assert factory.kw.get("expire_on_commit") is False, (
        f"expire_on_commit is {factory.kw.get('expire_on_commit')!r}; the "
        f"existing sqlite3 writers re-fetch nothing after a write, so the seam "
        f"must not be slower than the code it is meant to replace")


def test_a_missing_dependency_is_exit_2_and_names_itself(tmp_path):
    """The product half of F3: the dependency is not the database.

    db imports orm_engine lazily, so an install without SQLAlchemy reaches the
    engine call and fails there. Folded into the generic handler it printed
    "No module named 'sqlalchemy'" as a database error, which sends the
    operator to inspect a file that is fine. Exit 2 separates "I could not ask"
    from the exit 1 that means "I asked and the answer is bad" -- and neither
    is a pass.

    The dependency is hidden with a -X importtime-free sitecustomize shim
    rather than by uninstalling anything: no shared environment is touched.
    """
    import json as _json
    import os
    import subprocess
    import sys as _sys

    install = tmp_path / "install"
    install.mkdir()
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sqlalchemy.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'sqlalchemy'\", "
        "name='sqlalchemy')\n", encoding="utf-8")
    repo = Path(__file__).resolve().parent.parent
    subprocess.run(
        [_sys.executable, "-c", "from bulk_downloader import db; db.db_init()"],
        cwd=repo, env=dict(os.environ, BD_INSTALL_DIR=str(install)),
        capture_output=True, text=True, check=True)

    env = dict(os.environ, BD_INSTALL_DIR=str(install),
               PYTHONPATH=str(blocker) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    out = subprocess.run(
        [_sys.executable, "bdctl.py", "doctor", "--db-engine-check", "--json"],
        cwd=repo, env=env, capture_output=True, text=True)

    assert out.returncode == 2, (
        f"a missing dependency must exit 2, not {out.returncode}; "
        f"stdout={out.stdout!r} stderr={out.stderr!r}")
    payload = _json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False, payload
    assert "async engine" in payload["error"] and "sqlalchemy" in payload["error"], (
        f"the error names neither the engine nor the dependency: {payload}")
    assert "no database" not in payload["error"], (
        f"a dependency problem was reported as a database problem: {payload}")


def test_db_async_engine_read_only_refuses_a_missing_path(tmp_path, monkeypatch):
    """F2 (lens round 2): the binding, not just the URL builder, must refuse.

    Mutant M6 replaced async_engine_read_only() with async_engine() and every
    test passed. This node calls the binding directly on a missing path and
    asserts it refuses without creating a file.
    """
    _seam()
    missing_dir = tmp_path / "empty_install"
    missing_dir.mkdir()
    monkeypatch.setattr(db, "DB_PATH", str(missing_dir / "bulk.db"))
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)

    engine = db.async_engine_read_only()

    async def _open():
        try:
            async with engine.connect() as cx:
                await cx.exec_driver_sql("SELECT 1")
        finally:
            await engine.dispose()

    with pytest.raises(Exception):
        asyncio.run(_open())
    leftovers = sorted(q.name for q in missing_dir.iterdir())
    assert not leftovers, (
        f"async_engine_read_only() created {leftovers} on a missing path; "
        f"the read-only guarantee is broken at the binding layer")


def test_read_only_url_escapes_question_mark_in_path(tmp_path):
    """F1/PROBE-3 (lens round 2): a path containing '?mode=rwc' must not
    inject a writable mode ahead of mode=ro.
    """
    orm_engine = _seam()
    injecting_dir = tmp_path / "db?mode=rwc&z=.db"
    injecting_dir.mkdir(parents=True)
    target = injecting_dir / "test.db"

    url = orm_engine.sqlite_async_url(target, read_only=True)
    assert url.count("mode=ro") == 1, f"mode=ro missing or duplicated: {url}"
    assert "mode=rwc" not in url.split("?", 1)[-1].split("?", 1)[-1] or \
        url.index("mode=rwc") > url.index("?mode=ro") if "mode=rwc" in url else True, \
        f"injected mode=rwc appears unescaped in query position: {url}"

    async def _open():
        engine = orm_engine.create_async_engine_for(target, read_only=True)
        try:
            async with engine.connect() as cx:
                await cx.exec_driver_sql("SELECT 1")
        finally:
            await engine.dispose()

    with pytest.raises(Exception):
        asyncio.run(_open())
    assert not target.exists(), (
        f"a read-only engine with an injected ?mode=rwc created a database")


def test_read_only_url_handles_percent_in_path(tmp_path):
    """F1/PROBE-5 (lens round 2): a file named 'a%41b.db' must be reachable
    read-only without SQLite percent-decoding it to 'aAb.db'.
    """
    orm_engine = _seam()
    pct_dir = tmp_path / "pctdir"
    pct_dir.mkdir()
    db_file = pct_dir / "a%41b.db"
    import sqlite3 as _sqlite3
    with _sqlite3.connect(str(db_file)) as cx:
        cx.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
    assert db_file.is_file(), "control: the file exists on disk"

    async def _read():
        engine = orm_engine.create_async_engine_for(db_file, read_only=True)
        try:
            async with engine.connect() as cx:
                rows = await cx.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'")
                return {r[0] for r in rows}
        finally:
            await engine.dispose()

    tables = asyncio.run(_read())
    assert "marker" in tables, (
        f"the read-only engine could not open a file with '%' in its name; "
        f"SQLite percent-decoded the path. Saw: {sorted(tables)}")


def test_read_write_url_handles_question_mark_in_path(tmp_path):
    """F1/PROBE-2 (lens round 2): a read-write URL with '?' in the path must
    open the file that exists, not a different database.
    """
    orm_engine = _seam()
    qmark_dir = tmp_path / "q?x"
    qmark_dir.mkdir()
    db_file = qmark_dir / "test.db"
    import sqlite3 as _sqlite3
    with _sqlite3.connect(str(db_file)) as cx:
        cx.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
    assert db_file.is_file()

    async def _read():
        engine = orm_engine.create_async_engine_for(db_file)
        try:
            async with engine.connect() as cx:
                rows = await cx.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'")
                return {r[0] for r in rows}
        finally:
            await engine.dispose()

    tables = asyncio.run(_read())
    assert "marker" in tables, (
        f"the read-write engine opened a different database than the one on "
        f"disk when the path contains '?'. Saw: {sorted(tables)}")
