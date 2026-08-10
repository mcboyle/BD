"""One Postgres schema per test module, for the real-PG MOD3 files.

WHY THIS EXISTS. Five MOD3 test modules run in capture.sh's PARALLEL lane, so
`--dist loadfile` puts them on different xdist workers AT THE SAME TIME, and
they all point at the ONE database the operator exports as MOD3_PG_TEST_DSN.
That means one `public.history` table shared by concurrent writers.

Four of them scope their cleanup to their own rows. test_v3_66_804_mod3_cutover
runs a bare `DELETE FROM history`, and it is right to: `preflight_cutover()`
compares whole-table counts between SQLite and Postgres, so it needs a table
holding only its own rows. Those two requirements cannot both hold on a shared
table and both hold trivially on separate ones.

Measured consequence, on the box at d2fa6bb: one capture in three failed
test_agreeing_stores_compare_and_match with `matched: 0, diverged: 2` on a row
the test had just written -- because the row was deleted underneath it.

THE PRODUCT ALREADY DOES THIS. pg_backend.rehearse_migration builds a scratch
`mod3_rehearsal_<uuid>` schema for the same reason, in its own words: "Scratch
schema: isolated from the live mirror, so a rehearsal can [run] without touching
it." This is that idea applied to the test modules.

GRANULARITY IS THE MODULE, DELIBERATELY. That is what `--dist loadfile`
distributes, so two tests in one module are on one worker in sequence and cannot
race each other. A switch to `--dist load` would invalidate that reasoning --
re-derive it there rather than assuming this still holds.
"""
from __future__ import annotations

import hashlib
import os


_PREFIX = "bd_t_"


def real_dsn() -> str:
    """The operator's DSN, or "" when the real-PG lane is not armed."""
    return (os.environ.get("MOD3_PG_DSN")
            or os.environ.get("MOD3_PG_TEST_DSN") or "").strip()


def schema_for(module_name: str) -> str:
    """A stable, legal, per-module schema name.

    HASHED RATHER THAN DERIVED FROM THE FILENAME, for two reasons that are
    both failure modes rather than preferences:

      * PostgreSQL identifiers are capped at 63 bytes (NAMEDATALEN - 1) and
        `test_v3_66_803_mod3_migration_rehearsal.py` is already 42; a longer
        module name would be TRUNCATED by the server, and two modules sharing a
        truncated prefix would silently share a schema again -- the exact bug
        this module exists to remove, reintroduced by its own fix.
      * The name goes into `-csearch_path=<name>`, a connection option libqp
        does NOT quote. Anything needing quotes would fail to take effect and
        fall back to `public`, silently, with every assertion still green.

    Deterministic across processes: xdist workers are separate processes and
    each computes this independently, so a random component would give the two
    halves of one module's run different schemas.
    """
    digest = hashlib.sha256(module_name.encode("utf-8")).hexdigest()[:16]
    return _PREFIX + digest


def isolated_dsn(base_dsn: str, schema: str) -> str:
    """`base_dsn` with search_path pinned to `schema`.

    The `=` in `-csearch_path=x` is percent-encoded: inside a URI query value
    libpq splits on the first unencoded `=`, so the raw form arrives as the
    option `-csearch_path` with the value `x` and is rejected. Measured against
    a live cluster -- `SHOW search_path` returns the schema with %3D and does
    not without it.
    """
    sep = "&" if "?" in base_dsn else "?"
    return f"{base_dsn}{sep}options=-csearch_path%3D{schema}"


def ensure_schema(base_dsn: str, schema: str) -> None:
    """CREATE SCHEMA IF NOT EXISTS, over the BASE dsn.

    Over the base rather than the isolated one on purpose: connecting with
    search_path pointed at a schema that does not exist yet is legal but leaves
    unqualified DDL nowhere sensible to land.
    """
    import psycopg
    with psycopg.connect(base_dsn) as c:
        c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        c.commit()


_ensured: set = set()


def dsn_for(module_name: str) -> str:
    """The whole thing: "" when the lane is not armed, else an isolated DSN
    whose schema exists.

    Returning "" rather than raising keeps every caller's existing
    `if not dsn: skip` shape working unchanged.

    The CREATE is memoized per (base, schema) because callers reach this from
    `_pg_available()`, which runs once per test -- without it a 14-test module
    opens 14 extra connections to run a statement that is idempotent after the
    first. Keyed on the base DSN too, so a test that changes the target
    database mid-session is not handed a schema that was created elsewhere.
    """
    base = real_dsn()
    if not base:
        return ""
    schema = schema_for(module_name)
    key = (base, schema)
    if key not in _ensured:
        try:
            ensure_schema(base, schema)
        except Exception:
            # An unreachable server is the caller's _pg_available() story to
            # tell, with its own message. Handing back the un-isolated DSN
            # would be worse than useless -- it would look like isolation and
            # not be it. NOT memoized: a transient failure must not pin this
            # module to "" for the rest of the process.
            return ""
        _ensured.add(key)
    return isolated_dsn(base, schema)
