"""U27 — security cluster: dependency-audit (D-79), secret-scan
(D-80), path-allowlist tester (D-82), SAST/DAST viewer (D-78).

All read-only. None replace the tools/ SAST/DAST pipeline — these are
quick in-app checks plus a viewer for that pipeline's output.
"""
from bulk_downloader import dev_suite as ds
from bulk_downloader import app as bd_app


# ── dependency_audit (D-79) ────────────────────────────────────────

def test_dependency_audit_classifies_pins():
    r = ds.dependency_audit()
    assert r["ok"] is True
    req = r["files"]["requirements.txt"]
    assert req["present"] is True
    # every active dep falls into exactly one bucket
    assert req["total_active"] == (
        len(req["pinned"]) + len(req["bounded"]) + len(req["unpinned"]))
    assert req["total_active"] > 0


def test_dependency_audit_verdict_matches_unpinned():
    r = ds.dependency_audit()
    if r["unpinned_across_all"]:
        assert "unpinned" in r["verdict"]
    else:
        assert "pinned or bounded" in r["verdict"]


# ── secret_scan (D-80) ─────────────────────────────────────────────

def test_secret_scan_runs_and_skips_tests_dir():
    r = ds.secret_scan()
    assert r["ok"] is True
    assert r["files_scanned"] > 50
    # tests/ carries fake creds by design and must be skipped
    for f in r["findings"]:
        assert not f["file"].startswith("tests/"), f


def test_secret_scan_never_echoes_the_secret():
    r = ds.secret_scan()
    # F-COREBD02-01: the context must be REDACTED -- never the raw matched
    # secret. Every finding's context shows a redaction marker and stays short.
    for f in r["findings"]:
        ctx = f["context"]
        assert "redacted" in ctx.lower(), f"context not redacted: {ctx!r}"
        assert len(ctx) <= 80, f"context too long: {ctx!r}"


def test_secret_scan_detects_a_planted_shape(tmp_path, monkeypatch):
    # Keep the planted credential shape inside pytest's owned scratch tree.
    # Writing it into the shared checkout races whole-tree verifiers under
    # xdist: a verifier can enumerate the file immediately before this test
    # removes it, leaving its snapshot incomplete.
    from bulk_downloader.dev_suite import audit_security

    monkeypatch.setattr(audit_security, "_repo_root", lambda: tmp_path)
    planted = tmp_path / "_u27_secret_probe.py"
    planted.write_text("x = 'AKIA" + "ABCDEFGHIJKLMNOP'\n", encoding="utf-8")
    r = ds.secret_scan()
    hits = [f for f in r["findings"]
            if f["file"] == "_u27_secret_probe.py"]
    assert len(hits) == 1
    assert hits[0]["pattern"] == "aws_access_key"


def test_secret_scan_clamps_max_findings():
    assert ds.secret_scan(max_findings="bad")["ok"] is True
    assert ds.secret_scan(max_findings=99999)["ok"] is True


# ── path_allowlist_test (D-82) ─────────────────────────────────────

def test_path_allowlist_enforcing_mode():
    saved = bd_app._app_cfg.get("path_allowlist")
    try:
        bd_app._app_cfg["path_allowlist"] = ["/home/claude/bd"]
        r = ds.path_allowlist_test(
            test_paths=["/home/claude/bd/downloads", "/etc/passwd"])
        assert r["allowlist_configured"] is True
        by_path = {x["path"]: x for x in r["results"]}
        assert by_path["/home/claude/bd/downloads"]["accepted"] is True
        assert by_path["/etc/passwd"]["accepted"] is False
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


def test_path_allowlist_permissive_mode():
    saved = bd_app._app_cfg.get("path_allowlist")
    try:
        bd_app._app_cfg["path_allowlist"] = []
        r = ds.path_allowlist_test(
            test_paths=["/any/absolute", "relative/path", "../escape"])
        assert r["allowlist_configured"] is False
        by_path = {x["path"]: x for x in r["results"]}
        # permissive: any absolute path accepted
        assert by_path["/any/absolute"]["accepted"] is True
        # but still no relative paths, still no .. segments
        assert by_path["relative/path"]["accepted"] is False
        assert by_path["../escape"]["accepted"] is False
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


# ── sast_summary (D-78) ────────────────────────────────────────────

def test_sast_summary_reports_absence_cleanly():
    r = ds.sast_summary()
    assert r["ok"] is True
    # in the sandbox the pipeline has not been run
    assert "sast" in r["reports"] and "dast" in r["reports"]
    for kind in ("sast", "dast"):
        rep = r["reports"][kind]
        if not rep["present"]:
            assert "hint" in rep


# ── routes ─────────────────────────────────────────────────────────

def test_dependency_audit_route(fresh_app):
    body = fresh_app.get("/api/dev/dependency_audit").get_json()
    assert body["ok"] is True


def test_secret_scan_route(fresh_app):
    body = fresh_app.get("/api/dev/secret_scan").get_json()
    assert body["ok"] is True
    assert "findings" in body


def test_path_allowlist_test_route(fresh_app):
    body = fresh_app.get("/api/dev/path_allowlist_test").get_json()
    assert body["ok"] is True
    assert "allowlist_mode" in body


def test_sast_summary_route(fresh_app):
    body = fresh_app.get("/api/dev/sast_summary").get_json()
    assert body["ok"] is True
    assert "reports" in body
