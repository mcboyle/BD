"""v3.66.337 — Guided Capture Cut 1: the 3 preflight / widening routes.

RED-first. Pins the contract for the guided-mode backend surface:
  R1  GET  /api/captures/validate_download_dir?path=  — read-only allowlist check
  R2  POST /api/template_manager/promote_check {file}  — read-only promote preflight
  R3  POST /api/captures/allowlist_add {path, confirm} — gated + audited widening

All three reuse existing single-source predicates (``_validate_path``,
``promote_gate_errors`` / ``lint_template``, ``_app_cfg`` / ``_save_app_config``
+ the ``audit`` module) and add NO new authority: R1/R2 are read-only; R3 is the
one write and is confirm-gated + audited. Zero-arg fns + tempfile so it runs under
the custom runner and pytest.
"""
import json
import tempfile

# THB-3 (v3.66.528): restore path_allowlist to its EXACT prior state. The old pattern
# (saved = _app_cfg.get("path_allowlist"); finally: _app_cfg["path_allowlist"] = saved)
# wrote None (or []) into _app_cfg when the key had been ABSENT -> absent->None/[] global
# pollution leaking into later tests. _save copies when present (the allowlist_add route
# mutates the list in place) and _restore pops when the key was absent.
_MISSING = object()


def _save_allowlist(A):
    v = A._app_cfg.get("path_allowlist", _MISSING)
    return _MISSING if v is _MISSING else list(v)


def _restore_allowlist(A, prior):
    if prior is _MISSING:
        A._app_cfg.pop("path_allowlist", None)
    else:
        A._app_cfg["path_allowlist"] = prior


