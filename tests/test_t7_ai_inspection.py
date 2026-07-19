"""T7 (Tier 3) — D-52 prompt previewer + D-55 AI fallback-chain
tracer. Two read-only AI-inspection tools in Group C. D-52 catalogs
the named AI prompt templates and renders filled previews; D-55
evaluates the AI fallback chain against the current config + live
provider status, reporting which stage a call would reach.
"""
import os

from bulk_downloader import aiassist
from bulk_downloader import dev_suite as ds


# ── D-52 prompt_preview ────────────────────────────────────────────

def test_prompt_preview_catalogs_all_templates(clean_workdir):
    r = ds.prompt_preview()
    assert r["ok"] is True
    assert r["tool"] == "prompt_preview"
    # every template in the registry must render without error
    assert r["errored"] == []
    assert r["prompt_count"] == len(r["known_prompts"])
    names = {p["name"] for p in r["prompts"]}
    for expected in ("selector", "classify", "resolution",
                     "diff_repair", "login_detect", "template_refine"):
        assert expected in names


def test_prompt_preview_fills_placeholders(clean_workdir):
    r = ds.prompt_preview()
    sel = next(p for p in r["prompts"] if p["name"] == "selector")
    assert set(sel["placeholders"]) == {
        "dom_excerpt", "page_url", "context_hint"}
    # the filled preview must contain the sample value, and must NOT
    # still contain a raw {placeholder} brace for a known field
    assert "example.test" in sel["filled_preview"]
    assert "{dom_excerpt}" not in sel["filled_preview"]
    assert sel["preview_length"] > sel["template_length"] - 50


def test_prompt_preview_single_by_name(clean_workdir):
    r = ds.prompt_preview(name="resolution")
    assert r["ok"] is True
    p = r["prompt"]
    assert p["name"] == "resolution"
    assert p["placeholders"] == ["filename"]
    assert "MyVideo" in p["filled_preview"]


def test_prompt_preview_unknown_name_is_error(clean_workdir):
    r = ds.prompt_preview(name="not_a_real_prompt")
    assert r["ok"] is False
    assert "unknown prompt" in r["error"]


def test_prompt_preview_makes_no_ai_call(clean_workdir):
    # prompt_preview must never trigger inference — it only renders
    # strings. AI is disabled by default; the call must still succeed
    # and return filled previews regardless.
    assert aiassist.get_config().get("enabled") is False
    r = ds.prompt_preview()
    assert r["ok"] is True
    assert all("filled_preview" in p for p in r["prompts"])


# ── D-55 ai_fallback_trace ─────────────────────────────────────────

def test_ai_fallback_trace_stops_at_disabled(clean_workdir):
    # AI is off by default in the sandbox -> the chain stops at the
    # very first stage
    aiassist.configure(enabled=False)
    r = ds.ai_fallback_trace()
    assert r["ok"] is True
    assert r["tool"] == "ai_fallback_trace"
    assert r["reached_stage"] == "ai_disabled"
    assert r["operational"] is False
    assert "disabled" in r["reason"]


def test_ai_fallback_trace_lists_the_full_chain(clean_workdir):
    r = ds.ai_fallback_trace()
    # the documented stage order must be exposed so a caller can see
    # how far past 'disabled' a healthy environment would get
    assert r["stages"][0] == "ai_disabled"
    assert r["stages"][-1] == "fully_operational"
    assert "provider_unreachable" in r["stages"]
    assert "text_only" in r["stages"]


def test_ai_fallback_trace_unreachable_when_enabled_no_provider(
        clean_workdir):
    # enable AI but point it at an endpoint that will not answer ->
    # the chain must get past 'ai_disabled' and stop later
    aiassist.configure(enabled=True, provider="ollama",
                       endpoint="http://127.0.0.1:1")
    try:
        r = ds.ai_fallback_trace()
        assert r["reached_stage"] != "ai_disabled"
        assert r["operational"] is False
        # an unreachable provider lands on the unreachable stage
        assert r["reached_stage"] in (
            "provider_unreachable", "provider_invalid",
            "model_missing")
    finally:
        aiassist.configure(enabled=False)


# ── dev routes ─────────────────────────────────────────────────────

def test_prompt_preview_route(fresh_app):
    j = fresh_app.get("/api/dev/prompt_preview").get_json()
    assert j["ok"] is True
    assert j["tool"] == "prompt_preview"
    assert j["prompt_count"] >= 1


def test_prompt_preview_route_by_name(fresh_app):
    j = fresh_app.get(
        "/api/dev/prompt_preview?name=classify").get_json()
    assert j["ok"] is True
    assert j["prompt"]["name"] == "classify"


def test_ai_fallback_route(fresh_app):
    j = fresh_app.get("/api/dev/ai_fallback").get_json()
    assert j["ok"] is True
    assert j["tool"] == "ai_fallback_trace"
    assert "reached_stage" in j


def test_t7_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/prompt_preview", "/api/dev/ai_fallback"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
