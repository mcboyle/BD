"""Cluster satellite node heartbeat and drain sentinel (Row 871).

Proactively monitors cluster nodes on management fabric (10.0.20.40/28) via /health
endpoints every 5s, cordoning unresponsive nodes within 10s and rerouting active
in-flight tasks to healthy twins to avoid 300s timeout hangs.

Zero site logins touched (Fleet Rule 21).
"""
from __future__ import annotations

import http.client
import time
from typing import Any, Callable, Dict, List, Optional


class NodeSentinel:
    """Monitors node health heartbeats, coordinates cordoning and job rerouting."""

    def __init__(
        self,
        nodes: List[Dict[str, Any]],
        ping_interval_s: int = 5,
        fail_threshold: int = 2,
    ):
        self.ping_interval_s = ping_interval_s
        self.fail_threshold = fail_threshold
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._jobs: Dict[str, str] = {}  # job_id -> node_id

        for n in nodes:
            nid = n["node_id"]
            self._nodes[nid] = {
                "node_id": nid,
                "endpoint": n.get("endpoint", ""),
                "twin_id": n.get("twin_id"),
                "status": "healthy",
                "consecutive_failures": 0,
                "last_heartbeat": 0.0,
                "cordoned": False,
            }

    def _probe_health(self, node: Dict[str, Any]) -> bool:
        """Default health probe over HTTP."""
        endpoint = node.get("endpoint", "")
        if not endpoint:
            return False
        try:
            target = endpoint
            if target.startswith("http://"):
                target = target[7:]
            elif target.startswith("https://"):
                target = target[8:]
            parts = target.split("/", 1)
            host_port = parts[0]
            path = "/" + parts[1] if len(parts) > 1 else "/"

            if ":" in host_port:
                host, port_str = host_port.split(":", 1)
                port = int(port_str)
            else:
                host = host_port
                port = 80

            conn = http.client.HTTPConnection(host, port, timeout=3.0)
            conn.request("GET", path, headers={"User-Agent": "bd-sentinel/1.0"})
            resp = conn.getresponse()
            return resp.status == 200
        except (OSError, TimeoutError, http.client.HTTPException):
            return False

    def check_nodes(self, current_time: Optional[float] = None) -> None:
        """Run one sweep across all registered nodes."""
        now = time.time() if current_time is None else current_time

        for nid, info in self._nodes.items():
            healthy = self._probe_health(info)
            if healthy:
                info["last_heartbeat"] = now
                info["consecutive_failures"] = 0
                if info["cordoned"]:
                    # Automatic un-cordon upon recovery
                    info["cordoned"] = False
                    info["status"] = "healthy"
                else:
                    info["status"] = "healthy"
            else:
                info["consecutive_failures"] += 1
                if info["consecutive_failures"] >= self.fail_threshold:
                    if not info["cordoned"]:
                        info["cordoned"] = True
                        info["status"] = "cordoned"
                        # Automatic drain & reroute in-flight jobs to twin
                        twin = info.get("twin_id")
                        if twin and twin in self._nodes and not self._nodes[twin]["cordoned"]:
                            self._reroute_jobs(from_node=nid, to_node=twin)
                else:
                    info["status"] = "degraded"

    def assign_job(self, job_id: str, node_id: str) -> None:
        """Assign an active job to a node."""
        self._jobs[job_id] = node_id

    def get_job_node(self, job_id: str) -> Optional[str]:
        """Get the current node handling a job."""
        return self._jobs.get(job_id)

    def _reroute_jobs(self, from_node: str, to_node: str) -> int:
        """Reroute in-flight jobs from one node to another."""
        count = 0
        for jid, nid in list(self._jobs.items()):
            if nid == from_node:
                self._jobs[jid] = to_node
                count += 1
        return count

    def is_cordoned(self, node_id: str) -> bool:
        """Check if a node is currently cordoned."""
        return self._nodes.get(node_id, {}).get("cordoned", False)

    def get_node_status(self, node_id: str) -> Dict[str, Any]:
        """Return the status dictionary for a node."""
        return self._nodes.get(node_id, {"status": "unknown"})
