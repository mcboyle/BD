"""Row 1018 -- the SQLAlchemy 2.0 async seam over the existing SQLite file.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT. The tree's persistence is 26
direct `sqlite3` call sites across six db modules. This module does not migrate
any of them: it is the typed async engine seam a later per-module row can
adopt, landed on its own so the dependency and the engine shape are proven
before any behaviour moves. `bulk_downloader.db` imports it lazily for the
two entry points `async_engine()` and `async_engine_read_only()`.

NO INTRA-PACKAGE IMPORT, ON PURPOSE. The path is a PARAMETER, never read from
`.constants` or `.db` here. Two reasons, and the second is the load-bearing one:

  * `db._resolve_db_path()` resolves at CALL time -- monkeypatched DB_PATH,
    then BD_INSTALL_DIR, then cwd -- so a module that captured the path at
    import would silently address a different file than every existing writer.
    Taking it as an argument means the caller resolves it with the one resolver
    that already exists, and this module cannot disagree with it.
  * A new edge out of this file would be a new edge in the frozen import graph
    (tools/decomp/import_graph_baseline.json), and re-freezing a shared
    baseline inside the cut that adds the edge is what that gate tells workers
    not to do. Zero edges, nothing to re-freeze.

The engine is never built at import. `create_async_engine` opens no connection
until first use, but constructing one still pins a URL, and a module-level URL
is the import-time capture this file exists to avoid.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote as _uri_quote

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

BD_GATE_SCOPE = "module"

#: The async driver the URL names. aiosqlite is declared in requirements.txt
#: beside SQLAlchemy: without it `create_async_engine` raises at URL parse
#: time, which is a missing dependency reported as a bad URL.
ASYNC_SQLITE_DRIVER = "sqlite+aiosqlite"


class ORMBase(DeclarativeBase):
    """Typed declarative base for models a later per-module row will add.

    Empty by design. Declaring a model here would make this seam a second
    description of a schema `db.db_init()` already owns in SQL, and the two
    would drift; models belong in the row that migrates their module.
    """


def sqlite_async_url(db_path: str | os.PathLike[str], *,
                     read_only: bool = False) -> str:
    """The async URL for an existing SQLite file, absolute so cwd cannot move it.

    `Path.resolve()` and not `os.path.abspath`: the deploy path reaches the DB
    through a symlinked install dir, and the two disagree there -- `abspath` is
    a string operation that keeps the symlink in the path, `resolve()` follows
    it to the real file. Two engines built from those two strings can address
    two different files on the same box.

    `read_only=True` builds SQLite's URI form with `mode=ro`, which makes the
    driver REFUSE a missing file instead of creating an empty one. The default
    stays read-write because that is what a persistence seam is for; a caller
    whose job is to REPORT on the database rather than use it asks for `ro`,
    and then cannot manufacture the state it is reporting on.
    """
    resolved = str(Path(db_path).resolve())
    if read_only:
        escaped = _uri_quote(resolved, safe="/")
        return f"{ASYNC_SQLITE_DRIVER}:///file:{escaped}?mode=ro&uri=true"
    return str(URL.create(ASYNC_SQLITE_DRIVER, database=resolved))


def create_async_engine_for(db_path: str | os.PathLike[str], *,
                            read_only: bool = False,
                            **kwargs: Any) -> AsyncEngine:
    """An `AsyncEngine` bound to the SQLite file at `db_path`.

    `db_path` is whatever the caller's own resolver returned -- for the live
    database that is `bulk_downloader.db._resolve_db_path()`. Keyword arguments
    pass through to `create_async_engine`, so a caller can set `echo` or a pool
    without this signature growing a flag per option.
    """
    resolved = str(Path(db_path).resolve())
    if read_only:
        escaped = _uri_quote(resolved, safe="/")
        url: str | URL = f"{ASYNC_SQLITE_DRIVER}:///file:{escaped}?mode=ro&uri=true"
    else:
        url = URL.create(ASYNC_SQLITE_DRIVER, database=resolved)
    return create_async_engine(url, **kwargs)


def async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions for `engine`, with `expire_on_commit` off.

    The default re-fetches every attribute after a commit, which on SQLite
    means a second round trip per committed object for values the caller just
    wrote. The existing sqlite3 writers have no such behaviour, so leaving the
    default on would make the seam slower than the code it is meant to replace.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
