"""fed-trust r1 -- federation-mesh FINDINGS F3 F4 F5 F12 (S68-D probes P08 P09 P14 P15 P17-P21).

F3  a signed re-announce of an existing instance_id moved its base_url and kept
    its trust tier: every peer shares one fed_token, so any token holder could
    re-point a trusted peer (P08).
F4  a blocked peer's template_push was queued (P14); with fed_token set an
    unregistered from_instance was queued (P15); from_instance was free text.
F5  an approved peer template beat the operator's own template for a host: file
    sort order (P17), match.hosts alias (P18), site_id rewrote the body host
    (P19), two long hosts truncated into one file (P21).
F12 register_peer stored any base_url: file:, javascript:, loopback,
    link-local metadata address (P09).
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import hashlib
import hmac
import json

import pytest

from bulk_downloader import federation as fed
from bulk_downloader import global_config, marketplace, template_registry as tr

FEDTOK = "fed-trust-r1-fixture-token"


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    d1, d2 = tmp_path / "t_reviewed", tmp_path / "t_enabled"
    d1.mkdir()
    d2.mkdir()
    monkeypatch.setattr(tr, "DEFAULT_TEMPLATE_DIRS", [d1, d2])
    return d1


@pytest.fixture
def client(fresh_app, tdir, monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    fed._ensure_tables()
    return fresh_app


@pytest.fixture
def fedtok(monkeypatch):
    orig = global_config.get_config
    monkeypatch.setattr(global_config, "get_config", lambda: {**(orig() or {}), "fed_token": FEDTOK})


def _signed_post(c, path, obj):
    raw = json.dumps(obj).encode()
    sig = hmac.new(FEDTOK.encode(), raw, hashlib.sha256).hexdigest()
    return c.post(path, data=raw, headers={"Content-Type": "application/json", "X-BD-Fed-Sig": sig})


def _peers():
    return {p["instance_id"]: p for p in fed.active_peers()}


def _bundle(site_id, template, signed=False):
    b = {"schema": marketplace.SCHEMA_VERSION, "site_id": site_id, "template": template,
         "metadata": {"kind": "federated_template", "host": site_id}}
    if signed:
        b["signature"] = marketplace.sign_bundle(b, secret=FEDTOK)
    return b


# ── F3: a re-announce cannot re-point an existing peer ───────────────

def test_f3_signed_reannounce_cannot_move_trusted_peer(client, fedtok):
    assert fed.register_peer("vic", "http://vic.test")
    assert fed.set_peer_trust("vic", "trusted")
    r = _signed_post(client, "/api/fed/announce", {"instance_id": "vic", "base_url": "http://attacker.test"})
    got = _peers()["vic"]["base_url"]
    assert got == "http://vic.test", \
        f"fed-trust F3: signed re-announce moved trusted vic to {got} (announce -> {r.status_code} {r.get_json()})"
    assert r.get_json() == {"ok": False} and fed.peer_trust("vic") == "trusted"


def test_f3_control_same_url_reannounce_refreshes(client, fedtok):
    assert fed.register_peer("vic", "http://vic.test", version="1")
    r = _signed_post(client, "/api/fed/announce",
                     {"instance_id": "vic", "base_url": "http://vic.test", "version": "2", "last_history_id": 7})
    p = _peers()["vic"]
    assert r.get_json() == {"ok": True} and (p["version"], p["last_history_id"]) == ("2", 7), p


def test_f3_operator_flag_moves_a_peer(client):
    assert fed.register_peer("vic", "http://vic.test")
    assert fed.register_peer("vic", "http://vic-new.test", operator=True)
    assert _peers()["vic"]["base_url"] == "http://vic-new.test"


# ── F12: base_url scheme / host validated at register ────────────────

BAD_URLS = ["file:///etc/passwd", "javascript:alert(1)", "http://127.0.0.1:22/",
            "http://169.254.169.254/latest/meta-data/", "http://localhost:5000", "http://a.localhost/",
            "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://2130706433/",
            "http://0x7f.1/", "http://127.1/", "http://224.0.0.1/", "ftp://ok.test/", "http:///nohost",
            "http://user:pw@ok.test/", "http://ok.test /x", "http://ok.test:99999/", "http://ok.test:x/"]


@pytest.mark.parametrize("url", BAD_URLS)
def test_f12_bad_base_url_refused(client, url):
    r = client.post("/api/fed/announce", json={"instance_id": "u1", "base_url": url})
    listed = [p["base_url"] for p in client.get("/api/fed/peers").get_json()["peers"]]
    assert url not in listed and r.get_json() == {"ok": False}, \
        f"fed-trust F12: base_url {url!r} stored and listed by /api/fed/peers: {listed}"


@pytest.mark.parametrize("url", ["http://ok.test", "https://nas.local:8443/bd",
                                 "http://192.168.1.20:5000", "http://[2001:db8::1]:8080/"])
def test_f12_control_lan_and_named_peers_accepted(client, url):
    r = client.post("/api/fed/announce", json={"instance_id": "u2", "base_url": url})
    assert r.get_json() == {"ok": True} and _peers()["u2"]["base_url"] == url


# ── F4: blocked peer push refused; from_instance is an id ────────────

def test_f4_blocked_peer_template_push_refused(client, fedtok):
    assert fed.register_peer("blk", "http://blk.test")
    assert fed.set_peer_trust("blk", "blocked")
    r = _signed_post(client, "/api/fed/template_push",
                     {"instance_id": "blk", "bundle": _bundle("a.test", {"host": "a.test"}, signed=True)})
    assert r.status_code == 400 and not fed.list_pending_templates(), \
        f"fed-trust F4: blocked peer template_push -> {r.status_code} {r.get_json()}"
    assert r.get_json()["error"] == "peer is blocked"


def test_f4_control_observed_peer_push_queued(client, fedtok):
    assert fed.register_peer("obs", "http://obs.test")
    r = _signed_post(client, "/api/fed/template_push",
                     {"instance_id": "obs", "bundle": _bundle("a.test", {"host": "a.test"}, signed=True)})
    assert r.status_code == 200 and [p["from_instance"] for p in fed.list_pending_templates()] == ["obs"]


def test_f4_token_set_unregistered_sender_refused(client, fedtok):
    r = _signed_post(client, "/api/fed/template_push",
                     {"instance_id": "trusted-hq", "bundle": _bundle("b.test", {"host": "b.test"}, signed=True)})
    pend = [p["from_instance"] for p in fed.list_pending_templates()]
    assert r.status_code == 400 and not pend, \
        f"fed-trust F4: unregistered from_instance 'trusted-hq' queued -> {r.status_code} {r.get_json()} {pend}"
    assert r.get_json()["error"] == "peer is not registered"


def test_f4_control_no_token_unregistered_sender_queued(client):
    # auth / fed_token off stays open by design (PM ANSWER-pm-B-batch-1307Z item 1)
    r = client.post("/api/fed/template_push",
                    json={"instance_id": "trusted-hq", "bundle": _bundle("b.test", {"host": "b.test"})})
    assert r.status_code == 200 and [p["from_instance"] for p in fed.list_pending_templates()] == ["trusted-hq"]


@pytest.mark.parametrize("inst", ["", "a b", "x" * 129, "<b>hq</b>", "-lead", 5, None])
def test_f4_from_instance_not_an_id_refused(client, inst):
    res = fed.receive_template(inst, _bundle("a.test", {"host": "a.test"}))
    assert res.get("ok") is False and not fed.list_pending_templates(), \
        f"fed-trust F4: from_instance {inst!r} queued: {res}"


def test_f4_control_id_charset_accepted(client):
    assert fed.receive_template("peer-A.1:2_x", _bundle("a.test", {"host": "a.test"}))["ok"] is True


# ── F5: the operator's own template always wins for a host ───────────

def _write_op(d, host, marker, **extra):
    (d / f"{host}.template.json").write_text(json.dumps({"host": host, "status": "enabled",
                                                         "marker": marker, **extra}))


def _push_approve(c, inst, b):
    r = c.post("/api/fed/template_push", json={"instance_id": inst, "bundle": b})
    assert r.status_code == 200, r.get_json()
    r2 = c.post("/api/fed/pending_review", json={"id": r.get_json()["pending_id"], "action": "approve"})
    assert r2.status_code == 200 and r2.get_json()["applied"], r2.get_json()
    return r2.get_json()["path"]


def _marker(url, **kw):
    return (tr.find_template_for_url(url, **kw) or {}).get("marker")


def test_f5_operator_exact_host_beats_peer_whatever_the_sort_order(client, tdir):
    _write_op(tdir, "zz.test", "operator")
    _write_op(tdir, "aa.test", "operator")
    _push_approve(client, "p1", _bundle("zz.test", {"host": "zz.test", "marker": "peer"}))
    _push_approve(client, "p1", _bundle("aa.test", {"host": "aa.test", "marker": "peer"}))
    assert _marker("https://aa.test/x") == "operator"  # control: sorts before fed_
    got = _marker("https://zz.test/x")
    assert got == "operator", f"fed-trust F5: zz.test operator template shadowed by approved peer -> {got}"


def test_f5_peer_alias_cannot_take_an_operator_suffix_host(client, tdir):
    _write_op(tdir, "parent.test", "operator")
    _push_approve(client, "p2", _bundle("other.test", {"host": "other.test", "marker": "alias",
                                                      "match": {"hosts": ["sub.parent.test"]}}))
    got = _marker("https://sub.parent.test/x")
    assert got == "operator", f"fed-trust F5: peer alias captured sub.parent.test -> {got}"
    assert _marker("https://other.test/x") == "alias"  # control: the peer template serves its own host


def test_f5_cap3_variant_pick_keeps_operator(client, tdir):
    _write_op(tdir, "v.test", "operator", selectors={"video": "video.op"})
    _push_approve(client, "p5", _bundle("v.test", {"host": "v.test", "marker": "peer",
                                                  "selectors": {"video": "video.peer"}}))
    html = '<video class="peer"></video>'
    got = [_marker("https://v.test/x", html=html), (tr.select_best_variant("https://v.test/x", html) or {}).get("marker"),
           [t.get("marker") for t in tr.find_template_variants_for_url("https://v.test/x")]]
    assert got == ["operator", "operator", ["operator"]], f"fed-trust F5: html variant pick chose the peer: {got}"


def test_f5_control_peer_template_serves_an_unclaimed_host(client, tdir):
    _push_approve(client, "p6", _bundle("free.test", {"host": "free.test", "marker": "peer"}))
    assert _marker("https://free.test/x") == "peer"


def test_f5_body_host_differing_from_site_id_refused(client, tdir):
    r = client.post("/api/fed/template_push",
                    json={"instance_id": "p3", "bundle": _bundle("site.test", {"host": "elsewhere.test"})})
    assert r.status_code == 400 and not fed.list_pending_templates(), \
        f"fed-trust F5: body host elsewhere.test queued under site.test -> {r.status_code} {r.get_json()}"
    # a row queued before the receive-side check is refused at approve, not rewritten
    from bulk_downloader import db
    fed._ensure_pending_table()
    with db.db_conn() as cx:
        pid = cx.execute("INSERT INTO fed_pending_templates(from_instance, site_id, bundle_json, received_ts) "
                         "VALUES ('p3','site.test',?,0)",
                         (json.dumps(_bundle("site.test", {"host": "elsewhere.test"})),)).lastrowid
    res = fed.review_pending_template(pid, "approve")
    assert res == {"ok": False, "error": "template host differs from site_id"}, res
    assert not list(tdir.glob("fed_*")), "fed-trust F5: mismatched queued row written at approve"


def test_f5_control_body_without_host_takes_site_id(client, tdir):
    path = _push_approve(client, "p3", _bundle("site.test", {"marker": "m"}))
    assert json.loads(open(path).read())["host"] == "site.test"


def test_f5_long_hosts_get_distinct_files(client, tdir):
    h1, h2 = "x" * 80 + ".one.test", "x" * 80 + ".two.test"
    a1 = _push_approve(client, "p4", _bundle(h1, {"host": h1, "marker": "one"}))
    a2 = _push_approve(client, "p4", _bundle(h2, {"host": h2, "marker": "two"}))
    assert a1 != a2, f"fed-trust F5: two long hosts written to one file {a1}"
    assert [_marker(f"https://{h1}/"), _marker(f"https://{h2}/")] == ["one", "two"]


def test_f5_control_short_name_unchanged(client, tdir):
    path = _push_approve(client, "p4", _bundle("example.test", {"host": "example.test"}))
    assert path.endswith("/fed_p4_example.test.template.json"), path
