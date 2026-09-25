"""Unit tests for toolchain/bin/bd-dag-schedule.

Verifies DAG parsing, topological ordering, wave partitioning, and leaf dispatching.
Fleet Rule 21 compliant: zero site interactions touched.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

BIN_PATH = Path(__file__).resolve().parent.parent / "toolchain" / "bin" / "bd-dag-schedule"


@pytest.fixture
def sample_dag_dict():
    return {
        "tasks": [
            {"id": "t1_setup", "action_type": "build", "dependencies": []},
            {"id": "t2_unit", "action_type": "test", "dependencies": ["t1_setup"]},
            {"id": "t3_lint", "action_type": "lint", "dependencies": ["t1_setup"]},
            {"id": "t4_e2e", "action_type": "test", "dependencies": ["t2_unit", "t3_lint"]},
        ]
    }


def test_bd_dag_schedule_waves(sample_dag_dict):
    p = subprocess.run(
        [sys.executable, str(BIN_PATH), "--waves"],
        input=json.dumps(sample_dag_dict),
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 0, f"Failed with stderr: {p.stderr}"
    assert "Wave 0 [1 tasks]: t1_setup" in p.stdout
    assert "t2_unit" in p.stdout
    assert "t3_lint" in p.stdout
    assert "t4_e2e" in p.stdout


def test_bd_dag_schedule_json(sample_dag_dict):
    p = subprocess.run(
        [sys.executable, str(BIN_PATH), "--json"],
        input=json.dumps(sample_dag_dict),
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 0, f"Failed with stderr: {p.stderr}"
    data = json.loads(p.stdout)
    assert data["total_tasks"] == 4
    assert data["wave_count"] == 3
    assert data["ready_leaves"] == ["t1_setup"]
    assert "t4_e2e" in data["topological_order"]


def test_bd_dag_schedule_prune(sample_dag_dict):
    p = subprocess.run(
        [sys.executable, str(BIN_PATH), "--json", "--prune", "t1_setup"],
        input=json.dumps(sample_dag_dict),
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 0, f"Failed with stderr: {p.stderr}"
    data = json.loads(p.stdout)
    assert data["total_tasks"] == 4


def test_bd_dag_schedule_invalid_json():
    p = subprocess.run(
        [sys.executable, str(BIN_PATH)],
        input="invalid-json-content",
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 1
    assert "Invalid JSON input" in p.stderr
