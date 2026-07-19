"""v3.66.681 (B2/P5): federated template push/pull + review-on-receive.

Backend logic is tested directly with a synthetic template + temp template
dir (no real registry state); read-only routes are smoke-tested via the
Flask client; the peer import route is exercised without a shared token.
"""
import json
import pytest


_TEMPLATE = {
    "host": "example.test",
    "status": "enabled",
    "selectors": {"video": "video.player", "title": "h1.name"},
    "network_patterns": ["*/api/playback*"],
}


@pytest.fixture
def fed_env(tmp_path, monkeypatch):
    import bulk_downloader.template_registry as tr
    import bulk_downloader.federation as fed
    monkeypatch.setattr(tr, "DEFAULT_TEMPLATE_DIRS", [str(tmp_path)], raising=False)
    monkeypatch.setattr(tr, "load_templates", lambda *a, **k: [dict(_TEMPLATE)])
    # no shared fed_token in this env -> unsigned bundles accepted
    monkeypatch.setattr(fed, "_fed_token", lambda: "")
    return fed, tr, tmp_path


def test_list_shareable_and_build_bundle(fed_env):
    fed, tr, _ = fed_env
    shareable = fed.list_shareable_templates()
    assert any(t["host"] == "example.test" for t in shareable)
    bundle = fed.build_template_bundle("example.test")
    assert bundle is not None
    assert bundle["site_id"] == "example.test"
    assert isinstance(bundle["template"], dict)
    assert bundle["template"].get("host") == "example.test"


def test_build_bundle_unknown_host_is_none(fed_env):
    fed, _, _ = fed_env
    assert fed.build_template_bundle("nope.invalid") is None


def test_receive_queues_and_review_approve_writes_file(fed_env):
    fed, tr, tmp_path = fed_env
    bundle = fed.build_template_bundle("example.test")
    res = fed.receive_template("peer-A", bundle)
    assert res["ok"] is True
    pid = res["pending_id"]
    pend = fed.list_pending_templates()
    assert any(p["id"] == pid and p["status"] == "pending" for p in pend)
    # approve -> writes a non-destructive fed_<peer>_<host>.template.json
    rev = fed.review_pending_template(pid, "approve")
    assert rev["ok"] is True and rev["applied"] is True
    written = list(tmp_path.glob("fed_peer-A_example.test.template.json"))
    assert written, "approved template not written to store"
    data = json.loads(written[0].read_text())
    assert data["host"] == "example.test"
    assert data["_federated_from"] == "peer-A"
    assert data["status"] == "enabled"
    # queue row now approved
    assert not any(p["id"] == pid for p in fed.list_pending_templates())


def test_review_reject_does_not_write(fed_env):
    fed, tr, tmp_path = fed_env
    res = fed.receive_template("peer-B", fed.build_template_bundle("example.test"))
    rev = fed.review_pending_template(res["pending_id"], "reject")
    assert rev["ok"] is True and rev["applied"] is False
    assert not list(tmp_path.glob("fed_peer-B_*.template.json"))


def test_receive_rejects_invalid_bundle(fed_env):
    fed, _, _ = fed_env
    bad = fed.receive_template("peer-C", {"not": "a bundle"})
    assert bad["ok"] is False


def test_signed_bundle_roundtrip_verifies(tmp_path, monkeypatch):
    import bulk_downloader.template_registry as tr
    import bulk_downloader.federation as fed
    monkeypatch.setattr(tr, "DEFAULT_TEMPLATE_DIRS", [str(tmp_path)], raising=False)
    monkeypatch.setattr(tr, "load_templates", lambda *a, **k: [dict(_TEMPLATE)])
    monkeypatch.setattr(fed, "_fed_token", lambda: "shared-secret")
    bundle = fed.build_template_bundle("example.test")
    assert bundle.get("signature")  # signed with the shared token
    assert fed.receive_template("peer-D", bundle)["ok"] is True
    # tamper -> signature must fail
    bundle["template"]["host"] = "evil.test"
    assert fed.receive_template("peer-E", bundle)["ok"] is False


# ── routes ──
def test_readonly_routes_and_registration():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r1 = c.get("/api/fed/templates_available")
    assert r1.status_code == 200 and "templates" in r1.get_json()
    r2 = c.get("/api/fed/pending_templates")
    assert r2.status_code == 200 and "pending" in r2.get_json()
    # mutating routes are registered
    rules = {str(r.rule) for r in a.app.url_map.iter_rules()}
    assert "/api/fed/template_push" in rules
    assert "/api/fed/pending_review" in rules
    assert "/api/fed/template_pull" in rules
