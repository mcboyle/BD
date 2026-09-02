"""Row 395: paid captcha egress is disclosed and explicitly acknowledged.

The captcha API key is the existing enable switch: an empty key makes the
runtime return before it imports or calls a solver.  These route-level controls
prove that moving from that default-off state to a configured third-party
solver cannot happen through the canonical writer without a one-shot
acknowledgement, and that a rejected write leaves the solver off.
"""

BD_GATE_SCOPE = "module"


def test_transform_control_imports_disclosure_module_without_judging_the_gate():
    from bulk_downloader import runner

    assert callable(runner.captcha_egress_disclosure_error)


def _new_site(client) -> str:
    response = client.post("/api/sites", json={"name": "captcha-disclosure"})
    assert response.status_code == 200
    return response.get_json()["id"]


def _captcha_status(client, sid: str) -> dict:
    response = client.get(f"/api/sites/{sid}/captcha/stats")
    assert response.status_code == 200
    return response.get_json()


def test_new_solver_key_without_egress_ack_is_refused_and_not_persisted(fresh_app):
    sid = _new_site(fresh_app)
    before = _captcha_status(fresh_app, sid)
    assert before["has_key"] is False
    assert before["provider"] == "2captcha"

    response = fresh_app.put(
        f"/api/sites/{sid}",
        json={"captcha_api_key": "row395-zero-entropy-test-key"},
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
            "captcha_api_key": "row395-zero-entropy-test-key",
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


def test_site_creation_cannot_bypass_the_enablement_ack(fresh_app):
    response = fresh_app.post(
        "/api/sites",
        json={
            "name": "captcha-create-without-ack",
            "captcha_api_key": "row395-zero-entropy-test-key",
        },
    )
    assert response.status_code == 400
    assert "captcha_egress_disclosure_ack=true" in response.get_json()["error"]

    response = fresh_app.post(
        "/api/sites",
        json={
            "name": "captcha-create-with-ack",
            "captcha_api_key": "row395-zero-entropy-test-key",
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
        "captcha_api_key": "row395-zero-entropy-test-key",
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
            "captcha_api_key": "row395-zero-entropy-test-key",
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

    # A normal merge export omits secrets. Preserving an already-enabled key is
    # not another point of enablement and must not demand a second ack.
    response = fresh_app.post(
        "/api/config/import",
        json={"sites": [{"name": "bulk-import-captcha"}]},
    )
    assert response.status_code == 200
    assert _captcha_status(fresh_app, imported_sid)["has_key"] is True
