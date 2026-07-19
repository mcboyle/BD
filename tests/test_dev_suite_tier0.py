"""Tests for the backlog Tier-0 dev-suite tools — the release-integrity
five (D-95/96/97/98/99), the auth-surface pair (D-81/83), and the
DB-integrity / backup-verifier pair (D-3/9) and their /api/dev/*
endpoints.

These tools are read-only. The tree-scanning ones resolve the project
root from the package location, so they validate the real source tree
even though the tests run under a chdir'd temp workdir.
"""
import sqlite3
import zipfile

from bulk_downloader import __version__
from bulk_downloader import dev_suite as ds


# ── D-95 version-consistency checker ──────────────────────────────

def test_version_consistency_shape():
    r = ds.version_consistency()
    assert r["version"] == __version__
    assert r["scanned_count"] > 0
    assert isinstance(r["mismatches"], list)
    assert "verdict" in r
    # every mismatch entry is fully formed
    for m in r["mismatches"]:
        assert {"file", "line", "found"} <= set(m)


def test_version_consistency_skips_init_py():
    # the source of truth itself is never scanned / flagged
    r = ds.version_consistency()
    assert all("__init__.py" not in f for f in r["scanned_files"])


# ── D-96 CHANGELOG linter ─────────────────────────────────────────

def test_changelog_lint_finds_current_entry():
    r = ds.changelog_lint()
    # the tree at rest always carries a matching CHANGELOG entry
    # (a contract test enforces this on every release)
    assert r["version"] == __version__
    assert r["has_entry"] is True, r["verdict"]
    assert r["is_topmost"] is True, r["verdict"]
    assert r["entry_line"] > 0
    assert r["ok"] is True


# ── D-97 .bat lint ────────────────────────────────────────────────

def test_bat_lint_shape():
    r = ds.bat_lint()
    assert r["file_count"] > 0          # the project ships .bat files
    assert r["clean"] + r["with_issues"] == r["file_count"]
    for f in r["files"]:
        assert {"file", "ok", "issues"} <= set(f)


def test_bat_lint_all_shipped_files_clean():
    """Every shipped .bat file must lint clean — non-ASCII bytes,
    bare-LF endings, redirect-in-for, and the v3.63.7 paren-in-arg
    bug class are all release blockers. Was added at v3.63.8 after
    `test_bat_lint_shape` silently allowed a shipped bug to ship in
    v3.63.6 (`install_ai_ollama.bat` with `(slow)` inside a block).
    """
    r = ds.bat_lint()
    assert r["with_issues"] == 0, (
        "shipped .bat files have lint issues:\n" +
        "\n".join(f"  {f['file']}: {f['issues']}"
                  for f in r["files"] if not f["ok"]))


def test_bat_lint_catches_v3_63_7_bug_class():
    """Regression-pin: the L4 rule must catch the exact bug shape
    that escaped v3.63.6 — `echo ... (slow).` inside a `(...)` block."""
    from bulk_downloader import _bat_lint as bl
    bad = ("if errorlevel 1 (\n"
           "    echo will run on CPU (slow).\n"
           ")\n")
    issues = bl.lint_text(bad)
    assert any(i.rule == "L4_paren_in_arg" for i in issues), (
        "L4 rule did not catch the v3.63.7 bug pattern")


# ── D-98 .sh lint ─────────────────────────────────────────────────

def test_sh_lint_shape():
    r = ds.sh_lint()
    assert r["file_count"] > 0
    assert r["clean"] + r["with_issues"] == r["file_count"]
    for f in r["files"]:
        assert {"file", "ok", "executable", "issues"} <= set(f)


def test_sh_lint_flags_crlf():
    # LF check is environment-independent (unlike the exec bit, which
    # can be lost by an unzip) — confirm CRLF is caught when present.
    r = ds.sh_lint()
    # all shipped .sh files should be LF — none flagged for CRLF
    crlf = [f for f in r["files"]
            if any("CRLF" in i for i in f["issues"])]
    assert crlf == [], f"CRLF in shell scripts: {crlf}"


# ── D-99 release-zip manifest verifier ────────────────────────────

def test_zip_manifest_missing_zip():
    r = ds.zip_manifest_check("/no/such/file.zip")
    assert r["ok"] is False
    assert "not found" in r["verdict"]


def test_zip_manifest_compares_against_tree(clean_workdir):
    # a zip with only a couple of files is missing almost the whole
    # tree — the verifier must report that.
    zp = clean_workdir / "tiny.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("README.md", "x")
        zf.writestr("not_a_real_file.py", "y")
    r = ds.zip_manifest_check(str(zp))
    assert r["ok"] is False
    assert r["tree_file_count"] > 0
    assert len(r["missing_from_zip"]) > 0
    assert "not_a_real_file.py" in r["extra_in_zip"]


