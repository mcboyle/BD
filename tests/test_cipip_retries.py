"""CIPIP: every CI pip call gets a longer read timeout and bounded retries, and a dead index still fails.

FINDING-CI-PIP-INSTALL-FLAKES (MED): "Install dependencies" failed on 5x "ReadTimeoutError pypi.org ... read timeout=15"
followed by "No matching distribution found ... (from versions: none)" (run 36409432728) -- an index blip, not the train.
The workflow now sets PIP_TIMEOUT / PIP_RETRIES at workflow level, which pip reads for every invocation.
The behavioural test points pip at a local index that accepts connections and never answers, with PIP_RETRIES=3 and a
1 s timeout: pip must try exactly PIP_RETRIES+1 times and then FAIL (the env-var route is the one the workflow uses).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

BD_GATE_SCOPE = "module"

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _pip_env() -> dict[str, str]:
    return {k: str(v) for k, v in (_workflow().get("env") or {}).items() if k.startswith("PIP_")}


def test_workflow_sets_pip_timeout_and_retries():
    env = _pip_env()
    assert int(env.get("PIP_TIMEOUT", "0")) >= 60, f"CIPIP: PIP_TIMEOUT {env.get('PIP_TIMEOUT')} (pip default 15)"
    assert 5 <= int(env.get("PIP_RETRIES", "0")) <= 10, f"CIPIP: PIP_RETRIES {env.get('PIP_RETRIES')} not bounded 5..10"


def test_no_job_or_step_overrides_them():
    wf = _workflow()
    hits = []
    for name, job in wf["jobs"].items():
        scopes = [(name, job.get("env") or {})] + [(f"{name}/{s.get('name')}", s.get("env") or {})
                                                     for s in job.get("steps", [])]
        hits += [f"{where}:{k}" for where, env in scopes for k in env if k in ("PIP_TIMEOUT", "PIP_RETRIES")]
    runs = [s.get("run", "") for job in wf["jobs"].values() for s in job.get("steps", [])]
    hits += [r.split("\n")[0] for r in runs if "--timeout " in r and "pip" in r and "pytest" not in r]
    assert not hits, f"CIPIP: a narrower scope overrides the workflow pip settings: {hits}"


@pytest.fixture
def dead_index():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
    conns: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                conns.append(srv.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}/simple", conns
    srv.close()
    for c in conns:
        c.close()


def _pip(index: str, tmp_path: Path, retries: str):
    env = dict(os.environ, PIP_RETRIES=retries, PIP_TIMEOUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1",
               PIP_CONFIG_FILE=os.devnull)
    env.pop("PIP_INDEX_URL", None)
    env.pop("PIP_EXTRA_INDEX_URL", None)
    start = time.monotonic()
    res = subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "-d", str(tmp_path / "dl"),
                          "--index-url", index, "zz-cipip-fixture"], env=env, capture_output=True, text=True,
                         timeout=180, check=False)
    return res, time.monotonic() - start


def test_dead_index_retries_then_fails(dead_index, tmp_path):
    # 3, not the workflow's value: pip's exponential backoff makes 8 retries ~70 s; the workflow value is pinned above.
    retries = "3"
    index, conns = dead_index
    res, secs = _pip(index, tmp_path, retries)
    assert res.returncode != 0, f"CIPIP negative: dead index PASSED: {res.stdout[-300:]}"
    assert "No matching distribution" in res.stderr, res.stderr[-300:]
    assert len(conns) == int(retries) + 1, f"CIPIP: {len(conns)} attempts, want PIP_RETRIES+1={int(retries) + 1}"
    assert secs < 60, f"CIPIP: dead index took {secs:.0f}s -- an outage must not become a hang"


def test_control_zero_retries_is_one_attempt(dead_index, tmp_path):
    """Positive control: the attempt counter distinguishes retry settings."""
    index, conns = dead_index
    res, _ = _pip(index, tmp_path, "0")
    assert res.returncode != 0 and len(conns) == 1, (res.returncode, len(conns))
