"""tests/test_bh_wave1_config.py -- Wave 1 bug hunt tests for BH-CONFIG.

Findings:
- FINDING-BH-bd-agy-audit-1-004: bulk_downloader/embeddings_client.py DEFAULT_ENDPOINT
  and bulk_downloader/guardrails.py DEFAULT_GUARDRAILS_ENDPOINT must support
  EMBEDDINGS_ENDPOINT and GUARDRAILS_ENDPOINT env var overrides with fallback to defaults,
  and must be honoured on the actual execution/read paths (Shape Note N1).
- FINDING-BH-bd-agy-audit-1-005: .github/workflows/ci.yml process-tests job must
  install requirements-test.txt alongside requirements.txt for gate-suites consistency.
- REFUTE-1/2: Single read of env var into module constants; teardown environment and
  module state restoration; guardrails SSRF exemption documentation update.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
EXPECTED_DEFAULT_EMBEDDINGS_ENDPOINT = "http://10.0.70.72:8081/api/embeddings"
EXPECTED_DEFAULT_GUARDRAILS_ENDPOINT = "http://10.0.70.125:8005/api/chat"


@pytest.fixture(autouse=True)
def _restore_environment_and_modules() -> Generator[None, None, None]:
    """Isolate environment mutations and restore module constants on teardown."""
    orig_emb = os.environ.get("EMBEDDINGS_ENDPOINT")
    orig_gr = os.environ.get("GUARDRAILS_ENDPOINT")
    try:
        yield
    finally:
        if orig_emb is not None:
            os.environ["EMBEDDINGS_ENDPOINT"] = orig_emb
        else:
            os.environ.pop("EMBEDDINGS_ENDPOINT", None)

        if orig_gr is not None:
            os.environ["GUARDRAILS_ENDPOINT"] = orig_gr
        else:
            os.environ.pop("GUARDRAILS_ENDPOINT", None)

        if "bulk_downloader.embeddings_client" in sys.modules:
            importlib.reload(sys.modules["bulk_downloader.embeddings_client"])
        if "bulk_downloader.guardrails" in sys.modules:
            importlib.reload(sys.modules["bulk_downloader.guardrails"])


# --- FINDING-BH-bd-agy-audit-1-004 tests ---

def test_embeddings_client_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """When EMBEDDINGS_ENDPOINT is unset, DEFAULT_ENDPOINT matches the cluster default."""
    monkeypatch.delenv("EMBEDDINGS_ENDPOINT", raising=False)
    import bulk_downloader.embeddings_client as ec
    importlib.reload(ec)
    assert ec.DEFAULT_ENDPOINT == EXPECTED_DEFAULT_EMBEDDINGS_ENDPOINT


def test_embeddings_client_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """When EMBEDDINGS_ENDPOINT is set, DEFAULT_ENDPOINT reflects the override upon reload."""
    custom_endpoint = "http://custom-satellite.local:9099/api/embeddings"
    monkeypatch.setenv("EMBEDDINGS_ENDPOINT", custom_endpoint)
    import bulk_downloader.embeddings_client as ec
    importlib.reload(ec)
    assert ec.DEFAULT_ENDPOINT == custom_endpoint
    # Negative control: ensure it did not retain the default
    assert ec.DEFAULT_ENDPOINT != EXPECTED_DEFAULT_EMBEDDINGS_ENDPOINT


def test_embeddings_client_call_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shape Note N1: embed() execution path honors configured DEFAULT_ENDPOINT."""
    custom_endpoint = "http://custom-satellite-call.local:9099/api/embeddings"
    monkeypatch.setenv("EMBEDDINGS_ENDPOINT", custom_endpoint)
    import bulk_downloader.embeddings_client as ec
    importlib.reload(ec)

    seen: dict[str, Any] = {}

    def fake_http_post(self: Any, endpoint: str, body: Any, headers: Any, timeout: float) -> tuple[bool, int, dict[str, Any], float]:
        seen["endpoint"] = endpoint
        seen["body"] = body
        return True, 200, {"embedding": [0.1, 0.2, 0.3]}, 1.0

    monkeypatch.setattr(ec.OllamaProvider, "_http_post", fake_http_post)

    vec = ec.embed("sample query")
    assert vec == [0.1, 0.2, 0.3]
    assert seen["endpoint"] == custom_endpoint
    assert seen["endpoint"] != EXPECTED_DEFAULT_EMBEDDINGS_ENDPOINT


