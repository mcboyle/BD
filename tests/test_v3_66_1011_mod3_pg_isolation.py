"""@1011. The real-Postgres MOD3 files stop sharing one `history` table.

MEASURED ON THE BOX. Three captures were taken on 2026-08-10 at d2fa6bb, ab9cbcb
and 342001e. The first carried exactly one unit failure and the other two did
not:

    tests/test_v3_66_801_mod3_shadow_read.py:217
      assert after["diverged"] == before["diverged"], after
    AssertionError: {'compared': 2, 'matched': 0, 'diverged': 2, 'skipped': 2}
    assert 2 == 1

Passing twice afterwards is not a fix, it is a coin landing the other way. The
mechanism is not in doubt:

  * FIVE MOD3 files run in capture.sh's PARALLEL lane, so with `--dist loadfile`
    they are on different xdist workers AT THE SAME TIME.
  * All of them point at ONE Postgres database -- the single MOD3_PG_TEST_DSN
    the operator exports -- and therefore at one `public.history` table.
  * Four scope their cleanup to their own rows
    (`DELETE FROM history WHERE site_id = %s`). test_v3_66_804_mod3_cutover.py
    does a bare `DELETE FROM history`, wiping the table for everyone.

804 is not being careless: `preflight_cutover()` compares whole-table counts
between SQLite and Postgres, so it genuinely needs a table holding only its own
rows. Scoping its DELETE would break it. The two requirements are incompatible
ON A SHARED TABLE and perfectly compatible on separate ones.

So the fix is a schema per test module, which is the pattern the PRODUCT already
uses for exactly this reason -- pg_backend.rehearse_migration builds a scratch
`mod3_rehearsal_<uuid>` schema and says why: "Scratch schema: isolated from the
live mirror, so a rehearsal can [run] without touching it."

MEASURED, not assumed, before any of this was written -- against a live cluster,
with `?options=-csearch_path%3D<schema>` on the DSN:

    search_path                                  = bd_probe_iso
    rows in the isolated schema                  = 1
    rows in public                               = 0
    rows in the isolated schema after `DELETE FROM history` in public = 1

That last line is the whole cut: the unscoped delete cannot reach another
module's rows.

WHAT THIS FILE DOES NOT CLAIM. Isolation is asserted between MODULES, which is
the granularity xdist's `--dist loadfile` distributes at -- two tests in one file
still share a schema, and they are on one worker in sequence, so they cannot
race. A future switch to `--dist load` would break that assumption, and the
module-scope reasoning here would need re-deriving rather than trusting.
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

# The real-Postgres MOD3 modules. test_v3_66_795_mod3_seam.py is deliberately
# absent: it names no DSN env var and its `INSERT INTO history` is SQLite, so
# including it would assert an edit that has no reason to exist.
_REAL_PG_MODULES = (
    "test_v3_66_800_mod3_dual_write.py",
    "test_v3_66_801_mod3_shadow_read.py",
    "test_v3_66_803_mod3_migration_rehearsal.py",
    "test_v3_66_804_mod3_cutover.py",
)


def _dsn():
    return (os.environ.get("MOD3_PG_DSN")
            or os.environ.get("MOD3_PG_TEST_DSN") or "").strip()


def _skip_without_pg():
    if not _dsn():
        pytest.skip("no MOD3_PG_TEST_DSN in the environment")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pytest.skip("psycopg not installed")


# ── the helper exists and is sane ─────────────────────────────────

def test_the_isolation_helper_exists():
    import mod3_pg_isolation as iso
    for name in ("schema_for", "isolated_dsn", "ensure_schema"):
        assert hasattr(iso, name), "mod3_pg_isolation.%s is missing" % name


def test_each_module_gets_its_OWN_schema_name():
    """Distinct, or the whole cut is decorative."""
    import mod3_pg_isolation as iso
    names = [iso.schema_for(m) for m in _REAL_PG_MODULES]
    assert len(set(names)) == len(names), names


def test_the_schema_name_is_a_legal_unquoted_identifier():
    """A name needing quotes would work in `CREATE SCHEMA "x"` and then fail in
    the `-csearch_path=x` connection option, which does not quote. That failure
    is silent: libpq falls back and the test lands in public -- shared again,
    with every assertion still green."""
    import re
    import mod3_pg_isolation as iso
    for m in _REAL_PG_MODULES:
        s = iso.schema_for(m)
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", s), (m, s)
        assert len(s) <= 63, (m, s, len(s))   # PostgreSQL NAMEDATALEN - 1


def test_the_same_module_always_gets_the_same_schema():
    """Deterministic across processes. xdist workers are separate processes and
    each computes this independently; a random name would give the two halves of
    one module's run different schemas."""
    import mod3_pg_isolation as iso
    a = [iso.schema_for(m) for m in _REAL_PG_MODULES]
    b = [iso.schema_for(m) for m in _REAL_PG_MODULES]
    assert a == b


