"""Row 700: paid captcha egress is disclosed and explicitly acknowledged.

The captcha API key is the existing enable switch: an empty key makes the
runtime return before it imports or calls a solver.  These route-level controls
prove that moving from that default-off state to a configured third-party
solver cannot happen through the canonical writer without a one-shot
acknowledgement, and that a rejected write leaves the solver off.
"""

import json
from pathlib import Path

from bulk_downloader.runner import CAPTCHA_EGRESS_ACK_FIELD

BD_GATE_SCOPE = "module"

# Documented zero-entropy fixture value; this is not a secret.
_TEST_API_KEY = "captcha-api-key-zero-entropy"


def test_captcha_egress_gate_is_owned_by_its_canonical_row():
    matches = sorted(
        Path(__file__).parent.glob("test_row*_captcha_egress_disclosure.py")
    )
    assert len(matches) == 1
    assert matches[0].name == "test_row700_captcha_egress_disclosure.py"


def test_transform_control_imports_disclosure_module_without_judging_the_gate():
    from bulk_downloader import runner

    assert callable(runner.captcha_egress_disclosure_error)


def test_ack_persistence_transform_control_imports_writer_without_judging_behavior():
    from bulk_downloader import app_config as config_api

    assert callable(config_api.api_config_import)


def _new_site(client) -> str:
    response = client.post("/api/sites", json={"name": "captcha-disclosure"})
    assert response.status_code == 200
    return response.get_json()["id"]


def _captcha_status(client, sid: str) -> dict:
    response = client.get(f"/api/sites/{sid}/captcha/stats")
    assert response.status_code == 200
    return response.get_json()


def _count_real_saves(monkeypatch, writer_module) -> list[bool]:
    real_save = writer_module._save_sites_config
    results = []

    def counted_save():
        result = real_save()
        results.append(result)
        return result

    monkeypatch.setattr(writer_module, "_save_sites_config", counted_save)
    return results


def _persisted_site(clean_workdir, sid: str) -> dict:
    config_path = clean_workdir / "sites_config.json"
    assert config_path.is_file()
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert sid in persisted
    return persisted[sid]


