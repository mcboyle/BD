"""app-core-surface widgets-fix r1: F4, F5, F11 of the O1789 app-core-surface sweep (FINDINGS S69-A).

F4  PUT/DELETE /api/widgets/<scope> before any GET (= first request after a restart) never loaded widgets.json:
    a PUT rewrote the file from defaults (other sites' overrides lost), a DELETE answered removed_override=false and
    left the override on disk.
F5  a widget size that is not hashable (size=[]) raised TypeError in _sanitize_list -> 500; an unknown hashable
    size ("zz") already falls back to "sm".
F11 a 100k-deep JSON body on PUT raised RecursionError -> 500 (widgets half only; interop register is interop-fix's).

Real app (conftest fresh_app) with a bearer token and a scratch BD_HOME / BD_WIDGETS_CONFIG_PATH; no live site.
"""

import json

import pytest

BD_GATE_SCOPE = "module"

TOK = "widgets-fix-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TOK}"}
SEED = {
    "global": [{"id": "stuck", "size": "md"}],
    "per_site": {"a": [{"id": "stuck", "size": "lg"}]},
}


@pytest.fixture
def client(fresh_app, monkeypatch, tmp_path):
    from bulk_downloader import widgets_config

    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.setenv("BD_WIDGETS_CONFIG_PATH", str(tmp_path / "widgets.json"))
    monkeypatch.setenv("BD_AUTH_TOKEN", TOK)
    monkeypatch.setitem(fresh_app.application.config, "PROPAGATE_EXCEPTIONS", False)
    widgets_config._reset_for_tests()
    try:
        yield fresh_app
    finally:
        widgets_config._reset_for_tests()


def _restart_with(tmp_path, seed):
    """write widgets.json and drop the in-memory state: the next request is the first after a restart"""
    from bulk_downloader import widgets_config

    (tmp_path / "widgets.json").write_text(json.dumps(seed))
    widgets_config._reset_for_tests()


def _on_disk(tmp_path):
    return json.loads((tmp_path / "widgets.json").read_text())


def test_f4_put_first_after_restart_keeps_other_scopes(client, tmp_path):
    _restart_with(tmp_path, SEED)
    r = client.put(
        "/api/widgets/b", json={"widgets": [{"id": "queue_depth"}]}, headers=AUTH
    )
    disk = _on_disk(tmp_path)
    assert r.status_code == 200, (
        f"F4: PUT -> {r.status_code} {r.get_data(as_text=True)[:200]}"
    )
    assert (
        disk["per_site"].get("a") == SEED["per_site"]["a"]
        and disk["global"] == SEED["global"]
    ), (
        f"F4: PUT before load rewrote widgets.json from defaults: per_site={disk['per_site']} global={disk['global']}"
    )
    assert disk["per_site"].get("b") == [{"id": "queue_depth", "size": "sm"}], (
        f"F4: PUT b not saved: {disk}"
    )


def test_f4_put_global_first_after_restart_keeps_site_overrides(client, tmp_path):
    _restart_with(tmp_path, SEED)
    r = client.put(
        "/api/widgets/_global", json={"widgets": [{"id": "workers"}]}, headers=AUTH
    )
    disk = _on_disk(tmp_path)
    assert (
        r.status_code == 200 and disk["per_site"].get("a") == SEED["per_site"]["a"]
    ), (
        f"F4: PUT _global before load dropped site overrides: {r.status_code} per_site={disk['per_site']}"
    )


def test_f4_delete_first_after_restart_removes_the_override(client, tmp_path):
    _restart_with(tmp_path, SEED)
    r = client.delete("/api/widgets/a", headers=AUTH)
    disk = _on_disk(tmp_path)
    assert r.status_code == 200 and r.get_json()["removed_override"] is True, (
        f"F4: DELETE before load -> {r.status_code} {r.get_json()}"
    )
    assert "a" not in disk["per_site"], (
        f"F4: DELETE before load left the override on disk: {disk['per_site']}"
    )
    assert disk["global"] == SEED["global"], (
        f"F4: DELETE a changed global: {disk['global']}"
    )


