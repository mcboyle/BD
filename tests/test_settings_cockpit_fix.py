"""settings-cockpit-fix: cockpit state writes that lost or wiped what they held.

F02 a corrupt operator_state.json read as empty and the next write erased it.
F03 /cockpit/api/ui_prefs refused the UI's own 'auto' theme, 500'd on a list
    body, and concurrent saves dropped keys through one shared tmp file.
F18 the blueprint on a standalone app 500'd on a JSON list body, and
    queue/reorder 500'd on a non-string id.
"""

import inspect
import json
import threading

import pytest
from flask import Flask

from tools import cockpit_console as C
from tools import cockpit_core as cc

BD_GATE_SCOPE = "module"

_NOT_OBJECT = {
    "ok": False,
    "error": "request body must be a JSON object",
    "hint": "check Content-Type and body shape",
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "tasks_root", lambda: tmp_path / "tasks")
    monkeypatch.setenv("BD_HOME", str(tmp_path / "home"))
    a = Flask("standalone_cockpit")
    a.register_blueprint(C.bp)
    return a


def _seed_store():
    cc.campaign_create("c1", "general", "example_site", "")
    return cc._store_path()


@pytest.mark.parametrize("corrupt", [b'{"campaigns": {"c1": #', b"[]"])
def test_f02_corrupt_store_is_not_overwritten(app, corrupt):
    p = _seed_store()
    p.write_bytes(corrupt)
    r = app.test_client().post(
        "/cockpit/api/notes",
        json={"site": "example_site", "kind": "observation", "text": "x"},
    )
    assert p.read_bytes() == corrupt, (
        f"F02: corrupt operator_state.json was overwritten (status {r.status_code}): "
        f"{p.read_bytes()[:80]!r}"
    )
    assert r.status_code == 400, f"F02: status {r.status_code}"
    assert "corrupt" in r.get_json()["error"]


def test_f02_control_valid_and_missing_store_still_write(app):
    r = app.test_client().post(
        "/cockpit/api/notes",
        json={"site": "example_site", "kind": "observation", "text": "first"},
    )
    assert r.status_code == 200, f"F02 control (no store yet): {r.get_json()}"
    p = _seed_store()
    r = app.test_client().post(
        "/cockpit/api/notes",
        json={"site": "example_site", "kind": "observation", "text": "second"},
    )
    assert r.status_code == 200, f"F02 control (valid store): {r.get_json()}"
    st = json.loads(p.read_text(encoding="utf-8"))
    assert len(st["campaigns"]) == 1 and len(st["notes"]["example_site"]) == 2


def test_f03_auto_theme_saves(app):
    c = app.test_client()
    r = c.post("/cockpit/api/ui_prefs", json={"theme": "auto"})
    assert r.status_code == 200, f"F03: theme 'auto' refused: {r.get_json()}"
    assert c.get("/cockpit/api/ui_prefs").get_json()["prefs"] == {"theme": "auto"}
    assert c.post("/cockpit/api/ui_prefs", json={"theme": "nope"}).status_code == 400


def test_f03_list_body_is_400(app):
    r = app.test_client().post("/cockpit/api/ui_prefs", json=["layout"])
    assert r.status_code == 400, f"F03: list body status {r.status_code}"


def test_f03_concurrent_saves_keep_every_key(app):
    f = C._ui_prefs_file()
    pairs = [("layout", "top"), ("theme", "ocean"), ("vtier", "advanced")] * 4
    bad = []

    def go(codes, k, v):
        codes.append(
            app.test_client().post("/cockpit/api/ui_prefs", json={k: v}).status_code
        )

    for rnd in range(5):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")
        codes = []
        ts = [threading.Thread(target=go, args=(codes, *kv)) for kv in pairs]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            saved = "corrupt file"
        if set(codes) != {200} or saved != {
            "layout": "top",
            "theme": "ocean",
            "vtier": "advanced",
        }:
            bad.append((rnd, sorted(set(codes)), saved))
    assert not bad, f"F03: concurrent ui_prefs saves lost keys or failed: {bad}"


def _body_reading_post_rules(app):
    out = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/cockpit/api/") or "POST" not in rule.methods:
            continue
        if "get_json(silent=True)" in inspect.getsource(
            app.view_functions[rule.endpoint]
        ):
            out.append(rule)
    return out


def test_f18_standalone_post_routes_refuse_list_body(app):
    rules = _body_reading_post_rules(app)
    assert len(rules) >= 21, f"F18 denominator: {len(rules)} body-reading POST routes"
    c = app.test_client()
    wrong = {}
    for rule in rules:
        path = rule.rule
        for arg in rule.arguments:
            path = path.replace(f"<{arg}>", "x").replace(f"<path:{arg}>", "x")
        r = c.post(path, json=[1])
        if "/api/shell/" in path:
            # the shell origin guard runs first and refuses an untrusted caller
            if r.status_code != 403:
                wrong[path] = r.status_code
        elif r.status_code != 400 or r.get_json() != _NOT_OBJECT:
            wrong[path] = r.status_code
    assert not wrong, (
        f"F18: {len(wrong)}/{len(rules)} POST routes mishandled a list body: {wrong}"
    )


def test_f18_object_and_null_bodies_pass_the_guard(app):
    c = app.test_client()
    r = c.post("/cockpit/api/notes", json={"site": "example_site", "kind": "bogus"})
    assert r.status_code == 400 and "kind must be" in r.get_json()["error"]
    r = c.post("/cockpit/api/notes", data="null", content_type="application/json")
    assert r.status_code == 400 and r.get_json() != _NOT_OBJECT


def test_f18_queue_reorder_refuses_non_string_ids(app):
    c = app.test_client()
    ids = [
        cc.queue_add("example_site", f"l{i}", "", "workflow", "medium", None)["id"]
        for i in range(2)
    ]
    for order in ([[1]], [{"a": 1}], [1]):
        r = c.post("/cockpit/api/queue/reorder", json={"order": order})
        assert r.status_code == 400, (
            f"F18: reorder order={order!r} status {r.status_code}"
        )
    r = c.post("/cockpit/api/queue/reorder", json={"order": ["nope"]})
    assert r.status_code == 200 and len(cc.queue_list()["queue"]) == 2
    r = c.post("/cockpit/api/queue/reorder", json={"order": ids[::-1]})
    assert [i["id"] for i in r.get_json()["queue"]] == ids[::-1]
