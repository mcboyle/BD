"""O1503 P0: LiteLLM workload aliases are dedicated and 125 leaves the shared pools.

BD_O1503_GPU_P0_CANDIDATE = absolute path of the candidate LiteLLM config.yaml (opt-in; no live fallback).
BD_O1503_GPU_P0_ORIG (optional) = the config the candidate was cut from; enables the "nothing else changed" check.
Evidence for each rule: findings/GPU-WORKLOAD-READINESS-bd-worker-B1-B.md (F2 125 thrash, F5 non-dedicated aliases);
G2 (O1505): findings/HERMES-125-bd-worker-B8-B.md -- 125 now holds hermes3 only; qwen3:8b and llama-guard3:8b were removed.
"""
BD_GATE_SCOPE = "module"
import os
from pathlib import Path

import pytest
import yaml

CANDIDATE = os.environ.get("BD_O1503_GPU_P0_CANDIDATE", "")
ORIG = os.environ.get("BD_O1503_GPU_P0_ORIG", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

H125, H228, H72 = "http://10.0.70.125:11434", "http://10.0.70.228:11434", "http://10.0.70.72:11434"
HERMES = "ollama/hermes3:8b-llama3.1-q4_K_M"
WORKLOAD = ("ci-summary", "rag-embed", "distill", "intake")


def load(path):
    p = Path(path)
    assert p.is_file(), f"config supplied but absent: {p}"
    cfg = yaml.safe_load(p.read_text())
    assert cfg.get("model_list"), f"no model_list in {p}"  # the fixture holds data before any zero is read
    return cfg


def deployments(cfg, name):
    return [d["litellm_params"] for d in cfg["model_list"] if d["model_name"] == name]


def fallback_keys(cfg):
    rs = cfg.get("router_settings") or {}
    return {k for entry in rs.get("fallbacks") or [] for k in entry}, rs.get("default_fallbacks")


def test_ci_summary_is_one_deployment_on_228():
    d = deployments(load(CANDIDATE), "ci-summary")
    assert d == [{"model": "ollama/qwen2.5-coder:7b", "api_base": H228}], f"O1503: ci-summary not dedicated to coder-7b@228: {d}"


def test_rag_embed_is_one_embedding_deployment_on_72():
    cfg = load(CANDIDATE)
    d = deployments(cfg, "rag-embed")
    assert d == [{"model": "ollama/bge-m3:latest", "api_base": H72}], f"O1503: rag-embed not dedicated to bge-m3@72: {d}"
    info = [m.get("model_info") for m in cfg["model_list"] if m["model_name"] == "rag-embed"]
    assert info == [{"mode": "embedding"}]


@pytest.mark.parametrize("name", ["qwen2.5-coder-14b", "trivial"])
def test_125_removed_from_shared_pools(name):
    d = deployments(load(CANDIDATE), name)
    assert d, f"{name} pool vanished entirely"
    on125 = [x for x in d if x["api_base"] == H125]
    assert not on125, f"O1503: {name} still routes to 125 (MAX_LOADED_MODELS=1 thrash, F2): {on125}"


def test_workload_aliases_have_no_fallback_chain():
    keys, default = fallback_keys(load(CANDIDATE))
    assert not (set(WORKLOAD) & keys), f"O1503: a fallback chain leaves the dedicated host: {keys}"
    assert not default, f"O1503: default_fallbacks would reroute a workload alias: {default}"


def test_workload_aliases_pin_no_num_ctx():
    for name in WORKLOAD:
        for d in deployments(load(CANDIDATE), name):
            assert "num_ctx" not in d, f"O1503: {name} pins num_ctx (reload/evict, F3): {d}"


@pytest.mark.parametrize("name", ["distill", "intake"])
def test_hermes_aliases_are_one_deployment_on_125(name):
    d = deployments(load(CANDIDATE), name)
    assert d == [{"model": HERMES, "api_base": H125}], f"O1505: {name} not dedicated to hermes3@125: {d}"


def test_125_serves_hermes_only():
    # qwen3:8b and llama-guard3:8b are gone from 125 (B8-B 19:37Z); any other 125 deployment routes to a missing
    # model, and a second resident model would thrash (MAX_LOADED_MODELS=1).
    other = [(m["model_name"], m["litellm_params"]["model"]) for m in load(CANDIDATE)["model_list"]
             if m["litellm_params"]["api_base"] == H125 and m["litellm_params"]["model"] != HERMES]
    assert not other, f"O1505: 125 routes to a non-Hermes model: {other}"


@pytest.mark.skipif(not ORIG, reason="BD_O1503_GPU_P0_ORIG not supplied")
def test_nothing_else_changed():
    def rows(cfg):
        return sorted((m["model_name"], m["litellm_params"]["model"], m["litellm_params"]["api_base"]) for m in cfg["model_list"])
    old, new = load(ORIG), load(CANDIDATE)
    removed = set(rows(old)) - set(rows(new))
    added = set(rows(new)) - set(rows(old))
    assert removed == {("qwen2.5-coder-14b", "ollama/qwen2.5-coder:14b", H125), ("trivial", "ollama/qwen3:8b", H125),
                       ("qwen3-8b", "ollama/qwen3:8b", H125), ("llama-guard3-8b", "ollama/llama-guard3:8b", H125)}, removed
    assert added == {("ci-summary", "ollama/qwen2.5-coder:7b", H228), ("rag-embed", "ollama/bge-m3:latest", H72),
                     ("distill", HERMES, H125), ("intake", HERMES, H125)}, added
    for key in ("router_settings", "general_settings", "litellm_settings"):
        assert old.get(key) == new.get(key), f"O1503: {key} changed"