def _new_client():
    """Paired test client -> (client, csrf, A-module)."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    r = c.get("/api/pair"); token = r.get_json()["token"]
    r = c.post("/api/pair/redeem", json={"token": token})
    csrf = r.get_json()["csrf_token"]
    return c, csrf, A


# ─────────────────────────── R1: validate_download_dir ───────────────────────────

def test_r1_empty_allowlist_accepts_any_abs():
    c, _csrf, A = _new_client()
    saved = _save_allowlist(A)
    try:
        A._app_cfg["path_allowlist"] = []          # permissive
        b = c.get("/api/captures/validate_download_dir?path=/tmp/dl").get_json()
        assert b["ok"] is True, b
    finally:
        _restore_allowlist(A, saved)


def test_r1_outside_allowlist_rejected():
    c, _csrf, A = _new_client()
    saved = _save_allowlist(A)
    try:
        root = tempfile.mkdtemp()
        other = tempfile.mkdtemp()                  # sibling, NOT under root
        A._app_cfg["path_allowlist"] = [root]
        r = c.get(f"/api/captures/validate_download_dir?path={other}")
        assert r.status_code == 200, r.status_code
        b = r.get_json()
        assert b["ok"] is False, b
        assert b.get("error"), b
    finally:
        _restore_allowlist(A, saved)


def test_r1_inside_allowlist_ok():
    c, _csrf, A = _new_client()
    saved = _save_allowlist(A)
    try:
        root = tempfile.mkdtemp()
        A._app_cfg["path_allowlist"] = [root]
        b = c.get(f"/api/captures/validate_download_dir?path={root}/sub").get_json()
        assert b["ok"] is True, b
    finally:
        _restore_allowlist(A, saved)


def test_r1_relative_rejected():
    c, _csrf, _A = _new_client()
    r = c.get("/api/captures/validate_download_dir?path=relative/dir")
    assert r.status_code == 200, r.status_code
    assert r.get_json()["ok"] is False, r.get_json()


def test_r1_traversal_rejected():
    c, _csrf, _A = _new_client()
    r = c.get("/api/captures/validate_download_dir?path=/tmp/../etc")
    assert r.status_code == 200, r.status_code
    assert r.get_json()["ok"] is False, r.get_json()


# ─────────────────────────────── R2: promote_check ───────────────────────────────

_DRAFT_SUFFIX = ".template-draft.json"


def _good_draft():
    # Mirrors the proven-passing shape from test_promote_accept_api: candidate
    # schema, specific selectors, resolutions, a media-relevant pattern.
    return {
        "schema": "bulk_downloader.template.review_candidate.v1",
        "host": "gcw337good.example.com",
        "status": "draft_review_required",
        "selectors": {"download": {"trigger": ".dl", "row_selectors": [".row"]},
                      "player": {"play_button": ".play"}},
        "resolutions": [1080, 720],
        "network_patterns": ["https://example.com/video/play.mp4"],
    }


def _write_draft(name, obj):
    from bulk_downloader import template_manager as tm
    tm.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    p = tm.DRAFTS_DIR / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _reviewed_count():
    from bulk_downloader import template_manager as tm
    return len(list(tm.REVIEWED_DIR.glob("*"))) if tm.REVIEWED_DIR.exists() else 0


def test_r2_clean_draft_ok():
    c, csrf, _A = _new_client()
    name = "_gcw337_good" + _DRAFT_SUFFIX
    p = _write_draft(name, _good_draft())
    try:
        b = c.post("/api/template_manager/promote_check", json={"file": name},
                   headers={"X-CSRF-Token": csrf}).get_json()
        assert b["ok"] is True, b
    finally:
        p.unlink(missing_ok=True)


def test_r2_bad_terms_blocked():
    c, csrf, _A = _new_client()
    d = _good_draft(); d["host"] = "gcw337bad.example.com"
    d["network_patterns"] = ["https://example.com/video/play.mp4",
                             "https://stats.doubleclick.net/beacon"]
    name = "_gcw337_bad" + _DRAFT_SUFFIX
    p = _write_draft(name, d)
    try:
        b = c.post("/api/template_manager/promote_check", json={"file": name},
                   headers={"X-CSRF-Token": csrf}).get_json()
        assert b["ok"] is False, b
        assert "doubleclick" in " ".join(b.get("gate_errors") or []).lower(), b
    finally:
        p.unlink(missing_ok=True)


def test_r2_unsafe_selector_lint():
    c, csrf, _A = _new_client()
    d = _good_draft(); d["host"] = "gcw337unsafe.example.com"
    d["selectors"] = {"download": {"trigger": "a", "row_selectors": ["nav a"]}}
    name = "_gcw337_unsafe" + _DRAFT_SUFFIX
    p = _write_draft(name, d)
    try:
        b = c.post("/api/template_manager/promote_check", json={"file": name},
                   headers={"X-CSRF-Token": csrf}).get_json()
        assert b["ok"] is False, b
        assert b.get("lint_warnings"), b
    finally:
        p.unlink(missing_ok=True)


def test_r2_is_read_only_no_reviewed_written():
    c, csrf, _A = _new_client()
    name = "_gcw337_good2" + _DRAFT_SUFFIX
    p = _write_draft(name, _good_draft())
    before = _reviewed_count()
    try:
        c.post("/api/template_manager/promote_check", json={"file": name},
               headers={"X-CSRF-Token": csrf})
        assert _reviewed_count() == before, "promote_check must not write reviewed/"
    finally:
        p.unlink(missing_ok=True)


# ─────────────────────────── R3: allowlist_add (gated write) ───────────────────────────

def test_r3_confirm_absent_rejected():
    c, csrf, _A = _new_client()
    root = tempfile.mkdtemp()
    r = c.post("/api/captures/allowlist_add", json={"path": root},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, r.status_code


def test_r3_confirm_false_rejected():
    c, csrf, _A = _new_client()
    root = tempfile.mkdtemp()
    r = c.post("/api/captures/allowlist_add",
               json={"path": root, "confirm": False},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, r.status_code


def test_r3_confirm_true_appends_and_audits():
    c, csrf, A = _new_client()
    from bulk_downloader import audit as _audit
    saved = _save_allowlist(A)
    root = tempfile.mkdtemp()
    try:
        r = c.post("/api/captures/allowlist_add",
                   json={"path": root, "confirm": True},
                   headers={"X-CSRF-Token": csrf})
        b = r.get_json()
        assert r.status_code == 200, (r.status_code, b)
        assert b["ok"] is True, b
        assert root in (A._app_cfg.get("path_allowlist") or []), A._app_cfg.get("path_allowlist")
        rows = _audit.audit_for_target("path_allowlist", limit=20)
        assert any(root in json.dumps(row) for row in rows), rows
    finally:
        _restore_allowlist(A, saved)


def test_r3_duplicate_no_double_append():
    c, csrf, A = _new_client()
    saved = _save_allowlist(A)
    root = tempfile.mkdtemp()
    try:
        for _ in range(2):
            c.post("/api/captures/allowlist_add",
                   json={"path": root, "confirm": True},
                   headers={"X-CSRF-Token": csrf})
        assert (A._app_cfg.get("path_allowlist") or []).count(root) == 1
    finally:
        _restore_allowlist(A, saved)


def test_r3_relative_rejected():
    c, csrf, _A = _new_client()
    r = c.post("/api/captures/allowlist_add",
               json={"path": "relative/dir", "confirm": True},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, r.status_code


def test_r3_traversal_rejected():
    c, csrf, _A = _new_client()
    r = c.post("/api/captures/allowlist_add",
               json={"path": "/tmp/../etc/evil", "confirm": True},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, r.status_code
