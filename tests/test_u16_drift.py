"""U16 — duplicate-site (D-88) + orphan-row (D-7) + stale-reference
(D-92) drift detectors. Scoped around the existing config_integrity:
these cover login_url/name dups, DB-vs-config orphans, and reference
resolution — none of which config_integrity does.
"""
from bulk_downloader import dev_suite as ds
from bulk_downloader import db as _db


# ── duplicate_sites (D-88) ────────────────────────────────────────

def test_duplicate_sites_detects_normalised_url_and_name():
    cfgs = {
        "s1": {"name": "Alpha", "login_url": "https://alpha.example/login"},
        # same URL up to case + trailing slash, same name up to case
        "s2": {"name": "alpha", "login_url": "https://ALPHA.example/login/"},
        "s3": {"name": "Beta", "login_url": "https://beta.example/login"},
    }
    r = ds.duplicate_sites(site_configs=cfgs)
    assert len(r["duplicate_login_url_groups"]) == 1
    assert len(r["duplicate_name_groups"]) == 1
    grp = r["duplicate_login_url_groups"][0]
    assert sorted(grp["site_ids"]) == ["s1", "s2"]


def test_duplicate_sites_clean_when_all_distinct():
    cfgs = {
        "a": {"name": "A", "login_url": "https://a.example/login"},
        "b": {"name": "B", "login_url": "https://b.example/login"},
    }
    r = ds.duplicate_sites(site_configs=cfgs)
    assert r["duplicate_login_url_groups"] == []
    assert r["duplicate_name_groups"] == []
    assert "no duplicate" in r["verdict"]


# ── orphan_rows (D-7) ─────────────────────────────────────────────

def test_orphan_rows_clean_with_no_data(fresh_app):
    # fresh_app gives empty tables — nothing orphaned.
    r = ds.orphan_rows(site_configs={"realsite": {}})
    assert r["ok"] is True
    assert r["queue_orphan_rows"] == 0
    assert r["other_orphan_rows"] == 0


def test_orphan_rows_flags_unconfigured_site_id(fresh_app):
    # Insert a history row for a site that has no config.
    with _db.db_conn() as cx:
        cx.execute("INSERT INTO history (site_id, url, status) "
                   "VALUES (?,?,?)", ("ghost_site", "https://x/y", "done"))
    # ghost_site is not in the configured set -> orphan
    r = ds.orphan_rows(site_configs={"realsite": {}})
    assert r["ok"] is True
    assert r["other_orphan_rows"] >= 1
    hist = [t for t in r["tables"] if t["table"] == "history"][0]
    assert any(o["site_id"] == "ghost_site"
               for o in hist["orphan_site_ids"])


def test_orphan_rows_not_flagged_when_site_is_configured(fresh_app):
    with _db.db_conn() as cx:
        cx.execute("INSERT INTO history (site_id, url, status) "
                   "VALUES (?,?,?)", ("known_site", "https://x/z", "done"))
    r = ds.orphan_rows(site_configs={"known_site": {}})
    hist = [t for t in r["tables"] if t["table"] == "history"][0]
    assert all(o["site_id"] != "known_site"
               for o in hist["orphan_site_ids"])


def test_orphan_rows_queue_orphan_verdict(fresh_app):
    with _db.db_conn() as cx:
        cx.execute("INSERT INTO queue (site_id, url) VALUES (?,?)",
                   ("vanished", "https://q/1"))
    r = ds.orphan_rows(site_configs={})
    assert r["queue_orphan_rows"] >= 1
    assert "cannot run" in r["verdict"]


# ── stale_references (D-92) ───────────────────────────────────────

def test_stale_references_flags_all_four_kinds():
    cfgs = {
        "s1": {"name": "A",
               "cookie_file": "/nonexistent/cookies.json",
               "download_dir": "/no/such/dir",
               "login_template": "made_up_template",
               "password": "@cred:ghost_label_xyz"},
        "s2": {"name": "B"},
    }
    r = ds.stale_references(site_configs=cfgs)
    assert r["sites_with_stale_refs"] == 1
    kinds = {sr["kind"]
             for s in r["sites"] for sr in s["stale_references"]}
    assert kinds == {"cookie_file", "download_dir",
                     "login_template", "cred_reference"}


def test_stale_references_clean_config_has_none(tmp_path):
    # download_dir that DOES exist, no other refs -> clean
    cfgs = {"s1": {"name": "Clean", "download_dir": str(tmp_path)}}
    r = ds.stale_references(site_configs=cfgs)
    assert r["sites_with_stale_refs"] == 0
    assert "no stale" in r["verdict"]


def test_stale_references_accepts_a_real_login_template():
    # a genuine login-template id should NOT be flagged
    from bulk_downloader import login_templates_data as _lt
    real = _lt.list_login_templates()[0]["id"]
    cfgs = {"s1": {"name": "A", "login_template": real}}
    r = ds.stale_references(site_configs=cfgs)
    flagged = [sr for s in r["sites"]
               for sr in s["stale_references"]
               if sr["kind"] == "login_template"]
    assert flagged == []


# ── routes ────────────────────────────────────────────────────────

def test_duplicate_sites_route(fresh_app):
    resp = fresh_app.get("/api/dev/duplicate_sites")
    assert resp.status_code == 200
    assert resp.get_json()["tool"] == "duplicate_sites"


def test_orphan_rows_route(fresh_app):
    resp = fresh_app.get("/api/dev/orphan_rows")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_stale_references_route(fresh_app):
    resp = fresh_app.get("/api/dev/stale_references")
    assert resp.status_code == 200
    assert resp.get_json()["tool"] == "stale_references"