def test_the_isolated_dsn_keeps_the_base_and_adds_the_search_path():
    import mod3_pg_isolation as iso
    base = "postgresql://u:p@127.0.0.1:5432/db"
    got = iso.isolated_dsn(base, "bd_t_abc")
    assert got.startswith(base), got
    assert "search_path" in got and "bd_t_abc" in got, got
    # `=` inside a URI query value must be percent-encoded or libpq mis-splits
    assert "%3D" in got, got

    # and it must compose with a DSN that already carries a query string
    got2 = iso.isolated_dsn(base + "?connect_timeout=5", "bd_t_abc")
    assert "connect_timeout=5" in got2 and "&" in got2, got2


# ── every real-PG module actually routes through it ───────────────

def test_the_module_scan_can_see_its_subjects():
    """Non-empty denominator, before the verdict below."""
    missing = [m for m in _REAL_PG_MODULES if not (REPO / "tests" / m).is_file()]
    assert not missing, "these MOD3 modules moved or were renamed: %r" % missing


def test_every_real_pg_module_routes_its_dsn_through_the_isolator():
    """The edit that matters. A helper nothing calls is a comment.

    KEYED ON `dsn_for` AND ON THE IMPORT, and the first draft of this test is
    the reason. It accepted a call to `ensure_schema`, which is ALSO the name of
    a function on the product's pg_backend -- every one of these four modules
    already calls `pg.ensure_schema()`, so the check passed on four files that
    had not been edited at all. It reported clean, truthfully, about a different
    function. That is CLAUDE.md section 0 inside the gate written to fix a
    section 0 defect, and only noticing it went green before the edits existed
    caught it.

    `dsn_for` is defined in exactly one place in the repository, so the
    predicate cannot bind to something else. The import is asserted as well:
    a call alone could be to a same-named local.

    AST rather than text, because this file's own prose names the helper
    repeatedly and a grep-based version would pass on the docstring.
    """
    offenders = []
    for m in _REAL_PG_MODULES:
        tree = ast.parse((REPO / "tests" / m).read_text(encoding="utf-8"))
        imported = any(
            (isinstance(n, ast.Import)
             and any(a.name == "mod3_pg_isolation" for a in n.names))
            or (isinstance(n, ast.ImportFrom) and n.module == "mod3_pg_isolation")
            for n in ast.walk(tree))
        called = any(
            isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == "dsn_for")
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == "dsn_for"))
            for n in ast.walk(tree))
        if not (imported and called):
            offenders.append("%s (import=%s call=%s)" % (m, imported, called))
    assert not offenders, (
        "these modules still use the raw shared DSN, so they share "
        "public.history with every other MOD3 file on every other worker: %r"
        % offenders)


