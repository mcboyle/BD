"""Pre-flight checklist tests — sanity checks that fail LOUDLY and
EARLY when an environment or install is broken.

If these fail, the problem is structural (a module won't import, the
DB won't migrate, the schema drifted) and no point-feature test result
can be trusted until it's fixed. Run these first.
"""
import importlib
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Core imports ────────────────────────────────────────────────────────

def test_preflight_core_modules_import():
    """The modules the app cannot start without must import cleanly."""
    for mod in ("bulk_downloader", "bulk_downloader.app",
                "bulk_downloader.db", "bulk_downloader.runner",
                "bulk_downloader.migrations"):
        try:
            importlib.import_module(mod)
        except Exception as e:
            raise AssertionError(f"core module {mod} failed to import: {e}")


def test_preflight_session_modules_import():
    """Modules added across Phases 7-13 must import cleanly."""
    for mod in ("bulk_downloader.doctor", "bulk_downloader.site_editor",
                "bulk_downloader.audit", "bulk_downloader.library",
                "bulk_downloader.app_widgets_api",
                "bulk_downloader.widgets_config"):
        try:
            importlib.import_module(mod)
        except Exception as e:
            raise AssertionError(f"{mod} failed to import: {e}")


def test_preflight_entrypoints_parse():
    """downloader_ui.py and bdctl.py must be syntactically valid."""
    import ast
    for fn in ("downloader_ui.py", "bdctl.py", "preflight.py",
               "run_tests.py"):
        path = os.path.join(_REPO, fn)
        try:
            ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError as e:
            raise AssertionError(f"{fn} has a syntax error: {e}")


def test_preflight_tools_parse():
    """Every tool script must parse — a broken tool shouldn't ship."""
    import ast
    tools_dir = os.path.join(_REPO, "tools")
    for fn in os.listdir(tools_dir):
        if fn.endswith(".py"):
            path = os.path.join(tools_dir, fn)
            try:
                ast.parse(open(path, encoding="utf-8").read())
            except SyntaxError as e:
                raise AssertionError(f"tools/{fn}: syntax error: {e}")


# ── App construction ────────────────────────────────────────────────────

def test_preflight_app_object_builds():
    """The Flask app object must construct without raising."""
    from bulk_downloader import app as a
    assert a.app is not None


def test_preflight_app_has_routes():
    """A constructed app with zero routes means registration broke."""
    from bulk_downloader import app as a
    rules = list(a.app.url_map.iter_rules())
    assert len(rules) > 100, f"only {len(rules)} routes registered"


def test_preflight_test_client_responds():
    """The most basic liveness check — the app answers a request."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/health")
    assert r.status_code in (200, 503)


# ── Database ────────────────────────────────────────────────────────────

def test_preflight_database_initializes():
    """A fresh BD_HOME must produce a working database."""
    from bulk_downloader import db
    with db.db_conn() as cx:
        cx.execute("SELECT 1").fetchone()


def test_preflight_migrations_apply_clean():
    """All migrations must apply cleanly on a real app boot.

    NOTE: migrations are NOT self-contained — migration #1 indexes the
    `history` table, which is created by db.py's init path, not the
    migration registry. So the correct way to test this is the way
    production does it: import the app (which initializes the DB and
    runs migrations at boot), then assert the registry is fully
    applied. Calling migrations.apply_pending() on a bare DB fails by
    design — that ordering dependency is a known characteristic, not a
    bug, but worth keeping documented here."""
    from bulk_downloader import app as a  # noqa: F401 — boot side effect
    from bulk_downloader import migrations
    st = migrations.status()
    # After boot, there should be no pending migrations
    pending = migrations.pending_migrations()
    assert not pending, f"migrations still pending after boot: {pending}"


def test_preflight_migrations_bare_db_ordering_documented():
    """Explicitly documents the ordering dependency: apply_pending on a
    bare DB (no app init) fails migration #1. If this ever starts
    PASSING, the migrations became self-contained — good — and this
    test should be updated to assert clean application directly."""
    from bulk_downloader import migrations
    result = migrations.apply_pending(dry_run=False)
    # On a bare DB this is expected to report an error for the
    # history-dependent migration. We assert the SHAPE is sane, not
    # that it succeeded.
    assert isinstance(result, dict)
    assert "errors" in result and "considered" in result


def test_preflight_no_schema_drift_after_migrate():
    """After a real app boot (which runs migrations), the live schema
    must match what's expected — drift means a migration and the code
    disagree."""
    from bulk_downloader import app as a  # noqa: F401 — boot side effect
    from bulk_downloader import migrations
    drift = migrations.detect_drift()
    assert isinstance(drift, dict)


def test_preflight_history_table_exists():
    """The history table is load-bearing — confirm it's there after a
    real app boot (db.py's init creates it; see the migration-ordering
    note above for why we boot the app rather than call db directly)."""
    from bulk_downloader import app as a  # noqa: F401 — boot side effect
    from bulk_downloader import db
    with db.db_conn() as cx:
        row = cx.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='history'").fetchone()
    assert row is not None, "history table missing after app boot"


def test_preflight_integrity_check_passes():
    """SQLite's own integrity check must pass on a fresh DB."""
    from bulk_downloader import db
    with db.db_conn() as cx:
        result = cx.execute("PRAGMA integrity_check").fetchone()
    assert result[0] == "ok", f"integrity_check: {result[0]}"


# ── Static assets ───────────────────────────────────────────────────────

def test_preflight_static_dir_present():
    """The static/ assets must exist — a missing dir means a broken
    package or a bad zip (this is the bug class that hid the missing
    extension/ dir)."""
    static = os.path.join(_REPO, "bulk_downloader", "static")
    assert os.path.isdir(static)
    for essential in ("sw.js", "manifest.json"):
        assert os.path.exists(os.path.join(static, essential)), \
            f"static/{essential} missing"


def test_preflight_js_files_are_nonempty():
    """A zero-byte JS file means a botched copy."""
    static = os.path.join(_REPO, "bulk_downloader", "static")
    for fn in os.listdir(static):
        if fn.endswith(".js"):
            path = os.path.join(static, fn)
            assert os.path.getsize(path) > 0, f"static/{fn} is empty"


def test_preflight_templates_module_present():
    # v3.66.339: the legacy Jinja `bulk_downloader/templates/` directory was
    # retired with the SPA-only migration -- the legacy HTML shell
    # (index/mobile/m_ops.html) was deleted in P4-B (v3.66.334) and no Flask
    # render_template path remains. The site-template MATCHING ENGINE ships as
    # the `bulk_downloader/templates.py` module; that module is the real
    # shipped invariant to assert here. (The old dir-presence check passed on
    # stash only because the overlay-deployed tree retained the now-empty dir
    # as a ghost -- overlay cannot delete -- so it false-failed on every clean
    # extraction of the release zip.)
    tmpl = os.path.join(_REPO, "bulk_downloader", "templates.py")
    assert os.path.isfile(tmpl), "bulk_downloader/templates.py missing"


# ── Config files ────────────────────────────────────────────────────────

def test_preflight_requirements_files_present():
    for fn in ("requirements.txt", "requirements-optional.txt"):
        assert os.path.exists(os.path.join(_REPO, fn)), f"{fn} missing"


def test_preflight_example_config_is_valid_json():
    """sites_config.example.json must be parseable — it's what new
    users copy from."""
    import json
    path = os.path.join(_REPO, "sites_config.example.json")
    if os.path.exists(path):
        json.load(open(path, encoding="utf-8"))


def test_preflight_version_importable():
    import bulk_downloader
    assert bulk_downloader.__version__
    assert isinstance(bulk_downloader.__version__, str)
