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
import subprocess
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
    "test_v3_66_1266_mod3_mirror_scope.py",
    "test_v3_66_1678_mod3_shadow_scope.py",
)


def _dsn():
    # One source of truth: never the product's MOD3_PG_DSN (see the
    # pg-test-dsn-only block at the end of this file).
    import mod3_pg_isolation as iso
    return iso.real_dsn()


def _skip_without_pg():
    if not _dsn():
        # Rule 46: in the shard that exists to run these, absent is a failure.
        if os.environ.get("GITHUB_JOB") == "postgres-integration":
            pytest.fail("PG-SHARD-UNARMED: postgres-integration ran without "
                        "MOD3_PG_TEST_DSN; every real-PG file would skip green")
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
    # SYNTHETIC module names (sg-pg-band-host, stale gate 2026-10-03). This probe used to plant its
    # keyless `history(site_id, url, status)` in the REAL 801/804 schemas and leave it behind; 801/804
    # running later against the same database then hit `CREATE TABLE IF NOT EXISTS` as a no-op and
    # every id-aligned mirror INSERT died on `column "id" does not exist` (UndefinedColumn). The real
    # names must still map to distinct schemas -- that is pure and checked here without touching them.
    assert iso.schema_for("test_v3_66_801_mod3_shadow_read.py") != \
        iso.schema_for("test_v3_66_804_mod3_cutover.py")
    a = iso.schema_for("test_v3_66_1011_isolation_probe_a.py")
    b = iso.schema_for("test_v3_66_1011_isolation_probe_b.py")
    # ...and the probe's schemas are NOBODY's real schema: they are dropped below, and dropping a
    # real module's schema would wipe it under that module when the lane runs them concurrently.
    real = {iso.schema_for(m) for m in _REAL_PG_MODULES}
    assert not {a, b} & real, "the isolation probe must never create or drop a real module's schema"
    try:
        _cross_module_delete_probe(psycopg, iso, base, a, b)
    finally:
        with psycopg.connect(base) as c:
            for schema in (a, b):
                c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            c.commit()


def _cross_module_delete_probe(psycopg, iso, base, a, b):
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


# ── CI postgres-integration shard census (FLEET_RULE 46) ──────────

