"""Cut 871: cluster node health sentinel contract tests."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib
import time
from unittest.mock import MagicMock
import pytest


def _get_node_sentinel():
    return importlib.import_module("bulk_downloader.node_sentinel")


def test_unresponsive_node_marked_cordoned_within_10s():
    """Verify that an unresponsive node is cordoned within 10s (e.g. 2 consecutive failed 5s checks)."""
    node_sentinel = _get_node_sentinel()

    nodes = [
        {"node_id": "satellite-01", "endpoint": "http://10.0.20.41:8080/health", "twin_id": "satellite-02"},
        {"node_id": "satellite-02", "endpoint": "http://10.0.20.42:8080/health", "twin_id": "satellite-01"},
    ]
    sentinel = node_sentinel.NodeSentinel(nodes=nodes, ping_interval_s=5, fail_threshold=2)

    # Initial state: all healthy
    assert sentinel.get_node_status("satellite-01")["status"] == "healthy"
    assert sentinel.get_node_status("satellite-02")["status"] == "healthy"

    # Mock health probe: satellite-01 healthy, satellite-02 fails
    probe_results = {"satellite-01": True, "satellite-02": False}
    sentinel._probe_health = lambda node: probe_results[node["node_id"]]

    # First check at t=0s
    sentinel.check_nodes(current_time=0.0)
    assert sentinel.get_node_status("satellite-02")["status"] == "degraded"
    assert not sentinel.is_cordoned("satellite-02")

    # Second check at t=5s (within 10s)
    sentinel.check_nodes(current_time=5.0)
    assert sentinel.get_node_status("satellite-02")["status"] == "cordoned"
    assert sentinel.is_cordoned("satellite-02")
    assert not sentinel.is_cordoned("satellite-01")


def test_inflight_jobs_automatically_rerouted_to_healthy_twin():
    """Verify that in-flight jobs on a cordoned node are automatically rerouted to its healthy twin."""
    node_sentinel = _get_node_sentinel()

    nodes = [
        {"node_id": "satellite-01", "endpoint": "http://10.0.20.41:8080/health", "twin_id": "satellite-02"},
        {"node_id": "satellite-02", "endpoint": "http://10.0.20.42:8080/health", "twin_id": "satellite-01"},
    ]
    sentinel = node_sentinel.NodeSentinel(nodes=nodes, ping_interval_s=5, fail_threshold=2)

    # Register in-flight job on satellite-02
    job_id = "job-task-99"
    sentinel.assign_job(job_id=job_id, node_id="satellite-02")
    assert sentinel.get_job_node(job_id) == "satellite-02"

    # satellite-02 fails and gets cordoned
    probe_results = {"satellite-01": True, "satellite-02": False}
    sentinel._probe_health = lambda node: probe_results[node["node_id"]]

    sentinel.check_nodes(current_time=0.0)
    sentinel.check_nodes(current_time=5.0)

    assert sentinel.is_cordoned("satellite-02")
    # Job must be rerouted to twin satellite-01
    assert sentinel.get_job_node(job_id) == "satellite-01"


def test_automatic_uncordon_upon_recovery():
    """Verify that a cordoned node automatically un-cordons when health checks recover."""
    node_sentinel = _get_node_sentinel()

    nodes = [
        {"node_id": "satellite-01", "endpoint": "http://10.0.20.41:8080/health", "twin_id": "satellite-02"},
        {"node_id": "satellite-02", "endpoint": "http://10.0.20.42:8080/health", "twin_id": "satellite-01"},
    ]
    sentinel = node_sentinel.NodeSentinel(nodes=nodes, ping_interval_s=5, fail_threshold=2)

    # Force node into cordoned state
    sentinel._probe_health = lambda node: False
    sentinel.check_nodes(current_time=0.0)
    sentinel.check_nodes(current_time=5.0)
    assert sentinel.is_cordoned("satellite-02")

    # Node recovers
    sentinel._probe_health = lambda node: True
    sentinel.check_nodes(current_time=10.0)

    assert not sentinel.is_cordoned("satellite-02")
    assert sentinel.get_node_status("satellite-02")["status"] == "healthy"
