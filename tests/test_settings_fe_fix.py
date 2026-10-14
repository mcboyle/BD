"""settings-fe-fix: the settings surfaces stored or reported what had not happened.

F09 runs here: POST /api/global_config stored any string for five keys whose
readers only act on a fixed set (a typo silently became "default"). The
frontend findings (F05-F07, F10-F16) run in the tracked vitest spec below.
"""
import json
from pathlib import Path

import pytest

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

_F09_KEYS = {
    "browser_backend": ("", "cloakbrowser", "playwright"),
    "secrets_audit_sink": ("", "file", "log"),
    "youtube_cipher": ("", "off", "yt-dlp", "player-js"),
    "dev_mode": ("", "0", "1"),
    "cockpit_shell": ("", "0", "1"),
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from bulk_downloader import app as A
    from bulk_downloader import global_config as GC

    Path("app_config.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(GC, "_cached", None)
    monkeypatch.setattr(GC, "_cached_mtime", 0.0)
    from bulk_downloader import app_kernel as K

    before = dict(K._app_cfg)
    yield A.app.test_client()
    K._app_cfg.clear()
    K._app_cfg.update(before)


@pytest.mark.parametrize("key", sorted(_F09_KEYS))
def test_f09_unknown_value_is_refused(client, key):
    r = client.post("/api/global_config", json={key: "bogus-typo"})
    assert r.status_code == 400, (
        f"F09: {key}='bogus-typo' was stored (status {r.status_code}), the reader would act on its default"
    )
    assert f"{key} must be one of" in r.get_json()["error"]
    stored = json.loads(Path("app_config.json").read_text(encoding="utf-8"))
    assert stored.get(key) != "bogus-typo", f"F09: {key} refused but still written"


@pytest.mark.parametrize("key,value", [(k, v) for k, vs in sorted(_F09_KEYS.items()) for v in vs])
def test_f09_known_values_still_save(client, key, value):
    r = client.post("/api/global_config", json={key: value})
    assert r.status_code == 200, f"F09 control: {key}={value!r} refused: {r.get_json()}"
    assert r.get_json().get(key) == value


def test_f09_choices_probe_can_refuse(client):
    # photo_sets carried "choices" before this cut: the same path refuses it.
    r = client.post("/api/global_config", json={"photo_sets": "bogus-typo"})
    assert r.status_code == 400, f"F09 positive control: photo_sets status {r.status_code}"


# r2 (R130-C F1): spellings each reader acts on -- it strips + lowers and takes aliases
# (cloak._coerce_backend, youtube._yt_cipher_backend, secrets_audit._sink,
# dev_tools.is_dev_mode; r3 F1: cockpit_shell._shell_pref strips). BASE stored any
# string, so these can already be in a store.
_READER_ALIASES = [
    ("browser_backend", "cloak"),
    ("browser_backend", "Playwright"),
    ("browser_backend", " pw "),
    ("browser_backend", "true"),
    ("youtube_cipher", "ytdlp"),
    ("youtube_cipher", "Player_JS"),
    ("secrets_audit_sink", "LOG"),
    ("dev_mode", "true"),
    ("dev_mode", "Off"),
    ("cockpit_shell", " 0"),
    ("cockpit_shell", "0 "),
    ("cockpit_shell", " 1"),
]


def _save_with_unrelated_edit(client, key, value):
    from bulk_downloader import app_kernel as K

    K._app_cfg[key] = value  # already in the store (BASE write or hand edit)
    body = client.get("/api/global_config").get_json()
    assert body.get(key) == value
    body["slow_query_ms"] = 250  # the user edits something else and clicks Save
    return client.post("/api/global_config", json=body)


@pytest.mark.parametrize("key,value", _READER_ALIASES)
def test_f09_stored_reader_alias_does_not_block_unrelated_save(client, key, value):
    r = _save_with_unrelated_edit(client, key, value)
    assert r.status_code == 200, (
        f"F1: stored {key}={value!r} + unrelated save -> {r.status_code} {r.get_json()}"
    )
    assert r.get_json()["slow_query_ms"] == 250 and r.get_json()[key] == value


@pytest.mark.parametrize("key,value", _READER_ALIASES)
def test_f09_reader_alias_saves(client, key, value):
    r = client.post("/api/global_config", json={key: value})
    assert r.status_code == 200, (
        f"F1: reader alias {key}={value!r} refused: {r.get_json()}"
    )


def test_f09_stored_canonical_value_unrelated_save_control(client):
    r = _save_with_unrelated_edit(client, "browser_backend", "playwright")
    assert r.status_code == 200, f"F1 control: {r.status_code} {r.get_json()}"


@pytest.mark.parametrize(
    "key,value",
    [
        ("browser_backend", "Cloak-Browser"),
        ("youtube_cipher", "YT DLP"),
        ("secrets_audit_sink", "Logs"),
        ("dev_mode", "maybe"),
    ],
)
def test_f09_unnamed_spelling_still_refused(client, key, value):
    r = client.post("/api/global_config", json={key: value})
    assert r.status_code == 400, f"F09: {key}={value!r} stored (status {r.status_code})"
    assert f"{key} must be one of" in r.get_json()["error"]


# r3 (A1): a value no reader names (BASE typo / hand edit; both readers treat these
# as ON) that is already stored does not block saving other fields.
_STORED_UNNAMED = [("dev_mode", "enabled"), ("cockpit_shell", "true")]


@pytest.mark.parametrize("key,value", _STORED_UNNAMED)
def test_a1_stored_unnamed_value_does_not_block_unrelated_save(client, key, value):
    r = _save_with_unrelated_edit(client, key, value)
    assert r.status_code == 200, (
        f"A1: stored {key}={value!r} + unrelated save -> {r.status_code} {r.get_json()}"
    )
    assert r.get_json()["slow_query_ms"] == 250 and r.get_json()[key] == value


@pytest.mark.parametrize("key,value", _STORED_UNNAMED)
def test_a1_same_value_not_stored_is_refused(client, key, value):
    r = client.post("/api/global_config", json={key: value})
    assert r.status_code == 400, f"A1: new {key}={value!r} stored (status {r.status_code})"


@pytest.mark.parametrize("key,value", _STORED_UNNAMED)
def test_a1_new_garbage_over_stored_value_is_refused(client, key, value):
    from bulk_downloader import app_kernel as K

    K._app_cfg[key] = value
    r = client.post("/api/global_config", json={key: "bogus-typo"})
    assert r.status_code == 400, (
        f"A1: {key} changed {value!r} -> 'bogus-typo' stored (status {r.status_code})"
    )
    assert K._app_cfg[key] == value
    stored = json.loads(Path("app_config.json").read_text(encoding="utf-8"))
    assert stored.get(key) != "bogus-typo", f"A1: {key} refused but still written"


def test_settings_fe_fix_vitest():
    spec = "src/routes/Settings.settingsFeFix.test.tsx"
    receipt = run_vitest(spec, expected_tests=11)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 11,
        "tests_collected": 11,
    }
    assert receipt == expected, (
        f"settings-fe-fix vitest receipt mismatch: expected={expected!r}, observed={receipt!r}"
    )
