"""Exercise the real acknowledgement writers and read their persisted output."""
import json

import pytest

BD_GATE_SCOPE = "repo-wide"
ACK = "captcha_egress_disclosure_ack"
KEY = "row817-zero-entropy-fixture-key"


def _new_site(client, name="row817"):
    response = client.post("/api/sites", json={"name": name})
    assert response.status_code == 200
    return response.get_json()["id"]


def _persisted(clean_workdir):
    path = clean_workdir / "sites_config.json"
    assert path.is_file()
    return json.loads(path.read_text())


def _count_saves(monkeypatch, writer):
    real_save = writer._save_sites_config
    saved = []

    def save():
        result = real_save()
        saved.append(result)
        return result

    monkeypatch.setattr(writer, "_save_sites_config", save)
    return saved


def _observe_request_body(monkeypatch, client, path):
    """Keep Flask's parsed payload semantics; observe only calls on that dict."""
    request_class = client.application.request_class
    real_get_json = request_class.get_json
    observed = []

    class Body(dict):
        def __init__(self, values):
            super().__init__(values)
            self.pops = []

        def pop(self, key, *default):
            if key == ACK:
                self.pops.append((key, default))
            return super().pop(key, *default)

    def get_json(request, *args, **kwargs):
        parsed = real_get_json(request, *args, **kwargs)
        if request.method != "PUT" or request.path != path:
            return parsed
        if not hasattr(request, "_row817_body"):
            request._row817_body = Body(parsed)
            observed.append(request._row817_body)
        return request._row817_body

    monkeypatch.setattr(request_class, "get_json", get_json)
    return observed


@pytest.mark.parametrize("acknowledged", [True, False])
def test_update_consumes_ack_exactly_once_before_success_or_refusal(
        fresh_app, clean_workdir, monkeypatch, acknowledged):
    from bulk_downloader import app_sites_id_core as writer

    sid = _new_site(fresh_app)
    path = f"/api/sites/{sid}"
    bodies = _observe_request_body(monkeypatch, fresh_app, path)
    saves = _count_saves(monkeypatch, writer)
    response = fresh_app.put(path, json={"captcha_api_key": KEY, ACK: acknowledged})
    assert response.status_code == (200 if acknowledged else 400)
    assert len(bodies) == 1
    assert bodies[0].pops == [(ACK, (None,))]
    assert ACK not in bodies[0]
    assert saves == ([True] if acknowledged else [])
    persisted = _persisted(clean_workdir)[sid]
    assert ACK not in persisted
    assert persisted.get("captcha_api_key", "") == (KEY if acknowledged else "")


def test_request_without_ack_keeps_its_body_and_persists_ordinary_update(
        fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app_sites_id_core as writer

    sid = _new_site(fresh_app)
    path = f"/api/sites/{sid}"
    bodies = _observe_request_body(monkeypatch, fresh_app, path)
    saves = _count_saves(monkeypatch, writer)
    payload = {"name": "ordinary update"}
    response = fresh_app.put(path, json=payload)
    assert response.status_code == 200
    assert len(bodies) == 1
    assert bodies[0] == payload
    assert bodies[0].pops == [(ACK, (None,))]
    assert saves == [True]
    assert _persisted(clean_workdir)[sid]["name"] == payload["name"]
    assert ACK not in _persisted(clean_workdir)[sid]


@pytest.mark.parametrize("existing", [True, False])
def test_bulk_import_does_not_persist_temporary_ack(
        fresh_app, clean_workdir, monkeypatch, existing):
    from bulk_downloader import app_config as writer

    name = "row817-import"
    sid = _new_site(fresh_app, name) if existing else None
    saves = _count_saves(monkeypatch, writer)
    response = fresh_app.post("/api/config/import", json={
        ACK: True, "sites": [{"name": name, "captcha_api_key": KEY}],
    })
    assert response.status_code == 200
    assert response.get_json()["updated"] == int(existing)
    assert response.get_json()["imported"] == int(not existing)
    assert saves == [True]
    matches = [(site_id, cfg) for site_id, cfg in _persisted(clean_workdir).items()
               if cfg.get("name") == name]
    assert len(matches) == 1
    if existing:
        assert matches[0][0] == sid
    assert matches[0][1]["captcha_api_key"] == KEY
    assert ACK not in matches[0][1]
