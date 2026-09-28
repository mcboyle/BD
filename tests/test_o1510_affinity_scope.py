"""O1510 (RULING-O1510-137-AFFINITY amend): bge-m3 and nomic-embed-text exempt from deployment_affinity, per alias.

BD_O1510_AFFINITY_CANDIDATE = absolute path of the candidate LiteLLM config.yaml (opt-in; no live fallback).
BD_O1510_AFFINITY_ORIG (optional) = the live config it was cut from (enables the exact-delta check).
Why: the affinity TTL slides on every call, so one master-key hash pins each alias to one deployment for good and the
137 CPU embedding variants never get traffic (findings/O1510-137-FLUSH-bd-worker-B1-B.md). LiteLLM's Router reads
router_settings.model_group_affinity_config; an explicit group entry REPLACES the global flags for that group.
"""
BD_GATE_SCOPE = "module"
import os
from pathlib import Path

import pytest
import yaml

CANDIDATE = os.environ.get("BD_O1510_AFFINITY_CANDIDATE", "")
ORIG = os.environ.get("BD_O1510_AFFINITY_ORIG", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
EXEMPT = ("bge-m3", "nomic-embed-text")
VALID = {"deployment_affinity", "responses_api_deployment_check", "session_affinity", "encrypted_content_affinity"}


def load(p):
    cfg = yaml.safe_load(Path(p).read_text())
    assert cfg.get("model_list") and cfg.get("router_settings"), f"not a LiteLLM proxy config: {p}"
    return cfg


def effective(rs, group):
    """Mirror of DeploymentAffinityCheck._get_effective_flags: group entry wins, else the global list."""
    per = (rs.get("model_group_affinity_config") or {}).get(group)
    return set(per) if per is not None else set(rs.get("optional_pre_call_checks") or []) & VALID


def test_embedding_aliases_are_exempt_from_deployment_affinity():
    rs = load(CANDIDATE)["router_settings"]
    for g in EXEMPT:
        assert "deployment_affinity" not in effective(rs, g), f"O1510: {g} still pinned by deployment_affinity"


def test_exempt_aliases_keep_session_affinity():
    rs = load(CANDIDATE)["router_settings"]
    for g in EXEMPT:
        assert effective(rs, g) == {"session_affinity"}, f"O1510: {g} lost more than deployment_affinity: {effective(rs, g)}"


def test_exemption_is_per_alias_not_global():
    cfg = load(CANDIDATE)
    rs = cfg["router_settings"]
    assert "deployment_affinity" in rs["optional_pre_call_checks"], "O1510: deployment_affinity removed globally (option C)"
    assert set(rs["model_group_affinity_config"]) == set(EXEMPT), f"O1510: exemption scope wrong: {rs['model_group_affinity_config']}"
    for g in {m["model_name"] for m in cfg["model_list"]} - set(EXEMPT):
        assert "deployment_affinity" in effective(rs, g), f"O1510: {g} lost deployment_affinity (rag-embed must stay on 72)"


def test_exempt_groups_exist_and_flags_are_known():
    cfg = load(CANDIDATE)
    names = {m["model_name"] for m in cfg["model_list"]}
    for g, flags in cfg["router_settings"]["model_group_affinity_config"].items():
        assert g in names, f"O1510: {g} is not a model group; LiteLLM would silently ignore it"
        assert set(flags) <= VALID, f"O1510: unknown flag(s) {set(flags) - VALID} for {g}; LiteLLM ignores them"


@pytest.mark.skipif(not ORIG, reason="BD_O1510_AFFINITY_ORIG not supplied")
def test_exact_delta_against_live():
    old, new = load(ORIG), load(CANDIDATE)
    assert old["model_list"] == new["model_list"], "O1510: model_list changed"
    for k in ("general_settings", "litellm_settings"):
        assert old.get(k) == new.get(k), f"O1510: {k} changed"
    added = {k: v for k, v in new["router_settings"].items() if old["router_settings"].get(k) != v}
    assert added == {"model_group_affinity_config": {g: ["session_affinity"] for g in EXEMPT}}, added
    assert set(old["router_settings"]) - set(new["router_settings"]) == set(), "O1510: a router setting was dropped"
