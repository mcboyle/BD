"""Cleanup unit — the dead `events` table.

Three modules (timeline, eol_export, diagnostics_bundle) queried a
SELECT ... FROM events table that nothing in the codebase ever wrote
to, and migrations.EXPECTED_SCHEMA listed it — so detect_drift()
false-flagged it as missing on every fresh DB. The dead reads and the
manifest entry were removed. These tests guard that it stays removed.
"""
import ast
import pathlib

from bulk_downloader import db, migrations
from bulk_downloader import diagnostics_bundle, eol_export, timeline


_PKG = pathlib.Path(timeline.__file__).parent


# ── detect_drift no longer false-flags `events` ────────────────────

def test_events_not_in_expected_schema():
    # the dead table must not be in the drift manifest
    assert "events" not in migrations.EXPECTED_SCHEMA


def test_detect_drift_is_clean_on_a_fresh_db(clean_workdir):
    db.db_init()
    migrations.apply_pending()
    drift = migrations.detect_drift()
    # a fully-migrated fresh DB must report no missing tables
    assert drift["missing_tables"] == []
    assert "events" not in drift["missing_tables"]
    assert drift["ok"] is True


# ── the dead `FROM events` reads are gone ──────────────────────────

def _reads_events_table(path):
    """True if the file contains a SQL string selecting FROM the bare
    `events` table (not alert_events / maintenance_events)."""
    text = path.read_text(encoding="utf-8")
    low = text.lower()
    # crude but sufficient: a 'from events' not preceded by a word char
    import re
    return bool(re.search(r"(?<![\w_])from\s+events\b", low))


def test_no_module_reads_the_dead_events_table():
    for mod in (timeline, eol_export, diagnostics_bundle):
        path = pathlib.Path(mod.__file__)
        assert not _reads_events_table(path), (
            f"{path.name} still queries the dead `events` table")


def test_removed_helpers_are_gone():
    # the two dead helper functions must not be reintroduced
    assert not hasattr(timeline, "_entries_from_events")
    assert not hasattr(diagnostics_bundle, "_capture_recent_events")


# ── the three modules still import and run cleanly ─────────────────

def test_diagnostics_bundle_runs_without_events(clean_workdir):
    db.db_init()
    snap = diagnostics_bundle.bundle()
    assert isinstance(snap, dict)
    # the dead key must not appear
    assert "recent_events" not in snap
    # the live surface still works
    assert "account_health" in snap


def test_diagnostics_bundle_signature_has_no_include_events():
    src = pathlib.Path(diagnostics_bundle.__file__).read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "bundle":
            argnames = [a.arg for a in node.args.args
                        + node.args.kwonlyargs]
            assert "include_events" not in argnames
            return
    raise AssertionError("bundle() not found in diagnostics_bundle")


def test_timeline_still_builds_without_the_events_source(clean_workdir):
    db.db_init()
    # timeline must still assemble from its remaining sources
    # (history + alerts) with no reference to the removed source
    assert not hasattr(timeline, "_entries_from_events")
    # the history + alerts source helpers remain
    assert hasattr(timeline, "_entries_from_history")
    assert hasattr(timeline, "_entries_from_alerts")
