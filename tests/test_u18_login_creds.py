"""U18 — login-template dry-run (D-24) + credential-reference resolver
(D-26). The dry-run runs the real learn.merge_learned on a deep copy;
the resolver classifies @cred references without decrypting anything.
"""
import copy
import types

from bulk_downloader import dev_suite as ds
from bulk_downloader import login_templates_data as _lt


def _runner(cfg):
    return {"s1": types.SimpleNamespace(config=cfg)}


def _a_real_template_id():
    return _lt.list_login_templates()[0]["id"]


# ── login_template_dry_run (D-24) ─────────────────────────────────

def test_dry_run_requires_template_and_site():
    assert ds.login_template_dry_run("", site_id="s1")["ok"] is False
    assert ds.login_template_dry_run("x", site_id="")["ok"] is False


def test_dry_run_rejects_unknown_template():
    r = ds.login_template_dry_run("no_such_template_xyz", site_id="s1",
                                  runners=_runner({"name": "S"}))
    assert r["ok"] is False
    assert "unknown" in r["error"]


def test_dry_run_rejects_unknown_site():
    r = ds.login_template_dry_run(_a_real_template_id(), site_id="ghost",
                                  runners=_runner({"name": "S"}))
    assert r["ok"] is False


def test_dry_run_shows_learned_login_change():
    tid = _a_real_template_id()
    cfg = {"name": "Site", "login_url": "https://x/login"}
    r = ds.login_template_dry_run(tid, site_id="s1", runners=_runner(cfg))
    assert r["ok"] is True
    assert r["template_has_selectors"] is True
    assert r["learned_login_before"] is None
    assert r["learned_login_after"] is not None
    assert r["would_change"] is True
    assert r["would_set_applied_login_template"] == tid


def test_dry_run_does_not_mutate_the_real_config():
    tid = _a_real_template_id()
    cfg = {"name": "Site", "login_url": "https://x/login"}
    original = copy.deepcopy(cfg)
    ds.login_template_dry_run(tid, site_id="s1", runners=_runner(cfg))
    # the dry-run runs merge_learned on a COPY — the real config is intact
    assert cfg == original


# ── credential_resolver (D-26) ────────────────────────────────────

def test_credential_resolver_classifies_kinds():
    runners = {
        "plain": types.SimpleNamespace(config={"password": "hunter2"}),
        "public": types.SimpleNamespace(config={"password": ""}),
        "ref": types.SimpleNamespace(
            config={"password": "@cred:bulkdl-site-ref"}),
    }
    r = ds.credential_resolver(runners=runners)
    kinds = {s["site_id"]: s["credentials"][0]["kind"] for s in r["sites"]}
    assert kinds["plain"] == "plaintext"
    assert kinds["public"] == "empty"
    assert kinds["ref"] == "cred_reference"


def test_credential_resolver_flags_unresolved_reference():
    r = ds.credential_resolver(runners=_runner(
        {"password": "@cred:definitely_not_a_real_label_999"}))
    assert r["unresolved_references"] == 1
    cred = r["sites"][0]["credentials"][0]
    assert cred["resolves"] is False
    assert cred["vault_label"] == "definitely_not_a_real_label_999"


def test_credential_resolver_covers_account_pool():
    cfg = {"password": "@cred:top",
           "accounts": [{"password": "@cred:acct0"},
                        {"password": "plainpw"}]}
    r = ds.credential_resolver(runners=_runner(cfg))
    fields = [c["field"] for c in r["sites"][0]["credentials"]]
    assert "password" in fields
    assert "accounts[0].password" in fields
    assert "accounts[1].password" in fields


def test_credential_resolver_never_emits_a_value():
    # the resolver output must not contain the plaintext password
    r = ds.credential_resolver(runners=_runner({"password": "topsecret"}))
    blob = repr(r)
    assert "topsecret" not in blob


def test_credential_resolver_reports_backend():
    r = ds.credential_resolver(runners=_runner({"password": ""}))
    assert r["ok"] is True
    assert isinstance(r["vault_backend"], str) and r["vault_backend"]


# ── routes ────────────────────────────────────────────────────────

def test_login_template_dryrun_route_needs_params(fresh_app):
    resp = fresh_app.get("/api/dev/login_template_dryrun")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False


def test_credential_resolver_route(fresh_app):
    resp = fresh_app.get("/api/dev/credential_resolver")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool"] == "credential_resolver"
    assert body["ok"] is True