def test_postgres_integration_shard_reports_zero_skips_for_real_pg_files():
    """CENSUS (ROW127-1678-SHARD-SKIP):
    When MOD3_PG_TEST_DSN is armed, executing the postgres-integration shard
    in CI list order must report 0 skips across all real-PG test files.
    Fail-closed: missing schemas or connection failures must fail, never skip (FLEET_RULE 46).
    """
    _skip_without_pg()
    base_dsn = _dsn()

    ci_yml = REPO / ".github" / "workflows" / "ci.yml"
    assert ci_yml.is_file(), f"missing {ci_yml}"
    lines = ci_yml.read_text(encoding="utf-8").splitlines()
    shard_files = []
    in_pg_job = False
    in_run_step = False
    for line in lines:
        if line.strip().startswith("postgres-integration:"):
            in_pg_job = True
            continue
        if in_pg_job:
            if line and not line.startswith(" ") and not line.startswith("\t"):
                break
            if "run: |" in line:
                in_run_step = True
                continue
            if in_run_step:
                stripped = line.strip().rstrip("\\").strip()
                if stripped.startswith("tests/test_") and stripped.endswith(".py"):
                    shard_files.append(stripped)
                elif (stripped and not stripped.startswith("python -m pytest")
                      and not stripped.startswith("#")
                      and not line.startswith("            ")):
                    in_run_step = False

    assert len(shard_files) >= 5, f"expected at least 5 shard files, found: {shard_files}"
    for f in shard_files:
        assert (REPO / f).is_file(), f"shard file does not exist: {f}"

    # Verify that all real-PG modules are part of this shard
    for m in _REAL_PG_MODULES:
        assert any(f.endswith(m) for f in shard_files), (
            f"real-PG module {m} is missing from postgres-integration shard in ci.yml"
        )

    # Execute the exact shard list in a clean subprocess
    env = dict(os.environ, MOD3_PG_TEST_DSN=base_dsn)
    env.pop("MOD3_PG_DSN", None)

    # Row 127: this module is itself in the shard; running it here would re-enter
    # this census without end.
    cmd = [
        sys.executable, "-m", "pytest", "-q",
        *[f for f in shard_files if pathlib.Path(f).name != pathlib.Path(__file__).name]
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, (
        f"shard run failed (rc={res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    # Must report zero skips: if any tests skipped, "skipped" will appear in the pytest summary line
    summary_line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    assert "skipped" not in summary_line.lower() or " 0 skipped" in summary_line, (
        f"postgres-integration shard reported skips in real-PG files:\n{summary_line}\nFull stdout:\n{res.stdout}"
    )


# ── pg-test-dsn-only: the PRODUCT DSN never arms a real-PG test ───────────────
#
# MEASURED 2026-10-16 22:34-22:51Z (deflake-1901, DF-D): `import bulk_downloader`
# seeds MOD3_PG_DSN from ~/BulkDownloader/.env (bulk_downloader/_envfile.py
# `_candidate_paths()`), and the helpers above fell back to it, so an ad-hoc
# pytest on a host with an install ran the real-PG modules -- unscoped DELETE,
# ALTER TABLE -- against the hub's PRODUCT database. Nine iterations, before
# anyone noticed. Two guards, both asserted here:
#   * the helpers read ONLY MOD3_PG_TEST_DSN; the product DSN is never a source;
#   * tests/conftest.py neutralises the host .env before the package is imported.
# Every DSN below is a sentinel on port 1: nothing in this block can reach a
# server, on any host, whatever it is run with.

_SENTINEL_PRODUCT = "postgresql://bd_product_sentinel@127.0.0.1:1/product_sentinel"
_SENTINEL_TEST = "postgresql://bd_test_sentinel@127.0.0.1:1/test_sentinel"
_REAL_PG_TARGET = "tests/test_v3_66_800_mod3_dual_write.py::TestRealRoundTrip"
_NO_TEST_DSN = "no MOD3_PG_TEST_DSN in the environment"


def test_the_product_dsn_is_never_a_real_pg_source(monkeypatch):
    import mod3_pg_isolation as iso
    monkeypatch.delenv("MOD3_PG_TEST_DSN", raising=False)
    monkeypatch.setenv("MOD3_PG_DSN", _SENTINEL_PRODUCT)
    assert iso.real_dsn() == "", "PG-PRODUCT-DSN-FALLBACK: mod3_pg_isolation.real_dsn()"
    assert _dsn() == "", "PG-PRODUCT-DSN-FALLBACK: test_v3_66_1011 _dsn()"
    monkeypatch.setenv("MOD3_PG_TEST_DSN", _SENTINEL_TEST)
    assert iso.real_dsn() == _SENTINEL_TEST
    assert _dsn() == _SENTINEL_TEST


def test_the_pg_shard_fails_loudly_when_its_dsn_is_missing(monkeypatch):
    """Rule 46: with the fallback gone, a postgres-integration job whose
    MOD3_PG_TEST_DSN went missing would skip every real-PG file and stay green.
    GITHUB_JOB is the job id on GitHub and under act, so the shard needs no flag."""
    monkeypatch.delenv("MOD3_PG_TEST_DSN", raising=False)
    monkeypatch.setenv("GITHUB_JOB", "postgres-integration")
    with pytest.raises(pytest.fail.Exception, match="PG-SHARD-UNARMED"):
        _skip_without_pg()
    monkeypatch.setenv("GITHUB_JOB", "gate-suites")
    with pytest.raises(pytest.skip.Exception, match=_NO_TEST_DSN):
        _skip_without_pg()


_PROBE = """
import json, os, sys
_skips = []
def pytest_runtest_logreport(report):
    if report.skipped and isinstance(report.longrepr, tuple):
        _skips.append(report.longrepr[2])
def pytest_sessionfinish(session, exitstatus):
    sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
    import mod3_pg_isolation
    with open(os.environ["PROBE_OUT"], "w") as f:
        json.dump({"exitstatus": int(exitstatus),
                   "product_dsn": os.environ.get("MOD3_PG_DSN", ""),
                   "real_dsn": mod3_pg_isolation.real_dsn(),
                   "skips": _skips}, f)
"""


def _host_with_an_install(tmp_path):
    """A child env with NOTHING inherited but PATH: HOME is a temp dir holding
    the `~/BulkDownloader/.env` an installed host carries. Built, not copied --
    the parent runs with BD_ENVFILE=/dev/null and MOD3_PG_DSN= exported, and an
    inherited env would never read the .env, so the test could not go red."""
    assert not (REPO / ".env").exists(), "COULD NOT LOOK: a cwd .env in the tree"
    home = tmp_path / "home"
    (home / "BulkDownloader").mkdir(parents=True)
    (home / "BulkDownloader" / ".env").write_text(
        "MOD3_PG_DSN=%s\n" % _SENTINEL_PRODUCT, encoding="utf-8")
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    (probe_dir / "pgdsnprobe.py").write_text(_PROBE, encoding="utf-8")
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
            "PYTHONPATH": "%s:%s" % (probe_dir, REPO),
            "PROBE_OUT": str(tmp_path / "probe.json"),
            "BD_DISABLE_KEEPALIVE": "1"}


def _child_pytest(env):
    import json
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pgdsnprobe", "-p", "no:randomly",
         "-p", "no:cacheprovider", "-q", "-rs", _REAL_PG_TARGET],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)
    out = pathlib.Path(env["PROBE_OUT"])
    assert out.is_file(), "child pytest never finished:\n%s\n%s" % (
        proc.stdout[-3000:], proc.stderr[-3000:])
    return json.loads(out.read_text(encoding="utf-8")), proc


