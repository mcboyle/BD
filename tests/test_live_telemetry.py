"""Regression coverage for live runner telemetry on health endpoints."""
from __future__ import annotations


class _RunnerWithStatus:
    def __init__(self, status):
        self._status = status

    def get_status(self, *, light):
        assert light is True
        return self._status


def test_health_uses_current_and_legacy_runner_count_schemas(fresh_app):
    """The v1 health probe sums nested counts without dropping legacy ones."""
    from bulk_downloader.app_state import runners

    runners["current"] = _RunnerWithStatus({"counts": {"pending": 7, "running": 1}})
    runners["legacy"] = _RunnerWithStatus({"queued": 3, "active": 2})

    body = fresh_app.get("/api/health").get_json()

    assert body["queue_depth"] == 10
    assert body["active_downloads"] == 3