def test_new_solver_key_without_egress_ack_is_refused_and_not_persisted(fresh_app):
    sid = _new_site(fresh_app)
    before = _captcha_status(fresh_app, sid)
    assert before["has_key"] is False
    assert before["provider"] == "2captcha"

    response = fresh_app.put(
        f"/api/sites/{sid}",
        json={"captcha_api_key": _TEST_API_KEY},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert "captcha_egress_disclosure_ack=true" in body["error"]
    assert "page URL" in body["error"]
    assert "per solve" in body["error"]
    assert "$0.001" in body["error"]
    assert "$0.00299" in body["error"]
    assert "terms" in body["error"].lower()
    after = _captcha_status(fresh_app, sid)
    assert after["has_key"] is False
    assert after["submitted"] == 0


def test_acknowledged_solver_key_is_persisted_but_ack_is_not(fresh_app):
    sid = _new_site(fresh_app)

    response = fresh_app.put(
        f"/api/sites/{sid}",
        json={
            "captcha_provider": "capsolver",
            "captcha_api_key": _TEST_API_KEY,
            "captcha_egress_disclosure_ack": True,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    status = _captcha_status(fresh_app, sid)
    assert status["has_key"] is True
    assert status["provider"] == "capsolver"
    assert status["submitted"] == 0

    # The acknowledgement authorizes this transition only.  It is not a
    # durable bypass that a later provider change can inherit.
    response = fresh_app.put(
        f"/api/sites/{sid}",
        json={"captcha_provider": "2captcha"},
    )
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert "captcha_egress_disclosure_ack=true" in error
    assert "$0.001" in error
    assert "$0.00299" in error
    status = _captcha_status(fresh_app, sid)
    assert status["provider"] == "capsolver"


def test_site_update_ack_is_absent_from_persisted_config(
        fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app_sites_id_core as site_core

    sid = _new_site(fresh_app)
    save_results = _count_real_saves(monkeypatch, site_core)

    response = fresh_app.put(
        f"/api/sites/{sid}",
        json={
            "captcha_provider": "capsolver",
            "captcha_api_key": _TEST_API_KEY,
            CAPTCHA_EGRESS_ACK_FIELD: True,
        },
    )

    assert response.status_code == 200
    assert save_results == [True]
    persisted = _persisted_site(clean_workdir, sid)
    assert persisted["captcha_provider"] == "capsolver"
    assert persisted["captcha_api_key"] == _TEST_API_KEY
    assert CAPTCHA_EGRESS_ACK_FIELD not in persisted


def test_config_import_existing_site_ack_is_absent_from_persisted_config(
        fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app_config as config_api

    sid = _new_site(fresh_app)
    response = fresh_app.put(
        f"/api/sites/{sid}",
        json={
            "name": "captcha-import-existing",
            "captcha_api_key": _TEST_API_KEY,
            CAPTCHA_EGRESS_ACK_FIELD: True,
        },
    )
    assert response.status_code == 200
    assert _captcha_status(fresh_app, sid)["provider"] == "2captcha"
    save_results = _count_real_saves(monkeypatch, config_api)

    response = fresh_app.post(
        "/api/config/import",
        json={
            CAPTCHA_EGRESS_ACK_FIELD: True,
            "sites": [{
                "name": "captcha-import-existing",
                "captcha_provider": "capsolver",
            }],
        },
    )

    assert response.status_code == 200
    assert response.get_json()["updated"] == 1
    assert response.get_json()["imported"] == 0
    assert save_results == [True]
    persisted = _persisted_site(clean_workdir, sid)
    assert persisted["captcha_provider"] == "capsolver"
    assert persisted["captcha_api_key"] == _TEST_API_KEY
    assert CAPTCHA_EGRESS_ACK_FIELD not in persisted


def test_config_import_created_site_ack_is_absent_from_persisted_config(
        fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app_config as config_api

    save_results = _count_real_saves(monkeypatch, config_api)
    response = fresh_app.post(
        "/api/config/import",
        json={
            CAPTCHA_EGRESS_ACK_FIELD: True,
            "sites": [{
                "name": "captcha-import-created",
                "captcha_provider": "capsolver",
                "captcha_api_key": _TEST_API_KEY,
            }],
        },
    )

    assert response.status_code == 200
    assert response.get_json()["updated"] == 0
    assert response.get_json()["imported"] == 1
    assert save_results == [True]
    persisted = json.loads(
        (clean_workdir / "sites_config.json").read_text(encoding="utf-8")
    )
    created = [
        (sid, cfg) for sid, cfg in persisted.items()
        if cfg.get("name") == "captcha-import-created"
    ]
    assert len(created) == 1
    _sid, config = created[0]
    assert config["captcha_provider"] == "capsolver"
    assert config["captcha_api_key"] == _TEST_API_KEY
    assert CAPTCHA_EGRESS_ACK_FIELD not in config


def test_site_creation_cannot_bypass_the_enablement_ack(fresh_app):
    response = fresh_app.post(
        "/api/sites",
        json={
            "name": "captcha-create-without-ack",
            "captcha_api_key": _TEST_API_KEY,
        },
    )
    assert response.status_code == 400
    assert "captcha_egress_disclosure_ack=true" in response.get_json()["error"]

    response = fresh_app.post(
        "/api/sites",
        json={
            "name": "captcha-create-with-ack",
            "captcha_api_key": _TEST_API_KEY,
            "captcha_egress_disclosure_ack": True,
        },
    )
    assert response.status_code == 200
    status = _captcha_status(fresh_app, response.get_json()["id"])
    assert status["has_key"] is True
    assert status["provider"] == "2captcha"


def test_import_writers_apply_the_same_nonpersistent_gate(fresh_app):
    site_payload = {
        "name": "single-import-captcha",
        "captcha_api_key": _TEST_API_KEY,
    }
    response = fresh_app.post("/api/sites/import", json=site_payload)
    assert response.status_code == 400
    assert "captcha_egress_disclosure_ack=true" in response.get_json()["errors"][0]

    response = fresh_app.post(
        "/api/sites/import",
        json={**site_payload, "captcha_egress_disclosure_ack": True},
    )
    assert response.status_code == 200
    assert _captcha_status(fresh_app, response.get_json()["id"])["has_key"] is True

    bulk_payload = {
        "sites": [{
            "name": "bulk-import-captcha",
            "captcha_api_key": _TEST_API_KEY,
        }],
    }
    response = fresh_app.post("/api/config/import", json=bulk_payload)
    assert response.status_code == 400
    assert "captcha_egress_disclosure_ack=true" in response.get_json()["error"]

    response = fresh_app.post(
        "/api/config/import",
        json={**bulk_payload, "captcha_egress_disclosure_ack": True},
    )
    assert response.status_code == 200
    imported_sid = next(
        sid for sid, row in fresh_app.get("/api/status").get_json().items()
        if row["config"].get("name") == "bulk-import-captcha"
    )
    assert _captcha_status(fresh_app, imported_sid)["has_key"] is True

    # Exercise the existing-site branch independently from the create branch.
    response = fresh_app.put(
        f"/api/sites/{imported_sid}",
        json={
            "captcha_provider": "capsolver",
            "captcha_egress_disclosure_ack": True,
        },
    )
    assert response.status_code == 200
    assert _captcha_status(fresh_app, imported_sid)["provider"] == "capsolver"

    response = fresh_app.post(
        "/api/config/import",
        json={"sites": [{
            "name": "bulk-import-captcha",
            "captcha_provider": "2captcha",
        }]},
    )
    assert response.status_code == 400
    assert "captcha_egress_disclosure_ack=true" in response.get_json()["error"]
    assert _captcha_status(fresh_app, imported_sid)["provider"] == "capsolver"

    # A normal merge export omits secrets. Preserving an already-enabled key is
    # not another point of enablement and must not demand a second ack.
    response = fresh_app.post(
        "/api/config/import",
        json={"sites": [{
            "name": "bulk-import-captcha",
            "captcha_provider": "capsolver",
        }]},
    )
    assert response.status_code == 200
    assert _captcha_status(fresh_app, imported_sid)["has_key"] is True
