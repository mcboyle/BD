"""tpl95-bang-2 (O1517 A1-A finding T2): template_status ignored an applied user template.

Measured on test2 (tpl-bang/template-status-after-apply.json): after /api/sites/<sid>/templates/apply merged
trigger_selectors, row_selectors and url_attribute from a user template, template_status still said
"No reviewed template", template.enabled false, selectors []. GREEN: template_status reports the applied template
(id, name, source) and the learned roles it merged, and labels the card with it; a reviewed template still wins.

Hermetic: fresh_app (clean workdir, in-memory sqlite); the user template is saved into the test's workdir.
"""
from __future__ import annotations

import bulk_downloader.app as bd_app
from bulk_downloader import user_templates

BD_GATE_SCOPE = "module"

BANG_DOWNLOAD = {"row_selectors": ["a[href*='bngcdn.com']", "a:has-text('2160p')"],
                 "trigger_selectors": ["button:has-text('Download')"],
                 "url_attribute": "href"}


def _bang_site():
    sid, err = bd_app._create_site({"name": "bang", "login_url": "https://www.bang.com/login"})
    assert err is None and sid
    return sid


def _save_user_template():
    ok, tpl = user_templates.save_user_template(
        name="A1A bang O1517", description="test", patterns=[r"bang\.com"],
        learned={"download": dict(BANG_DOWNLOAD)})
    assert ok, tpl
    return tpl["id"]


def test_applied_user_template_is_reported_with_its_roles_and_source(fresh_app):
    sid, tid = _bang_site(), _save_user_template()
    applied = fresh_app.post(f"/api/sites/{sid}/templates/apply", json={"template_id": tid}).get_json()
    assert applied["ok"] and sorted(applied["added_roles"]) == sorted(BANG_DOWNLOAD), applied
    body = fresh_app.get(f"/api/sites/{sid}/template_status").get_json()
    assert body["ok"] is True, body
    assert body["template"]["enabled"] is False  # still no REVIEWED template for bang.com
    assert body["applied_template"] == {"id": tid, "name": "A1A bang O1517", "source": "user",
                                        "roles": sorted(BANG_DOWNLOAD)}, body
    assert body["label"] == "User template applied: A1A bang O1517", body


def test_applied_builtin_template_is_labelled_built_in(fresh_app):
    sid = _bang_site()
    assert bd_app._apply_template_by_id(sid, "video_js")[0]
    body = fresh_app.get(f"/api/sites/{sid}/template_status").get_json()
    assert body["applied_template"]["source"] == "builtin" and body["applied_template"]["roles"], body
    assert body["label"].startswith("Built-in template applied: "), body


def test_nothing_applied_is_unchanged(fresh_app):
    # (bang.com itself gets the bang_originals built-in applied at site creation)
    sid, err = bd_app._create_site({"name": "plain", "login_url": "https://auth.no-such-host.example/login"})
    assert err is None and bd_app.s_cfg[sid].get("applied_template") is None
    body = fresh_app.get(f"/api/sites/{sid}/template_status").get_json()
    assert body["applied_template"] is None
    assert body["label"] == "No reviewed template"


def test_unresolvable_applied_template_does_not_claim_roles(fresh_app):
    sid = _bang_site()
    bd_app.s_cfg[sid]["applied_template"] = "<auto-detect>"  # the registry auto-detect marker
    bd_app.s_cfg[sid].setdefault("learned", {})["download"] = dict(BANG_DOWNLOAD)
    body = fresh_app.get(f"/api/sites/{sid}/template_status").get_json()
    assert body["applied_template"] == {"id": "<auto-detect>", "name": "<auto-detect>", "source": "unknown",
                                        "roles": []}, body
    assert body["label"] == "No reviewed template"


def test_reviewed_template_label_still_wins(fresh_app):
    sid, err = bd_app._create_site({"name": "rep", "login_url": "https://app.reptyle.com/"})
    assert err is None
    tid = _save_user_template()
    assert fresh_app.post(f"/api/sites/{sid}/templates/apply", json={"template_id": tid}).get_json()["ok"]
    body = fresh_app.get(f"/api/sites/{sid}/template_status").get_json()
    assert body["template"]["enabled"] is True and body["label"].startswith("Reviewed Template: "), body
    assert body["applied_template"]["source"] == "user"
