def test_ai_status_adds_boot_readiness_without_removing_existing_fields(fresh_app, monkeypatch):
    from bulk_downloader import ai_boot_status, aiassist

    monkeypatch.setattr(aiassist, "ai_status", lambda: {
        "ok": True, "enabled": True, "provider": "ollama",
        "configured_models": ["vision", "text"],
    })
    monkeypatch.setattr(ai_boot_status, "read_status", lambda: {
        "schema_version": 1, "state": "ready",
    })
    body = fresh_app.get("/api/ai/status").get_json()
    assert body["ok"] is True
    assert body["configured_models"] == ["vision", "text"]
    assert body["boot_readiness"] == {"schema_version": 1, "state": "ready"}
