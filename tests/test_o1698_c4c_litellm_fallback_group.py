"""O1698 C4c: the C4 LiteLLM fragment must be FALLBACK-ONLY for the .120 CPU overflow.

The first fragment appended local-embed / local-coder deployments with `order: 2`. LiteLLM 1.103.0 keeps only the
min-order deployments (utils.get_order_filtered_deployments) and drops unset ones, so that made .120 the ONLY primary;
a bare append shuffles ~50/50. Fallback-only = new alias groups + router_settings.fallbacks, primaries untouched.
Opt-in: BD_O1698_C4C_LITELLM_FALLBACK_GROUP_CANDIDATE=<fragment path>, or BD_TEST_O1698_C4C_LITELLM_FALLBACK_GROUP=1
(defaults to the FIX candidate). RED = env pointing at the old fragment (LITELLM-DIFF.proposed.base).
"""

import copy
import os
from pathlib import Path

import pytest
import yaml

BD_GATE_SCOPE = "module"
FIX = Path("/home/mboyle/bd-persist/harness-work/FIX/o1698-c4c-litellm-fallback-group")
CANDIDATE = os.environ.get("BD_O1698_C4C_LITELLM_FALLBACK_GROUP_CANDIDATE") or (
    str(FIX / "LITELLM-DIFF.proposed") if os.environ.get("BD_TEST_O1698_C4C_LITELLM_FALLBACK_GROUP") == "1" else "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

PRIMARY = {"local-embed": "ollama/bge-m3:latest", "local-coder": "ollama/qwen2.5-coder:7b-instruct-q4_K_M"}
API_BASE = "http://10.0.70.120:11435"


def fallback_only_problems(doc):
    """Every reason this fragment could put .120 in a primary alias's pick set, or leave a fallback unresolvable."""
    if not isinstance(doc, dict) or not set(doc) <= {"model_list", "router_settings"}:
        return ["TOP_LEVEL_KEYS"]
    entries = doc.get("model_list") or []
    problems, groups = [], {}
    for entry in entries:
        name = entry.get("model_name")
        params = entry.get("litellm_params") or {}
        groups.setdefault(name, []).append(params)
        if name in PRIMARY:
            problems.append(f"PRIMARY_ALIAS_TOUCHED:{name}")   # shuffled with (or, with order, replaces) the primary
        if "order" in params or "order" in (entry.get("model_info") or {}):
            problems.append(f"ORDER_KEY:{name}")
        if params.get("api_base") != API_BASE:
            problems.append(f"API_BASE:{name}:{params.get('api_base')}")
    mapping = {}
    for item in ((doc.get("router_settings") or {}).get("fallbacks") or []):
        if isinstance(item, dict):
            mapping.update(item)
    for primary, model in PRIMARY.items():
        targets = mapping.get(primary)
        if not targets:
            problems.append(f"NO_FALLBACK:{primary}")
            continue
        for alias in targets:
            if alias not in groups:
                problems.append(f"UNRESOLVED_FALLBACK:{primary}->{alias}")
            elif any(p.get("model") != model for p in groups[alias]):
                problems.append(f"FALLBACK_MODEL:{alias}")
    return problems


def load(path):
    return yaml.safe_load(Path(path).read_text())


def test_candidate_is_fallback_only():
    assert fallback_only_problems(load(CANDIDATE)) == []


def test_old_order_fragment_is_refused():
    base = FIX / "LITELLM-DIFF.proposed.base"
    if not base.is_file():
        pytest.skip("base fragment copy not on this host")
    problems = fallback_only_problems(load(base))
    assert "PRIMARY_ALIAS_TOUCHED:local-coder" in problems and "ORDER_KEY:local-coder" in problems, problems
    assert "NO_FALLBACK:local-embed" in problems, problems


@pytest.mark.parametrize("mutate,expected", [
    pytest.param(lambda d: d["model_list"].append(
        {"model_name": "local-coder", "litellm_params": {"model": PRIMARY["local-coder"], "api_base": API_BASE}}),
        "PRIMARY_ALIAS_TOUCHED:local-coder", id="bare-local-coder-re-added"),
    pytest.param(lambda d: d["model_list"][0]["litellm_params"].__setitem__("order", 2), "ORDER_KEY:", id="order-key"),
    pytest.param(lambda d: d["router_settings"]["fallbacks"].pop(), "NO_FALLBACK:", id="fallback-mapping-dropped"),
    pytest.param(lambda d: d["model_list"].pop(), "UNRESOLVED_FALLBACK:", id="fallback-alias-undefined"),
    pytest.param(lambda d: d["model_list"][0]["litellm_params"].__setitem__("api_base", "http://10.0.10.137:11434"),
                 "API_BASE:", id="wrong-host"),
    pytest.param(lambda d: [e["litellm_params"].__setitem__("model", PRIMARY["local-embed"])
                            for e in d["model_list"] if e["model_name"] == "local-coder-cpu"],
                 "FALLBACK_MODEL:local-coder-cpu", id="coder-fallback-serves-embed-model"),
    pytest.param(lambda d: d["model_list"][0].setdefault("model_info", {}).__setitem__("order", 2),
                 "ORDER_KEY:", id="order-key-in-model-info"),
    pytest.param(lambda d: d.__setitem__("general_settings", {}), "TOP_LEVEL_KEYS", id="extra-top-level-key"),
])
def test_each_defect_is_caught_on_a_copy_of_the_candidate(mutate, expected):
    doc = copy.deepcopy(load(CANDIDATE))
    assert fallback_only_problems(doc) == []          # the twin: unmutated candidate is clean
    mutate(doc)
    assert any(p.startswith(expected) for p in fallback_only_problems(doc)), expected