def test_the_host_envfile_is_reachable_without_the_conftest(tmp_path):
    """Rule 7 control. The package alone DOES seed the product DSN from the
    temp-HOME .env; without this, a green test below could mean the .env was
    never in reach."""
    env = _host_with_an_install(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import os, bulk_downloader; print(os.environ.get('MOD3_PG_DSN', ''))"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
    assert proc.stdout.strip().splitlines()[-1:] == [_SENTINEL_PRODUCT], (
        proc.stdout[-2000:], proc.stderr[-2000:])


def test_a_host_envfile_cannot_arm_the_real_pg_modules(tmp_path):
    got, proc = _child_pytest(_host_with_an_install(tmp_path))
    assert got["product_dsn"] == "", (
        "PG-HOST-ENVFILE-BLEED: conftest let the host .env seed MOD3_PG_DSN=%r"
        % got["product_dsn"])
    assert got["real_dsn"] == "", (
        "PG-PRODUCT-DSN-FALLBACK: real-PG helper armed with %r" % got["real_dsn"])
    assert got["skips"] and all(_NO_TEST_DSN in s for s in got["skips"]), (
        "PG-PRODUCT-DSN-FALLBACK: real-PG tests were not skipped for want of a "
        "test DSN: %r\n%s" % (got["skips"], proc.stdout[-3000:]))


def test_a_test_dsn_still_arms_the_real_pg_modules(tmp_path):
    """The other half: the dedicated test DSN still reaches the helper, so the
    guard above is not just 'never run'. Port 1 refuses, so they get past the
    DSN gate and stop at the server -- never at the absent-DSN skip."""
    env = _host_with_an_install(tmp_path)
    env["MOD3_PG_TEST_DSN"] = _SENTINEL_TEST
    got, proc = _child_pytest(env)
    assert got["real_dsn"] == _SENTINEL_TEST, got
    assert got["product_dsn"] == "", got
    assert not any(_NO_TEST_DSN in s for s in got["skips"]), (
        got["skips"], proc.stdout[-3000:])


# ── r2 (cx62 F1): an INHERITED BD_ENVFILE, and the pgvector fixtures ──────────
#
# MEASURED 2026-10-17 (cx62, connect-interception, no DB touched): r1's
# `os.environ.setdefault("BD_ENVFILE", os.devnull)` let an inherited BD_ENVFILE
# survive -- a path, or "" (which `resolve_envfile_path()` treats as absent) --
# so the package import re-seeded MOD3_PG_DSN after the pop, and the two
# pgvector fixtures, which still read PGVECTOR_DSN / MOD3_PG_DSN, picked it up
# (CREATE EXTENSION, DROP/CREATE SCHEMA, inserts); with -n 2 in every worker.
# Here psycopg.connect is REPLACED before collection: every call is recorded and
# raises, so whatever the code under test selects, no server is contacted.

_PGVECTOR_NODES = (
    "tests/test_pgvector_hybrid_search.py::TestPgvectorHybridSearchAcceptance"
    "::test_1_create_hybrid_table_and_insert_docs",
    "tests/test_row875_pgvector_hnsw.py::TestRow875Acceptance"
    "::test_1_create_vector_table_and_insert_50k",
)

_CONNECT_PROBE = """
import json, os
import psycopg
_calls = []
def _intercept(dsn="", *args, **kwargs):
    _calls.append(str(dsn))
    raise psycopg.OperationalError("PG-CONNECT-INTERCEPTED: no server is contacted")
psycopg.connect = _intercept
def pytest_sessionfinish(session, exitstatus):
    path = os.path.join(os.environ["PROBE_DIR"], "connect-%d.json" % os.getpid())
    with open(path, "w") as f:
        json.dump({"worker": os.environ.get("PYTEST_XDIST_WORKER", "controller"),
                   "product_dsn": os.environ.get("MOD3_PG_DSN", ""),
                   "calls": _calls}, f)
"""


def _connect_probe_run(tmp_path, targets, *, envfile, extra=(), **env_over):
    """Child pytest on a temp-HOME 'installed host' (its ~/BulkDownloader/.env
    carries the product sentinel) with BD_ENVFILE INHERITED as given (None =
    unset). Returns one record per process (controller + any xdist workers)."""
    import json
    env = _host_with_an_install(tmp_path)
    home_env = pathlib.Path(env["HOME"]) / "BulkDownloader" / ".env"
    if envfile is not None:
        env["BD_ENVFILE"] = envfile.replace("{home_env}", str(home_env))
    env.update(env_over)
    probe_dir = tmp_path / "connects"
    probe_dir.mkdir()
    (tmp_path / "probe" / "pgconnectprobe.py").write_text(_CONNECT_PROBE, encoding="utf-8")
    env["PROBE_DIR"] = str(probe_dir)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # MEASURED 2026-10-17 on wrk-test06 (24 cores, load1 1.6): slowest child 8.66s (xdist-2), the
    # rest 4.0-4.7s. 120s is ~14x that -- room for band-load inflation -- and below the 240s item
    # bound (test_v3_66_1222), so a hung child fails here instead of killing the worker.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pgconnectprobe", "-p", "xdist.plugin",
         "-p", "no:randomly", "-p", "no:cacheprovider", "-q", "-rs",
         *extra, *targets],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
    records = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted(probe_dir.glob("connect-*.json"))]
    assert records, "COULD NOT LOOK: child pytest wrote no connect record:\n%s\n%s" % (
        proc.stdout[-3000:], proc.stderr[-3000:])
    return records, proc


