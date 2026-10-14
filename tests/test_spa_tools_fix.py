"""spa-tools-fix: AI and drift-repair routes that 500'd or lied on a bad body.

F5 /api/ai/classify 500'd on a non-string element_desc (dict -> KeyError
   slice, int/null -> TypeError); ai_chat.chat raised AttributeError on a
   non-string prompt or a non-object payload despite its "never raises"
   contract.
F6 /api/automation/drift_repair/toggle read {"enabled": "false"} as True and
   answered {"ok": true} when set_config refused to persist.
The F1/F2/F3/F6/F8 UI halves run through frontend/src/routes/SpaToolsFix.test.tsx.
"""

import types

import pytest

from bulk_downloader import ai_chat, aiassist
from bulk_downloader import drift_repair as dr
from bulk_downloader import global_config as gc
from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


@pytest.fixture
def ai_on(monkeypatch):
    calls = []

    def fake_call(prompt, **kw):
        calls.append(prompt)
        return types.SimpleNamespace(
            ok=True,
            text='{"role": "pass_field", "confidence": 90}',
            latency_ms=1,
            model="m",
            provider="ollama",
            error="",
            error_kind="",
        )

    monkeypatch.setitem(aiassist._config, "enabled", True)
    monkeypatch.setattr(aiassist, "warm_once", lambda: None)
    monkeypatch.setattr(aiassist, "_call_model", fake_call)
    return calls


@pytest.mark.parametrize("desc", [{"tag": "input"}, 123, None, ["a"]])
def test_f5_classify_non_string_desc_is_refused(fresh_app, ai_on, desc):
    r = fresh_app.post("/api/ai/classify", json={"element_desc": desc})
    assert r.status_code == 200, (
        f"F5: classify element_desc={desc!r} status {r.status_code}"
    )
    b = r.get_json()
    assert b["ok"] is False and "element_desc" in b["error"], f"F5: {b}"
    assert ai_on == [], "F5: a refused element_desc still reached the model"


def test_f5_classify_string_desc_control(fresh_app, ai_on):
    r = fresh_app.post(
        "/api/ai/classify", json={"element_desc": '<input type="password">'}
    )
    assert r.status_code == 200 and r.get_json()["role"] == "pass_field"
    assert len(ai_on) == 1 and '<input type="password">' in ai_on[0]


@pytest.mark.parametrize(
    "payload", [{"prompt": 123}, {"prompt": ["hi"]}, ["hi"], "hi", None]
)
def test_f5_chat_never_raises_on_bad_shape(monkeypatch, payload):
    monkeypatch.setattr(aiassist, "warm_once", lambda: None)
    sink = []
    out = ai_chat.chat(
        payload,
        config={"enabled": True, "provider": "ollama", "model_text": "m"},
        _call=lambda *a, **k: sink.append(a),
    )
    assert out["ok"] is False and out["error"], f"F5: chat({payload!r}) -> {out}"
    assert sink == []


def test_f5_chat_route_bad_prompt_is_a_chat_error(fresh_app, ai_on):
    r = fresh_app.post("/api/ai/chat", json={"prompt": 123})
    assert r.status_code == 200, f"F5: chat prompt=123 status {r.status_code}"
    assert r.get_json()["error"] == "prompt must be a string"


def test_f5_host_app_list_body_is_400_control(fresh_app, ai_on):
    for path in (
        "/api/ai/models",
        "/api/ai/suggest_selectors",
        "/api/ai/classify",
        "/api/ai/normalize_resolution",
        "/api/ai/diff_repair",
        "/api/ai/chat",
    ):
        r = fresh_app.post(path, json=[1])
        assert r.status_code == 400, f"F5 control: {path} list body {r.status_code}"


@pytest.mark.parametrize("val", ["false", "true", 0, 1, None, {}])
def test_f6_toggle_non_bool_is_400(fresh_app, val):
    gc.set_config({dr.ENABLE_KEY: False})
    r = fresh_app.post("/api/automation/drift_repair/toggle", json={"enabled": val})
    assert r.status_code == 400, f"F6: enabled={val!r} status {r.status_code}"
    assert r.get_json()["ok"] is False
    assert (
        fresh_app.get("/api/automation/drift_repair").get_json()["enabled"] is False
    ), f"F6: enabled={val!r} flipped the sweep on"


def test_f6_toggle_unpersisted_write_is_500(fresh_app, tmp_path):
    cfg = tmp_path / "app_config.json"
    cfg.write_bytes(b'{"automation.drift_repair_enabled": false, #')
    r = fresh_app.post("/api/automation/drift_repair/toggle", json={"enabled": True})
    assert cfg.read_bytes() == b'{"automation.drift_repair_enabled": false, #'
    assert r.status_code == 500, (
        f"F6: refused write answered {r.status_code} {r.get_json()}"
    )
    assert r.get_json()["ok"] is False and r.get_json()["error"]


def test_spa_tools_fix_vitest():
    run_vitest("src/routes/SpaToolsFix.test.tsx", expected_tests=13)