def test_that_predicate_would_NOT_accept_the_products_ensure_schema():
    """The refutation of the first draft, kept as a test so the mistake cannot
    come back as a 'simplification'. Every one of these modules calls the
    product's `pg.ensure_schema()`; that must not, on its own, satisfy the
    routing check above."""
    src = "import x\nclass P:\n    pass\npg = P()\npg.ensure_schema()\n"
    tree = ast.parse(src)
    imported = any(
        (isinstance(n, ast.Import)
         and any(a.name == "mod3_pg_isolation" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and n.module == "mod3_pg_isolation")
        for n in ast.walk(tree))
    called = any(
        isinstance(n, ast.Call)
        and ((isinstance(n.func, ast.Name) and n.func.id == "dsn_for")
             or (isinstance(n.func, ast.Attribute) and n.func.attr == "dsn_for"))
        for n in ast.walk(tree))
    assert not (imported and called), (
        "a bare pg.ensure_schema() satisfies the routing predicate -- that is "
        "the false pass this test exists to prevent")


def test_the_unscoped_delete_still_exists_and_is_now_HARMLESS():
    """The cutover module's bare `DELETE FROM history` is not a defect to
    remove -- preflight_cutover() compares whole-table counts and needs a table
    holding only its own rows. This asserts it is still there, so that if
    someone 'fixes' it by scoping it, they have to come here and read why.

    If it ever legitimately goes away, delete this test rather than weakening
    it: an assertion kept alive past its subject is the dead exclusion problem.
    """
    src = (REPO / "tests" / "test_v3_66_804_mod3_cutover.py").read_text(
        encoding="utf-8")
    assert 'c.execute("DELETE FROM history")' in src, (
        "the unscoped delete is gone; if that was deliberate, remove this test")


# ── the collision, live, in both directions ───────────────────────

def test_an_unscoped_delete_in_one_schema_cannot_reach_another(tmp_path):
    """THE BOX'S FAILURE, MADE DETERMINISTIC.

    No race: 804's exact statement is executed against its own schema while a
    sibling module's row sits in a different one. On the shared table this
    removes the sibling's row -- which is precisely how
    test_agreeing_stores_compare_and_match saw `matched: 0` on a row it had
    just written.
    """
    _skip_without_pg()
    import psycopg
    import mod3_pg_isolation as iso

    base = _dsn()
    a = iso.schema_for("test_v3_66_801_mod3_shadow_read.py")
    b = iso.schema_for("test_v3_66_804_mod3_cutover.py")
    iso.ensure_schema(base, a)
    iso.ensure_schema(base, b)

    for schema in (a, b):
        with psycopg.connect(iso.isolated_dsn(base, schema)) as c:
            c.execute("CREATE TABLE IF NOT EXISTS history"
                      "(site_id text, url text, status text)")
            c.execute("DELETE FROM history")
            c.commit()

    with psycopg.connect(iso.isolated_dsn(base, a)) as c:
        c.execute("INSERT INTO history(site_id, status) VALUES (%s,%s)",
                  ("agree", "done"))
        c.commit()

    # 804's statement, verbatim, in ITS schema
    with psycopg.connect(iso.isolated_dsn(base, b)) as c:
        c.execute("DELETE FROM history")
        c.commit()

    with psycopg.connect(iso.isolated_dsn(base, a)) as c:
        n = c.execute("SELECT count(*) FROM history WHERE site_id=%s",
                      ("agree",)).fetchone()[0]
    assert n == 1, (
        "the sibling's row was destroyed by another module's unscoped DELETE -- "
        "the schemas are not isolated")


def test_dsn_for_CREATES_the_schema_it_hands_back():
    """CLOSES A MEASURED MUTATION ESCAPE. Deleting the `ensure_schema(...)` call
    from `dsn_for` left the whole band green, because every schema this suite
    names already existed from an earlier run -- the residue hid the defect, and
    on a fresh database it would have been an immediate hard failure.

    So the schema is DROPPED first, and a synthetic module name is used so no
    real module's data is touched. The memo is cleared directly: it exists to
    skip the CREATE after the first call, which is exactly the behaviour under
    test here, and reaching past it is the point rather than a shortcut.
    """
    _skip_without_pg()
    import psycopg
    import mod3_pg_isolation as iso

    base = iso.real_dsn()
    probe = "test_v3_66_1011_probe_module.py"      # not a real module
    schema = iso.schema_for(probe)
    assert schema not in [iso.schema_for(m) for m in _REAL_PG_MODULES]

    try:
        with psycopg.connect(base) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            c.commit()
        iso._ensured.discard((base, schema))

        dsn = iso.dsn_for(probe)
        assert dsn, "dsn_for returned nothing with a reachable server"

        with psycopg.connect(base) as c:
            n = c.execute(
                "SELECT count(*) FROM information_schema.schemata "
                "WHERE schema_name = %s", (schema,)).fetchone()[0]
        assert n == 1, (
            "dsn_for handed back a DSN pointing at schema %r, which does not "
            "exist -- on a fresh database every real-PG test would fail on its "
            "first CREATE TABLE" % schema)
    finally:
        with psycopg.connect(base) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            c.commit()
        iso._ensured.discard((base, schema))


def test_the_isolation_is_REAL_and_not_a_name_that_lands_in_public(tmp_path):
    """THE OVER-CONFIDENT DIRECTION. Everything above passes if `isolated_dsn`
    silently fails to pin the search_path, because then BOTH connections land in
    `public` and the row is deleted... which the test above WOULD catch. This
    catches the subtler half: both landing in public while nothing deletes.

    Ask the server, never the string.
    """
    _skip_without_pg()
    import psycopg
    import mod3_pg_isolation as iso
    base = _dsn()
    s = iso.schema_for("test_v3_66_801_mod3_shadow_read.py")
    iso.ensure_schema(base, s)
    with psycopg.connect(iso.isolated_dsn(base, s)) as c:
        got = c.execute("SHOW search_path").fetchone()[0]
    assert s in got and "public" not in got.split(","), (
        "search_path is %r, not the isolated schema -- the option did not take, "
        "and every MOD3 module is still sharing public.history" % got)
