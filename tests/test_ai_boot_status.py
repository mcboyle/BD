import json
from pathlib import Path

from bulk_downloader import ai_boot_status as status


def test_load_effective_config_uses_app_keys_and_defaults():
    cfg = status.load_effective_config({
        "ai_enabled": True,
        "ai_provider": "OLLAMA",
        "ai_endpoint": "http://user:secret@127.0.0.1:11434/api/",
        "ai_model_text": "text-model",
        "ai_model_vision": "vision-model",
    })
    assert cfg == {
        "enabled": True,
        "provider": "ollama",
        "endpoint": "http://127.0.0.1:11434/api",
        "model_text": "text-model",
        "model_vision": "vision-model",
    }
    assert status.load_effective_config({}) == {
        "enabled": False,
        "provider": "ollama",
        "endpoint": "http://localhost:11434",
        "model_text": "qwen2.5:7b",
        "model_vision": "qwen2.5vl:7b",
    }


def test_write_then_read_current_status(tmp_path):
    path = tmp_path / "state" / "ai_boot_readiness.json"
    written = status.write_status(
        {"state": "ready", "models": {}},
        path,
        now=1_000.0,
        boot_id="boot-a",
    )
    loaded = status.read_status(path, now=1_100.0, boot_id="boot-a")
    assert loaded == written
    assert loaded["schema_version"] == 1
    assert loaded["updated_at"] == "1970-01-01T00:16:40Z"


def test_status_from_prior_boot_or_after_keepalive_is_stale(tmp_path):
    path = tmp_path / "state.json"
    status.write_status({"state": "ready"}, path, now=1_000.0, boot_id="boot-a")
    prior = status.read_status(path, now=1_001.0, boot_id="boot-b")
    expired = status.read_status(path, now=1_601.0, boot_id="boot-a")
    assert prior["state"] == "stale"
    assert prior["stale_reason"] == "previous_boot"
    assert expired["state"] == "stale"
    assert expired["stale_reason"] == "expired"


def test_missing_or_malformed_status_is_safe_unknown(tmp_path):
    missing = status.read_status(tmp_path / "missing.json", boot_id="boot-a")
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    malformed = status.read_status(bad, boot_id="boot-a")
    assert missing == {"schema_version": 1, "state": "unknown", "reason": "missing"}
    assert malformed == {"schema_version": 1, "state": "unknown", "reason": "malformed"}


def test_write_is_atomic_and_never_persists_url_credentials(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    replacements = []
    real_replace = status.os.replace

    def capture_replace(source, target):
        replacements.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(status.os, "replace", capture_replace)
    status.write_status(
        {
            "state": "degraded",
            "endpoint": status.sanitize_endpoint(
                "http://name:password@localhost:11434/private"
            ),
        },
        path,
        now=1_000.0,
        boot_id="boot-a",
    )
    raw = path.read_text(encoding="utf-8")
    assert replacements and replacements[0][0].name.endswith(".tmp")
    assert replacements[0][1] == path
    assert "name" not in raw and "password" not in raw
    assert json.loads(raw)["endpoint"] == "http://localhost:11434/private"
