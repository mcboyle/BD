"""RULING-0080 s1(b) -- requirements.txt must resolve under a bound.

MEASURED (fixer-B, 2026-09-23 06:34-07:00Z): with boto3 open to <2.0 beside aioboto3 13.x, aioboto3 13.4.0 pins
aiobotocore[boto3] 2.18.0, which takes only boto3 1.36.0/1.36.1, and pip backtracks every boto3 release from 1.43.x down
(the devpi index serves no PEP 658 metadata, ~6 s each). The band's per-cut venv build (`timeout 1800 pip install`)
never finished and every cut on main >= a7e729c8 read rc 87. Bounded ranges: dry-run resolves in ~19 s.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement

BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
RESOLVE_BOUND_S = 120   # bounded: 19 s; unbounded: 214 s with a warm local pip cache, > 1800 s on band hosts


def _reqs():
    out = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            r = Requirement(line)
            out[r.name.lower()] = r
    return out


def test_boto3_and_aiobotocore_ranges_are_what_aioboto3_13_resolves():
    r = _reqs()
    assert r["aioboto3"].specifier.contains("13.4.0")
    assert r["aiobotocore"].specifier.contains("2.18.0") and not r["aiobotocore"].specifier.contains("2.19.0"), (
        f"aiobotocore {r['aiobotocore'].specifier} is open past what aioboto3 13.x pins (2.18.0)")
    assert r["boto3"].specifier.contains("1.36.1") and not r["boto3"].specifier.contains("1.36.2"), (
        f"boto3 {r['boto3'].specifier} lets pip backtrack releases aiobotocore 2.18.0 can never take (<1.36.2)")


def test_pip_resolves_the_manifests_within_the_bound(tmp_path):
    report = tmp_path / "report.json"
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--dry-run", "--ignore-installed",
           "--report", str(report), "-r", str(ROOT / "requirements.txt"),
           "-r", str(ROOT / "requirements-test.txt")]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=RESOLVE_BOUND_S,
                           env=dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1"))
    except subprocess.TimeoutExpired:
        pytest.fail(f"pip did not resolve requirements.txt within {RESOLVE_BOUND_S}s (backtracking; band venv rc 87)")
    if p.returncode != 0 and ("NewConnectionError" in p.stderr or "No matching distribution" in p.stderr
                              and "boto" not in p.stderr):
        pytest.skip(f"package index unreachable -- COULD NOT LOOK: {p.stderr[-300:]}")
    assert p.returncode == 0, p.stderr[-1500:]
    got = {i["metadata"]["name"].lower(): i["metadata"]["version"]
           for i in json.loads(report.read_text())["install"]}
    assert got.get("boto3", "").startswith("1.36.") and got.get("aiobotocore") == "2.18.0", got
