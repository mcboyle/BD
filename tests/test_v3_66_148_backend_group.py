"""v3.66.148 — backend group tests.

  #10 Template Manager (`template_manager`) — list reviewed/drafts; promote a
      draft (explicit, refuses unsafe selectors, never auto-enables); disable a
      reviewed template; path-safe filenames.
  #7  Worker profile seed (`/api/sites/<sid>/profile/seed`) — wraps profile_sync;
      value-free response; 404 for unknown site.
  #8  Per-site profile storage status (`profile_sync.profile_storage_status` +
      `/api/sites/<sid>/profile/status`) — presence/size/mtime only, no values.

Risk-fix locks:
  * diagnostics bundle redacts secrets and never carries profile/cookie storage.
  * backup `_iter_files` excludes `.sync_backups` rotating copies.
"""
from __future__ import annotations

import json
import contextlib
import zipfile
from pathlib import Path

import bulk_downloader.app as bd_app
from bulk_downloader import profile_sync as ps
from bulk_downloader import template_manager as tm
from bulk_downloader import template_registry as tr
from bulk_downloader import backup as bk
from bulk_downloader import diagnostics_bundle as db


REPTYLE = "https://app.reptyle.com/"


def _seed(sid, **cfg):
    cfg.setdefault("name", sid)
    bd_app.s_cfg[sid] = cfg
    return cfg


# ── #8 profile_storage_status ────────────────────────────────────────────

def _mk_profile(root: Path, site: str, profile: str, *, cookies=True, ls=False):
    data = root / site / profile / "Default"
    data.mkdir(parents=True, exist_ok=True)
    if cookies:
        (data / "Cookies").write_bytes(b"SQLite format 3\x00fakecookiedb")
    if ls:
        (data / "Local Storage").mkdir(exist_ok=True)
        (data / "Local Storage" / "leveldb.log").write_bytes(b"xx")


def test_profile_status_reports_presence_size_mtime_no_values(tmp_path):
    root = tmp_path / "profiles"
    _mk_profile(root, "s1", "manual", cookies=True, ls=True)
    _mk_profile(root, "s1", "w0", cookies=True)
    _mk_profile(root, "s1", "keepalive_0", cookies=False)
    res = ps.profile_storage_status("s1", profiles_root=str(root))
    assert res["present"] is True
    site = res["sites"][0]
    assert site["site"] == "s1"
    kinds = {p["profile"]: p["kind"] for p in site["profiles"]}
    assert kinds == {"manual": "manual", "w0": "worker",
                     "keepalive_0": "keeper"}
    manual = next(p for p in site["profiles"] if p["profile"] == "manual")
    ck = next(i for i in manual["items"] if i["name"] == "Cookies")
    assert ck["present"] is True and ck["bytes"] > 0 and ck["mtime"]
    ls = next(i for i in manual["items"] if i["name"] == "Local Storage")
    assert ls["present"] is True
    keeper = next(p for p in site["profiles"] if p["profile"] == "keepalive_0")
    assert keeper["present"] is False
    # value-free: item rows expose only metadata keys
    assert set(ck.keys()) == {"name", "present", "bytes", "mtime"}


def test_profile_status_endpoint(fresh_app):
    _seed("p1", login_url=REPTYLE)
    r = fresh_app.get("/api/sites/p1/profile/status")
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_profile_status_unknown_site_404(fresh_app):
    r = fresh_app.get("/api/sites/ghost/profile/status")
    assert r.status_code == 404


# ── #7 worker seed endpoint ──────────────────────────────────────────────

def test_seed_endpoint_no_manual_profile(fresh_app):
    _seed("seed1", login_url=REPTYLE)
    r = fresh_app.post("/api/sites/seed1/profile/seed", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["seeded"] == []
    assert "no manual profile" in (body["skipped_reason"] or "")
    # value-free contract: seeded entries expose only metadata keys
    allowed = {"profile", "items", "count", "backup_count", "last_backup"}
    assert all(set(s.keys()) <= allowed for s in body["seeded"])


def test_seed_endpoint_unknown_site_404(fresh_app):
    r = fresh_app.post("/api/sites/ghost/profile/seed", json={})
    assert r.status_code == 404


def test_seed_logic_copies_named_items(tmp_path):
    # The endpoint wraps this; prove the seed copies item NAMES (no values).
    root = tmp_path / "profiles"
    _mk_profile(root, "s2", "manual", cookies=True, ls=True)
    (root / "s2" / "w0" / "Default").mkdir(parents=True, exist_ok=True)
    summary = ps.sync_manual_to_runtime("s2", profiles_root=str(root),
                                        ensure=["main"])
    assert summary["skipped_reason"] is None
    # at least the seeded 'main' + existing 'w0' received Cookies
    copied_all = {item for items in summary["synced"].values() for item in items}
    assert "Cookies" in copied_all


# ── #10 template manager (pure, temp dirs — never touch repo templates) ──

def _write(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), "utf-8")