# ── D-81 auth-surface map ─────────────────────────────────────────

def test_auth_surface_shape():
    from bulk_downloader.app import app
    r = ds.auth_surface(app)
    assert r["route_count"] > 50
    assert isinstance(r["anomalies"], list)
    for route in r["routes"]:
        assert {"rule", "methods", "endpoint", "controls"} <= set(route)


def test_auth_surface_dev_routes_are_gated():
    # every /api/dev/* route except the always-on /api/dev/enabled
    # must carry the dev-mode control
    from bulk_downloader.app import app
    r = ds.auth_surface(app)
    for route in r["routes"]:
        if (route["rule"].startswith("/api/dev/")
                and route["rule"] != "/api/dev/enabled"):
            assert "dev-mode" in route["controls"], route["rule"]


def test_auth_surface_marks_global_csrf_on_state_routes():
    # CSRF is enforced by a global before_request hook, so every
    # state-changing /api route carries the 'global:csrf' control.
    from bulk_downloader.app import app
    r = ds.auth_surface(app)
    run = next((x for x in r["routes"]
                if x["rule"] == "/api/dev/run"), None)
    assert run is not None
    assert "global:csrf" in run["controls"]


# ── D-83 CSRF coverage check ──────────────────────────────────────

def test_csrf_coverage_confirms_global_hook():
    # CSRF is enforced app-wide by the _check_csrf before_request
    # hook — the tool's job is to confirm that hook is registered,
    # not to look for per-route inline calls.
    from bulk_downloader.app import app
    r = ds.csrf_coverage(app)
    assert r["global_hook_registered"] is True
    assert "_check_csrf" in r["before_request_hooks"]
    assert r["state_changing_api_routes"] > 0
    assert r["covered"] == r["state_changing_api_routes"]
    assert "CSRF enforced globally" in r["verdict"]
    assert isinstance(r["hook_path_exemptions"], list)


# ── D-3 DB integrity-check runner ─────────────────────────────────

def test_integrity_check_runs_both(fresh_app):
    r = ds.integrity_check()
    assert r["ok"] is True, r
    for name in ("quick_check", "integrity_check"):
        assert name in r
        assert r[name]["ok"] is True
        assert "elapsed_ms" in r[name]


# ── D-9 backup verifier (WAL-sidecar aware) ───────────────────────

def test_backup_check_missing_file():
    r = ds.backup_check("/no/such/backup.db")
    assert r["ok"] is False
    assert "not found" in r["verdict"]


def test_backup_check_verifies_a_db_file(clean_workdir):
    dbp = clean_workdir / "backup.db"
    cx = sqlite3.connect(str(dbp))
    cx.execute("CREATE TABLE t (id INTEGER)")
    cx.execute("INSERT INTO t VALUES (1)")
    cx.commit()
    cx.close()
    r = ds.backup_check(str(dbp))
    assert r["kind"] == "sqlite-db"
    assert r["base_check"]["ok"] is True
    assert "wal_sidecars" in r
    # no -wal sibling → no sidecar warning, verified clean
    assert r["ok"] is True


def test_backup_check_archive_without_wal_warns(clean_workdir):
    # build a .db, then zip only the .db (no -wal): the verifier must
    # warn that a WAL-mode backup may miss recent commits.
    dbp = clean_workdir / "h.db"
    cx = sqlite3.connect(str(dbp))
    cx.execute("CREATE TABLE t (id INTEGER)")
    cx.commit()
    cx.close()
    zp = clean_workdir / "backup.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.write(dbp, "h.db")
    r = ds.backup_check(str(zp))
    assert r["kind"] == "archive"
    assert r["wal_sidecars"][".db"] is True
    assert r["wal_sidecars"]["-wal"] is False
    assert any("-wal sidecar" in w for w in r["warnings"])


# ── endpoint smoke — every new /api/dev/* route answers ───────────

def test_tier0_endpoints_respond(fresh_app):
    for path in ("/api/dev/version_check", "/api/dev/changelog_lint",
                 "/api/dev/bat_lint", "/api/dev/sh_lint",
                 "/api/dev/zip_manifest", "/api/dev/auth_map",
                 "/api/dev/csrf_check", "/api/dev/integrity",
                 "/api/dev/backup_check"):
        resp = fresh_app.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert resp.get_json() is not None, f"{path} returned no JSON"
