"""Phase 9.1 -- model capability registry (RED-first).

Turns Cut 7.1's flat name-dropdown into an operational registry: each model is an
entry with provider/local/reachable/capability/lane metadata; capability gating
rejects text-only models for vision tasks; the registry fails open with safe
defaults and never blocks Settings save (free-text names still usable).
"""

from bulk_downloader import model_registry as mr
from bulk_downloader.model_registry import (
    classify_capabilities, can_use, make_entry, build_registry, TASK_LANES,
)


# ── classification ───────────────────────────────────────────────────────
def test_classify_text_only():
    for n in ("qwen2.5:0.5b", "qwen2.5:7b", "llama3.1:8b", "mistral:7b"):
        t, v = classify_capabilities(n)
        assert t is True and v is False, n


def test_classify_vision():
    for n in ("qwen2.5vl:7b", "llava:13b", "moondream", "minicpm-v:8b", "bakllava"):
        t, v = classify_capabilities(n)
        assert v is True, n
        assert t is True, n   # vision models are also text-capable


def test_long_hf_name_accepted():
    n = "hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M"
    t, v = classify_capabilities(n)
    assert t is True
    e = make_entry(n, "ollama")
    assert e.name == n and e.text_capable is True


# ── capability gating ────────────────────────────────────────────────────
def test_can_use_rejects_text_only_for_vision():
    ok, why = can_use("qwen2.5:0.5b", "vision")
    assert ok is False and why
    ok, _ = can_use("qwen2.5vl:7b", "vision")
    assert ok is True
    ok, _ = can_use("qwen2.5:0.5b", "text")
    assert ok is True


def test_can_use_text_always_ok():
    for n in ("qwen2.5:0.5b", "qwen2.5vl:7b", "whatever:latest", ""):
        ok, _ = can_use(n, "text")
        assert ok is True, n


# ── entry shape ──────────────────────────────────────────────────────────
def test_make_entry_fields():
    e = make_entry("qwen2.5:0.5b", "ollama", reachable=True)
    for f in ("provider", "name", "display_name", "local", "reachable",
              "last_checked", "last_error", "text_capable", "vision_capable",
              "json_reliability", "recommended_lane", "approx_size", "approx_context"):
        assert hasattr(e, f), f
    assert e.provider == "ollama"
    assert e.text_capable is True and e.vision_capable is False


def test_make_entry_local_vs_cloud():
    assert make_entry("qwen2.5:0.5b", "ollama").local is True
    assert make_entry("claude-haiku-4-5", "claude").local is False
    assert make_entry("gpt-4o-mini", "openai").local is False


# ── registry build (fail-open) ───────────────────────────────────────────
def _live(models):
    return lambda: {"ok": True, "models": models, "provider": "ollama", "error": ""}


def test_build_registry_from_live_list():
    reg = build_registry("ollama", _lister=_live(["qwen2.5:0.5b", "qwen2.5vl:7b"]))
    assert reg["ok"] is True
    names = [e["name"] for e in reg["entries"]]
    assert "qwen2.5:0.5b" in names and "qwen2.5vl:7b" in names
    assert all(e["reachable"] for e in reg["entries"])
    assert reg["lanes"] == list(TASK_LANES)


def test_build_registry_fails_open_on_error():
    bad = lambda: {"ok": False, "models": [], "provider": "ollama", "error": "unreachable"}
    reg = build_registry("ollama", _lister=bad)
    assert reg["ok"] is False
    assert reg["entries"] == []
    assert reg.get("error")
    assert "defaults" in reg            # safe defaults present, never raises


def test_build_registry_swallows_exception():
    def boom():
        raise RuntimeError("kaboom")
    reg = build_registry("ollama", _lister=boom)   # must NOT raise
    assert reg["ok"] is False and reg["entries"] == []


def test_unavailable_model_reachable_false_without_throwing():
    # a typed-in model the endpoint doesn't have -> reachable False, no exception
    e = make_entry("not-pulled:latest", "ollama", reachable=False, last_error="absent")
    assert e.reachable is False
    assert e.last_error == "absent"


# ── lane defaults ────────────────────────────────────────────────────────
def test_lane_defaults_pick_vision_for_vision_lane():
    reg = build_registry("ollama", _lister=_live(["qwen2.5:0.5b", "qwen2.5vl:7b"]))
    d = reg["defaults"]
    assert d["vision/screenshot review"] == "qwen2.5vl:7b"
    assert d["UI/accessibility review"] == "qwen2.5vl:7b"
    assert d["cheap classify/parse"] in ("qwen2.5:0.5b", "qwen2.5vl:7b")


def test_lane_defaults_no_vision_model_is_none():
    reg = build_registry("ollama", _lister=_live(["qwen2.5:0.5b"]))
    assert reg["defaults"]["vision/screenshot review"] is None
    assert reg["defaults"]["cheap classify/parse"] == "qwen2.5:0.5b"


# ── Settings free-text fallback preserved ────────────────────────────────
def test_free_text_unknown_name_still_usable():
    e = make_entry("some-custom-model:latest", "ollama", reachable=False)
    assert e.name == "some-custom-model:latest"
    assert e.text_capable is True
    ok, _ = can_use(e.name, "text")
    assert ok is True


# ── lane catalogue ───────────────────────────────────────────────────────
def test_task_lanes_constant():
    expected = {
        "cheap classify/parse", "selector repair", "login reasoning",
        "template summary", "failure triage", "redaction second-pass",
        "report summarization", "filename metadata", "vision/screenshot review",
        "UI/accessibility review", "OPV evidence summarization",
    }
    assert expected.issubset(set(TASK_LANES))


def test_json_reliability_hint_present():
    e = make_entry("qwen2.5:0.5b", "ollama")
    assert e.json_reliability in ("unknown", "low", "medium", "high")