@contextlib.contextmanager
def _seeded_reptyle_draft():
    """Self-seed a reptyle draft from the shipped reviewed gold into the real
    templates/drafts (the dir the live /api/template_manager reads via
    tr.PROJECT_ROOT) when absent, then remove ONLY what we seeded. The clean zip
    ships only the reviewed gold, so the drafts-list assertion is otherwise an
    env-coupled sandbox-RED / stash-GREEN divergence. The 515 pattern."""
    root = Path(tr.PROJECT_ROOT)
    fname = "app.reptyle.com.template-draft.json"
    gold = root / "templates" / "reviewed" / "app.reptyle.com.template.json"
    drafts = root / "templates" / "drafts"
    cands = root / "templates" / "review_candidates"
    pre_existing = (cands / fname).is_file() or (drafts / fname).is_file()
    seeded = None
    if not pre_existing:
        assert gold.is_file(), f"shipped reviewed gold missing: {gold}"
        cand = json.loads(gold.read_text("utf-8"))
        cand["host"] = "app.reptyle.com"
        cand["status"] = "draft"
        drafts.mkdir(parents=True, exist_ok=True)
        seeded = drafts / fname
        seeded.write_text(json.dumps(cand), "utf-8")
    try:
        yield
    finally:
        if seeded is not None and seeded.is_file():
            seeded.unlink()


def test_list_templates_endpoint(fresh_app):
    with _seeded_reptyle_draft():
        r = fresh_app.get("/api/template_manager")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        hosts = {t["host"] for t in body["reviewed"]}
        assert "app.reptyle.com" in hosts
        reptyle = next(t for t in body["reviewed"] if t["host"] == "app.reptyle.com")
        assert reptyle["enabled"] is True
        assert reptyle["has_blocking_lint"] is False
        assert any(t["file"].endswith(".template-draft.json") for t in body["drafts"])


def test_promote_clean_draft(tmp_path):
    rd, dd = tmp_path / "reviewed", tmp_path / "drafts"
    _write(dd / "good.test.template-draft.json", {
        "host": "good.test", "status": "draft",
        "selectors": {"download": {
            "row_selectors": ['[role="dialog"] a[href*="download" i]']}},
        "network_patterns": ["https://good.test/video/play.mp4"],
        "resolutions": [1080]})
    res = tm.promote_draft("good.test.template-draft.json",
                           reviewed_dir=str(rd), drafts_dir=str(dd))
    assert res["ok"] is True and res["enabled"] is True
    assert (rd / "good.test.template.json").is_file()
    # now matchable as a reviewed/enabled template
    t = tr.find_template_for_url("https://good.test/x", template_dirs=[rd])
    assert t is not None and t["host"] == "good.test"


def test_promote_refuses_unsafe_draft(tmp_path):
    rd, dd = tmp_path / "reviewed", tmp_path / "drafts"
    _write(dd / "bad.test.template-draft.json", {
        "host": "bad.test", "status": "draft",
        # a candidate (carries a network_patterns list) so it is NOT re-normalized;
        # the unsafe generic row then survives to the selector-lint "unsafe" refusal.
        "selectors": {"download": {"row_selectors": ["a"]}},
        "network_patterns": ["https://bad.test/x.mp4"]})
    res = tm.promote_draft("bad.test.template-draft.json",
                           reviewed_dir=str(rd), drafts_dir=str(dd))
    assert res["ok"] is False
    assert "unsafe" in res["error"]
    assert not (rd / "bad.test.template.json").exists()


def test_promote_rejects_bad_filename(tmp_path):
    res = tm.promote_draft("../../etc/passwd",
                           reviewed_dir=str(tmp_path), drafts_dir=str(tmp_path))
    assert res["ok"] is False and "invalid" in res["error"]


def test_disable_reviewed(tmp_path):
    rd = tmp_path / "reviewed"
    _write(rd / "x.test.template.json", {
        "host": "x.test", "status": "enabled",
        "selectors": {"download": {"row_selectors": [".modal a"]}},
        "resolutions": [720]})
    assert tr.find_template_for_url("https://x.test/", template_dirs=[rd])
    res = tm.disable_reviewed("x.test.template.json", reviewed_dir=str(rd))
    assert res["ok"] is True and res["enabled"] is False
    # disabled template no longer matches
    assert tr.find_template_for_url("https://x.test/", template_dirs=[rd]) is None


def test_promote_endpoint_validation(fresh_app):
    r = fresh_app.post("/api/template_manager/promote", json={"file": "../evil"})
    assert r.status_code == 400
    r = fresh_app.post("/api/template_manager/promote",
                       json={"file": "nope.template-draft.json"})
    assert r.status_code == 400


# ── risk-fix locks ───────────────────────────────────────────────────────

def test_backup_excludes_sync_backups(tmp_path):
    src = tmp_path / "cookies"
    (src).mkdir()
    (src / "real.json").write_text("{}")
    sb = src / ".sync_backups" / "20260604T000000" 
    sb.mkdir(parents=True)
    (sb / "Cookies").write_bytes(b"stale")
    arcnames = [arc for _, arc in bk._iter_files(tmp_path, src)]
    assert "cookies/real.json" in arcnames
    assert not any(".sync_backups" in a for a in arcnames)


def test_diagnostics_bundle_redacts_and_excludes_profiles(tmp_path):
    res = db.bundle(s_cfg={"s1": {
        "name": "s1", "cookies": "SECRETCOOKIEVAL", "password": "PW12345",
        "auth_token": "TOK", "cookies_path": "/home/u/cookies/s1.json"}})
    j = json.dumps(res)
    assert "SECRETCOOKIEVAL" not in j and "PW12345" not in j and "TOK" not in j
    assert ".sync_backups" not in j
    # zip carries only the redacted JSON — no profile/cookie storage files
    dest = tmp_path / "diag.zip"
    db.bundle_as_zip(str(dest), s_cfg={"s1": {"name": "s1",
                                              "cookies": "SECRETCOOKIEVAL"}})
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert not any("profiles" in n or ".sync_backups" in n or "Cookies" in n
                   for n in names)
    assert "SECRETCOOKIEVAL" not in (tmp_path / "diag.zip").read_bytes().decode(
        "latin-1")