def test_embeddings_client_explicit_endpoint_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit endpoint parameter overrides module DEFAULT_ENDPOINT."""
    import bulk_downloader.embeddings_client as ec

    explicit_endpoint = "http://explicit-endpoint.local:9099/api/embeddings"
    seen: dict[str, Any] = {}

    def fake_http_post(self: Any, endpoint: str, body: Any, headers: Any, timeout: float) -> tuple[bool, int, dict[str, Any], float]:
        seen["endpoint"] = endpoint
        return True, 200, {"embedding": [0.4, 0.5, 0.6]}, 1.0

    monkeypatch.setattr(ec.OllamaProvider, "_http_post", fake_http_post)

    vec = ec.embed("sample query", endpoint=explicit_endpoint)
    assert vec == [0.4, 0.5, 0.6]
    assert seen["endpoint"] == explicit_endpoint


def test_guardrails_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """When GUARDRAILS_ENDPOINT is unset, DEFAULT_GUARDRAILS_ENDPOINT matches the cluster default."""
    monkeypatch.delenv("GUARDRAILS_ENDPOINT", raising=False)
    import bulk_downloader.guardrails as gr
    importlib.reload(gr)
    assert gr.DEFAULT_GUARDRAILS_ENDPOINT == EXPECTED_DEFAULT_GUARDRAILS_ENDPOINT


def test_guardrails_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """When GUARDRAILS_ENDPOINT is set, DEFAULT_GUARDRAILS_ENDPOINT reflects the override."""
    custom_endpoint = "http://custom-guardrails.local:9005/api/chat"
    monkeypatch.setenv("GUARDRAILS_ENDPOINT", custom_endpoint)
    import bulk_downloader.guardrails as gr
    importlib.reload(gr)
    assert gr.DEFAULT_GUARDRAILS_ENDPOINT == custom_endpoint
    # Negative control: ensure it did not retain the default
    assert gr.DEFAULT_GUARDRAILS_ENDPOINT != EXPECTED_DEFAULT_GUARDRAILS_ENDPOINT


def test_guardrails_call_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shape Note N1: pre_download_safety_check execution path honors GUARDRAILS_ENDPOINT."""
    custom_endpoint = "http://custom-guardrails-call.local:9005/api/chat"
    monkeypatch.setenv("GUARDRAILS_ENDPOINT", custom_endpoint)
    import bulk_downloader.guardrails as gr
    importlib.reload(gr)

    captured_endpoint: list[str] = []

    def fake_request(payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
        captured_endpoint.append(endpoint)
        return {"content": "safe to download"}

    allowed = asyncio.run(
        gr.pre_download_safety_check(
            {"title": "test item"},
            enabled=True,
            request=fake_request,
        )
    )
    assert allowed is True
    assert captured_endpoint == [custom_endpoint]
    assert captured_endpoint[0] != EXPECTED_DEFAULT_GUARDRAILS_ENDPOINT


def test_guardrails_ssrf_egress_exemption_registered() -> None:
    """REFUTE-2: Verify guardrails SSRF egress exemption describes operator-configured endpoint."""
    from bulk_downloader import ssrf_egress_exemptions

    key = "bulk_downloader/guardrails.py::_default_request"
    assert key in ssrf_egress_exemptions.ACCOUNTED
    status, description = ssrf_egress_exemptions.ACCOUNTED[key]
    assert status == "exempt"
    assert "GUARDRAILS_ENDPOINT" in description
    assert "10.0.70.125:8005" in description


# --- FINDING-BH-bd-agy-audit-1-005 tests ---

def _validate_ci_process_tests(workflow_data: dict[str, Any]) -> None:
    jobs = workflow_data.get("jobs", {})
    assert "process-tests" in jobs, "process-tests job missing in ci.yml"
    pt_job = jobs["process-tests"]
    steps = pt_job.get("steps", [])

    install_step = None
    setup_python_step = None
    for step in steps:
        if isinstance(step, dict):
            if step.get("name") == "Install Python dependencies":
                install_step = step
            uses = step.get("uses", "")
            if "setup-python" in uses:
                setup_python_step = step

    assert install_step is not None, "Install Python dependencies step missing in process-tests"
    run_cmd = install_step.get("run", "")
    assert "requirements-test.txt" in run_cmd, (
        f"process-tests must install requirements-test.txt, found: {run_cmd}"
    )
    assert "-r requirements.txt" in run_cmd, (
        f"process-tests must install requirements.txt, found: {run_cmd}"
    )

    assert setup_python_step is not None, "setup-python step missing in process-tests"
    with_clause = setup_python_step.get("with", {})
    cache_path = with_clause.get("cache-dependency-path", "")
    assert "requirements-test.txt" in cache_path, (
        f"setup-python in process-tests must include requirements-test.txt in cache-dependency-path, found: {cache_path}"
    )


def test_ci_process_tests_installs_requirements_test() -> None:
    """The process-tests job in ci.yml must install requirements-test.txt."""
    assert CI_YML_PATH.is_file(), f"Missing CI workflow at {CI_YML_PATH}"
    with open(CI_YML_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _validate_ci_process_tests(data)


def test_ci_process_tests_negative_controls() -> None:
    """Negative controls: mutating the parsed workflow must trigger validation failures."""
    with open(CI_YML_PATH, "r", encoding="utf-8") as f:
        real_data = yaml.safe_load(f)

    # Mutant 1: drop requirements-test.txt from run command
    mutant_run = copy.deepcopy(real_data)
    for step in mutant_run["jobs"]["process-tests"]["steps"]:
        if isinstance(step, dict) and step.get("name") == "Install Python dependencies":
            step["run"] = "pip install -r requirements.txt"
    with pytest.raises(AssertionError, match="must install requirements-test.txt"):
        _validate_ci_process_tests(mutant_run)

    # Mutant 2: drop requirements-test.txt from cache-dependency-path
    mutant_cache = copy.deepcopy(real_data)
    for step in mutant_cache["jobs"]["process-tests"]["steps"]:
        if isinstance(step, dict) and "setup-python" in step.get("uses", ""):
            step["with"]["cache-dependency-path"] = "requirements.txt\n"
    with pytest.raises(AssertionError, match="must include requirements-test.txt in cache-dependency-path"):
        _validate_ci_process_tests(mutant_cache)