@pytest.mark.parametrize("envfile, extra", [
    ("{home_env}", ()),                # inherited explicit path to the host .env
    ("", ()),                          # inherited empty string == 'absent' to the loader
    ("/nonexistent/bd-test.env", ()),  # inherited unreadable path -> loader falls back
    ("{home_env}", ("-n", "2")),       # every xdist worker inherits it
], ids=["explicit", "empty", "unreadable", "xdist-2"])
def test_an_inherited_envfile_cannot_reach_the_product_db(tmp_path, envfile, extra):
    records, proc = _connect_probe_run(tmp_path, _PGVECTOR_NODES, envfile=envfile,
                                       extra=extra)
    if extra:
        workers = {r["worker"] for r in records}
        assert {"gw0", "gw1"} <= workers, (
            "COULD NOT LOOK: xdist workers wrote no record: %r\n%s"
            % (workers, proc.stdout[-3000:]))
    product = [(r["worker"], c) for r in records for c in r["calls"]
               if "bd_product_sentinel" in c]
    assert product == [], (
        "PG-PRODUCT-CONNECT: %d connection(s) to the product DSN without "
        "MOD3_PG_TEST_DSN: %r" % (len(product), product))
    leaked = [(r["worker"], r["product_dsn"]) for r in records if r["product_dsn"]]
    assert leaked == [], "PG-HOST-ENVFILE-BLEED: MOD3_PG_DSN re-seeded: %r" % leaked


def test_an_ambient_product_dsn_cannot_arm_the_pgvector_fixtures(tmp_path):
    """MOD3_PG_DSN exported by the caller, never via the .env."""
    records, proc = _connect_probe_run(tmp_path, _PGVECTOR_NODES, envfile="/dev/null",
                                       MOD3_PG_DSN=_SENTINEL_PRODUCT,
                                       PGVECTOR_DSN=_SENTINEL_PRODUCT)
    product = [c for r in records for c in r["calls"] if "bd_product_sentinel" in c]
    assert product == [], "PG-PRODUCT-CONNECT: pgvector fixture used %r" % product


def test_the_connect_probe_can_see_a_connection(tmp_path):
    """Rule 7 control for the zeros above, true at RED and GREEN: with the test
    DSN set, the mod3 real-PG fixture DOES reach psycopg.connect, and the probe
    records it."""
    records, proc = _connect_probe_run(tmp_path, (_REAL_PG_TARGET,), envfile="/dev/null",
                                       MOD3_PG_TEST_DSN=_SENTINEL_TEST)
    calls = [c for r in records for c in r["calls"]]
    assert any("bd_test_sentinel" in c for c in calls), (calls, proc.stdout[-3000:])


def test_the_pgvector_fixtures_arm_on_the_test_dsn(tmp_path):
    """The other half for pgvector: MOD3_PG_TEST_DSN is now their only source,
    so with it set both fixtures connect -- to it -- rather than going dark."""
    records, proc = _connect_probe_run(tmp_path, _PGVECTOR_NODES, envfile="{home_env}",
                                       MOD3_PG_TEST_DSN=_SENTINEL_TEST)
    calls = [c for r in records for c in r["calls"]]
    assert sum("bd_test_sentinel" in c for c in calls) >= 2, (
        "PG-TEST-DSN-UNARMED: pgvector fixtures ignored MOD3_PG_TEST_DSN: %r\n%s"
        % (calls, proc.stdout[-3000:]))
    assert not any("bd_product_sentinel" in c for c in calls), calls