def test_f4_delete_global_first_after_restart_keeps_site_overrides(client, tmp_path):
    _restart_with(tmp_path, SEED)
    r = client.delete("/api/widgets/_global", headers=AUTH)
    disk = _on_disk(tmp_path)
    assert (
        r.status_code == 200 and disk["per_site"].get("a") == SEED["per_site"]["a"]
    ), (
        f"F4: DELETE _global before load dropped site overrides: {r.status_code} per_site={disk['per_site']}"
    )


def test_f4_control_get_then_put_keeps_other_scopes(client, tmp_path):
    """pin: the GET path always loaded (green on BASE)"""
    _restart_with(tmp_path, SEED)
    client.get("/api/widgets/all", headers=AUTH)
    client.put(
        "/api/widgets/b", json={"widgets": [{"id": "queue_depth"}]}, headers=AUTH
    )
    assert _on_disk(tmp_path)["per_site"].get("a") == SEED["per_site"]["a"], (
        "F4-CONTROL: GET-then-PUT lost site a"
    )


@pytest.mark.parametrize("size", [[], {}, ["md"]], ids=["list", "dict", "list-md"])
def test_f5_unhashable_size_falls_back_to_sm(client, size):
    r = client.put(
        "/api/widgets/b",
        json={"widgets": [{"id": "stuck", "size": size}]},
        headers=AUTH,
    )
    assert r.status_code == 200, (
        f"F5: size={size!r} -> {r.status_code} (want 200, size sm)"
    )
    assert r.get_json()["widgets"] == [{"id": "stuck", "size": "sm"}], (
        f"F5: size={size!r} saved {r.get_json()}"
    )


def test_f5_unhashable_size_in_the_file_does_not_fail_get(client, tmp_path):
    _restart_with(tmp_path, {"global": [{"id": "stuck", "size": []}], "per_site": {}})
    r = client.get("/api/widgets/_global", headers=AUTH)
    assert r.status_code == 200, (
        f"F5: GET with size=[] in widgets.json -> {r.status_code}"
    )
    assert r.get_json()["widgets"] == [{"id": "stuck", "size": "sm"}], (
        f"F5: GET read {r.get_json()}"
    )


def test_f5_control_unknown_hashable_size_falls_back_to_sm(client):
    """pin: "zz" already became "sm" on BASE"""
    r = client.put(
        "/api/widgets/b",
        json={"widgets": [{"id": "stuck", "size": "zz"}]},
        headers=AUTH,
    )
    assert r.status_code == 200 and r.get_json()["widgets"] == [
        {"id": "stuck", "size": "sm"}
    ], f"F5-CONTROL: size zz -> {r.status_code} {r.get_json(silent=True)}"


def test_f11_deep_json_put_is_a_400(client, tmp_path):
    _restart_with(tmp_path, SEED)
    body = "[" * 100000 + "]" * 100000
    r = client.put(
        "/api/widgets/b", data=body, content_type="application/json", headers=AUTH
    )
    assert r.status_code == 400, (
        f"F11: 100k-deep JSON PUT -> {r.status_code} (want 400)"
    )
    assert r.get_json()["ok"] is False, f"F11: body {r.get_data(as_text=True)[:200]}"
    assert _on_disk(tmp_path) == SEED, (
        f"F11: refused PUT changed widgets.json: {_on_disk(tmp_path)}"
    )


def test_f11_control_shallow_json_put_saves(client):
    """pin: a normal nested body still saves after the deep-body refusal"""
    r = client.put(
        "/api/widgets/b",
        data=json.dumps({"widgets": [{"id": "stuck"}]}),
        content_type="application/json",
        headers=AUTH,
    )
    assert r.status_code == 200 and r.get_json()["widgets"] == [
        {"id": "stuck", "size": "sm"}
    ], f"F11-CONTROL: shallow PUT -> {r.status_code} {r.get_json(silent=True)}"
