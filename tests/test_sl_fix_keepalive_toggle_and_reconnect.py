"""sl-fix (bughunt sites-lifecycle rows 3 and 5): keep-alive toggle and reconnect input.

Row 3: api_site_keep_alive_toggle read the body with bool(), so the STRING "false"
or "0" enabled keep-alive, persisted keep_alive_enabled=True and spawned keepers.
Row 5: api_site_reconnect called int(account_idx) unguarded, so "x" was a 500.
Both are driven through the routes with the keeper side effects recorded, not run.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import pytest


@pytest.fixture
def site(fresh_app, monkeypatch):
    from bulk_downloader import app_sites_lifecycle as lifecycle
    from bulk_downloader import session_keeper

    r = fresh_app.post("/api/sites", json={"name": "sl-fix-site"})
    assert r.status_code == 200
    sid = r.get_json()["id"]
    calls = {"started": [], "stopped": [], "checked": []}
    monkeypatch.setattr(
        lifecycle,
        "_start_session_keepers",
        lambda site_id=None: calls["started"].append(site_id),
    )
    monkeypatch.setattr(
        session_keeper, "get_status", lambda: [{"site_id": sid, "account_idx": 0}]
    )
    monkeypatch.setattr(
        session_keeper, "stop_keeper", lambda s, i: calls["stopped"].append((s, i))
    )
    monkeypatch.setattr(
        session_keeper,
        "force_check",
        lambda s, i: calls["checked"].append((s, i)) or True,
    )
    return fresh_app, sid, calls


def _stored(sid):
    from bulk_downloader import app as app_module

    return app_module.s_cfg[sid]["keep_alive_enabled"]


@pytest.mark.parametrize("value", ["false", "0", "off", "False "])
def test_string_false_disables_keepalive(site, value):
    client, sid, calls = site
    r = client.post(f"/api/sites/{sid}/keep_alive_toggle", json={"enabled": value})
    body = r.get_json()
    assert r.status_code == 200 and body["enabled"] is False, (
        f"sl-fix row 3: enabled={value!r} did not disable keep-alive: "
        f"{r.status_code} {body}"
    )
    assert _stored(sid) is False
    assert calls["started"] == [] and calls["stopped"] == [(sid, 0)]


@pytest.mark.parametrize("value", [True, "true", "1", 1])
def test_control_true_spellings_enable_and_spawn(site, value):
    client, sid, calls = site
    r = client.post(f"/api/sites/{sid}/keep_alive_toggle", json={"enabled": value})
    assert r.status_code == 200 and r.get_json()["enabled"] is True
    assert _stored(sid) is True and calls["started"] == [sid]


def test_control_json_false_disables(site):
    client, sid, calls = site
    r = client.post(f"/api/sites/{sid}/keep_alive_toggle", json={"enabled": False})
    assert r.status_code == 200 and r.get_json()["enabled"] is False
    assert _stored(sid) is False and calls["started"] == []


@pytest.mark.parametrize("value", ["maybe", 2, None, [], {}])
def test_unreadable_value_is_refused_and_nothing_changes(site, value):
    client, sid, calls = site
    before = _stored(sid)
    r = client.post(f"/api/sites/{sid}/keep_alive_toggle", json={"enabled": value})
    assert r.status_code == 400, (
        f"sl-fix row 3: enabled={value!r} was accepted: {r.status_code} {r.get_json()}"
    )
    assert _stored(sid) == before and calls["started"] == [] and calls["stopped"] == []


@pytest.mark.parametrize("value", ["x", "1.5", [1]])
def test_reconnect_non_integer_index_is_400(site, value):
    client, sid, calls = site
    try:
        r = client.post(f"/api/sites/{sid}/reconnect", json={"account_idx": value})
    except Exception as e:  # fresh_app propagates the route's exception
        pytest.fail(
            f"sl-fix row 5: account_idx={value!r} raised {type(e).__name__}: {e}"
        )
    assert r.status_code == 400, (
        f"sl-fix row 5: account_idx={value!r} -> HTTP {r.status_code}"
    )
    assert "account_idx" in r.get_json()["error"] and calls["checked"] == []


@pytest.mark.parametrize("value", [1, "1"])
def test_control_reconnect_integer_index(site, value):
    client, sid, calls = site
    r = client.post(f"/api/sites/{sid}/reconnect", json={"account_idx": value})
    assert r.status_code == 200 and r.get_json()["triggered"] == [1]
    assert calls["checked"] == [(sid, 1)]
