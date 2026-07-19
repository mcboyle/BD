"""U24 — migration status (D-4). A read-only wrapper on the existing
migrations.py ledger; it must never apply a migration.

Note: these tests pin the *honest current behaviour* — on a fresh DB
the migrations module reports 6 pending migrations until they are
applied. (Earlier, detect_drift() also false-flagged a missing
`events` table; that was a dead table — three modules queried it but
nothing wrote it — and the dead reads plus the manifest entry have
since been removed, so a fresh DB now reports no drift.)
"""
from bulk_downloader import db, migrations
from bulk_downloader import dev_suite as ds


def test_status_reports_pending_on_fresh_db(clean_workdir):
    db.db_init()
    r = ds.migration_status()
    assert r["ok"] is True
    # the built-in migrations are registered but not yet applied
    assert r["registered_migrations"] == r["pending_count"]
    assert r["pending_count"] >= 1
    assert r["applied_versions"] == []


def test_status_reflects_applied_migrations(clean_workdir):
    db.db_init()
    migrations.apply_pending()
    r = ds.migration_status()
    assert r["pending_count"] == 0
    assert len(r["applied_versions"]) == r["registered_migrations"]


def test_status_surfaces_schema_drift(clean_workdir):
    db.db_init()
    r = ds.migration_status()
    # detect_drift is included verbatim from migrations.status()
    assert "drift" in r
    assert isinstance(r["drift_ok"], bool)
    # the verdict line must mention drift when drift_ok is False
    if not r["drift_ok"]:
        assert "drift" in r["verdict"]


def test_status_verdict_is_clean_when_nothing_pending_and_no_drift(
        clean_workdir, monkeypatch):
    db.db_init()
    migrations.apply_pending()
    # force a no-drift result so the clean-verdict branch is exercised
    monkeypatch.setattr(migrations, "detect_drift",
                        lambda: {"ok": True, "missing_tables": [],
                                 "missing_columns": {},
                                 "extra_tables": []})
    r = ds.migration_status()
    assert r["drift_ok"] is True
    assert r["pending_count"] == 0
    assert r["verdict"] == ("DB schema up to date — no pending "
                            "migrations, no drift")


def test_status_is_read_only(clean_workdir, monkeypatch):
    db.db_init()
    # the tool must NOT apply migrations — apply_pending stays untouched
    called = {"apply": False}
    real_apply = migrations.apply_pending

    def _spy(*a, **k):
        called["apply"] = True
        return real_apply(*a, **k)

    monkeypatch.setattr(migrations, "apply_pending", _spy)
    ds.migration_status()
    assert called["apply"] is False


# ── route ─────────────────────────────────────────────────────────

def test_migration_status_route(fresh_app):
    body = fresh_app.get("/api/dev/migration_status").get_json()
    assert body["ok"] is True
    assert "pending" in body and "applied_versions" in body
    assert "verdict" in body
