"""O1510b: LiteLLM balances trivial and embeddings onto 137 (2080 Ti) -- GPU for qwen2.5-coder only, embeddings on CPU variants.

BD_O1510_LITELLM_CANDIDATE = absolute path of the candidate LiteLLM config.yaml (opt-in; no live fallback).
BD_O1510_LITELLM_ORIG (optional) = the live config it was cut from (enables the exact-delta check).
Order: bd-persist/ORDER-2080TI-137-bd-pm-C-B.md (RESUME 21:52Z amendments). 137 model names: NOTE-O1510-LITELLM-NAMES.
"""
BD_GATE_SCOPE = "module"
import os
from pathlib import Path

import pytest
import yaml

CANDIDATE = os.environ.get("BD_O1510_LITELLM_CANDIDATE", "")
ORIG = os.environ.get("BD_O1510_LITELLM_ORIG", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
H137, H72, H228 = "http://10.0.10.137:11434", "http://10.0.70.72:11434", "http://10.0.70.228:11434"
GPU137 = "ollama/qwen2.5-coder:7b-16k"   # the resident coder on 137 (ctx 16384)
CPU137 = {"bge-m3": "ollama/bge-m3-cpu:latest", "nomic-embed-text": "ollama/nomic-embed-text-cpu:latest",
          "rag-embed": "ollama/bge-m3-cpu:latest"}


def load(p):
    cfg = yaml.safe_load(Path(p).read_text())
    assert cfg.get("model_list"), f"no model_list in {p}"
    return cfg


def deps(cfg, name):
    return [m["litellm_params"] for m in cfg["model_list"] if m["model_name"] == name]


def test_137_gpu_serves_only_the_coder():
    cfg = load(CANDIDATE)
    on137 = [(m["model_name"], m["litellm_params"]["model"]) for m in cfg["model_list"] if m["litellm_params"]["api_base"] == H137]
    assert on137, "fixture: no 137 deployments at all"
    bad = [x for x in on137 if x[1] != GPU137 and x[1] not in CPU137.values()]
    assert not bad, f"O1510: 137 would load a second GPU model and evict the coder: {bad}"


@pytest.mark.parametrize("alias", sorted(CPU137))
def test_embeddings_balanced_onto_137_cpu_variant(alias):
    d = deps(load(CANDIDATE), alias)
    on137 = [x for x in d if x["api_base"] == H137]
    assert on137 == [{"model": CPU137[alias], "api_base": H137}], f"O1510: {alias}@137 is not the CPU variant: {on137}"
    assert [x for x in d if x["api_base"] != H137], f"O1510: {alias} lost its non-137 deployment (balance, not move)"


def test_trivial_uses_the_resident_coder_on_137():
    d = deps(load(CANDIDATE), "trivial")
    assert {"model": GPU137, "api_base": H137} in d, f"O1510: trivial not on the 137 coder: {d}"


@pytest.mark.parametrize("alias,host", [("ci-summary", H228), ("distill", "http://10.0.70.125:11434"),
                                        ("intake", "http://10.0.70.125:11434")])
def test_dedicated_aliases_untouched(alias, host):
    d = deps(load(CANDIDATE), alias)
    assert len(d) == 1 and d[0]["api_base"] == host, f"O1510: {alias} no longer dedicated to {host}: {d}"


def test_no_per_request_ctx_or_gpu_pins():
    for m in load(CANDIDATE)["model_list"]:
        p = m["litellm_params"]
        assert "num_ctx" not in p and "num_gpu" not in p, f"O1510: {m['model_name']} pins {p} (per-request pins reload)"


@pytest.mark.skipif(not ORIG, reason="BD_O1510_LITELLM_ORIG not supplied")
def test_exact_delta_against_live():
    rows = lambda c: {(m["model_name"], m["litellm_params"]["model"], m["litellm_params"]["api_base"]) for m in c["model_list"]}
    old, new = load(ORIG), load(CANDIDATE)
    assert rows(old) - rows(new) == {("nomic-embed-text", "ollama/nomic-embed-text", H137), ("bge-m3", "ollama/bge-m3:latest", H137),
                                     ("trivial", "ollama/qwen2.5:7b", H137)}, rows(old) - rows(new)
    assert rows(new) - rows(old) == {("nomic-embed-text", CPU137["nomic-embed-text"], H137), ("bge-m3", CPU137["bge-m3"], H137),
                                     ("rag-embed", CPU137["rag-embed"], H137), ("trivial", GPU137, H137)}, rows(new) - rows(old)
    for k in ("router_settings", "general_settings", "litellm_settings"):
        assert old.get(k) == new.get(k), f"O1510: {k} changed"
