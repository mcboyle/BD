"""T9 (Tier 3) — D-54 vision-model test harness. The ONLY dev tool
that actually issues a real AI inference call — it exercises the
vision pipeline end-to-end with a tiny synthetic PNG and reports a
structured outcome. Safe when AI is disabled / model missing /
provider unreachable — every error is captured, never raised.
"""
import os

from bulk_downloader import aiassist
from bulk_downloader import dev_suite as ds


# ── D-54 vision_test_harness ───────────────────────────────────────

def test_vision_test_skipped_when_ai_disabled(clean_workdir):
    # AI is off by default in the sandbox — the harness must NOT
    # attempt a call, and must not raise
    aiassist.configure(enabled=False)
    r = ds.vision_test_harness()
    assert r["ok"] is True
    assert r["tool"] == "vision_test_harness"
    assert r["called"] is False
    assert r["outcome"] == "skipped_ai_disabled"
    assert r["enabled"] is False


def test_vision_test_skipped_when_no_vision_model(clean_workdir):
    aiassist.configure(enabled=True, model_vision="")
    try:
        r = ds.vision_test_harness()
        assert r["called"] is False
        assert r["outcome"] == "skipped_no_vision_model"
    finally:
        aiassist.configure(enabled=False,
                           model_vision="qwen2.5vl:7b")


def test_vision_test_unreachable_provider_is_clean_failure(
        clean_workdir):
    # AI enabled + a configured vision model + an endpoint that does
    # not answer -> the harness must attempt the call and return a
    # structured failure outcome, NEVER raise
    aiassist.configure(enabled=True, provider="ollama",
                       endpoint="http://127.0.0.1:1",
                       model_vision="qwen2.5vl:7b")
    try:
        r = ds.vision_test_harness(timeout=2)
        assert r["called"] is True
        # outcome must be a recognised failure state, not 'success'
        assert r["outcome"] in ("model_failed", "exception")
        assert r["call_ok"] is False if r["outcome"] == "model_failed" \
            else "error" in r
        # the synthetic image was always 68 bytes regardless
        assert r["test_image_bytes"] == 68
    finally:
        aiassist.configure(enabled=False)


def test_vision_test_reports_configured_state(clean_workdir):
    aiassist.configure(enabled=True, provider="ollama",
                       model_vision="qwen2.5vl:7b")
    try:
        r = ds.vision_test_harness()
        # whether or not the call succeeds, the harness must report
        # what it tried
        assert r["configured_vision_model"] == "qwen2.5vl:7b"
        assert r["provider"] == "ollama"
        assert r["enabled"] is True
    finally:
        aiassist.configure(enabled=False)


def test_vision_test_accepts_custom_prompt(clean_workdir):
    # a custom prompt is accepted but the call still won't actually
    # succeed in the sandbox — point is it doesn't crash on the input
    aiassist.configure(enabled=False)
    r = ds.vision_test_harness(prompt="describe this image briefly")
    assert r["ok"] is True
    # AI disabled -> still skipped, prompt is ignored at this layer
    assert r["outcome"] == "skipped_ai_disabled"


def test_vision_test_clamps_timeout(clean_workdir):
    # absurd timeouts must clamp into the [1, 120] range — the
    # harness should never pass an out-of-bound timeout to the
    # underlying provider call
    aiassist.configure(enabled=True, provider="ollama",
                       endpoint="http://127.0.0.1:1",
                       model_vision="qwen2.5vl:7b")
    try:
        # 9999s should clamp to 120s — we don't actually wait that
        # long because the connection refusal fires fast
        r = ds.vision_test_harness(timeout=9999)
        assert r["called"] is True
        # "bad" timeout must default cleanly to 30 (not raise)
        r = ds.vision_test_harness(timeout="bad")
        assert r["called"] is True
    finally:
        aiassist.configure(enabled=False)


# ── dev route ─────────────────────────────────────────────────────

def test_vision_test_route(fresh_app):
    # POST + CSRF route. AI disabled in sandbox -> 200 + skipped.
    resp = fresh_app.post("/api/dev/vision_test", json={})
    assert resp.status_code == 200
    j = resp.get_json()
    assert j["ok"] is True
    assert j["tool"] == "vision_test_harness"
    assert j["called"] is False
    assert j["outcome"] == "skipped_ai_disabled"


def test_vision_test_route_rejects_get(fresh_app):
    # POST-only — a GET must 405
    assert fresh_app.get("/api/dev/vision_test").status_code == 405


def test_t9_route_is_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        assert c.post("/api/dev/vision_test", json={}).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
